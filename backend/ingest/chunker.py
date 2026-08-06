"""AST 기반 청킹. 함수/클래스 단위로 분할 (단순 N줄 분할 금지).

전략: 루트의 top-level 자식 중 def/class-like 노드는 각각 청크,
그 사이 코드(import·전역)는 묶어서 preamble 청크.
초과 청크는 슬라이딩 분할.

ponytail: top-level만 인식. 깊이 중첩된 정의(모듈 안 네임스페이스 등)는 부모 청크에 포함됨.
언어팩 미지원 확장자는 전체파일 폴백(초과시 슬라이딩).
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from tree_sitter_language_pack import get_parser
from config import CHUNK_MAX_CHARS

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".java": "java",
    ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".cs": "c_sharp",
    ".kt": "kotlin", ".scala": "scala", ".swift": "swift",
}
DEF_KINDS = ("function", "method", "class", "constructor", "interface", "struct", "impl")


@dataclass
class Chunk:
    text: str
    file: str          # root 기준 상대경로
    start_line: int    # 1-indexed
    end_line: int
    symbol: str
    language: str


def chunk_file(path: Path, root: Path) -> list[Chunk]:
    rel = str(Path(path).relative_to(root))
    try:
        src = Path(path).read_text(errors="replace")
    except OSError:
        return []
    lang = LANG_BY_EXT.get(Path(path).suffix.lower())
    if not lang:
        return _fallback(src, rel, "text")
    try:
        parser = get_parser(lang)
        tree = parser.parse(src.encode())
    except Exception:
        return _fallback(src, rel, lang)

    lines = src.splitlines()
    chunks: list[Chunk] = []
    preamble: list = []  # 연속된 비-def 노드 묶음

    def flush_preamble():
        if not preamble:
            return
        s, e = preamble[0].start_point[0], preamble[-1].end_point[0]
        _emit(chunks, lines, s, e, rel, lang, "(module)")
        preamble.clear()

    for node in tree.root_node.children:
        if any(k in node.type for k in DEF_KINDS):
            flush_preamble()
            s, e = node.start_point[0], node.end_point[0]
            _emit(chunks, lines, s, e, rel, lang, _symbol(node))
        else:
            preamble.append(node)
    flush_preamble()
    return chunks or _fallback(src, rel, lang)


def _symbol(node) -> str:
    n = node.child_by_field_name("name")
    if n is not None:
        return n.text.decode(errors="replace")
    return node.type


def _emit(chunks, lines, s, e, rel, lang, symbol):
    text = "\n".join(lines[s:e + 1])
    if len(text) <= CHUNK_MAX_CHARS:
        chunks.append(Chunk(text, rel, s + 1, e + 1, symbol, lang))
        return
    # 초과 → 슬라이딩(줄 기준) 분할
    step = max(1, CHUNK_MAX_CHARS // 80)  # 대략 줄당 80자 가정
    for i in range(s, e + 1, step):
        j = min(e, i + step - 1)
        chunks.append(Chunk("\n".join(lines[i:j + 1]), rel, i + 1, j + 1, f"{symbol}#{i+1}", lang))


def _fallback(src: str, rel: str, lang: str) -> list[Chunk]:
    lines = src.splitlines()
    return _sliding(lines, rel, lang) if src else []


def _sliding(lines, rel, lang):
    step = max(1, CHUNK_MAX_CHARS // 80)
    out = []
    for i in range(0, len(lines), step):
        j = min(len(lines) - 1, i + step - 1)
        out.append(Chunk("\n".join(lines[i:j + 1]), rel, i + 1, j + 1, f"(lines {i+1})", lang))
    return out
