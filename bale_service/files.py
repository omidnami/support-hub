"""Bounded temporary disk cache for files downloaded from providers.

The cache is deliberately not a system of record: entries expire after a TTL
and are pruned to a maximum total size. Nothing here is message history.
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class FileCache:
    def __init__(self, directory=None, *, ttl=3600, max_bytes=512 * 1024 * 1024):
        if not directory:
            directory = Path(tempfile.gettempdir()) / "support-hub-files"
        self.directory = Path(directory)
        self.ttl = max(1, int(ttl))
        self.max_bytes = max(0, int(max_bytes))

    @staticmethod
    def key(robot_id, file_id):
        digest = hashlib.sha256(
            f"{robot_id}\0{file_id}".encode("utf-8")
        ).hexdigest()
        return digest

    def _data_path(self, key):
        return self.directory / f"{key}.bin"

    def path(self, key):
        return self._data_path(key)

    def _meta_path(self, key):
        return self.directory / f"{key}.json"

    def _ensure_directory(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def get(self, key):
        """Return (path, metadata) for a fresh entry, or None."""
        data_path = self._data_path(key)
        meta_path = self._meta_path(key)
        try:
            if not data_path.is_file() or not meta_path.is_file():
                return None
            if time.time() - data_path.stat().st_mtime > self.ttl:
                self._remove(key)
                return None
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                self._remove(key)
                return None
            content_type = metadata.get("content_type")
            file_name = metadata.get("file_name")
            if not isinstance(content_type, str) or not content_type:
                content_type = "application/octet-stream"
            if not isinstance(file_name, str) or not file_name:
                file_name = f"{key}.bin"
            return data_path, {
                "content_type": content_type,
                "file_name": file_name,
            }
        except (OSError, ValueError):
            self._remove(key)
            return None

    def put(self, key, data, content_type, file_name):
        """Write an entry atomically, then prune the cache."""
        self._ensure_directory()
        data_path = self._data_path(key)
        meta_path = self._meta_path(key)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{key[:16]}-", dir=self.directory
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, data_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        metadata = {
            "content_type": content_type or "application/octet-stream",
            "file_name": file_name or f"{key}.bin",
            "size": len(data),
            "created_at": int(time.time()),
        }
        meta_temporary = f"{meta_path}.tmp"
        try:
            with open(meta_temporary, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(meta_temporary, meta_path)
        except BaseException:
            try:
                os.unlink(meta_temporary)
            except OSError:
                pass
            self._remove(key)
            raise
        self.cleanup()

    def cleanup(self):
        """Delete expired entries and keep the total size under the cap."""
        if not self.directory.is_dir():
            return
        now = time.time()
        entries = []
        total = 0
        for data_path in self.directory.glob("*.bin"):
            key = data_path.stem
            meta_path = self._meta_path(key)
            try:
                stat = data_path.stat()
            except OSError:
                continue
            if not meta_path.is_file() or now - stat.st_mtime > self.ttl:
                self._remove(key)
                continue
            entries.append((stat.st_mtime, stat.st_size, key))
            total += stat.st_size
        for meta_path in self.directory.glob("*.json"):
            if not self._data_path(meta_path.stem).is_file():
                try:
                    meta_path.unlink()
                except OSError:
                    pass
        if total <= self.max_bytes:
            return
        for _mtime, size, key in sorted(entries):
            self._remove(key)
            total -= size
            if total <= self.max_bytes:
                break

    def _remove(self, key):
        for path in (self._data_path(key), self._meta_path(key)):
            try:
                path.unlink()
            except OSError:
                pass
