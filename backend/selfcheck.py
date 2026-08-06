"""Phase 0 self-check. 실제 Ollama 상대로 골격 동작 검증.

실행: cd backend && ../.venv/bin/python selfcheck.py
"""
import asyncio

from llm.ollama_client import chat, embed, ROUTER
from orchestrator.orchestrator import ORCH


async def main():
    # Router noop: 단일 노드 그대로 반환
    assert ROUTER.pick("x") == ROUTER.urls[0]

    # 임베딩 동작 + 차원
    v = await embed("hello")
    assert isinstance(v, list) and len(v) > 100, f"embed dim={len(v)}"

    # 채팅 동작
    txt = await chat([{"role": "user", "content": "2+2? 숫자만."}])
    assert txt and "4" in txt, f"chat={txt!r}"

    # Orchestrator 1-에이전트 경로
    state = await ORCH.answer("이 프로젝트는 무슨 언어로 짜였나?", context="app.py: FastAPI Python")
    assert state.answer, "orchestrator 빈 답변"
    assert state.trace == ["analyzer: answered"], state.trace

    print("OK — router/embed/chat/orchestrator 모두 통과")
    print(f"embed dim={len(v)}")


if __name__ == "__main__":
    asyncio.run(main())
