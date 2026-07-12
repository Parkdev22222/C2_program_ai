# C2 군사 AI — LangGraph 에이전트

EXAONE4(또는 Gemini) 기반 C2(지휘통제) 군사 AI 시스템의 **LangGraph 백엔드**입니다.
이 문서는 시스템 중 **LangGraph 기반으로 동작하는 부분**만을 다룹니다 — 전체 LangGraph
파이프라인, 지원 도구, 그리고 실행 방법이 중심입니다.

에이전트는 LangGraph `StateGraph`(그래프 기반 ReAct: LLM ↔ ToolNode)로 동작하며,
Python 워게임 시뮬레이터와 연동해 정찰·공격 임무계획 수립과 전술 판단을 수행합니다.

> 백엔드는 환경변수 `C2_AGENT_BACKEND`로 전환합니다. **기본값이 `langgraph`**이며,
> 이 문서는 그 기본 경로를 설명합니다. (`smolagents` 백엔드는 이 문서의 범위 밖입니다.)

---

## 1. LangGraph 파이프라인

### 1.1 전체 흐름

```
                     ┌──────────────────────── LangGraphBattlefieldAgent ────────────────────────┐
   사용자/UI 쿼리 ──▶ │                                                                            │
 (채팅·공격·정찰·COA) │   ① 온톨로지 상황 자동 주입      ② StateGraph 실행 (create_react_agent)     │
                     │      _inject_ontology()             ┌───────────────────────────────┐      │
                     │      Neo4j → 전장 상황 블록  ──────▶ │   ┌────────┐  tool_calls        │      │
                     │                                     │   │  LLM    │ ───────────────┐  │      │
                     │                                     │   │ (Chat)  │ ◀───────────┐  │  │      │
                     │                                     │   └────────┘  ToolMessage │  ▼  │      │
                     │                                     │        ▲                  │ ┌──────┐  │
                     │                                     │        └──────────────────┴─│ Tool │  │
                     │                                     │        (도구 필요 없을 때까지 │ Node │  │
                     │                                     │         반복: ReAct 루프)     └──────┘  │
                     │                                     └───────────────────────────────┘      │
                     │   ③ 최종 메시지 추출               ④ (채팅) 이번 턴 대화 메모리 저장          │
                     │      _extract_result_text()          conversation_store (PG / in-memory)    │
   최종 응답/JSON ◀── │      → mission_plans JSON / 툴 성공 payload / 텍스트                          │
                     └────────────────────────────────────────────────────────────────────────────┘
```

**단계별 설명**

| 단계 | 함수 / 위치 | 하는 일 |
|------|-------------|---------|
| ① 온톨로지 주입 | `_inject_ontology()` → `ontology_situation_block()` | 매 판단마다 Neo4j 온톨로지에서 현재 전장 상황을 조회해 쿼리 앞에 `[현재 전장 상황]` 블록으로 주입 (별도 상황 조회 툴 대체) |
| ② 그래프 실행 | `create_react_agent(llm, tools, prompt)` | LangGraph `StateGraph` ReAct 루프. LLM이 **네이티브 function calling**으로 도구를 호출하면 ToolNode가 실행하고 결과(ToolMessage)를 되돌려주며, 더 이상 도구가 필요 없을 때까지 반복 |
| ③ 결과 추출 | `_extract_result_text()` | 실행 메시지 뒤에서부터 스캔해 최종 텍스트(임무계획 JSON / 툴 성공 payload / 응답)를 뽑아 UI가 파싱 가능한 형태로 반환 |
| ④ 대화 메모리 | `conversation_store` (채팅 경로 전용) | 전술채팅에서 이번 턴(쿼리+툴 호출/결과+응답)을 저장소에 적재 (PostgreSQL 또는 in-memory) |

### 1.2 실행 경로 (무상태 vs 멀티턴)

에이전트는 두 가지 진입점을 제공합니다.

| 메서드 | 경로 | 상태 | 용도 |
|--------|------|------|------|
| `_raw_run(query)` (= `agent.agent.run`) | 공격·정찰·COA 계획 | **무상태** — 매 `invoke`가 독립 | 이전 대화가 섞이면 안 되는 계획 수립 |
| `run(query)` | 전술채팅 | **멀티턴** — 이전 `_MEMORY_TURNS`(기본 2)턴 적재 | 대화형 질의응답 |

`create_react_agent`는 `invoke`마다 stateless이므로 무상태 경로는 별도 리셋이 필요 없습니다.
멀티턴 경로만 저장소에서 이전 턴을 복원해 현재 쿼리 앞에 붙입니다.

