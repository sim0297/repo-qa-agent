"""Agent 베이스 + Analyzer.

Phase 0~3: Analyzer 1개로 직선 동작.
Phase 3.5: Planner/Retriever/Critic/Synthesizer 추가 (에이전트 수만 늘림, 재작성 0).
"""
from __future__ import annotations
from dataclasses import dataclass, field

from config import MODEL
from llm.ollama_client import chat


@dataclass
class Blackboard:
    """에이전트 공유 상태. Phase 3.5에서 subtasks/critiques 등 필드 확장."""
    question: str
    repo_id: str = ""
    context: str = ""          # RAG 검색 코드청크
    hits: list = field(default_factory=list)   # 검색된 청크(메타 포함)
    answer: str = ""
    bad_cites: list = field(default_factory=list)  # 환각 인용
    trace: list[str] = field(default_factory=list)


class Agent:
    name: str = "agent"
    model: str = MODEL

    async def run(self, state: Blackboard) -> Blackboard:  # noqa: D401
        raise NotImplementedError


class Analyzer(Agent):
    name = "analyzer"

    async def run(self, state: Blackboard) -> Blackboard:
        sys = (
            "너는 코드베이스 분석 에이전트다. 제공된 코드 컨텍스트 안에서만 답하라. "
            "컨텍스트에 관련 코드가 없으면 '해당 코드를 찾지 못했습니다'라고 답하라. "
            "근거 코드가 없으면 '추정'이라 표기하고, 없는 파일/줄을 지어내지 마라. "
            "코드 위치는 반드시 file:line 형식으로 인용하라(예: llm/router.py:20). "
            "모든 답변은 반드시 한국어로 작성하라."
        )
        user = state.question
        if state.context:
            user = f"[코드 컨텍스트]\n{state.context}\n\n[질문]\n{state.question}"
        state.answer = await chat(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}],
            model=self.model,
        )
        state.trace.append(f"{self.name}: answered")
        return state
