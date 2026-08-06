# roadmap.md — Repo QA Agent (로컬 LLM 기반 코드베이스 자동 QA + 자연어 Q&A)

> **한 줄 정의**: Git 저장소를 투입하면 ① 자동으로 코드 품질 QA 리포트를 생성하고,
> ② 코드베이스에 대해 자연어로 질의응답할 수 있는 **100% 로컬(인하우스) 에이전트**.
>
> 외부 API 0개. 모든 추론·임베딩은 GB10 위 Ollama 로컬 모델로 처리.

---

## 0. 목표와 배경

### 0-1. 만드는 이유
- 새 코드베이스(사내 레포, 오픈소스, 외주 산출물)를 받았을 때 **구조 파악 + 품질 점검**에 드는 시간이 큼.
- 사내 코드는 **외부 클라우드 LLM에 올릴 수 없음**(보안). → 로컬 모델 필수.
- 기존에 보유한 인프라(GB10 + Ollama + bge-m3)를 그대로 재활용하면 추가 비용 0.

### 0-2. 핵심 가치
| 가치 | 설명 |
|---|---|
| **인하우스 폐쇄망** | 코드가 머신 밖으로 절대 안 나감 |
| **자동 QA** | 레포 투입 → 버그·취약점·코드냄새·복잡도 리포트 자동 생성 |
| **자연어 Q&A** | "로그인 흐름 어디서 처리해?" → 코드 줄(`file:line`) 인용 답변 |
| **출처 강제** | 모든 답변에 근거 코드 위치 표기 → 환각 차단 |
| **재사용** | 기존 사출성형 플랫폼의 RAG·환각방지 패턴 그대로 적용 |

### 0-3. 비목표 (이번 범위 아님)
- 코드 자동 수정/패치 적용 (리뷰·제안까지만, 적용은 사람)
- 멀티유저 동시 대규모 서빙 (PoC는 단일~소수 사용자, 추후 vLLM 전환)
- CI/CD 파이프라인 통합 (Phase 7 이후 선택)

---

## 1. 기술 스택 (전부 로컬)

```
┌─────────────────────────────────────────────────────────────┐
│ 인프라: NVIDIA GB10 (Grace Blackwell, unified memory)        │
│ 오케스트레이션: Docker Compose                                │
└─────────────────────────────────────────────────────────────┘

[모델 — Ollama localhost:11434]
  ├─ gemma4:31b        → 모든 LLM 역할: QA검토·분석·의도분류·답변·종합 (단일 추론모델)
  └─ bge-m3            → 코드/문서 임베딩 (다국어, 한국어 포함) — 임베딩 전용, 별도

  단일 모델 원칙: 여러 모델 혼용 이득 없음(답변 품질). keep_alive 저글링만 늘어남.
  예외 후보(지금은 미적용): ①Critic 다양성용 2nd 모델 ②의도분류용 경량 모델.
  필요해지면 그때 부활.

[저장]
  ├─ ChromaDB          → 코드 청크 벡터 저장소 (로컬 영속, 디스크)
  └─ SQLite            → 메타데이터(파일트리, QA결과, 세션) 저장

[백엔드]
  └─ FastAPI (Python 3.11+)
       ├─ /api/ingest   레포 수집·인덱싱
       ├─ /api/qa       자동 QA 실행
       ├─ /api/chat     자연어 Q&A (SSE 스트리밍)
       ├─ /api/nodes    GPU 노드 상태(부하/응답시간/health) 조회 — 프론트 가시화
       ├─ Orchestrator  멀티에이전트 체인(Planner→Analyzer↔Critic→Synthesizer)
       ├─ NodeRouter    다중노드 로드밸런싱(부하·EMA·health 기반 best 노드 선택)
       └─ Ollama OpenAI-호환 엔드포인트(/v1) 호출 (단일 또는 다중 노드)

[정적 분석 도구 (LLM 보조)]
  ├─ ripgrep           코드 검색
  ├─ tree-sitter       AST 파싱 → 함수/클래스 단위 청킹
  ├─ ruff / eslint     언어별 린터(있으면) 결과를 LLM 입력에 첨부
  └─ radon / lizard    복잡도 지표(순환복잡도 등)

[프론트엔드]
  └─ 정적 웹 (HTML + Vanilla JS, 빌드리스)
       ├─ 좌측: 채팅 패널 (Q&A, SSE 스트리밍, 마크다운/코드 렌더)
       └─ 우측: QA 리포트 패널 (파일트리 + 발견사항 표)
```

**설계 원칙**
- 기존 사출성형 플랫폼과 **동일한 Ollama 백엔드 공유** → 모델 중복 적재 없음.
- 의존성 최소화: 프론트는 빌드 도구 없이 정적 파일. 백엔드는 FastAPI 단일.
- 모든 LLM 호출은 OpenAI 호환 형식 → 추후 **Ollama→vLLM 전환 시 base_url만 교체**.

---

## 2. 전체 아키텍처

```
┌──────────────┐   git repo (경로 or URL)
│   사용자      │ ─────────────────────────┐
└──────┬───────┘                          ▼
       │                        ┌────────────────────────┐
       │  자연어 질문            │  Ingest Pipeline        │
       │  "인증 어디서 함?"      │  1. clone/load          │
       ▼                        │  2. gitignore 존중 필터  │
┌──────────────┐                │  3. tree-sitter 청킹     │
│  Web UI       │                │  4. bge-m3 임베딩        │
│  (채팅+리포트)│                │  5. Chroma 저장         │
└──────┬───────┘                └───────────┬────────────┘
       │ POST /api/chat                     │
       ▼                                    ▼
┌─────────────────────────┐      ┌────────────────────────┐
│  FastAPI 백엔드          │◄────►│  ChromaDB + SQLite      │
│  - 쿼리 의도 분류         │      │  (벡터 + 메타데이터)     │
│  - RAG 검색 (top-k)      │      └────────────────────────┘
│  - 컨텍스트 조립          │
│  - 환각방지 인용 강제     │      ┌────────────────────────┐
│  - QA 오케스트레이션      │◄────►│  Ollama (모델 3종)      │
└─────────────────────────┘      └────────────────────────┘
```

