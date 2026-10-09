"""A process-lifetime lock; never remove the lock file while a server is running."""
import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class ServiceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        stream = self.path.open('a+b', buffering=0)
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise AlreadyRunning('同一目录及端口的平台已经运行，请直接打开网页，不要重复启动。') from error
        self.file = stream
        return self

    def close(self):
        if self.file is None:
            return
        stream, self.file = self.file, None
        try:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __exit__(self, *args):
        self.close()
