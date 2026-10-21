"""Run with AstrBot on PYTHONPATH to test real decorators and command parsing."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

HAS_ASTRBOT = importlib.util.find_spec("astrbot") is not None
if os.environ.get('REQUIRE_ASTRBOT_TESTS') == '1' and not HAS_ASTRBOT:
    raise RuntimeError('AstrBot integration tests are required but AstrBot is unavailable')
if HAS_ASTRBOT:
    from astrbot.api import logger
    from astrbot_plugin_deepseek_search.main import DeepSeekSearchPlugin
    from astrbot_plugin_deepseek_search.output import AttachmentStore
    from astrbot.api.star import Context
    from astrbot.core.provider.register import llm_tools
    from astrbot.core.star.filter.command import CommandFilter, GreedyStr


@unittest.skipUnless(HAS_ASTRBOT, "Put an AstrBot checkout on PYTHONPATH for integration tests")
class AstrBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = SimpleNamespace(provider_config={"api_base": "https://api.deepseek.com/v1", "key": ["dummy"]})
        self.context = SimpleNamespace(
            get_provider_by_id=lambda _: self.provider,
            provider_manager=SimpleNamespace(llm_tools=llm_tools),
            get_llm_tool_manager=lambda: llm_tools,
        )
        self.context.add_llm_tools = lambda *tools: Context.add_llm_tools(self.context, *tools)
        self.plugin = DeepSeekSearchPlugin(self.context, {"provider_id": "deepseek"})
        self.event = SimpleNamespace(is_admin=lambda: False, get_group_id=lambda: "123",
                                     get_platform_id=lambda: "qq", get_sender_id=lambda: "user",
                                     unified_msg_origin="qq:group:123", stop_event=lambda: None,
                                     plain_result=lambda text: text, chain_result=lambda chain: chain)

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def test_framework_logger_and_real_tool_schema(self):
        self.assertIs(self.plugin.client.logger, logger)
        tools = {t.name: t for t in llm_tools.func_list}
        for name, arg in [("deepseek_web_search", "query"), ("deepseek_fetch_page", "url")]:
            self.assertEqual(tools[name].parameters['properties'][arg]['type'], 'string')
            self.assertTrue(tools[name].parameters['properties'][arg]['description'])
            self.assertIn(arg, tools[name].parameters['required'])
        # Omitted arguments still produce helpful errors if invoked outside schema validation.
        self.assertIn('1～128', await self.plugin.web_search(self.event))
        self.assertIn('网页地址', await self.plugin.fetch_page(self.event))

    async def test_space_preserving_command_and_result(self):
        params = CommandFilter('dssearch').validate_and_convert_params(['hello', 'world', 'site:example.com'], {'query': GreedyStr})
        self.assertEqual(params['query'], 'hello world site:example.com')
        self.plugin.client.request = AsyncMock(return_value={"results": []})
        results = [x async for x in self.plugin.search_command(self.event, **params)]
        self.assertIn('未找到', results[0])
        self.assertEqual(self.plugin.client.request.call_args.args[3]['query'], params['query'])

    async def test_access_denied_before_request(self):
        self.plugin.client.request = AsyncMock()
        self.plugin.config['allowed_groups'] = ['456']
        self.assertIn('允许列表', await self.plugin.web_search(self.event, 'test'))
        self.plugin.config['allowed_groups'] = []
        self.plugin.config['admin_only'] = True
        self.assertIn('管理员', await self.plugin.web_search(self.event, 'test'))
        self.plugin.client.request.assert_not_called()

    async def test_provider_key_not_sent_to_other_host(self):
        self.plugin.client.request = AsyncMock()
        self.plugin.config['base_url'] = 'https://other.example'
        self.assertIn('复用密钥仅支持官方', await self.plugin.web_search(self.event, 'test'))
        self.plugin.client.request.assert_not_called()

    async def test_direct_key_and_fetch_disabled(self):
        self.plugin.config.update(api_key='explicit', base_url='https://proxy.example/v1')
        self.assertEqual(self.plugin._credentials(), ('https://proxy.example', 'explicit'))
        self.plugin.config['fetch_enabled'] = False
        self.assertIn('已关闭', await self.plugin.fetch_page(self.event, 'https://example.com'))

    async def test_missing_key(self):
        self.provider.provider_config['key'] = None
        self.assertIn('API Key', await self.plugin.web_search(self.event, 'test'))

    async def test_tool_switches_and_reload_cleanup(self):
        await self.plugin.terminate()
        self.plugin = DeepSeekSearchPlugin(self.context, {"fetch_enabled": False})
        names = [t.name for t in llm_tools.func_list]
        self.assertIn('deepseek_web_search', names)
        self.assertNotIn('deepseek_fetch_page', names)
        old = self.plugin
        self.plugin = DeepSeekSearchPlugin(self.context, {"search_enabled": False})
        await old.terminate()
        self.assertIn(self.plugin.tools[0], llm_tools.func_list)
        await self.plugin.terminate()
        self.plugin = DeepSeekSearchPlugin(self.context, {"enabled": False})
        self.assertFalse(any(t.name.startswith('deepseek_') for t in llm_tools.func_list))

    async def test_registered_tool_executes_with_agent_context(self):
        self.plugin.client.request = AsyncMock(return_value={"results": []})
        tool = next(t for t in self.plugin.tools if t.name == 'deepseek_web_search')
        result = await tool.call(SimpleNamespace(context=SimpleNamespace(event=self.event)), query='test')
        self.assertIn('未找到', result)
        self.assertEqual(tool.handler_module_path, 'astrbot_plugin_deepseek_search.main')

    async def test_limits_shared_by_commands_and_tools(self):
        self.plugin.limiter.user_limit = 1
        self.plugin.client.request = AsyncMock(return_value={"results": []})
        _ = [r async for r in self.plugin.search_command(self.event, 'hello')]
        self.assertIn('频繁', await self.plugin.fetch_page(self.event, 'https://example.com'))
        self.assertEqual(self.plugin.client.request.await_count, 1)

    async def test_command_splitting_and_file_output(self):
        self.plugin.config['command_chunk_chars'] = 300
        text = '测试 https://example.com\n' * 200
        parts = [x async for x in self.plugin._command_results(self.event, text)]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertEqual(''.join(p.split('\n', 1)[1] for p in parts), text)
        self.plugin.config['command_output_mode'] = 'auto'
        with tempfile.TemporaryDirectory() as folder:
            self.plugin.attachments = AttachmentStore(folder)
            parts = [x async for x in self.plugin._command_results(self.event, text)]
            self.assertEqual(len(parts), 2)
            self.assertEqual(Path(parts[1][0].file).read_text(encoding='utf-8'), text)
            await self.plugin.attachments.close()

    async def test_file_creation_failure_falls_back_to_text(self):
        self.plugin.config['command_output_mode'] = 'file'
        self.plugin.attachments = SimpleNamespace(write=AsyncMock(side_effect=OSError('private path')), close=AsyncMock())
        parts = [x async for x in self.plugin._command_results(self.event, 'hello')]
        self.assertEqual(parts, ['hello'])