---

## 3. Phase 정의

```
Phase 0.  환경 셋업 + 프로젝트 골격
Phase 1.  레포 수집(Ingest) + 인덱싱
Phase 2.  자동 QA 분석 엔진
Phase 3.  자연어 Q&A (RAG + 환각방지)
Phase 3.5 멀티에이전트 오케스트레이션 + 롱 체인        ← 핵심 품질 레이어
Phase 4.  웹 UI (채팅 + 리포트)
Phase 5.  품질·평가·환각방지 강화
Phase 6.  고급 기능 (증분 인덱싱, 콜그래프, 깃 히스토리)
Phase 7.  GPU 멀티노드 분산처리 (다중 5090 로드밸런싱)  ← 상사 지시
Phase 8.  (선택) 운영 전환 — vLLM 서빙 / CI 통합
Phase 9.  tarball 수집 + 사용자 토큰 인증 (clone 제거)   ← 다중사용자 1단계
Phase 10. 노드별 워커 동시성 튜닝 (시흥 5090×3 활용)
```

---

### Phase 0 — 환경 셋업 + 프로젝트 골격 ✅ 완료 (2026-07-15)

**목표**: 빈 골격이 도는 상태(헬스체크 OK)까지.

**작업**
- 디렉터리 구조 생성 (아래 §6 참조)
- `docker-compose.yml` — FastAPI 컨테이너 + Chroma(임베디드 or 별도)
- Ollama 연결 확인 (`gemma4:31b`, `bge-m3` 적재 테스트)
- `/health` 엔드포인트 — 모델 3종 ping
- **인터페이스 선설계 (절충안 핵심)** — 구현은 최소, 껍데기만:
  - `Agent` 베이스 + `Orchestrator.answer()` — 처음엔 **에이전트 1개 등록**만 (Analyzer 단독)
  - `NodeRouter` — 처음엔 **URL 1개(noop)**. `pick()`이 그냥 그 URL 반환
  - → Phase 3까지 이 껍데기 위에서 단발로 동작. Phase 3.5는 **에이전트 수만 늘림**(재작성 0). Phase 7은 **URL만 추가**(재작성 0).

**설계 의도**: 완전 멀티에이전트 재설계의 유일한 이득("나중에 재작성 없음")을 인터페이스 2개로 확보.
프레임워크 무게는 안 짊 — Phase 0~3은 사실상 직선 코드, 구조만 에이전트 기반.

**완료 기준**: `curl localhost:8080/health` → 모델 3종 `ok`. Orchestrator가 에이전트 1개로 답변 반환.

---

### Phase 1 — 레포 수집(Ingest) + 인덱싱 ✅ 완료 (2026-07-15)

**배경**: 레포를 LLM이 검색 가능한 형태로 변환. **청킹 품질이 전체 성능을 좌우**.

> 구현: `git ls-files`로 .gitignore 존중(비-git은 walk 폴백), tree-sitter top-level def/class 청킹,
> bge-m3 배치 임베딩 → Chroma. SQLite meta는 YAGNI로 보류(Chroma 메타에 file:line 보유).
> self-check: 45청크 인덱싱 + 검색 top1 정확.

**기능 요구사항**
- (FR1) 입력: 로컬 경로 또는 git URL (`POST /api/ingest {"source": "..."}`)
- (FR2) `.gitignore` + 기본 무시 규칙 존중 (`node_modules`, `.git`, 바이너리, lock 파일 등 제외)
- (FR3) 언어 자동 감지 (확장자 기반) → 지원 언어별 tree-sitter 파서 선택
- (FR4) **AST 기반 청킹** — 함수/클래스/메서드 단위로 분할 (단순 N줄 분할 금지).
  - 함수가 너무 길면 슬라이딩 윈도우로 보조 분할
  - 각 청크에 메타데이터 부착: `{file_path, start_line, end_line, symbol_name, language}`
- (FR5) `bge-m3`로 임베딩 → ChromaDB 저장 (collection = repo_id)
- (FR6) 파일트리·심볼 인덱스를 SQLite에 저장 (Q&A 시 구조 참조용)
- (FR7) README/문서 파일(`.md`)은 별도 collection으로 분리 인덱싱

**비기능 요구사항**
- 중규모 레포(~5만 LOC) 인덱싱 < 5분 (GB10 기준)
- 임베딩 배치 처리 (bge-m3 batch=32~64)
- 진행률 스트리밍 (SSE: "120/450 파일 처리중")

**구현 핵심**
```python
# ingest/chunker.py
def chunk_file(path, language) -> list[Chunk]:
    tree = tree_sitter_parse(path, language)
    # 함수/클래스 노드 추출 → 각각 Chunk
    # 메타: file_path, start_line, end_line, symbol, language
    ...

# ingest/indexer.py
def index_repo(source) -> repo_id:
    files = collect_files(source)          # gitignore 존중
    chunks = [c for f in files for c in chunk_file(f)]
    embeddings = bge_m3_embed([c.text for c in chunks])  # 배치
    chroma.add(repo_id, chunks, embeddings)
    sqlite.save_tree(repo_id, files, symbols)
    return repo_id
```

**완료 기준**: 샘플 레포 인덱싱 → Chroma에 청크 N개 저장 확인 + 파일트리 조회 가능.

---

### Phase 2 — 자동 QA 분석 엔진 ✅ 완료 (2026-07-15)

**배경**: 레포 투입 직후 자동 생성되는 **품질 리포트**. 사람이 묻기 전에 먼저 점검.

