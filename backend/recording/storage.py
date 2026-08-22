"""Abstract storage provider and local filesystem implementation for recordings."""

import os
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Any

class StorageProvider:
    """Base class for storage providers."""
    def write(self, path: str, data: bytes, metadata: Optional[dict] = None) -> None:
        raise NotImplementedError
    def atomic_write(self, path: str, data: bytes, metadata: Optional[dict] = None) -> None:
        raise NotImplementedError
    def delete(self, path: str) -> None:
        raise NotImplementedError
    def get_url(self, path: str) -> str:
        raise NotImplementedError

class LocalFileStorageProvider(StorageProvider):
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
    def _full_path(self, path: str) -> Path:
        return self.base_dir / Path(path)
    def write(self, path: str, data: bytes, metadata: Optional[dict] = None) -> None:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        if metadata is not None:
            meta_path = full.with_suffix(full.suffix + ".meta")
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(metadata, mf)
    def atomic_write(self, path: str, data: bytes, metadata: Optional[dict] = None) -> None:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(delete=False, dir=full.parent)
        try:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, full)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        if metadata is not None:
            meta_path = full.with_suffix(full.suffix + ".meta")
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(metadata, mf)
    def delete(self, path: str) -> None:
        full = self._full_path(path)
        if full.is_file():
            full.unlink()
        meta_path = full.with_suffix(full.suffix + ".meta")
        if meta_path.is_file():
            meta_path.unlink()
    def get_url(self, path: str) -> str:
        return str(self._full_path(path).as_uri())
