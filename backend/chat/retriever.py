"""질문 → bge-m3 임베딩 → Chroma 검색 → file:line 헤더 붙인 컨텍스트 조립."""
from __future__ import annotations

from llm.ollama_client import embed
from store import vector


async def retrieve(repo_id: str, question: str, k: int = 8):
    qv = await embed(question)
    hits = vector.query(repo_id, qv, k=k)
    context = "\n\n---\n\n".join(_fmt(h) for h in hits)
    return context, hits


def _fmt(hit) -> str:
    m = hit["metadata"]
    head = f"[{m['file']}:{m['start_line']}-{m['end_line']} · {m['symbol']}]"
    return f"{head}\n{hit['document']}"