> 구현: 청크 우선순위(긴 함수 우선, 캡 MAX_QA_CHUNKS) → 배치로 묶어 gemma4:31b 검토(map) →
> evidence 필터 → 심각도 정렬(reduce). `/api/qa` + UI "자동 QA" 버튼·발견사항 패널.
> self-check: buggy.py에서 SQL injection(high)·bare except(medium) 검출, 환각0.
> 백그라운드 잡: POST 시작→즉시반환, GET 폴링(진행률), 완료시 data/qa/<repo>.json 자동저장, 재선택시 로드.
> QA 모델 분리: `QA_MODEL=gemma4:e4b`(속도, 31b 대비 8x) / Q&A는 31b(품질).
> 스킵(ceiling): 정적도구(ruff/radon) 미연동, 모듈레벨 코드는 (module) 스킵으로 놓침, markdown 리포트.

**기능 요구사항**
- (FR1) 분석 카테고리:
  | 카테고리 | 방법 |
  |---|---|
  | **버그·로직 오류** | LLM이 함수 단위 검토 (gemma4:31b) |
  | **보안 취약점** | LLM + 패턴(하드코딩 시크릿, SQL injection, 안전하지 않은 역직렬화) |
  | **코드 냄새** | LLM (중복, 거대 함수, 강결합) + radon 복잡도 |
  | **테스트 공백** | 테스트 파일 vs 소스 매핑 → 커버 안 된 모듈 |
  | **문서 부재** | docstring/주석 비율 |
- (FR2) **하이브리드**: 정적 도구(ruff/eslint/radon) 결과를 먼저 수집 → LLM이 그 결과 + 코드를 보고 우선순위·설명 부여 (LLM 단독 환각 방지)
- (FR3) 발견사항 스키마:
  ```json
  {
    "id": "F-001",
    "category": "security",
    "severity": "high|medium|low",
    "file": "src/auth.py",
    "line": 42,
    "title": "하드코딩된 API 키",
    "detail": "...",
    "suggestion": "환경변수로 분리 권장",
    "evidence": "src/auth.py:42-43"   // 출처 필수
  }
  ```
- (FR4) 심각도별 정렬 + 마크다운 리포트 + JSON 동시 산출
- (FR5) 대형 레포 대응: 전수 검사 대신 **위험 우선순위 샘플링** (복잡도 높은/최근 변경/엔트리포인트 우선)

**비기능 요구사항**
- 리포트 생성 시 모든 발견사항은 **반드시 `file:line` evidence 포함** (없으면 폐기)
- 토큰 한도 관리: 함수 단위로 분할 호출 (map), 마지막에 종합(reduce)

**구현 핵심 (map-reduce 체인)**
```
[Map]  각 위험 청크 → gemma4:31b 검토 → 발견사항 리스트
[Filter] evidence(file:line) 없는 항목 제거 (환각 차단)
[Reduce] 전체 발견사항 → 중복 병합 → 심각도 정렬 → 종합 요약
[Render] 마크다운 리포트 + JSON
```

**완료 기준**: 샘플 레포 → QA 리포트 생성. 발견사항 100%가 실제 코드 줄을 정확히 가리킴(수동 검증 10건).

---

### Phase 3 — 자연어 Q&A (RAG + 환각방지) ✅ 완료 (2026-07-15)

**배경**: 핵심 기능. 코드베이스에 대해 사람이 말로 묻고 답을 얻음.

> 구현: retriever(bge-m3 검색) → Analyzer(gemma4:31b, file:line 인용) → guard(컨텍스트 밖 인용 검출).
> self-check: router.py:16 정확 인용·환각0, 없는 내용은 "못찾음" 거절.
> 스킵(YAGNI): 의도분류→Phase 3.5, 멀티턴/SSE→Phase 4. guard는 파일존재만(줄범위 미검증).
**구조**: Phase 0 Orchestrator 위에서 **에이전트 1개(Analyzer 단독)**로 동작 — 직선 파이프라인이지만 껍데기는 에이전트 기반. Phase 3.5에서 에이전트를 늘려 승격(재작성 없음).

**기능 요구사항**
- (FR1) `POST /api/chat` — SSE 스트리밍 응답
- (FR2) 쿼리 처리 파이프라인:
  ```
  질문 → [의도 분류 gemma4:31b]
            ├─ "구조/위치" 질문  → RAG 검색 + 파일트리 참조
            ├─ "동작/설명" 질문  → RAG 검색 (함수 본문 위주)
            └─ "품질/리뷰" 질문  → Phase 2 QA 결과 참조
       → [bge-m3 임베딩 → Chroma top-k 검색]
       → [컨텍스트 조립: 청크 + file:line 메타]
       → [gemma4:31b 답변 생성]
       → [환각방지 후처리: 인용된 file:line 실제 존재 검증]
  ```
- (FR3) **출처 강제**: 답변의 모든 코드 주장은 `file:line` 인용 필수. 인용 없는 단정은 "추정"으로 표기하도록 시스템 프롬프트 강제.
- (FR4) 멀티턴 대화 (세션별 이력, SQLite/메모리)
- (FR5) 코드 인용 블록은 클릭 시 해당 파일·줄로 점프 (UI 연동)
- (FR6) "모름" 허용 — 검색 결과 관련성 낮으면 환각 대신 "해당 코드를 찾지 못함" 응답

**비기능 요구사항**
- 첫 토큰까지 < 3초 (간단 질의)
- top-k 기본 8, 재순위(rerank) 옵션
- 컨텍스트 윈도우 초과 시 청크 압축(요약) 후 주입

**환각방지 (기존 플랫폼 패턴 재사용)**
- 시스템 프롬프트에 "근거 코드 없이 단정 금지, 인용은 제공된 청크 내에서만" 명시
- 후처리: 답변에서 `file:line` 패턴 추출 → SQLite 파일트리와 대조 → 존재하지 않는 인용 경고/제거

