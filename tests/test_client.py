import asyncio
import unittest
from unittest.mock import patch

from aiohttp import web

from astrbot_plugin_deepseek_search.client import (
    DeepSeekClient, SearchError, api_base, fetch_body, format_fetch, format_search, search_body,
)


class ValidationTests(unittest.TestCase):
    def test_query_and_endpoint(self):
        self.assertEqual(api_base("https://api.deepseek.com/v1/"), "https://api.deepseek.com")
        self.assertEqual(search_body('a site:example.com')['query'], 'a site:example.com')
        for query in (" ", "字" * 129):
            with self.assertRaises(SearchError):
                search_body(query)
        for count in (0, 51, True):
            with self.assertRaises(SearchError):
                search_body("test", count)
        for url in ("http://api.deepseek.com", "https://user:secret@example.com", "https://example.com?a=1"):
            with self.assertRaises(SearchError):
                api_base(url)

    def test_fetch_validation_and_rendering(self):
        for url in ("file:///etc/passwd", "https://u:p@example.com", "http://", "https://example.com/a b"):
            with self.assertRaises(SearchError):
                fetch_body(url)
        self.assertEqual(fetch_body("https://example.com/a?q=1")["url"], "https://example.com/a?q=1")
        result = format_search({"results": [{"title": "Title", "url": "https://example.com", "summary": "abcdef"}]}, item_limit=3)
        self.assertIn("https://example.com", result)
        self.assertIn("abc", result)
        self.assertNotIn("abcdef", result)
        self.assertIn("未找到", format_search({"results": []}))
        self.assertIn("已截断", format_fetch({"content": "123456"}, "https://example.com", 3))


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.statuses = [200]
        self.data = {"results": [{"title": "Example", "url": "https://example.com", "summary": "test"}]}
        self.delay = 0
        self.raw = None
        async def handle(request):
            self.calls.append((request.path, request.headers.get("Authorization"), await request.json()))
            await asyncio.sleep(self.delay)
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            if self.raw is not None:
                return web.Response(body=self.raw, status=status)
            return web.json_response(self.data, status=status)
        app = web.Application()
        app.router.add_post('/v1/tools/{action}', handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        # HTTPS URL validation is tested separately; transport tests use loopback HTTP.
        self.normalizer = patch('astrbot_plugin_deepseek_search.client.api_base', return_value=f'http://127.0.0.1:{port}')
        self.normalizer.start()
        self.client = DeepSeekClient(timeout=2, cache_seconds=60)

    async def asyncTearDown(self):
        await self.client.close()
        self.normalizer.stop()
        await self.runner.cleanup()

    async def request(self, scope="a", key="test-key"):
        return await self.client.request("https://api.deepseek.com", key, "search", search_body("test"), scope)

    async def test_wire_format_cache_and_isolation(self):
        await self.request()
        await self.request()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0], ('/v1/tools/search', 'Bearer test-key', {'query': 'test', 'count': 5, 'need_content': False}))
        await self.request(scope="b")
        await self.request(key="different-key")
        self.assertEqual(len(self.calls), 3)

    async def test_expired_cache_and_empty_result(self):
        self.client.cache_seconds = 0.01
        self.data = {"results": []}
        self.assertEqual(await self.request(), {"results": []})
        await asyncio.sleep(0.02)
        await self.request()
        self.assertEqual(len(self.calls), 2)

    async def test_transient_retry(self):
        self.statuses = [429, 200]
        await self.request()
        self.assertEqual(len(self.calls), 2)

    async def test_auth_not_retried_or_leaked(self):
        self.statuses = [401]
        self.data = {"error": {"message": "sensitive upstream body"}}
        with self.assertRaises(SearchError) as err:
            await self.request()
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("sensitive", str(err.exception))
        self.assertNotIn("test-key", str(err.exception))

    async def test_redirect_not_followed(self):
        self.statuses = [302]
        with self.assertRaisesRegex(SearchError, "302"):
            await self.request()
        self.assertEqual(len(self.calls), 1)

    async def test_timeout_and_invalid_response(self):
        self.client.timeout = 0.02
        self.delay = 0.1
        with self.assertRaisesRegex(SearchError, "超时"):
            await self.request()
        self.client.timeout = 2
        self.delay = 0
        self.raw = b'not json'
        with self.assertRaisesRegex(SearchError, "解析"):
            await self.request()
        self.raw = None
        self.data = {"unexpected": True}
        with self.assertRaisesRegex(SearchError, "格式"):
            await self.request()

    async def test_size_limit_and_fetch(self):
        self.raw = b'x' * 100
        self.client.max_response_bytes = 50
        with self.assertRaisesRegex(SearchError, "过大"):
            await self.request()
        self.client.max_response_bytes = 1000
        self.raw = None
        self.data = {"title": "Example", "content": "# Page"}
        result = await self.client.request("https://api.deepseek.com", "test", "fetch", fetch_body("https://example.com"))
        self.assertEqual(result['content'], '# Page')
        self.assertEqual(self.calls[-1][0], '/v1/tools/fetch')

    async def test_identical_concurrent_requests_use_one_http_call(self):
        self.delay = 0.05
        results = await asyncio.gather(*(self.request() for _ in range(8)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(results[0], results[-1])
        results[0]['results'].clear()
        self.assertTrue(results[-1]['results'])

    async def test_cancelling_one_waiter_keeps_other_waiter_alive(self):
        self.delay = 0.08
        first = asyncio.create_task(self.request())
        second = asyncio.create_task(self.request())
        await asyncio.sleep(0.02)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertTrue((await second)['results'])
        self.assertEqual(len(self.calls), 1)

    async def test_close_cancels_inflight_and_cleans_cache(self):
        self.delay = 0.1
        waiter = asyncio.create_task(self.request())
        await asyncio.sleep(0.02)
        await self.client.close()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(self.client.inflight)
        self.assertEqual(self.client.cache.size_bytes, 0)
        with self.assertRaisesRegex(SearchError, '关闭'):
            await self.request()

    async def test_idle_expiry_and_no_sensitive_diagnostics(self):
        self.client.cache_seconds = 0.02
        with self.assertLogs('astrbot_plugin_deepseek_search.client', level='INFO') as logs:
            await self.request(scope='private-user', key='secret-token')
            await self.request(scope='private-user', key='secret-token')
        text = '\n'.join(logs.output)
        self.assertIn('cache_hit', text)
        for secret in ['private-user', 'secret-token', 'example.com']:
            self.assertNotIn(secret, text)
        await asyncio.sleep(0.08)
        self.assertEqual(self.client.cache.size_bytes, 0)

    async def test_pending_bound(self):
        self.client.timeout = 0.03
        self.client.max_pending = 1
        self.delay = 0.1
        first = asyncio.create_task(self.request())
        await asyncio.sleep(0.01)
        with self.assertRaisesRegex(SearchError, '较多'):
            await self.request(scope='other')
        with self.assertRaisesRegex(SearchError, '超时'):
            await first

    async def test_queue_wait_is_included_in_total_timeout(self):
        self.client.timeout = 0.03
        self.client.slots = asyncio.Semaphore(1)
        await self.client.slots.acquire()
        try:
            with self.assertRaisesRegex(SearchError, '超时'):
                await self.request()
            self.assertFalse(self.calls)
        finally:
            self.client.slots.release()
        self.client.timeout = 2
        await self.request()
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
