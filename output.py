"""Bounded command messages and optional expiring text attachments."""
import asyncio
import threading
import time
import uuid
from pathlib import Path


def split_text(text, limit=1800):
    """Preserve every character, prefer line boundaries, include part labels in limit."""
    if len(text) <= limit:
        return [text]
    payload_limit = limit - 32
    parts = []
    while text:
        cut = min(len(text), payload_limit)
        if cut < len(text):
            newline = text.rfind('\n', 0, cut)
            if newline >= payload_limit // 2:
                cut = newline + 1
        parts.append(text[:cut])
        text = text[cut:]
    return [f'[{i}/{len(parts)}]\n{part}' for i, part in enumerate(parts, 1)]


class AttachmentStore:
    def __init__(self, directory, ttl=600, max_files=128):
        self.directory = Path(directory)
        self.ttl = ttl
        self.max_files = max_files
        self.task = None
        self.write_lock = threading.Lock()

    def cleanup(self):
        if not self.directory.exists():
            return
        for path in self.directory.glob('deepseek-result-*.txt'):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_mtime <= time.time() - self.ttl:
                    path.unlink()
            except OSError:
                pass

    async def _clean_loop(self):
        while True:
            await asyncio.sleep(60)
            await asyncio.to_thread(self.cleanup)

    async def write(self, text):
        def create():
            # Serialize check + creation even if a caller cancels its to_thread await.
            with self.write_lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.cleanup()
                if sum(1 for _ in self.directory.glob('deepseek-result-*.txt')) >= self.max_files:
                    raise OSError('attachment capacity reached')
                path = self.directory / f'deepseek-result-{uuid.uuid4().hex}.txt'
                with path.open('x', encoding='utf-8') as f:
                    f.write(text)
                return path.resolve()
        path = await asyncio.to_thread(create)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._clean_loop())
        return path

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # Keep unexpired attachments: an adapter may still be transmitting them.
        await asyncio.to_thread(self.cleanup)