**완료 기준**: 샘플 레포에 10개 질문 → 8개+ 정확 답변, 모든 코드 인용이 실재. "모르는 질문"에 환각 없이 정직하게 응답.

---

### Phase 3.5 — 멀티에이전트 오케스트레이션 + 롱 체인

**배경**: Phase 3의 고정 파이프라인을 **자율 협업 에이전트 + 다단계 추론 체인**으로 승격.
복잡한 질문("이 결제 모듈에 동시성 버그 있어?")과 대형 레포 분석에서 단발 호출의 한계를 넘기 위함.
기존 사출성형 플랫폼의 5-subagent(Spec/Code/Test/Register/ReviewFix) 및 ReviewFix 루프 철학을 재사용.

#### 3.5-1. 에이전트 구성

| 에이전트 | 역할 | 기본 모델 |
|---|---|---|
| **Planner** | 질문 분해, 서브태스크 분배, 종료 조건 판단 | `gemma4:31b` |
| **Retriever** | 검색 쿼리 재작성·확장, 반복 검색(부족하면 재질의) | `gemma4:31b` |
| **Analyzer** | 코드 분석·버그·취약점·로직 판단 | `gemma4:31b` |
| **Critic / Verifier** | 답변·발견의 인용 검증, 환각·논리오류 지적 | `gemma4:31b` (다양성 필요시 2nd 모델) |
| **Synthesizer** | 다중 에이전트 결과 종합 → 최종 답변 | `gemma4:31b` |

> 각 에이전트의 LLM 호출은 Phase 7의 **노드 라우터를 통해 분산** (병렬 에이전트 = 병렬 노드 활용).

#### 3.5-2. 오케스트레이션 패턴 (롱 체인)

| 패턴 | 적용 위치 | 효과 |
|---|---|---|
| **Plan → Execute → Reflect** | 전체 질의 처리 | 복잡 질문을 단계로 분해 후 자기검증 |
| **Decomposition (질문 분해)** | Planner | "버그 있어?" → [동시성, 입력검증, 예외처리] 서브질문 |
| **Iterative Retrieval** | Retriever | 1차 검색 부족 → 쿼리 재작성 → 재검색 (최대 N회) |
| **Analyzer ↔ Critic 루프** | 분석/답변 | 생성 → 비평 → 수정 (수렴까지, 최대 K회) |
| **Map-Reduce** | 대형 레포 QA | 청크별 분석(map, **병렬**) → 종합(reduce) |
| **Chain-of-Verification (CoVe)** | 환각방지 | 답변의 각 주장에 대해 "근거 코드 존재?" 재질의·검증 |
| **Self-Consistency** | 고위험 판단 | 같은 분석 N회 → 다수결 (보안 취약점 등 정확도 요구 시) |

#### 3.5-3. 기능 요구사항
- (FR1) **Orchestrator** 모듈 — 에이전트 등록·실행·상태(blackboard) 관리
- (FR2) 에이전트 간 공유 상태(blackboard): 질문, 서브태스크, 검색결과, 중간답변, 비평 이력
- (FR3) **종료 조건**: Critic이 "통과" 판정하거나 최대 반복(loop budget) 도달 시 종료 (무한루프 방지)
- (FR4) **병렬 실행**: 독립 서브태스크는 동시 실행 (asyncio) → Phase 7 노드 분산과 결합
- (FR5) **체인 추적(trace)**: 각 단계 입력/출력/소요시간/사용노드 로깅 → UI에서 "사고 과정" 표시 옵션
- (FR6) 모드 전환: `simple`(Phase 3 단발) / `agentic`(멀티에이전트) — 질문 복잡도에 따라 Planner가 자동 선택

#### 3.5-4. 아키텍처
```
                   ┌──────────────┐
   질문 ─────────► │  Planner      │  질문 분해 + 모드 결정(simple/agentic)
                   └──────┬───────┘
            ┌─────────────┼──────────────┐   (서브태스크 병렬 분배)
            ▼             ▼              ▼
      ┌──────────┐ ┌──────────┐  ┌──────────┐
      │Retriever │ │Analyzer  │  │Analyzer  │   ← 각 호출이 Phase 7
      │(검색반복)│ │(서브Q-1) │  │(서브Q-2) │      라우터로 best 노드 선택
      └────┬─────┘ └────┬─────┘  └────┬─────┘
           └────────────┼─────────────┘
                        ▼
                 ┌──────────────┐
                 │  Critic       │  인용검증·환각·논리 점검
                 └──────┬───────┘
              통과 ◄────┤ 미통과 → Analyzer 재실행 (루프, 최대 K회)
                        ▼
                 ┌──────────────┐
                 │ Synthesizer   │  종합 → 최종 답변 + 출처
                 └──────────────┘
```

#### 3.5-5. 구현 핵심
```python
# orchestrator/agent.py
class Agent:
    name: str; model: str
    async def run(self, state: Blackboard) -> Blackboard: ...

# orchestrator/orchestrator.py
class Orchestrator:
    async def answer(self, question, repo_id) -> Answer:
        state = Blackboard(question, repo_id)
        plan = await Planner.run(state)            # 분해 + 모드
        if plan.mode == "simple":
            return await simple_pipeline(state)    # Phase 3 경로
        # agentic: 병렬 분석 + 비평 루프
        for _ in range(LOOP_BUDGET):
            await asyncio.gather(*[Analyzer.run(s) for s in state.subtasks])
            verdict = await Critic.run(state)
            if verdict.passed: break
        return await Synthesizer.run(state)
```

