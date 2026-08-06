"""레포 파일 수집. git repo면 `git ls-files`로 .gitignore 공짜 존중."""
from __future__ import annotations
import subprocess
from pathlib import Path

# git repo 아닐 때만 쓰는 폴백 무시셋
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
               "dist", "build", ".next", "target", ".idea", ".mypy_cache"}
IGNORE_EXT = {".lock", ".min.js", ".map", ".png", ".jpg", ".jpeg", ".gif",
              ".svg", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf"}
MAX_BYTES = 1_000_000  # 1MB 넘는 파일 스킵 (생성물/데이터 덤프)


def collect_files(root: Path) -> list[Path]:
    root = Path(root)
    files = _git_tracked(root)
    if files is None:  # git repo 아님 → 폴백 walk
        files = _walk(root)
    return [f for f in files if _keep(f)]


def _git_tracked(root: Path) -> list[Path] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        return [root / line for line in out.stdout.splitlines() if line]
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _walk(root: Path) -> list[Path]:
    res = []
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        res.append(p)
    return res


def _keep(f: Path) -> bool:
    if not f.is_file():
        return False
    if f.suffix.lower() in IGNORE_EXT:
        return False
    try:
        if f.stat().st_size > MAX_BYTES:
            return False
        chunk = f.read_bytes()[:2048]
    except OSError:
        return False
    if b"\x00" in chunk:  # 바이너리
        return False
    return True
