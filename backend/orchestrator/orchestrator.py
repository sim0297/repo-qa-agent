"""Orchestrator — 에이전트 체인 실행.

- fast: 검색 → Analyzer → 환각검증 (기존, 빠름)
- deep: 검색 → Analyzer → Critic↔재작성 루프(최대 CRITIC_LOOPS) → 환각검증
"""
from __future__ import annotations

from orchestrator.agent import Analyzer, Critic, Blackboard
from chat.retriever import retrieve
from chat import guard

CRITIC_LOOPS = 2   # Critic 지적 반영 재작성 상한 (무한루프 방지)


class Orchestrator:
    def __init__(self):
        self.analyzer = Analyzer()
        self.critic = Critic()

    async def answer(self, question: str, repo_id: str = "",
                     context: str = "", deep: bool = False) -> Blackboard:
        state = Blackboard(question=question, repo_id=repo_id, context=context)
        if repo_id and not context:
            state.context, state.hits = await retrieve(repo_id, question)
            state.trace.append(f"retrieve: {len(state.hits)} hits")

        await self.analyzer.run(state)   # 초안

        if deep:   # Critic↔재작성 루프
            for _ in range(CRITIC_LOOPS):
                await self.critic.run(state)
                if not state.critique:   # 통과
                    break
                await self.analyzer.run(state)   # 지적 반영 재작성

        if state.hits:
            state.bad_cites = guard.check(state.answer, state.hits)
            if state.bad_cites:
                state.trace.append(f"guard: {len(state.bad_cites)} bad cites")
        return state


ORCH = Orchestrator()
