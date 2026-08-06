"""전역 설정. 값 하나 바뀌는 것만 여기 모음. 민감정보는 .env (커밋 금지)."""
import os

# .env 로드 (stdlib만, python-dotenv 불필요)
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# 추론 모델. Q&A=로컬 MODEL 고정(품질). QA=노드풀 분산.
MODEL = os.environ.get("RQA_MODEL", "gemma4:31b")
QA_LOCAL_MODEL = os.environ.get("RQA_QA_LOCAL", "gemma4:e4b")  # 로컬 QA는 빠른 모델(GPU 경합 회피)
EMBED_MODEL = os.environ.get("RQA_EMBED", "bge-m3:latest")

# GPU 노드 풀 (Phase 7). 로컬 GB10 + (있으면) 원격 LiteLLM. 노드별 모델명 다름 주의.
# QA 배치가 이 풀에 분산됨. Q&A는 로컬 MODEL 고정(품질). 키·주소는 .env에서.
NODES = [
    {"name": "local-gb10", "url": "http://localhost:11434/v1",
     "key": "", "model": QA_LOCAL_MODEL},
]
if os.environ.get("RQA_LITELLM_KEY"):
    NODES.append({
        "name": "siheung-litellm",
        "url": os.environ.get("RQA_LITELLM_URL", ""),
        "key": os.environ["RQA_LITELLM_KEY"],
        "model": os.environ.get("RQA_LITELLM_MODEL", "gemma4:26b"),
    })
OLLAMA_URL = "http://localhost:11434"  # 임베딩·Q&A 직결 (bge-m3는 로컬에만 있음)

REQUEST_TIMEOUT = 240  # 초. 느린 노드 배치는 이 시각 후 실패→타노드 재시도.

# 데이터 저장 (gitignore 대상)
import os as _os
_DATA = _os.environ.get("RQA_DATA", _os.path.join(_os.path.dirname(__file__), "..", "data"))
CHROMA_DIR = _os.path.abspath(_os.path.join(_DATA, "chroma"))
REPOS_DIR = _os.path.abspath(_os.path.join(_DATA, "repos"))   # URL clone 위치
QA_DIR = _os.path.abspath(_os.path.join(_DATA, "qa"))         # QA 결과 자동저장
MANIFEST_DIR = _os.path.abspath(_os.path.join(_DATA, "manifest"))  # 파일 해시(증분용)

GITHUB_CLIENT_ID = os.environ.get("RQA_GH_CLIENT_ID", "")  # OAuth Device Flow — .env에서 주입

EMBED_BATCH = 64
CHUNK_MAX_CHARS = 4000   # 초과 청크는 슬라이딩 분할

# 자동 QA. gemma4:31b 느려서 캡 필수(전수검사=몇시간).
MAX_QA_CHUNKS = int(_os.environ.get("RQA_MAX_QA_CHUNKS", "24"))
QA_BATCH_CHARS = 3500    # 한 LLM 콜에 묶는 코드 예산 (작을수록 콜당 빠름)
