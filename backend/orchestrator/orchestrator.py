"""Orchestrator — 에이전트 체인 실행.

Phase 0: Analyzer 1개 실행 (직선).
Phase 3.5: Planner→병렬 Analyzer→Critic 루프→Synthesizer 로 확장.
"""
from __future__ import annotations

from orchestrator.agent import Analyzer, Blackboard
from chat.retriever import retrieve
from chat import guard


class Orchestrator:
    def __init__(self):
        self.analyzer = Analyzer()

    async def answer(self, question: str, repo_id: str = "", context: str = "") -> Blackboard:
        state = Blackboard(question=question, repo_id=repo_id, context=context)
        # 1) 검색 (context 직접 주어지면 스킵 — 테스트/특수용)
        if repo_id and not context:
            state.context, state.hits = await retrieve(repo_id, question)
            state.trace.append(f"retrieve: {len(state.hits)} hits")
        # 2) 분석/답변 (에이전트 1개 직선. Phase 3.5에서 plan/critic 루프 추가)
        state = await self.analyzer.run(state)
        # 3) 환각방지: 컨텍스트 밖 인용 검출
        if state.hits:
            state.bad_cites = guard.check(state.answer, state.hits)
            if state.bad_cites:
                state.trace.append(f"guard: {len(state.bad_cites)} bad cites")
        return state


ORCH = Orchestrator()