### 1.3 핵심 컴포넌트

| 파일 | 역할 |
|------|------|
| `agent/langgraph_agent.py` | `LangGraphBattlefieldAgent` — 그래프 구성·실행, 온톨로지 주입, 결과 추출, 대화 메모리 |
| `agent/langgraph_llm.py` | LLM 팩토리 — vLLM(EXAONE4) / Gemini 프로바이더 선택 및 `ChatOpenAI`/`ChatGoogleGenerativeAI` 생성 |
| `agent/langgraph_tools.py` | 도구 어댑터 — smolagents `@tool` → LangChain `StructuredTool` 변환 |
| `agent/conversation_store.py` | 전술채팅 멀티턴 대화 저장소 (PostgreSQL / in-memory) |
| `tools/ontology_query_tool.py` | `ontology_situation_block()` — 매 판단 시 주입할 전장 상황 |
| `config/models_config.yaml` | LLM 프로바이더·서빙·생성 파라미터 |
| `config/agent_custom_instructions.txt` | 시스템 지시사항([LEARNED_RULES] 등 전술 교리) |

> 그래프는 `config/agent_config.yaml`의 `code_agent.max_steps`를 읽어
> `recursion_limit = max_steps * 2 + 5`로 재귀 한도를 설정합니다.

---

## 2. LLM 프로바이더

LangGraph 백엔드는 두 가지 LLM을 지원하며, 둘 다 **function calling**을 지원하므로 그래프에서
동일하게 동작합니다. 프로바이더는 환경변수 `C2_LLM_PROVIDER`(또는 `models_config.yaml`의
최상위 `llm_provider`)로 전환합니다.

| `C2_LLM_PROVIDER` | LLM | 연결 방식 | 필요 조건 |
|-------------------|-----|-----------|-----------|
| `vllm` (기본) | 직접 서빙한 EXAONE4 | `ChatOpenAI` → vLLM OpenAI 호환 API | vLLM 서버 기동 (tool-calling 활성화) |
| `gemini` | Google Gemini API | `ChatGoogleGenerativeAI` | `GOOGLE_API_KEY` 환경변수 |

**프로바이더 선택 우선순위:** `C2_LLM_PROVIDER` > `models_config.yaml`의 `llm_provider` > `vllm`

- **vLLM 서버 주소:** `C2_AGENT_VLLM_BASE_URL` > `agent_model.serving.base_url` > `host:port`(기본 `127.0.0.1:8000/v1`)
- **Gemini 모델:** `models_config.yaml`의 `gemini_model.model`(기본 `gemini-2.5-flash`)
- **API 키:** 코드/설정 파일이 아닌 **환경변수**로 주입 (`api_key_env`는 키 값이 아니라 키가 담긴 환경변수 **이름**)

---

## 3. 지원 도구

### 3.1 도구 어댑터

LangGraph 에이전트는 별도의 도구를 새로 만들지 않고, 기존 smolagents `@tool` 객체를
`agent/langgraph_tools.py`가 LangChain `StructuredTool`로 감싸 **그대로 재사용**합니다.

- `build_langchain_tools()` → `build_battlefield_tools()`(단일 소스)의 각 smolagents 툴을 변환
- smolagents 툴의 `forward` 시그니처 + `inputs`로 pydantic `args_schema` 자동 생성 (기본값 보존)
- 실행 로직·워게임 엔진 연동·반환 구조가 동일 → smolagents 경로와 **완전히 같은 기능**
- 반환값이 dict/list면 JSON 문자열로 직렬화해 `ToolMessage`로 전달
- smolagents의 조기 종료 예외(`FinalAnswerException` 등)는 **성공 결과로 환원** (성공을 툴 에러로 오인 방지)

### 3.2 등록되는 도구 목록

LangGraph 에이전트에 등록되는 워게임 도구입니다. (상태 스냅샷 조회 툴은 **온톨로지 자동 주입**으로
대체되므로 등록하지 않습니다.)

