"""Atomic admission in one event loop; rejected calls never consume quota."""
import math
import time
from collections import deque


class RateLimitError(Exception):
    pass


class RateLimiter:
    def __init__(self, user_per_minute=10, group_per_minute=30, cooldown=1, max_buckets=4096, clock=time.monotonic):
        self.user_limit = user_per_minute
        self.group_limit = group_per_minute
        self.cooldown = cooldown
        self.max_buckets = max_buckets
        self.clock = clock
        self.buckets = {}

    def check(self, user, group, action):
        now = self.clock()
        for key, events in list(self.buckets.items()):
            while events and events[0] <= now - 60:
                events.popleft()
            if not events:
                del self.buckets[key]
        checks = []
        if self.user_limit > 0:
            checks.append((('user', user), self.user_limit, 60, '个人'))
        if group and self.group_limit > 0:
            checks.append((('group', group), self.group_limit, 60, '群聊'))
        # Separate action cooldowns allow immediate search -> fetch.
        if self.cooldown > 0:
            checks.append((('cooldown', user, action), 1, self.cooldown, '同类工具'))
        for key, limit, window, label in checks:
            events = self.buckets.get(key, ())
            live = [t for t in events if t > now - window]
            if len(live) >= limit:
                wait = max(1, math.ceil(live[-limit] + window - now))
                raise RateLimitError(f"{label}调用过于频繁，请 {wait} 秒后再试；请勿立即反复调用。")
        missing = sum(key not in self.buckets for key, *_ in checks)
        if len(self.buckets) + missing > self.max_buckets:
            raise RateLimitError("当前调用用户较多，请稍后再试。")
        for key, _, window, _ in checks:
            events = self.buckets.setdefault(key, deque())
            while events and events[0] <= now - window:
                events.popleft()
            events.append(now)

    def clear(self):
        self.buckets.clear()
