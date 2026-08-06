"""Agent 베이스 + Analyzer + Critic (Phase 3.5 멀티에이전트).

- 기본(fast): Analyzer 1개 직선.
- 심층(deep): Analyzer 초안 → Critic 검토 → 지적 있으면 Analyzer 재작성 (루프).
ponytail: Planner(질문분해)·Synthesizer는 지연만 늘어 보류. Critic 루프가 품질·환각 핵심.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field

from config import MODEL
from llm.ollama_client import chat


@dataclass
class Blackboard:
    """에이전트 공유 상태."""
    question: str
    repo_id: str = ""
    context: str = ""          # RAG 검색 코드청크
    hits: list = field(default_factory=list)   # 검색된 청크(메타 포함)
    answer: str = ""
    bad_cites: list = field(default_factory=list)  # 환각 인용
    critique: str = ""         # Critic이 남긴 지적(재작성 반영용)
    iterations: int = 0        # Critic 루프 횟수
    trace: list[str] = field(default_factory=list)


class Agent:
    name: str = "agent"
    model: str = MODEL

    async def run(self, state: Blackboard) -> Blackboard:  # noqa: D401
        raise NotImplementedError


class Analyzer(Agent):
    name = "analyzer"

    SYS = (
        "너는 코드베이스 분석 에이전트다. 제공된 코드 컨텍스트 안에서만 답하라. "
        "컨텍스트에 관련 코드가 없으면 '해당 코드를 찾지 못했습니다'라고 답하라. "
        "근거 코드가 없으면 '추정'이라 표기하고, 없는 파일/줄을 지어내지 마라. "
        "코드 위치는 반드시 file:line 형식으로 인용하라(예: llm/router.py:20). "
        "모든 답변은 반드시 한국어로 작성하라."
    )

    async def run(self, state: Blackboard) -> Blackboard:
        user = state.question
        if state.context:
            user = f"[코드 컨텍스트]\n{state.context}\n\n[질문]\n{state.question}"
        if state.critique:   # 재작성: 이전 답변의 지적 반영
            user += (f"\n\n[이전 답변]\n{state.answer}\n\n[검토자 지적 — 반드시 반영]\n{state.critique}\n"
                     "위 지적을 고쳐 답변을 다시 작성하라.")
        state.answer = await chat(
            [{"role": "system", "content": self.SYS}, {"role": "user", "content": user}],
            model=self.model,
        )
        state.trace.append(f"{self.name}: {'revised' if state.critique else 'answered'}")
        return state


class Critic(Agent):
    name = "critic"

    SYS = (
        "너는 코드 답변 검토자다. 질문·코드 컨텍스트·답변을 보고, 답변이 "
        "① 컨텍스트에 근거하는지 ② 틀린/근거없는 주장이 없는지 ③ 질문에 실제로 답했는지 판정하라. "
        "반드시 JSON만 출력: {\"ok\": true/false, \"issues\": \"문제점(없으면 빈문자열, 한국어)\"}"
    )

    async def run(self, state: Blackboard) -> Blackboard:
        user = (f"[질문]\n{state.question}\n\n[코드 컨텍스트]\n{state.context}\n\n"
                f"[답변]\n{state.answer}")
        raw = await chat([{"role": "system", "content": self.SYS},
                          {"role": "user", "content": user}], model=self.model)
        ok, issues = _parse_verdict(raw)
        state.critique = "" if ok else issues
        state.iterations += 1
        state.trace.append(f"critic: {'ok' if ok else 'issues'}")
        return state


def _parse_verdict(raw: str) -> tuple[bool, str]:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return True, ""   # 파싱 실패 → 통과(무한루프 방지)
    try:
        d = json.loads(m.group(0))
        return bool(d.get("ok", True)), str(d.get("issues", ""))
    except json.JSONDecodeError:
        return True, ""