| 도구 | 파일 | 파라미터 | 설명 |
|------|------|----------|------|
| `apply_wargame_mission_plan` | `wargame_mission_tool.py` | `plan_json`, `dry_run=False` | BLUFOR 임무계획(이동 경로·목표·공중지원)을 워게임 엔진에 즉시 적용 |
| `apply_wargame_air_support` | `wargame_mission_tool.py` | `support_json`, `dry_run=False` | CAS·타격·포병·헬기 공중지원을 즉시 적용 (탐지 OPFOR 좌표로 자동 스냅) |
| `get_pending_plan_tool` | `mission_plan_validator_tool.py` | — | 승인 대기 중인 임무계획·세션 상태 조회 |
| `predict_opfor_routes` | `wargame_opfor_routes_tool.py` | — | 탐지된 OPFOR의 예상 기동 경로 3방향(정면/우회/좌회)을 지형 기반으로 생성 |
| `get_optimal_attack_positions` | `wargame_attack_advisor_tool.py` | `top_n=3`, `opfor_routes_json` | 탐지 OPFOR 위치·고도·엄폐 분석 → 최적 공격 위치·수단 추천 (+ COHA 교리 컨텍스트) |
| `get_fire_priority_schedule` | `wargame_fire_priority_tool.py` | — | 병종·현황을 반영한 포병·CAS 타격 우선순위 스케줄 산출 |
| `assess_recon_need` | `wargame_recon_tool.py` | — | OPFOR 탐지 현황 평가 — 탐지 상태별 부대 목록·정찰 필요 여부 |
| `recommend_recon_routes` | `wargame_recon_tool.py` | — | 교전 회피 정찰 경로 생성 (미터 좌표 `apply_json` + COHA 교리 컨텍스트) |
| `graph_rag_military_query` | `graph_rag_tool.py` | `query` | COHA 군사 전술 온톨로지에서 교리 개념·관계 검색 (Graph RAG) |

> 추가로 `videodb_query_tool`·`pdf_rag_tool`이 설치 환경에서만 선택적으로 로드됩니다
> (모듈 부재 시 자동 스킵). 위 워게임 도구가 핵심 툴셋입니다.

### 3.3 임무계획 JSON 형식

LLM은 도구 호출로 데이터를 조회한 뒤, 최종 임무계획을 하나의 JSON 블록으로 출력하거나
`apply_wargame_mission_plan`으로 직접 적용합니다.

```json
{
  "reasoning": "한국어 판단 근거",
  "mission_plans": [
    {
      "company_id": "Alpha",
      "mission_type": "attack|defend|flank|withdraw|hold|recon",
      "target_unit_id": "Red1",
      "waypoints": [[x_m, y_m], ...],
      "objective": "임무 목표 설명"
    }
  ],
  "air_support_plans": [
    {
      "call_sign": "EAGLE-1",
      "support_type": "cas|strike|artillery|helicopter",
      "target": [x_m, y_m],
      "radius": 1500,
      "delay": 60
    }
  ]
}
```

- `waypoints` 좌표는 반드시 **미터(m) 정수** (`target` 좌표는 탐지된 OPFOR 실제 좌표 사용)
- `mission_plans`에 포함된 부대만 업데이트 (선택적 재배정)

### 3.4 임무계획 수립 흐름

**공격 임무:** `assess_recon_need()` → `predict_opfor_routes()`(선택) →
`get_optimal_attack_positions(opfor_routes_json=...)` → 최종 JSON 생성 및 적용

**정찰 임무:** `assess_recon_need()` → `recommend_recon_routes()` → 정찰부대(Delta)만 임무 부여

---

## 4. 실행 방법

### 4.1 설치

```bash
pip install -r requirements.txt
```

LangGraph 백엔드 관련 핵심 의존성(`requirements.txt`에 포함):

```
langgraph>=0.2.0
langchain-core>=0.3.0
langchain-openai>=0.2.0        # vLLM(EXAONE4) 연결
langchain-google-genai>=2.0.0  # Gemini 연결 (선택)
psycopg2-binary>=2.9.0         # PostgreSQL 대화 메모리 (선택)
```

### 4.2 (A) vLLM 서버로 EXAONE4 사용 — 기본

LangGraph 백엔드는 **function calling**으로 도구를 호출하므로, vLLM 서버를
**tool-calling 활성화 옵션**으로 기동해야 합니다 (`--enable-auto-tool-choice --tool-call-parser hermes`).

```bash
# EXAONE4 vLLM 서버 기동 (:8000) — tool-calling 활성화 (A100 80GB 기준)
nohup vllm serve LGAI-EXAONE/EXAONE-4.0-32B-AWQ --host 127.0.0.1 --port 8000 \
  --served-model-name exaone4-agent --trust-remote-code \
  --quantization awq_marlin --dtype float16 \
  --gpu-memory-utilization 0.90 --max-model-len 32768 \
  --enable-prefix-caching --max-num-seqs 64 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  > out1.log 2>&1 &

# 서버 준비 확인 (200이면 완료)
curl http://127.0.0.1:8000/health
```

