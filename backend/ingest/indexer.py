"""레포 → 청킹 → bge-m3 임베딩 → Chroma 인덱싱."""
from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path as _P

import httpx

from config import REPOS_DIR
from ingest.collector import collect_files
from ingest.chunker import chunk_file
from llm.ollama_client import embed_batch
from store import vector, manifest

# 백그라운드 인덱싱 잡 (repo_id 키)
JOBS: dict[str, dict] = {}

# git URL로 인덱싱된 레포 등록부 (드롭다운 노출용)
_REGISTRY = os.path.join(os.path.dirname(REPOS_DIR), "git_repos.json")


def registry() -> list[str]:
    if os.path.exists(_REGISTRY):
        with open(_REGISTRY, encoding="utf-8") as f:
            return json.load(f)
    # 최초 1회: 과거 clone 방식 레포를 승계
    old = sorted(os.listdir(REPOS_DIR)) if os.path.isdir(REPOS_DIR) else []
    _save_registry(old)
    return old


def _save_registry(ids: list[str]):
    os.makedirs(os.path.dirname(_REGISTRY), exist_ok=True)
    with open(_REGISTRY, "w", encoding="utf-8") as f:
        json.dump(sorted(set(ids)), f, ensure_ascii=False)


def register(repo_id: str):
    _save_registry(registry() + [repo_id])


def unregister(repo_id: str):
    _save_registry([r for r in registry() if r != repo_id])


def parse_github(source: str) -> tuple[str, str] | None:
    """github URL → (owner, repo). 아니면 None."""
    m = (re.match(r"(?:https?://github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?/?$", source.strip()))
    return (m.group(1), m.group(2)) if m else None


def derive_id(source: str) -> str:
    """다운로드 없이 repo_id 미리 계산 (잡 키용)."""
    gh = parse_github(source)
    if gh:
        return gh[1]
    if source.startswith(("http://", "https://", "git@")):
        return re.sub(r"\.git$", "", source.rstrip("/").split("/")[-1])
    return _P(source).expanduser().name


def start(source: str, token: str = "") -> dict:
    rid = derive_id(source)
    job = JOBS.get(rid)
    if job and job["status"] == "running":
        return status(rid)
    JOBS[rid] = {"status": "running", "t0": time.time(),
                 "phase": "준비", "done": 0, "total": 0}
    asyncio.create_task(_run(source, rid, token))
    return {"repo_id": rid, **status(rid)}


def status(rid: str) -> dict:
    job = JOBS.get(rid)
    if not job:
        return {"status": "none"}
    out = {"status": job["status"], "phase": job.get("phase", ""),
           "done": job["done"], "total": job["total"],
           "elapsed_sec": round(time.time() - job["t0"], 1)}
    if job["status"] == "done":
        out.update(job.get("result", {}))
    if job["status"] == "error":
        out["error"] = job.get("error", "")
    return out


async def _run(source: str, rid: str, token: str = ""):
    job = JOBS[rid]
    try:
        job["result"] = await index_repo(source, job, token)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"


async def index_repo(source: str, job: dict | None = None, token: str = "") -> dict:
    """증분 인덱싱: 바뀐/새 파일만 재청킹·재임베딩, 삭제 파일은 청크 제거.

    git URL → tarball 다운로드 → 임시폴더 → 인덱싱 → 삭제 (코드 원본 잔존 0).
    로컬 경로는 셀프체크용으로만 유지 (API에선 차단됨).
    """
    job = job if job is not None else {}
    tmp: str | None = None
    try:
        if source.startswith(("http://", "https://", "git@")):
            job["phase"] = "tarball 다운로드"
            root, repo_id, tmp = await _fetch_tarball(source, token)
        else:
            root, repo_id = _P(source).expanduser().resolve(), _P(source).expanduser().name
            if not root.is_dir():
                raise ValueError(f"디렉터리 아님: {source}")
        result = await _index_dir(root, repo_id, job)
        if tmp:
            register(repo_id)
        return result
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)  # 코드 원본 즉시 삭제


async def _fetch_tarball(source: str, token: str) -> tuple[_P, str, str]:
    """GitHub tarball API로 다운로드 → (압축해제 루트, repo_id, 임시폴더).

    토큰은 요청자가 준 것만 사용(저장 안 함). 서버 계정 토큰 폴백 없음
    — 각 사용자가 자기 권한 레포만 인덱싱하도록.
    """
    gh = parse_github(source)
    if not gh:
        raise ValueError("GitHub URL만 지원합니다 (https://github.com/owner/repo)")
    owner, repo = gh
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    tmp = tempfile.mkdtemp(prefix="rqa_")
    tar_path = os.path.join(tmp, "repo.tar.gz")
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as c:
        async with c.stream("GET", f"https://api.github.com/repos/{owner}/{repo}/tarball",
                            headers=headers) as r:
            if r.status_code in (401, 403, 404):
                shutil.rmtree(tmp, ignore_errors=True)
                if not token:
                    raise ValueError("NEED_TOKEN")   # private 추정 → UI가 토큰 요청
                raise ValueError(f"접근 실패({r.status_code}) — 토큰 권한 확인 (해당 레포 Contents:Read 필요)")
            r.raise_for_status()
            with open(tar_path, "wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)
    with tarfile.open(tar_path) as t:
        t.extractall(tmp, filter="data")
    os.remove(tar_path)
    # tarball 최상위 = {owner}-{repo}-{sha}/ 단일 폴더
    tops = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
    if not tops:
        raise ValueError("tarball 압축해제 실패")
    return _P(tmp) / tops[0], repo, tmp


async def _index_dir(root: _P, repo_id: str, job: dict) -> dict:
    job["phase"] = "파일 수집"
    files = collect_files(root)

    prev = manifest.load(repo_id)
    cur, changed = {}, []
    for f in files:
        rel = str(_P(f).relative_to(root))
        h = manifest.file_hash(f)
        cur[rel] = h
        if prev.get(rel) != h:      # 새 파일 or 내용 변경
            changed.append((f, rel))
    deleted = [rel for rel in prev if rel not in cur]

    # 첫 인덱싱이면 통째로(안전), 아니면 증분
    first = not prev
    if first:
        vector.reset(repo_id)

    # 바뀐/삭제 파일의 기존 청크 제거
    if not first:
        for _, rel in changed:
            vector.delete_file(repo_id, rel)
    for rel in deleted:
        vector.delete_file(repo_id, rel)

    # 바뀐 파일만 청킹·임베딩
    job["phase"] = "코드 청킹"
    chunks = []
    for f, _rel in changed:
        chunks.extend(chunk_file(f, root))
    if chunks:
        # 배치 단위 임베딩 + 진행률
        from config import EMBED_BATCH
        job["phase"] = "임베딩"
        job["total"] = (len(chunks) + EMBED_BATCH - 1) // EMBED_BATCH
        embeddings = []
        for i in range(0, len(chunks), EMBED_BATCH):
            embeddings.extend(await embed_batch([c.text for c in chunks[i:i + EMBED_BATCH]]))
            job["done"] += 1
        vector.add(
            repo_id,
            ids=[f"{c.file}:{c.start_line}:{i}" for i, c in enumerate(chunks)],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[{"file": c.file, "start_line": c.start_line,
                        "end_line": c.end_line, "symbol": c.symbol,
                        "language": c.language} for c in chunks],
        )

    job["phase"] = "저장"
    manifest.save(repo_id, cur)
    return {"repo_id": repo_id, "files": len(files),
            "changed_files": len(changed), "deleted_files": len(deleted),
            "chunks_added": len(chunks), "indexed": vector.count(repo_id),
            "incremental": not first}
