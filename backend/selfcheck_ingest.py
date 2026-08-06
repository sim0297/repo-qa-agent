"""Phase 1 self-check. backend/ 를 인덱싱하고 검색 정확도 확인.

실행: cd backend && ../.venv/bin/python selfcheck_ingest.py
"""
import asyncio
from pathlib import Path

from ingest.collector import collect_files
from ingest.chunker import chunk_file
from ingest.indexer import index_repo
from llm.ollama_client import embed
from store import vector

BACKEND = str(Path(__file__).parent)


async def main():
    # 수집: 파이썬 파일 잡힘, 바이너리/무시셋 제외
    files = collect_files(Path(BACKEND))
    assert any(f.name == "router.py" for f in files), "router.py 수집 실패"
    assert all(f.suffix != ".pyc" for f in files), "바이너리 새어나옴"

    # 청킹: 함수 단위 심볼 잡힘
    chunks = chunk_file(Path(BACKEND) / "llm" / "router.py", Path(BACKEND))
    syms = {c.symbol for c in chunks}
    assert "NodeRouter" in syms, f"클래스 심볼 누락: {syms}"
    assert all(c.start_line >= 1 for c in chunks), "라인번호 이상"

    # 인덱싱 (임베딩 → chroma)
    res = await index_repo(BACKEND)
    assert res["chunks"] > 0 and res["indexed"] == res["chunks"], res
    print(f"인덱싱: {res}")

    # 검색: 노드 선택 질문 → router 코드 회수
    qv = await embed("어떤 GPU 노드로 요청을 보낼지 고르는 코드")
    hits = vector.query(res["repo_id"], qv, k=5)
    top_files = [h["metadata"]["file"] for h in hits]
    assert any("router.py" in f for f in top_files), f"검색 실패: {top_files}"

    print(f"검색 top5 파일: {top_files}")
    print("OK — 수집/청킹/인덱싱/검색 통과")


if __name__ == "__main__":
    asyncio.run(main())
