"""文件锁模块。

对同一 term+campus+building 的刷新操作加锁，
确保同时只有一个请求访问上游。
"""

from __future__ import annotations

import os
import time
from pathlib import Path


class FileLock:
    """基于文件的简单互斥锁。

    用法：
        lock = FileLock(lock_path, timeout=8)
        acquired = lock.acquire()
        if acquired:
            try:
                ...  # 刷新上游
            finally:
                lock.release()
    """

    def __init__(
        self,
        lock_path: Path,
        timeout: float = 8.0,
        stale_after: float = 60.0,
    ):
        self._lock_path = Path(lock_path)
        self._timeout = timeout
        # Retained as a source-compatible argument. OS advisory locks are
        # released by the kernel when a process exits, so wall-clock stale
        # detection is both unnecessary and unsafe for long-running calls.
        del stale_after
        self._fd: int | None = None

    def acquire(self) -> bool:
        """尝试获取锁，超时返回 False。"""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            fd: int | None = None
            try:
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                self._lock_fd(fd)
                self._fd = fd
                return True
            except OSError:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                time.sleep(0.05)

        return False

    def release(self) -> None:
        """释放锁。"""
        if self._fd is None:
            return
        try:
            self._unlock_fd(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    @staticmethod
    def _lock_fd(fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_fd(fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *args: object) -> None:
        self.release()


def lock_path_for(term_code: str, campus_code: str, building_code: str, locks_dir: Path) -> Path:
    """生成锁文件路径。"""
    safe_name = (
        f"{term_code}_{campus_code}_{building_code}"
        .replace("/", "_")
        .replace("\\", "_")
        .replace(",", "_")
    )
    return locks_dir / f"{safe_name}.lock"
