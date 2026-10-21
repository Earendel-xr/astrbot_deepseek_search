"""Bounded in-memory byte cache; parsed JSON objects are never retained."""
import time
from collections import OrderedDict


class ResponseCache:
    def __init__(self, ttl=60, max_bytes=8 * 1024 * 1024, max_entries=128, clock=time.monotonic):
        self.ttl = ttl
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.clock = clock
        self.entries = OrderedDict()
        self.size_bytes = 0

    def _remove(self, key):
        _, raw = self.entries.pop(key)
        self.size_bytes -= len(raw)

    def purge(self):
        now = self.clock()
        for key, (expiry, _) in list(self.entries.items()):
            if expiry <= now:
                self._remove(key)

    def get(self, key):
        self.purge()
        entry = self.entries.get(key)
        if entry is None:
            return None
        self.entries.move_to_end(key)
        return entry[1]

    def put(self, key, raw):
        self.purge()
        if key in self.entries:
            self._remove(key)
        if self.ttl <= 0 or self.max_entries <= 0 or len(raw) > self.max_bytes or self.max_bytes <= 0:
            return
        self.entries[key] = (self.clock() + self.ttl, raw)
        self.size_bytes += len(raw)
        while self.size_bytes > self.max_bytes or len(self.entries) > self.max_entries:
            self._remove(next(iter(self.entries)))

    def clear(self):
        self.entries.clear()
        self.size_bytes = 0
