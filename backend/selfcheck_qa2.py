"""Phase 2 self-check. 버그 심은 samples/ 인덱싱 후 자동 QA.

실행: cd backend && ../.venv/bin/python selfcheck_qa2.py
"""
import asyncio

from ingest.indexer import index_repo
from qa.analyzer import run_qa

SAMPLES = "../samples"


async def main():
    res = await index_repo(SAMPLES)
    print(f"인덱싱: {res}")

    qa = await run_qa(res["repo_id"])
    fs = qa["findings"]
    print(f"\n검토 청크={qa['reviewed_chunks']}, 발견={len(fs)}")
    for f in fs:
        print(f"  [{f.get('severity')}] {f.get('category')} {f.get('file')}:{f.get('line')} — {f.get('title')}")

    assert fs, "발견사항 0 — buggy.py에서 뭐라도 잡아야"
    # 모든 발견은 실존 파일 인용 (환각 필터 통과)
    assert all("buggy.py" in f.get("file", "") for f in fs), "환각 파일 인용"
    # 보안(하드코딩/injection) 하나는 잡혀야
    cats = {f.get("category") for f in fs}
    assert "security" in cats, f"보안 결함 미검출: {cats}"

    print("\nOK — 자동 QA 발견+evidence 검증 통과")


if __name__ == "__main__":
    asyncio.run(main())