#### 3.5-6. 완료 기준
1. 복잡 질문(다중 서브태스크)에서 simple 대비 답변 정확도·완결성 향상
2. Critic 루프로 환각 인용 실재율 추가 개선 (Phase 5 기준 충족 가속)
3. 병렬 에이전트 실행이 Phase 7 다중 노드에서 실제로 분산됨 (단일 노드 대비 지연 감소)
4. loop budget으로 무한루프 0건, 응답시간 상한 보장

---

### Phase 4 — 웹 UI (채팅 + 리포트) ✅ 완료 (2026-07-15)

> 구현: 단일 `frontend/index.html`(인라인 CSS+JS, 빌드리스). 좌측 채팅+근거, 우측 소스패널, 상단 인덱싱 폼.
> `GET /`로 서빙. bad_citations 경고 표시. 스킵(ceiling): SSE 스트리밍·IDE 점프·QA패널(Phase 2)·파일트리.

**기능 요구사항**
- (FR1) 단일 페이지: 좌측 채팅 / 우측 QA 리포트 + 파일트리
- (FR2) 레포 투입 폼 (경로/URL 입력 → 인덱싱 진행률 표시)
- (FR3) 채팅: SSE 스트리밍, 마크다운·코드 하이라이트, 인용 `file:line` 클릭 점프
- (FR4) QA 리포트: 심각도 필터, 카테고리 탭, 발견사항 → 코드 위치 점프
- (FR5) 다중 레포 전환 (드롭다운으로 repo_id 선택)
- (FR6) 반응형 (PC 2단 / 모바일 탭 전환)

**기술**: Vanilla JS + 정적 서빙 (FastAPI StaticFiles). 빌드 없음.

**완료 기준**: 브라우저에서 레포 투입 → 리포트 표시 → 채팅 질의응답 → 인용 클릭 점프 동작.

---

### Phase 5 — 품질·평가·환각방지 강화 (검색 고도화 포함)

**검색(RAG) 고도화 — 적용 순서 확정 (2026-07-20 검토)**
```
5-A. Hybrid Search: rank_bm25 + Dense(bge-m3) → RRF 융합   ← 1순위, 코드 식별자 검색 ROI 최대
5-B. 평가셋 구축 (아래 FR1~2) — 효과 측정 기반
5-C. (측정 후 부족 시) Reranker: bge-reranker-v2-m3
     ⚠ Ollama 서빙 불가 → FlagEmbedding/torch 직접 구동(의존성 2~3GB) — 측정 없이 도입 금지
5-D. (필요 시) 인접 청크 확장 — 같은 파일 앞뒤 청크 컨텍스트 추가 (~20줄)
     ParentDocumentRetriever 개념만 차용. AST 함수단위 청킹이라 이미 절반 해결 — LangChain 도입 불필요.
```
- BGE-M3 유지 확정 (한국어+코드, 다만 Ollama는 dense만 제공 → sparse 몫을 BM25가 보완)

**기능 요구사항**
- (FR1) 평가셋: 샘플 레포 + 정답 Q&A 20쌍 → 정확도/인용정확도 자동 측정
- (FR2) 환각 측정: 답변 내 인용 중 실재율(%) 자동 집계
- (FR3) 재순위(rerank) — 위 5-C로 통합
- (FR4) 프롬프트 튜닝 (DSPy 도입 검토 — 체인 자동 최적화)
- (FR5) 응답 시간/토큰 사용량 로깅 대시보드

**완료 기준**: 인용 실재율 ≥ 98%, Q&A 정확도 기준선 수립 및 회귀 추적.

---

### Phase 6 — 고급 기능 (증분 ✅ 완료 2026-07-16 / 콜그래프·깃히스토리 ⬜)

> 증분 구현: 파일 md5 매니페스트(store/manifest.py) → 인덱싱은 바뀐/삭제 파일만 재처리,
> QA는 바뀐 파일 청크만 재검토 + 나머지 발견 유지(kept). self-check: 무변경시 재처리 0.
> 첫 재인덱싱은 매니페스트 생성 위해 전체 1회.

- (FR1) **증분 인덱싱**: git diff 기반 변경 파일만 재인덱싱 (전체 재처리 회피)
- (FR2) **콜그래프/의존성**: tree-sitter로 함수 호출 관계 추출 → "이 함수 누가 호출해?" 정밀 답변
- (FR3) **깃 히스토리 Q&A**: 커밋 로그 인덱싱 → "이 버그 언제 생겼어?", "이 파일 왜 이렇게 바뀜?"
- (FR4) **멀티레포 교차 검색**: 여러 레포 동시 질의
- (FR5) **PR/diff 리뷰 모드**: 특정 브랜치 diff만 QA

---

### Phase 7 — GPU 멀티노드 분산처리 ✅ 완료 (2026-07-16, LiteLLM 채택)

> 구현: 사내 LiteLLM(원격 GPU, 주소는 .env) + 로컬 GB10 Ollama 2노드 풀.
> NodeRouter: least-busy(inflight) → EMA 최단 선택, 실패노드 60s 쿨다운, 1회 fallback.
> QA 배치를 워커풀(노드 수만큼)로 병렬 분산. Q&A·임베딩은 로컬 고정(품질·bge-m3 호환).
> 노드별 모델: local=gemma4:31b, siheung=gemma4:26b. 키는 RQA_LITELLM_KEY 환경변수로 교체 가능.
> 실측: 동시 2건 → 두 노드 분산(9.1s/1.0s), /api/nodes에 inflight·ema 노출.
> 원설계(자체 /api/ps 폴링·가중점수)는 LiteLLM+least-busy로 갈음 — 5090 노드 추가시 NODES에 항목만 추가.

**배경 (상사 지시 원문 반영)**:
> "여러 개 5090 사이트(포트번호마다)에 선택적으로 프롬프트를 전송하여 병렬로 처리.
> 현재 프롬프트 로드가 가장 적은, 답변시간이 짧을 URL을 프론트엔드가 선택해주면 제일 좋음."

