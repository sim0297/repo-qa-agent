"""LLM 호출.

- chat():        로컬 Ollama 고정 (Q&A — 품질·인덱스 일관성)
- chat_routed(): 노드 풀 분산 (QA 배치 — 최저부하 노드 선택, Phase 7)
- embed():       로컬 bge-m3 고정 (원격엔 bge-m3 없음, 인덱스 호환)
"""
from __future__ import annotations
import time
import httpx

from config import MODEL, EMBED_MODEL, REQUEST_TIMEOUT, EMBED_BATCH, NODES, OLLAMA_URL
from llm.router import NodeRouter, Node

ROUTER = NodeRouter(NODES)


async def chat(messages: list[dict], model: str = MODEL) -> str:
    """로컬 고정 채팅 (Q&A 경로)."""
    return await _post(f"{OLLAMA_URL}/v1", "", model, messages)


async def chat_routed(messages: list[dict], temperature: float = 0.0) -> str:
    """노드 풀 분산 채팅 (QA 경로). 온도 0 기본=재현성. 실패시 타노드 1회 fallback."""
    node = ROUTER.pick()
    try:
        return await _chat_node(node, messages, temperature)
    except Exception:
        others = [n for n in ROUTER.nodes if n is not node and n.healthy]
        if not others:
            raise
        return await _chat_node(min(others, key=lambda n: n.inflight), messages, temperature)


async def _chat_node(node: Node, messages: list[dict], temperature: float = 0.0) -> str:
    node.inflight += 1
    t0 = time.time()
    ok = False
    try:
        out = await _post(node.url, node.key, node.model, messages, temperature)
        ok = True
        return out
    finally:
        node.inflight -= 1
        ROUTER.record(node, (time.time() - t0) * 1000, ok)


async def _post(base_url: str, key: str, model: str, messages: list[dict],
                temperature: float | None = None) -> str:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        body["temperature"] = temperature
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as c:
        r = await c.post(f"{base_url}/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def embed(text: str, model: str = EMBED_MODEL) -> list[float]:
    return (await embed_batch([text], model))[0]


async def embed_batch(texts: list[str], model: str = EMBED_MODEL) -> list[list[float]]:
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as c:
        for i in range(0, len(texts), EMBED_BATCH):
            r = await c.post(f"{OLLAMA_URL}/api/embed",
                             json={"model": model, "input": texts[i:i + EMBED_BATCH]})
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    return out
