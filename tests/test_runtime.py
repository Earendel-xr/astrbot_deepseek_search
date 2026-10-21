import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_deepseek_search.cache import ResponseCache
from astrbot_plugin_deepseek_search.limits import RateLimiter, RateLimitError
from astrbot_plugin_deepseek_search.output import AttachmentStore, split_text


class CacheTests(unittest.TestCase):
    def test_capacity_lru_and_oversized_entry(self):
        cache = ResponseCache(max_bytes=6)
        cache.put('a', b'aaa')
        cache.put('b', b'bb')
        cache.get('a')
        cache.put('c', b'ccc')
        self.assertIsNone(cache.get('b'))
        self.assertEqual(cache.size_bytes, 6)
        cache.put('large', b'1234567')
        self.assertIsNone(cache.get('large'))
        self.assertEqual(cache.size_bytes, 6)
        cache.clear()
        self.assertEqual(cache.size_bytes, 0)

    def test_expiry_and_entry_bound(self):
        now = [0.0]
        cache = ResponseCache(ttl=5, max_bytes=100, max_entries=2, clock=lambda: now[0])
        for key in ['a', 'b', 'c']:
            cache.put(key, b'1')
        self.assertIsNone(cache.get('a'))
        now[0] = 5
        cache.purge()
        self.assertEqual(cache.size_bytes, 0)
        self.assertFalse(cache.entries)


class RateTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.limit = RateLimiter(user_per_minute=2, group_per_minute=3, cooldown=1, clock=lambda: self.now)

    def test_cooldown_allows_search_then_fetch(self):
        self.limit.check('u', 'g', 'search')
        with self.assertRaises(RateLimitError):
            self.limit.check('u', 'g', 'search')
        self.limit.check('u', 'g', 'fetch')
        self.now = 2
        with self.assertRaises(RateLimitError):
            self.limit.check('u', 'other_group', 'search')
        self.now = 60
        self.limit.check('u', 'g', 'search')

    def test_group_limits_and_platform_isolation(self):
        for u in ['a', 'b', 'c']:
            self.limit.check(('qq', u), ('qq', 'g'), 'search')
        with self.assertRaises(RateLimitError):
            self.limit.check(('qq', 'd'), ('qq', 'g'), 'search')
        self.limit.check(('telegram', 'a'), ('telegram', 'g'), 'search')
        self.limit.check(('qq', 'd'), None, 'search')

    def test_bucket_capacity_rejects_without_eviction(self):
        limit = RateLimiter(2, 0, 0, max_buckets=1, clock=lambda: self.now)
        limit.check('u', None, 'search')
        with self.assertRaises(RateLimitError):
            limit.check('other', None, 'search')
        limit.check('u', None, 'search')
        with self.assertRaises(RateLimitError):
            limit.check('u', None, 'search')
        self.now = 61
        limit.check('other', None, 'search')

    def test_disabled_limits(self):
        limit = RateLimiter(0, 0, 0)
        for _ in range(200):
            limit.check('u', 'g', 'search')
        self.assertFalse(limit.buckets)


class OutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_attachments_respect_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AttachmentStore(folder, max_files=1)
            try:
                results = await asyncio.gather(
                    *(store.write(str(i)) for i in range(8)), return_exceptions=True,
                )
                self.assertEqual(sum(isinstance(r, Path) for r in results), 1)
                self.assertEqual(sum(isinstance(r, OSError) for r in results), 7)
                self.assertEqual(len(list(Path(folder).glob('deepseek-result-*.txt'))), 1)
            finally:
                await store.close()

    async def test_split_does_not_lose_characters(self):
        for text in ['短文本', 'a' * 10001, ('中文🙂\nhttps://example.com/path\n' * 200)]:
            parts = split_text(text, 300)
            self.assertTrue(all(len(p) <= 300 for p in parts))
            merged = ''.join(re.sub(r'^\[\d+/\d+\]\n', '', part) for part in parts) if len(parts) > 1 else parts[0]
            self.assertEqual(merged, text)

    async def test_attachment_cleanup_preserves_unrelated_and_fresh_files(self):
        with tempfile.TemporaryDirectory() as folder:
            store = AttachmentStore(folder, ttl=10, max_files=1)
            unrelated = Path(folder) / 'other.txt'
            unrelated.write_text('keep', encoding='utf-8')
            path = await store.write('中文')
            self.assertEqual(path.read_text(encoding='utf-8'), '中文')
            with self.assertRaises(OSError):
                await store.write('over capacity')
            await store.close()
            self.assertTrue(path.exists())
            os.utime(path, (1, 1))
            store.cleanup()
            self.assertFalse(path.exists())
            self.assertTrue(unrelated.exists())
