"""레포 소유권 — {repo_id: [login들]}. 같은 public repo는 여러 명 소유 가능."""
from __future__ import annotations
import json
import os

from config import _DATA

_PATH = os.path.join(_DATA, "owners.json")


def _load() -> dict:
    if os.path.exists(_PATH):
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(d: dict):
    os.makedirs(_DATA, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def add(repo_id: str, login: str):
    if not login:
        return
    d = _load()
    d.setdefault(repo_id, [])
    if login not in d[repo_id]:
        d[repo_id].append(login)
        _save(d)


def owners(repo_id: str) -> list[str]:
    return _load().get(repo_id, [])


def can_access(repo_id: str, login: str | None) -> bool:
    o = owners(repo_id)
    if not o:            # 소유자 없는 레거시 레포 = 공용(과거 데이터)
        return True
    return bool(login) and login in o


def repos_of(login: str | None) -> set[str]:
    d = _load()
    return {rid for rid, o in d.items() if not o or (login and login in o)}


def remove_repo(repo_id: str):
    d = _load()
    if d.pop(repo_id, None) is not None:
        _save(d)