현재는 GB10 단일 노드. 미래엔 RTX 5090 N대(각 노드가 Ollama/vLLM 서버, 포트별)로 확장.
LLM 호출을 **부하·응답시간 기준으로 최적 노드에 자동 라우팅**한다.
멀티에이전트(Phase 3.5)의 병렬 에이전트 호출이 이 분산과 결합될 때 효과 극대화.

#### 7-1. 핵심 설계 결정 — 라우팅 위치
| 방식 | 결정 |
|---|---|
| **백엔드 프록시가 노드 선택** (권장) | FastAPI 안의 Router가 best 노드 선택 후 forward. CORS·인증·통계수집이 한 곳에 집중. 프론트는 단일 엔드포인트(`/api/chat`)만 호출 |
| 프론트가 직접 노드 선택 | 상사 표현("프론트엔드가 선택")에 가깝지만, 각 5090 노드에 직접 호출 시 CORS·키노출·통계공유 문제. → **프론트는 노드 통계를 조회·표시만, 실제 선택·forward는 백엔드 Router가 수행**하는 절충안 채택 |

> 즉 "프론트가 선택"의 사용자 경험은 유지하되(노드 상태 가시화 + 수동 고정 옵션),
> 기본 동작은 백엔드 Router의 **자동 최적 노드 선택**.

#### 7-2. 기능 요구사항
- (FR1) `OLLAMA_URL`(단일) → `OLLAMA_URLS`(리스트)로 확장. 단일 URL도 길이 1 리스트로 동작(라우터 noop).
- (FR2) 매 LLM 호출 시 노드 풀에서 **best 노드 자동 선택**.
- (FR3) 라우팅 지표 수집:
  - (a) **부하**: 각 노드 `/api/ps` 폴링 → 적재 모델·진행중 요청 수
  - (b) **응답시간 EMA**: 노드별 최근 N회 지연의 지수이동평균
  - (c) **health**: 타임아웃(예: 5초) 내 무응답 노드 제외
- (FR4) **통합 점수** = `w_load·(1/(load+1)) + w_ema·(1/ema_ms) + w_health·health`
  - 가중치는 설정(config) 노출 → 튜닝 가능
- (FR5) 노드 실패 시 **자동 fallback** — 다른 best 노드로 1회 재시도.
- (FR6) 라우팅 전략 선택: `round-robin | least-conn | ema | hybrid`.
- (FR7) **노드별 적재 모델 확인** — 요청 모델이 적재된 노드만 후보로(미적재 노드 제외).
- (FR8) 프론트 노출: 노드 상태 패널(부하/응답시간/health) + "특정 노드 고정" 수동 옵션.

#### 7-3. 비기능 요구사항
- 라우팅 결정 < 50ms (캐시된 통계 기반, 호출 경로에서 폴링 금지)
- 폴링 주기 5초 (백그라운드 asyncio task)
- 노드 N=8까지 선형 확장
- 단일 노드 환경에서 회귀 0 (라우터가 noop처럼 동작)

#### 7-4. 아키텍처
```
[프론트 — 노드 상태 패널(가시화) + 단일 /api/chat 호출]
          │
          ▼
[FastAPI 백엔드]
  LLM 호출 직전 → Router.pick(model) → best node URL → forward
                      ▲
                      │ 통계 조회(캐시)
              ┌───────┴────────┐
              │ Router state    │  load_table  {url→진행중 req·적재모델}
              │ (in-memory)     │  ema_table   {url→ms}
              └───────▲────────┘  health_table {url→ok/fail}
                      │ 5초 polling task (/api/ps + health)
              ┌───────┴───────────────────────────┐
              │ GPU 노드 풀                         │
              │  http://gpu1:11434  (5090 #1)      │
              │  http://gpu2:11434  (5090 #2)      │
              │  http://gpu3:11434  (5090 #3) ...  │
              └───────────────────────────────────┘
```

#### 7-5. 구현 핵심
```python
# llm/router.py
class NodeRouter:
    def __init__(self, urls: list[str], weights, poll_sec=5): ...
    def pick(self, model: str) -> str:
        cands = [u for u in self.urls
                 if self.health[u] and model in self.loaded_models[u]]
        return max(cands, key=self._score)        # 통합 점수 최대
    def _score(self, url) -> float:
        load, ema, hp = self.load[url], self.ema[url], self.health[url]
        return (self.w_load*(1/(load+1)) + self.w_ema*(1/max(ema,1))
                + self.w_health*(1.0 if hp else 0.0))
    def record(self, url, ms, ok): ...             # 호출 후 EMA·health 갱신
    async def _poll_loop(self): ...                # /api/ps + health 5초마다

# llm/ollama_client.py
async def call(messages, tools, model):
    url = ROUTER.pick(model)
    t0 = time.time()
    try:
        r = await post(f"{url}/v1/chat/completions", ...)
        ROUTER.record(url, (time.time()-t0)*1000, ok=True)
        return r
    except Exception:
        ROUTER.record(url, 99999, ok=False)
        url2 = ROUTER.pick_excluding(model, [url])  # fallback 1회
        return await post(f"{url2}/v1/chat/completions", ...)
```

#### 7-6. 단계별 활성화 (인프라 가용성 기준)
```
[현재 — GB10 단일]   라우터 코드만 박음(URL 1개 → noop). 동작 영향 0.
[5090 1~2대 추가]    OLLAMA_URLS에 추가 → round-robin부터 시험.
[5090 3대+]          strategy=hybrid 전환. EMA+load 가중치 튜닝.
```

