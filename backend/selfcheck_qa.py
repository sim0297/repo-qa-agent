"""Phase 3 self-check. backend/ 인덱싱 후 실제 Q&A + 환각검증.

실행: cd backend && ../.venv/bin/python selfcheck_qa.py
"""
import asyncio

from ingest.indexer import index_repo
from orchestrator.orchestrator import ORCH
from chat import guard

BACKEND = "."


async def main():
    res = await index_repo(BACKEND)
    rid = res["repo_id"]
    print(f"인덱싱: {res}")

    # 1) 실제 코드 질문 → router.py 인용, 환각 없음
    s = await ORCH.answer("요청 보낼 GPU 노드는 어떻게 고르나?", repo_id=rid)
    print(f"\nQ1 답변:\n{s.answer[:400]}")
    print(f"trace={s.trace} bad_cites={s.bad_cites}")
    assert s.hits, "검색 0"
    assert not s.bad_cites, f"환각 인용: {s.bad_cites}"
    assert "router" in s.answer.lower(), "router 언급 없음"

    # 2) guard 단위검증: 없는 파일 인용은 잡아야
    fake = "이건 nonexistent/fake_module.py:99 에 있습니다."
    bad = guard.check(fake, s.hits)
    assert "nonexistent/fake_module.py:99" in bad, f"환각 미검출: {bad}"

    # 3) 컨텍스트 밖 질문 → 못찾음 (환각 대신 거절)
    s2 = await ORCH.answer("이 레포의 쿠버네티스 헬름 차트 설정 알려줘", repo_id=rid)
    print(f"\nQ3 답변:\n{s2.answer[:300]}")
    assert not s2.bad_cites, f"환각 인용: {s2.bad_cites}"

    print("\nOK — 검색+답변+인용검증 통과")


if __name__ == "__main__":
    asyncio.run(main())
