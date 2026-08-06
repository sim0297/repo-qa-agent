"""ChromaDB 래퍼. collection = repo_id. 임베딩은 우리가 직접 넣음(bge-m3)."""
from __future__ import annotations
import re
import chromadb

from config import CHROMA_DIR

_client = chromadb.PersistentClient(path=CHROMA_DIR)


def _safe(repo_id: str) -> str:
    # chroma collection 이름 규칙: 3~63자, 영숫자/_/- , 양끝 영숫자
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", repo_id)[:63]
    return (s + "_repo")[:63] if len(s) < 3 else s


def add(repo_id, ids, embeddings, documents, metadatas):
    col = _client.get_or_create_collection(_safe(repo_id))
    col.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def query(repo_id, embedding, k=8):
    col = _client.get_or_create_collection(_safe(repo_id))
    res = col.query(query_embeddings=[embedding], n_results=k)
    # 평탄화: [{document, metadata, distance}]
    out = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        out.append({"document": doc, "metadata": meta, "distance": dist})
    return out


def reset(repo_id):
    try:
        _client.delete_collection(_safe(repo_id))
    except Exception:
        pass


def delete_file(repo_id, file):
    """특정 파일의 청크만 삭제(증분 재인덱싱용)."""
    col = _client.get_or_create_collection(_safe(repo_id))
    col.delete(where={"file": file})


def count(repo_id) -> int:
    return _client.get_or_create_collection(_safe(repo_id)).count()


def all_chunks(repo_id):
    """전체 청크(문서+메타). 자동 QA용."""
    col = _client.get_or_create_collection(_safe(repo_id))
    r = col.get()
    return [{"document": d, "metadata": m}
            for d, m in zip(r["documents"], r["metadatas"])]


def files(repo_id) -> set:
    return {c["metadata"]["file"] for c in all_chunks(repo_id)}


def list_repos():
    """인덱싱된 레포 목록. collection 이름이 곧 repo_id(이미 safe)."""
    out = []
    for c in _client.list_collections():
        try:
            out.append({"repo_id": c.name, "chunks": c.count()})
        except Exception:
            pass
    return sorted(out, key=lambda r: r["repo_id"])