#### 7-7. 리스크
| 항목 | 대응 |
|---|---|
| 노드별 적재 모델 불일치 | `/api/ps`로 적재 모델 확인 후 후보 제한 (FR7) |
| 새 노드 cold start | 첫 호출 keep_alive 유지, 후속은 warm. 점수에 cold 페널티 |
| 네트워크 지연 | 같은 LAN 권장. inter-host는 EMA가 자동 회피 |
| 멀티에이전트 동시호출 폭주 | 노드별 동시성 상한(semaphore) + 큐잉 |
| 모든 노드 fail | 즉시 에러 반환(무한루프 금지) |

#### 7-8. 완료 기준
1. 단일 노드에서 라우터 도입 후 회귀 없음(응답시간 동일)
2. Ollama 2 인스턴스(docker) 시뮬레이션 → 부하 분산 확인
3. 한 노드 강제 종료 → 1초 내 다른 노드로 fallback
4. 프론트 노드 상태 패널에 부하·응답시간 실시간 표시

---

### Phase 9 — tarball 수집 + 사용자 토큰 인증 (clone 제거) ✅ 완료 (2026-07-20)

> 구현: GitHub tarball API → 임시폴더 인덱싱 → 즉시 삭제. UI 토큰 입력칸(저장 안 함).
> git/gh 의존 제거, 레포 등록부(git_repos.json) 도입, 🗑 삭제 버튼(임베딩·QA·매니페스트 일괄).
> 검증: 토큰無 private → 404 거절 / 토큰有 → 성공(증분도 tarball 전환 후 정상) / 임시폴더 잔존 0.

**배경**: 불특정 다수 사용 대비 1단계. 현재는 `git clone` + 서버(gh) 인증이라
① 모든 사용자가 서버 소유자 권한으로 private 레포를 당길 수 있고 ② clone 원본이 디스크에 잔존.

**기능 요구사항**
- (FR1) `git clone` → **GitHub tarball API**(`GET /repos/{o}/{r}/tarball`)로 교체.
  임시폴더에 풀고 청킹·임베딩 후 **즉시 삭제** — 디스크에 코드 원본 잔존 0.
- (FR2) UI에 **GitHub 토큰 입력칸**(선택) — 그 요청의 tarball 다운로드에만 사용, 서버 저장 금지.
  - private 레포 = 사용자가 자기 fine-grained PAT(해당 레포 Contents:Read) 입력
  - public 레포 = 토큰 없이 동작(비인증 rate limit 감수)
- (FR3) 서버의 gh/git ambient 인증 의존 제거 — 남이 서버 소유자 권한 못 씀.
- (FR4) 레포 삭제 버튼 — 임베딩(Chroma)·QA결과·매니페스트까지 일괄 삭제.
- (FR5) GitHub 외(GitLab 등)는 후순위 — URL 패턴 감지 후 미지원 안내.

**한계 명시**: 검색용 청크 텍스트는 Chroma에 남음(RAG에 필수). "코드가 서버에 전혀 안 옴"은 불가능.
증분 인덱싱은 tarball 전체 재다운로드 후 매니페스트 해시 비교로 동일하게 동작.

**2단계(별도, 배포 시점)**: GitHub OAuth 로그인 + 사용자별 레포 격리 + QA결과 분리.

---

### Phase 10 — 노드별 워커 동시성 튜닝

**배경**: 시흥 gpu-server2 실측(2026-07-20) — 5090×3 전부 유휴, 단일 요청은 GPU 1장만 사용.
현재 QA 워커 = 노드당 1개라 시흥의 3장 용량을 못 씀.

- (FR1) 노드 설정에 `concurrency` 추가 (local=1, siheung=2~3) — 워커 수를 노드별 동시성 합으로
- (FR2) 시흥에 동시 2~3배치 발사 → GPU 여러 장에 분산되는지 실측 (같은 모델 동시요청이 GPU 나눠 타는지 확인 필요 — Ollama 스케줄링에 달림)
- (FR3) EMA가 동시성에 의해 왜곡되지 않게 노드별 평균 재검토
- 선행조건: LiteLLM에 gemma4:31b 등록되면 모델 통일 후 측정이 더 깔끔

---

### Phase 8 — (선택) 운영 전환

- (FR1) 동시 사용자 증가 시 Ollama → **vLLM 서빙 전환** (OpenAI 호환이라 base_url만 교체)
  - 참고: GB10용 `gogamza/unsloth-vllm-gb10` 이미지 활용. 각 5090 노드도 vLLM로 통일 가능.
- (FR2) CI 훅: PR 생성 시 자동 QA 코멘트
- (FR3) 인증(OAuth) — 사내 배포 시

---

## 4. 데이터 흐름 요약

```
[Ingest]  repo → 필터 → tree-sitter 청킹 → bge-m3 → Chroma + SQLite
[QA]      위험청크 → (정적도구+gemma4:31b) map → filter(evidence) → reduce → 리포트
[Q&A]     질문 → 의도분류 → bge-m3 검색 → 컨텍스트 → gemma4:31b → 인용검증 → 답변
```

---

## 5. 핵심 설계 결정 (의사결정 기록)

| 항목 | 결정 | 이유 |
|---|---|---|
| 임베딩 모델 | `bge-m3` | 다국어(한국어 주석) 강함, 이미 보유 |
| 추론 모델 | `gemma4:31b` 단일 | 모든 역할 통일. 혼용 이득 없음, keep_alive 저글링만 감소 |
| 청킹 | tree-sitter AST 단위 | 함수 경계 보존 → 검색·인용 정확도↑ |
| 벡터DB | ChromaDB | 로컬 영속, 설치 간단. (대규모 시 Qdrant 검토) |
| 프론트 | Vanilla JS 빌드리스 | 의존성 최소, 유지보수 쉬움 |
| 환각방지 | file:line 인용 강제 + 후검증 | 기존 플랫폼 검증된 패턴 |
| QA 방식 | 정적도구 + LLM 하이브리드 | LLM 단독 환각 방지, 근거 확보 |
| 멀티에이전트 | simple/agentic 이중 모드 | PoC는 단발, 복잡 질문만 에이전트 승격 |
| 롱 체인 | plan-execute-reflect + Critic 루프 + CoVe | 분해·자기검증으로 정확도·환각방지 |
| GPU 분산 | 백엔드 Router 자동선택(프론트는 가시화) | CORS·키노출·통계공유 문제 회피, UX는 유지 |
| 라우팅 점수 | load·EMA·health 가중합 | 상사 요구(최저부하·최단응답) 정량화 |
| 서빙 | 우선 Ollama, 후에 vLLM | PoC 빠르게, 확장 시 전환 |