> `scripts/launch_vllm_servers.py`가 `config/models_config.yaml`의 값을 읽어 동일하게 기동합니다.
> `--served-model-name`(`exaone4-agent`)은 `serving.served_model_name`과 일치해야 합니다.

```bash
# LangGraph 백엔드로 UI 실행 (기본값이라 C2_AGENT_BACKEND 생략 가능)
python main.py ui                 # http://localhost:7860

# 단일 쿼리 (CLI)
python main.py query --query "적 기갑에 대한 공격 임무계획 수립"
```

### 4.3 (B) Gemini API 사용 — GPU/서버 불필요

```bash
# 1) API 키를 환경변수로 주입 (Google AI Studio 발급)
export GOOGLE_API_KEY="발급받은_키"     # 또는 GEMINI_API_KEY

# 2) 프로바이더 전환 후 실행 (vLLM 서버 불필요)
export C2_LLM_PROVIDER=gemini
python main.py ui
```

- 사용 모델은 `config/models_config.yaml`의 `gemini_model.model`에서 지정
- EXAONE4로 되돌리려면 `unset C2_LLM_PROVIDER` (또는 `C2_LLM_PROVIDER=vllm`)
- Gemini는 tool-calling 기본 지원 → EXAONE4와 **동일한 툴셋·동일한 동작**

### 4.4 백엔드 전환

```bash
C2_AGENT_BACKEND=langgraph python main.py ui   # LangGraph (기본)
C2_AGENT_BACKEND=smolagents python main.py ui   # 기존 smolagents 백엔드로 복귀
```

### 4.5 전술채팅 멀티턴 대화 메모리

전술채팅(`run` 경로)은 이전 `_MEMORY_TURNS`(기본 2)턴을 저장소에서 적재해 멀티턴을 지원합니다.
저장소는 **PostgreSQL** 또는 **in-memory 폴백**이며, 접속정보가 없으면 자동으로 in-memory로 동작합니다.
(공격·정찰·COA 계획 경로는 무상태이므로 대화가 섞이지 않습니다.)

| 환경변수 | 설명 |
|----------|------|
| `C2_CHAT_STORE` | `postgres` / `inmemory` 강제 선택 (미설정 시 접속정보 있으면 postgres) |
| `C2_PG_DSN` | PostgreSQL 접속 문자열 (예: `postgresql://user:pw@host:5432/c2`) |
| `C2_PG_HOST` / `C2_PG_PORT` / `C2_PG_DB` / `C2_PG_USER` / `C2_PG_PASSWORD` | DSN 대신 분리 지정 |
| `C2_CHAT_SESSION_ID` | 대화 세션 ID (기본 `wargame_chat`) |

```bash
export C2_PG_DSN="postgresql://postgres:pw@127.0.0.1:5432/c2"
python main.py ui   # 접속 실패 시 자동 in-memory 폴백
```

- 대화 턴은 `c2_chat_turns` 테이블에 LangChain 메시지(Human/AI/Tool) 직렬화로 적재
- 유지 턴 수는 `agent/langgraph_agent.py`의 `_MEMORY_TURNS`로 조정

### 4.6 (선택) 온톨로지 Neo4j 연결

매 판단마다 주입되는 전장 상황은 온톨로지에서 조회됩니다. 환경변수를 설정하지 않으면
in-memory 그래프로 자동 폴백되어 Neo4j 없이도 동작합니다.

```bash
export OI_NEO4J_URI="neo4j+s://<your-db>.databases.neo4j.io"
export OI_NEO4J_USER="neo4j"
export OI_NEO4J_PASSWORD="<password>"
```

---

## 5. 요약

- **파이프라인:** 온톨로지 상황 주입 → LangGraph `create_react_agent` ReAct 루프(LLM ↔ ToolNode) → 결과 추출 → (채팅) 대화 메모리 적재
- **LLM:** vLLM 서빙 EXAONE4(function calling) 또는 Gemini API — `C2_LLM_PROVIDER`로 전환
- **도구:** smolagents `@tool`을 LangChain `StructuredTool`로 감싼 워게임 도구 9종(+선택적 RAG 도구)
- **실행:** `python main.py ui` / `python main.py query --query "..."` — 백엔드 기본값 `langgraph`
</content>
</invoke>
