"""FastAPI 진입점. Phase 0: /health, /api/nodes, /api/chat.

실행: cd backend && uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")

from config import MODEL, EMBED_MODEL
from llm.ollama_client import chat, embed, ROUTER
from orchestrator.orchestrator import ORCH
from ingest import indexer
from qa import analyzer
from store import owners
import auth
from fastapi import HTTPException


def _guard(repo_id: str, session: str):
    """세션 사용자가 이 레포 접근 권한 있나. 없으면 404(존재 숨김)."""
    if not owners.can_access(repo_id, auth.login_for(session)):
        raise HTTPException(404, "레포를 찾을 수 없습니다")

app = FastAPI(title="Repo QA Agent", version="0.1")


@app.get("/")
async def index():
    return FileResponse(_FRONTEND)


@app.get("/health")
async def health():
    out = {}
    try:
        await chat([{"role": "user", "content": "ping"}])
        out["model"] = f"{MODEL}: ok"
    except Exception as e:
        out["model"] = f"{MODEL}: FAIL {e}"
    try:
        v = await embed("ping")
        out["embed"] = f"{EMBED_MODEL}: ok (dim={len(v)})"
    except Exception as e:
        out["embed"] = f"{EMBED_MODEL}: FAIL {e}"
    return out


_BACKEND_INFO: dict = {}  # LiteLLM 노드의 실제 백엔드(api_base) 캐시


@app.get("/api/nodes")
async def nodes():
    stats = ROUTER.stats()
    for s in stats:
        if "litellm" in s["name"] and s["model"] not in _BACKEND_INFO:
            _BACKEND_INFO[s["model"]] = await _fetch_backend(s)
        s["backend"] = _BACKEND_INFO.get(s["model"]) or ("로컬 Ollama" if "local" in s["name"] else None)
        if "local" in s["name"]:
            s["backend"] = "이 머신 (GB10)"
    return {"nodes": stats}


async def _fetch_backend(s: dict):
    """LiteLLM /model/info에서 실제 백엔드 호스트·원모델 조회."""
    import httpx
    from config import NODES
    node = next((n for n in NODES if n["model"] == s["model"] and n.get("key")), None)
    if not node:
        return None
    try:
        base = node["url"].replace("/v1", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/model/info",
                            headers={"Authorization": f"Bearer {node['key']}"})
            for m in r.json().get("data", []):
                if m.get("model_name") == s["model"]:
                    p = m.get("litellm_params", {})
                    host = p.get("api_base", "?").replace("http://", "")
                    return f"{host} · {p.get('model', '')}"
    except Exception:
        return None


@app.get("/api/repos")
async def repos(session: str = ""):
    from store import vector
    # 등록된 레포 중 이 사용자가 소유(접근권)한 것만
    login = auth.login_for(session)
    allowed = set(indexer.registry()) & owners.repos_of(login)
    return {"repos": [r for r in vector.list_repos() if r["repo_id"] in allowed]}


@app.delete("/api/repos/{repo_id}")
async def delete_repo(repo_id: str, session: str = ""):
    """레포 완전 삭제: 임베딩·QA결과·매니페스트·등록부."""
    _guard(repo_id, session)
    owners.remove_repo(repo_id)
    import shutil
    from store import vector, manifest as mf
    from config import REPOS_DIR, QA_DIR
    vector.reset(repo_id)
    for p in (os.path.join(QA_DIR, f"{repo_id}.json"),
              mf._path(repo_id)):
        if os.path.exists(p):
            os.remove(p)
    old_clone = os.path.join(REPOS_DIR, repo_id)   # 과거 clone 잔재도 제거
    if os.path.isdir(old_clone):
        shutil.rmtree(old_clone, ignore_errors=True)
    indexer.unregister(repo_id)
    return {"deleted": repo_id}


class IngestRequest(BaseModel):
    source: str          # git URL (GitHub)
    token: str = ""      # 선택: 직접 붙여넣은 토큰
    session: str = ""    # 선택: 로그인 세션 (있으면 그 토큰 사용)


@app.post("/api/ingest")
async def api_ingest(req: IngestRequest):
    if not indexer.parse_github(req.source):
        raise HTTPException(400, "GitHub URL만 지원합니다 (예: https://github.com/org/repo)")
    login = auth.login_for(req.session)
    rid = indexer.derive_id(req.source)
    _guard(rid, req.session)                # 이미 남의 소유면 거부
    if login:
        owners.add(rid, login)              # 소유 도장
    token = req.token or auth.token_for(req.session)
    return indexer.start(req.source, token)


# ── GitHub 로그인 (Device Flow) ──
@app.post("/auth/device/start")
async def auth_start():
    return await auth.device_start()


class PollReq(BaseModel):
    device_code: str


@app.post("/auth/device/poll")
async def auth_poll(req: PollReq):
    return await auth.device_poll(req.device_code)


@app.get("/auth/me")
async def auth_me(session: str = ""):
    return {"login": auth.login_for(session)}


class LogoutReq(BaseModel):
    session: str = ""


@app.post("/auth/logout")
async def auth_logout(req: LogoutReq):
    auth.logout(req.session)
    return {"ok": True}


@app.get("/api/ingest/{repo_id}")
async def api_ingest_status(repo_id: str, session: str = ""):
    _guard(repo_id, session)
    return indexer.status(repo_id)


class QARequest(BaseModel):
    repo_id: str
    session: str = ""


@app.post("/api/qa")
async def api_qa_start(req: QARequest):
    _guard(req.repo_id, req.session)
    return analyzer.start(req.repo_id)


@app.get("/api/qa/{repo_id}")
async def api_qa_status(repo_id: str, session: str = ""):
    _guard(repo_id, session)
    return analyzer.status(repo_id)


class ChatRequest(BaseModel):
    question: str
    repo_id: str = ""
    context: str = ""
    session: str = ""


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    _guard(req.repo_id, req.session)
    state = await ORCH.answer(req.question, req.repo_id, req.context)
    sources = [{"file": h["metadata"]["file"],
                "lines": f"{h['metadata']['start_line']}-{h['metadata']['end_line']}",
                "symbol": h["metadata"]["symbol"]} for h in state.hits]
    return {"answer": state.answer, "sources": sources,
            "bad_citations": state.bad_cites, "trace": state.trace}