---

## 6. 프로젝트 디렉터리 구조 (목표)

```
repo-qa-agent/
├── roadmap.md                  ← 본 문서
├── docker-compose.yml
├── requirements.txt
├── backend/
│   ├── app.py                  FastAPI 진입점 (/health /ingest /qa /chat)
│   ├── config.py               모델명·URL·임계값 (valves 유사)
│   ├── ingest/
│   │   ├── collector.py        파일 수집 + gitignore 필터
│   │   ├── chunker.py          tree-sitter AST 청킹
│   │   └── indexer.py          bge-m3 임베딩 → Chroma
│   ├── qa/
│   │   ├── static_tools.py     ruff/eslint/radon 래퍼
│   │   ├── analyzer.py         map-reduce LLM QA
│   │   └── report.py           마크다운/JSON 리포트
│   ├── chat/
│   │   ├── router.py           쿼리 의도 분류
│   │   ├── retriever.py        Chroma 검색 + 컨텍스트 조립
│   │   ├── answerer.py         gemma4:31b 답변 생성
│   │   └── guard.py            인용 검증(환각방지)
│   ├── orchestrator/           ← Phase 3.5 멀티에이전트
│   │   ├── agent.py            Agent 베이스 + Planner/Retriever/Analyzer/Critic/Synthesizer
│   │   ├── orchestrator.py     체인 실행·blackboard·loop budget
│   │   └── blackboard.py       에이전트 공유 상태
│   ├── llm/
│   │   ├── ollama_client.py    OpenAI 호환 호출 (교체 가능)
│   │   └── router.py           ← Phase 7 다중노드 로드밸런싱(NodeRouter)
│   └── store/
│       ├── vector.py           Chroma 래퍼
│       └── meta.py             SQLite (파일트리/세션/QA결과)
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/{chat.js, report.js, tree.js}
├── data/                       Chroma 영속 + SQLite (gitignore)
└── samples/                    테스트용 샘플 레포
```

---

## 7. 단계별 일정 (PoC 우선)

```
Day 1     Phase 0 — 골격 + 헬스체크
Day 2~3   Phase 1 — Ingest (작은 레포부터)
Day 4~5   Phase 3 — Q&A (UI 없이 API로 먼저 검증)   ← 핵심 가치 조기 검증
Day 6     Phase 4 — 최소 웹 UI
Day 7~8   Phase 2 — 자동 QA 리포트
이후      Phase 3.5 — 멀티에이전트 + 롱 체인 (품질 승격)
          Phase 5~6 — 평가·고급 기능
          Phase 7   — GPU 분산 (코드는 조기에 박아두고 noop 운영,
                      5090 노드 추가 시점에 활성화)
```

> **GPU 분산 팁**: Phase 7의 `NodeRouter` 추상화(단일 URL→리스트)는 **Phase 0~1 단계에서 미리 박아두는** 게 좋음.
> 지금은 noop으로 동작 영향 0이고, 나중에 5090 노드가 생기면 `OLLAMA_URLS`에 추가만 하면 됨.

> **전략**: Phase 2(QA)보다 **Phase 3(Q&A)를 먼저** 검증. Q&A가 동작하면 핵심 가치가 증명되고,
> QA 리포트는 그 위에 얹는 구조라 위험이 낮음.

---

## 8. 리스크 & 대응

| 리스크 | 대응 |
|---|---|
| 대형 레포 토큰 초과 | AST 청킹 + 위험 우선순위 샘플링 + map-reduce |
| LLM 환각(없는 코드 인용) | file:line 후검증 + "모름" 허용 + evidence 강제 |
| 청킹 경계 깨짐 | tree-sitter 함수 단위, 긴 함수는 슬라이딩 보조 |
| 검색 관련성 낮음 | rerank 도입, top-k 튜닝, 문서/코드 collection 분리 |
| 다언어 파서 부재 | tree-sitter 미지원 언어는 N줄 폴백 청킹 |
| 임베딩 시간 과다 | bge-m3 배치 + 증분 인덱싱(Phase 6) |
| 동시 사용자 증가 | Phase 7 vLLM 전환 (base_url 교체) |

---

## 9. 완료 기준 (전체 PoC)

1. 임의의 git 레포(로컬 경로/URL) 투입 → 자동 인덱싱 완료
2. 자동 QA 리포트 생성 — 발견사항 모두 실재 `file:line` 인용
3. 자연어 Q&A — 10개 질문 중 8개+ 정확, 인용 실재율 ≥ 98%
4. 모르는 질문에 환각 없이 정직 응답
5. 웹 UI에서 레포 투입 → 리포트 → 채팅 → 코드 점프 일괄 동작
6. 전 과정 100% 로컬 (외부 네트워크 호출 0)

---

## 10. 참고

- 재사용 자산: 기존 사출성형 플랫폼의 RAG(bge-m3)·환각방지(인용 강제)·오케스트레이터 패턴
- 인프라: GB10 unified memory, Ollama (`gemma4:31b`, `bge-m3`)
- 향후 서빙 확장: `gogamza/unsloth-vllm-gb10` (vLLM, Blackwell 전용)
