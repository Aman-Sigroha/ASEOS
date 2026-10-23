"""Memory-bounded capture of a process output stream.

Keeps the first `head_bytes` and the last `tail_bytes` in memory (errors
usually live at the end). Once the stream is larger than both, every byte
is also spooled to a log file (up to `max_spool_bytes`) so the complete
output can be retrieved by reference instead of being returned inline.
"""

import threading
from pathlib import Path
from typing import BinaryIO


class BoundedCapture:
    def __init__(
        self,
        head_bytes: int,
        tail_bytes: int,
        spool_path: Path | None,
        max_spool_bytes: int,
    ) -> None:
        self.head_limit = head_bytes
        self.tail_limit = tail_bytes
        self.spool_path = spool_path
        self.max_spool_bytes = max_spool_bytes
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self.spooled = 0
        self.spool_truncated = False
        self._spool: BinaryIO | None = None
        self._lock = threading.Lock()

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            limit = self.head_limit + self.tail_limit

            if (
                self._spool is None
                and self.spool_path is not None
                and self.total + len(chunk) > limit
            ):
                # Up to now head + tail still hold the entire stream.
                self.spool_path.parent.mkdir(parents=True, exist_ok=True)
                self._spool = open(self.spool_path, "wb")
                self._append(bytes(self.head) + bytes(self.tail))

            self.total += len(chunk)
            room = self.head_limit - len(self.head)

            if room > 0:
                self.head += chunk[:room]
                rest = chunk[room:]
            else:
                rest = chunk

            if rest:
                self.tail += rest
                overflow = len(self.tail) - self.tail_limit

                if overflow > 0:
                    del self.tail[:overflow]

            if self._spool is not None:
                self._append(chunk)

    def _append(self, data: bytes) -> None:
        assert self._spool is not None
        room = self.max_spool_bytes - self.spooled

        if room <= 0:
            self.spool_truncated = True
            return

        if len(data) > room:
            data = data[:room]
            self.spool_truncated = True

        self._spool.write(data)
        self.spooled += len(data)

    def close(self) -> None:
        with self._lock:
            if self._spool is not None:
                self._spool.close()
                self._spool = None

    @property
    def truncated(self) -> bool:
        return self.total > self.head_limit + self.tail_limit

    def text(self) -> str:
        if not self.truncated:
            return (bytes(self.head) + bytes(self.tail)).decode(
                "utf-8", errors="replace"
            )

        omitted = self.total - len(self.head) - len(self.tail)
        head = bytes(self.head).decode("utf-8", errors="replace")
        tail = bytes(self.tail).decode("utf-8", errors="replace")
        return f"{head}\n... [{omitted} bytes omitted] ...\n{tail}"
