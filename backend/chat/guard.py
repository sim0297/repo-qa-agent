"""환각방지: 답변의 file:line 인용이 실제 제공된 컨텍스트 파일인지 검증.

원칙: 모델은 준 컨텍스트 안에서만 인용해야 함. 컨텍스트에 없는 파일을 인용 = 환각.

ponytail: 파일 존재만 검증(줄 범위 정확도는 미검증). 줄 검증 필요해지면
hit의 start~end 범위 대조 추가.
"""
from __future__ import annotations
import re

# 예: src/foo.py:42  또는  llm/router.py:10-30
CITE = re.compile(r"([\w./\-]+\.[A-Za-z0-9]+):(\d+)")


def check(answer: str, hits: list[dict]) -> list[str]:
    """컨텍스트에 없는 파일을 인용한 것들 반환(환각 후보)."""
    allowed = {h["metadata"]["file"] for h in hits}
    bad = []
    for m in CITE.finditer(answer):
        cited = m.group(1)
        if not any(cited == f or f.endswith(cited) or cited.endswith(f) for f in allowed):
            bad.append(m.group(0))
    return sorted(set(bad))
