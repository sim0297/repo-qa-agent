"""NodeRouter — 다중 GPU 노드 로드밸런싱 (Phase 7).

선택 기준(상사 요구): 진행중 요청 최소(least-busy) 우선, 동률이면 응답시간 EMA 최단.
실패 노드는 잠시 제외(쿨다운).
"""
from __future__ import annotations
import time


class Node:
    def __init__(self, d: dict):
        self.name = d["name"]
        self.url = d["url"].rstrip("/")
        self.key = d.get("key", "")
        self.model = d["model"]
        self.inflight = 0
        self.ema_ms: float | None = None
        self.fail_until = 0.0  # 실패시 이 시각까지 제외
        self.total = 0         # 누적 처리 건수
        self.fails = 0

    @property
    def healthy(self) -> bool:
        return time.time() >= self.fail_until


class NodeRouter:
    COOLDOWN = 60  # 실패 노드 제외 초

    def __init__(self, nodes: list[dict]):
        self.nodes = [Node(d) for d in nodes]
        if not self.nodes:
            raise ValueError("NODES 비었음")

    def pick(self) -> Node:
        cands = [n for n in self.nodes if n.healthy] or self.nodes
        # 최저부하 → 최단 EMA (미측정 노드는 우선 시도)
        return min(cands, key=lambda n: (n.inflight, n.ema_ms if n.ema_ms is not None else -1))

    def record(self, node: Node, ms: float, ok: bool):
        node.total += 1
        if ok:
            node.ema_ms = ms if node.ema_ms is None else 0.7 * node.ema_ms + 0.3 * ms
        else:
            node.fails += 1
            node.fail_until = time.time() + self.COOLDOWN

    def stats(self) -> list[dict]:
        grand = sum(n.total for n in self.nodes) or 1
        return [{"name": n.name, "url": n.url, "model": n.model,
                 "inflight": n.inflight,
                 "ema_ms": round(n.ema_ms, 1) if n.ema_ms else None,
                 "total": n.total, "fails": n.fails,
                 "share": round(n.total / grand * 100),  # 처리 비중 %
                 "health": n.healthy} for n in self.nodes]
