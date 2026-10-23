import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

NEW_FILE_MODE = 0o644


def atomic_write_bytes(
    target: Path,
    payload: bytes,
    precondition: Callable[[], None] | None = None,
) -> None:
    """Write via a temp file in the same directory, then os.replace.

    `precondition` runs immediately before the replace so callers can
    re-check that the target did not change while the temp file was written.
    An existing file keeps its permission bits; new files get 0o644.
    """

    fd, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = NEW_FILE_MODE

        os.chmod(temp_path, mode)

        if precondition is not None:
            precondition()

        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
