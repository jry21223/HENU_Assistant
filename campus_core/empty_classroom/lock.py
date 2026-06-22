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

    def __init__(self, lock_path: Path, timeout: float = 8.0):
        self._lock_path = Path(lock_path)
        self._timeout = timeout
        self._fd: int | None = None

    def acquire(self) -> bool:
        """尝试获取锁，超时返回 False。"""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout

        while time.monotonic() < deadline:
            try:
                # POSIX: O_CREAT | O_EXCL 保证原子性
                self._fd = os.open(
                    str(self._lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                )
                os.write(self._fd, str(os.getpid()).encode())
                return True
            except FileExistsError:
                # 检查锁是否过期（超过 60 秒视为僵尸锁）
                if self._is_stale():
                    self._break_stale()
                    continue
                time.sleep(0.1)
            except OSError:
                time.sleep(0.1)

        return False

    def release(self) -> None:
        """释放锁。"""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _is_stale(self) -> bool:
        """检查锁文件是否超过 60 秒（僵尸锁）。"""
        try:
            mtime = self._lock_path.stat().st_mtime
            return (time.time() - mtime) > 60
        except OSError:
            return False

    def _break_stale(self) -> None:
        """移除僵尸锁文件。"""
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass

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
