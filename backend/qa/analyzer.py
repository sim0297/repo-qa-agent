"""자동 QA — 청크를 gemma4:31b가 검토해 발견사항(버그/보안/냄새) 추출.

map-reduce: 청크를 배치로 묶어(map) LLM 검토 → evidence 없는 것 필터 → 심각도 정렬(reduce).
ponytail: 정적도구(ruff/radon) 미연동, LLM-only + evidence 강제. 속도 위해 청크 캡.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import time

from config import MAX_QA_CHUNKS, QA_BATCH_CHARS, QA_DIR
from llm.ollama_client import chat_routed, ROUTER
from store import vector, manifest

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# repo_id -> 진행상태 (백그라운드 잡). 서버 살아있는 동안만.
JOBS: dict[str, dict] = {}

SYS = (
    "너는 정밀한 코드 품질 검토 에이전트다. 오탐(false positive)을 극도로 싫어한다. "
    "확실한 결함만 보고하고, 애매하면 보고하지 마라.\n"
    "\n"
    "카테고리 정의(엄격히 구분):\n"
    "- bug: 실행하면 실제로 틀린 결과·크래시·데이터 손상이 나는 명백한 로직 오류만. "
    "예: off-by-one, 잘못된 조건식, 항상 참/거짓인 분기, 자원 미해제로 인한 실제 누수. "
    "'개선하면 좋음'·'잠재적으로 문제될 수도'는 bug 아니다.\n"
    "- security: 실제 악용 가능한 취약점만 (하드코딩 시크릿, SQL/명령 인젝션, 안전하지 않은 역직렬화 등).\n"
    "- smell: 동작은 맞지만 구조가 나쁨 (중복, 거대 함수, 강결합). 오작동 아니면 bug 아니라 smell.\n"
    "- design: 설계·모듈경계·UI/CSS 일관성.\n"
    "- doc: 문서·주석 부재.\n"
    "\n"
    "★ 반드시 보고(조각만으로 확실한 것 — 절대 놓치지 마라):\n"
    "- 문자열 연결/포매팅으로 만든 SQL·셸 명령 (인젝션)\n"
    "- 하드코딩된 비밀번호·API키·토큰·시크릿\n"
    "- 안전하지 않은 역직렬화(eval, pickle.loads 등 신뢰 못할 입력)\n"
    "- 명백한 로직오류: off-by-one, 항상 참/거짓인 조건, 잘못된 비교연산자\n"
    "\n"
    "★ 오탐 방지(위 '반드시 보고'에 해당 안 되는 경우만 적용):\n"
    "1. 이 조각은 더 큰 프로그램의 일부다. 호출자·다른 파일에서 입력검증·예외처리·null체크가 "
    "이미 됐을 수 있다. 조각만으로 '검증 없음'·'예외처리 없음'·'null 위험'을 단정하지 마라.\n"
    "2. 확신 없으면 보고하지 않는다. '~일 수 있습니다'·'~할 가능성' 수준이면 제외.\n"
    "3. 스타일·취향·가독성은 bug/security가 아니다.\n"
    "4. file·line은 주어진 헤더 범위 안이어야 한다. 지어내지 마라. 결함 없으면 빈 배열 [].\n"
    "\n"
    "★ 언어 규칙: title, detail, suggestion은 예외 없이 100% 한국어. 영어 문장 금지. "
    "(category, severity는 영어 코드값 유지)\n"
    "반드시 JSON 배열만 출력. 예시:\n"
    '[{"category":"security","severity":"high","file":"auth.py","line":42,'
    '"title":"하드코딩된 비밀번호","detail":"소스에 비밀번호가 평문으로 박혀 있어 유출 위험이 있습니다.",'
    '"suggestion":"환경변수나 시크릿 매니저로 분리하세요."}]'
)


def _path(repo_id: str) -> str:
    os.makedirs(QA_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", repo_id)
    return os.path.join(QA_DIR, f"{safe}.json")


def load_saved(repo_id: str) -> dict | None:
    """자동저장된 QA 결과 로드(있으면)."""
    p = _path(repo_id)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def status(repo_id: str) -> dict:
    """진행중 잡 상태 or 저장된 결과."""
    job = JOBS.get(repo_id)
    if job and job["status"] == "running":
        return {"status": "running", "done": job["done"], "total": job["total"],
                "elapsed_sec": round(time.time() - job["t0"], 1)}
    saved = load_saved(repo_id)
    if saved:
        view = {k: v for k, v in saved.items() if k != "manifest"}  # manifest는 UI에 불필요
        return {"status": "done", **view}
    if job and job["status"] == "error":
        return {"status": "error", "error": job.get("error", "")}
    return {"status": "none"}


def start(repo_id: str) -> dict:
    """백그라운드 QA 시작. 이미 돌면 상태만 반환."""
    job = JOBS.get(repo_id)
    if job and job["status"] == "running":
        return status(repo_id)
    JOBS[repo_id] = {"status": "running", "t0": time.time(), "done": 0, "total": 0}
    asyncio.create_task(_run_job(repo_id))
    return status(repo_id)


async def _run_job(repo_id: str):
    job = JOBS[repo_id]
    try:
        all_ch = _prioritize(vector.all_chunks(repo_id))
        allowed = vector.files(repo_id)
        cur_man = manifest.load(repo_id)
        prior = load_saved(repo_id)
        prior_man = (prior or {}).get("manifest") or {}

        # 증분 조건: 이전 QA + 양쪽 매니페스트 존재
        incremental = bool(prior and prior_man and cur_man)
        if incremental:
            changed = {f for f, h in cur_man.items() if prior_man.get(f) != h}
            deleted = set(prior_man) - set(cur_man)
            review = [c for c in all_ch if c["metadata"]["file"] in changed][:MAX_QA_CHUNKS]
            kept = [f for f in prior.get("findings", [])
                    if f.get("file") not in changed and f.get("file") not in deleted]
        else:
            review = all_ch[:MAX_QA_CHUNKS]
            kept = []

        batches = list(_pack(review))
        job["total"] = len(batches)
        new_findings, reviewed = [], 0

        # 노드 풀 분산: 워커 = 노드 수, 각 워커가 최저부하 노드로 배치 처리 (Phase 7)
        queue = list(batches)

        async def worker():
            nonlocal reviewed
            while queue:
                batch = queue.pop(0)
                reviewed += len(batch)
                new_findings.extend(await _review(batch))
                job["done"] += 1

        n_workers = max(1, min(len(ROUTER.nodes), len(batches)))
        await asyncio.gather(*[worker() for _ in range(n_workers)])

        new_findings = [f for f in new_findings if _valid(f, allowed)]

        findings = kept + new_findings
        findings.sort(key=lambda f: SEVERITY_RANK.get(str(f.get("severity")).lower(), 3))
        for i, f in enumerate(findings):
            f["id"] = f"F-{i+1:03d}"

        # 이전 결과 대비 diff: 신규/유지/해결됨
        prev_findings = (prior or {}).get("findings", [])
        prev_keys = {_fkey(f): f for f in prev_findings}
        cur_keys = set()
        for f in findings:
            k = _fkey(f)
            cur_keys.add(k)
            f["status"] = "persist" if k in prev_keys else "new"
        resolved = [f for k, f in prev_keys.items() if k not in cur_keys] if prior else []

        result = {"repo_id": repo_id, "reviewed_chunks": reviewed,
                  "kept_findings": len(kept), "incremental": incremental,
                  "findings": findings, "manifest": cur_man,
                  "resolved": resolved, "compared": bool(prior),
                  "new_count": sum(1 for f in findings if f["status"] == "new"),
                  "resolved_count": len(resolved),
                  "elapsed_sec": round(time.time() - job["t0"], 1),
                  "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(_path(repo_id), "w", encoding="utf-8") as f:  # 자동저장
            json.dump(result, f, ensure_ascii=False, indent=2)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"


async def run_qa(repo_id: str) -> dict:
    """동기 실행(self-check용). API는 start()/status() 사용."""
    JOBS[repo_id] = {"status": "running", "t0": time.time(), "done": 0, "total": 0}
    await _run_job(repo_id)
    return load_saved(repo_id) or {"repo_id": repo_id, "findings": []}


def _fkey(f: dict) -> str:
    """발견 식별키. 라인번호 제외(수정하면 줄 밀림) — 파일+카테고리+제목."""
    title = re.sub(r"\s+", " ", str(f.get("title", ""))).strip().lower()
    return f"{f.get('file','')}|{f.get('category','')}|{title}"


def _prioritize(chunks):
    # (module) preamble·초단문 제외, 긴 것(위험 큰 함수) 우선
    real = [c for c in chunks
            if c["metadata"]["symbol"] != "(module)" and len(c["document"]) > 100]
    real.sort(key=lambda c: len(c["document"]), reverse=True)
    return real


def _pack(chunks):
    batch, size = [], 0
    for c in chunks:
        n = len(c["document"])
        if batch and size + n > QA_BATCH_CHARS:
            yield batch
            batch, size = [], 0
        batch.append(c)
        size += n
    if batch:
        yield batch


async def _review(batch) -> list[dict]:
    blocks = []
    for c in batch:
        m = c["metadata"]
        blocks.append(f"### {m['file']}:{m['start_line']}-{m['end_line']} ({m['symbol']})\n{c['document']}")
    user = "다음 코드들을 검토하라:\n\n" + "\n\n".join(blocks)
    raw = await chat_routed([{"role": "system", "content": SYS},
                             {"role": "user", "content": user}])
    return _parse(raw)


def _parse(raw: str) -> list[dict]:
    # ```json ... ``` 벗기고 첫 배열 추출
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _valid(f: dict, allowed: set) -> bool:
    cited = f.get("file", "")
    return bool(cited) and any(cited == a or a.endswith(cited) or cited.endswith(a)
                               for a in allowed)
