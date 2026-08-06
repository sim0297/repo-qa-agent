"""파일 해시 매니페스트 — 증분 인덱싱/QA용. {상대경로: md5}."""
from __future__ import annotations
import hashlib
import json
import os
import re

from config import MANIFEST_DIR


def _path(repo_id: str) -> str:
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", repo_id)
    return os.path.join(MANIFEST_DIR, f"{safe}.json")


def load(repo_id: str) -> dict:
    p = _path(repo_id)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(repo_id: str, manifest: dict):
    with open(_path(repo_id), "w", encoding="utf-8") as f:
        json.dump(manifest, f)


def file_hash(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()
