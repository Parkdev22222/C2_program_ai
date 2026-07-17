# C2 군사 AI 시스템 — 클린 아키텍처 전면 리팩토링 설계

- **작성일**: 2026-07-17
- **대상 브랜치**: `dev` → 작업은 분기 브랜치 `refactor/clean-architecture`
- **방식**: 점진적(Strangler) 마이그레이션
- **목표 스타일**: 실용형 4계층 클린 아키텍처

---

## 1. 배경과 목표

현재 코드베이스(~19,500줄, Python)는 다음 문제를 안고 있다.

- **테스트 안전망 부재**: 테스트 파일 2개(`tests/test_ontology_pipeline.py`, `tests/tool_trace_eval.py`)뿐. 리팩토링 회귀를 잡을 방법이 없다.
- **대형 모놀리스**: `ui/gradio_app.py`(2,502줄), `wargame/engine.py`(2,029줄)가 여러 책임을 한 파일에 담은 god object.
- **뒤섞인 계층 경계**: 아래 순환/역방향 의존이 존재한다.
  - `wargame ⇄ tools` **순환 의존** (엔진이 tools를 import, tools도 wargame을 import)
  - `ui/gradio_app.py`가 tools·wargame·agent·ontology·core_src를 모두 직접 import (god UI)
- **확장 어려움**: LLM(vLLM/EXAONE4), 저장소(SQLite/PostgreSQL/온톨로지 스토어)가 도메인 로직과 직접 결합되어 교체·확장이 어렵다.

### 리팩토링 목표 (사용자 확정)

1. **확장성 확보** — 새 시나리오·툴·저장소를 계층 교체만으로 추가
2. **계층 경계·의존성 정리** — 단방향 의존, 순환 제거
3. **대형 파일 분해** — engine·gradio god object 해체
4. **테스트 용이성 확보** — 비즈니스 로직을 프레임워크/IO에서 분리

### 스코프 축소 (사용자 확정)

이번 리팩토링과 함께 다음을 **제거**한다.

| 대상 | 처리 | 근거 |
|------|------|------|
| **ARMA3 연동** | 완전 삭제 | 더 이상 사용 안 함 |
| **PDF RAG** | 죽은 참조 제거 | `pdf_rag_tool.py` 파일 자체가 없고, `battlefield_agent.py`의 로드 시도만 남음 |
| **비디오(SAM/추적)** | 조치 없음 | dev 브랜치에 코드 없음(원격 `sam3-*` 브랜치 한정) |
| **Gradio UI** | 삭제, FastAPI+HTML로 대체 | 새 UI 방향 확정 |

**유지**: 워게임 시뮬레이터 · LLM 에이전트(LangGraph/smolagents) · 배틀필드 온톨로지 KG · **그래프 RAG(교리 온톨로지, `coha_full_ontology.ttl`)** · FastAPI + HTML 대시보드

#### 제거 대상 파일 (ARMA3)

- `arma3_integration/` (arma3_launcher.py, launch.py, relay.py)
- `api/arma3_receiver.py`
- `core_src/` (arma3_db_manager.py, arma3_order_manager.py)
- `tools/arma3_order_tool.py`, `tools/arma3_query_tool.py`
- `data/arma3_orders.json`, `data/arma3_state.json`
- 툴 등록부의 ARMA3 참조(`agent/battlefield_agent.py`, `agent/langgraph_tools.py`)
- 삭제로 `tools→core_src`, `api→core_src` 의존이 자동 소멸

#### 제거 대상 (PDF RAG)

- `agent/battlefield_agent.py:144-148`의 `from tools.pdf_rag_tool import ...` 죽은 블록

---

## 2. 목표 아키텍처 — 실용형 4계층

### 계층 정의

| 계층 | 역할 | 의존 방향 | 프레임워크 의존 |
|------|------|----------|----------------|
| **domain** | 순수 도메인 규칙. Unit·AirSupport·지형·온톨로지 엔티티, 전투/탐지/좌표 계산, 값 객체·불변식 | 없음 (최내층) | ❌ 순수 Python |
| **application** | 유스케이스·오케스트레이션(시뮬 틱 루프, 임무계획 적용, 자동 재계획, 에이전트 실행). **포트(인터페이스)** 정의 | domain만 | ❌ 포트로 추상화 |
| **infrastructure** | 포트 구현체. vLLM 클라이언트, SQLite 이벤트 DB, 온톨로지 스토어, PostgreSQL 대화 저장 | application 포트 구현 + domain 사용 | ✅ vLLM·DB 등 |
| **presentation** | 진입점·전달 계층. FastAPI web_api, HTML 대시보드, CLI, LLM 툴 어댑터(tools/) | application 유스케이스 호출 | ✅ FastAPI 등 |

### 의존성 규칙 (The Dependency Rule)

```
presentation ─┐
              ├─▶ application ─▶ domain
infrastructure┘   (ports)  ▲
                           └── infrastructure가 포트 구현 (의존성 역전)
```

- **모든 의존은 안쪽(domain)을 향한다.** domain은 누구도 import하지 않는다.
- infrastructure는 application이 정의한 **포트 인터페이스를 구현**하여 조립 루트에서 주입(DI)된다.
- 이 규칙은 `import-linter` 계약으로 CI/로컬에서 **자동 검증**한다.

### 이 구조가 해결하는 것

1. **`wargame ⇄ tools` 순환 제거**: 엔진(application)은 tools를 모른다. tools(presentation)가 엔진을 단방향 호출한다. 엔진이 tools에서 쓰던 유틸은 domain으로 내려보내거나 포트로 역전한다.
2. **god UI/god engine 해체**: gradio 오케스트레이션 → `application/simulation/replan.py`, engine 순수계산 → `domain/wargame/combat.py`, 이벤트 DB → `infrastructure/persistence`.
3. **LLM/DB 교체 가능**: 포트 뒤로 숨겨 EXAONE4→타 모델, SQLite→타 DB 교체 시 application 무변경.

---

## 3. 목표 디렉토리 구조

Strangler 방식이므로 **새 `src/c2/` 패키지를 신설**해 기존 top-level 패키지와 공존시키며 한 조각씩 옮긴다.

```
src/c2/
  domain/                        # 순수 도메인 (프레임워크·IO 없음)
    wargame/
      unit.py                    ← wargame/models.py (Unit, AirSupport)
      terrain.py                 ← wargame/terrain.py + terrain_korea.py
      combat.py                  ← engine.py에서 순수 전투/탐지 계산 추출
      coordinates.py             ← tools/coord_utils.py
    ontology/
      models.py                  ← ontology/models.py
    planning/
      mission_plan.py            ← tools/mission_plan_validator.py (값객체·스키마)

  application/                   # 유스케이스·오케스트레이션 + 포트
    simulation/
      engine.py                  ← wargame/engine.py (틱 루프만, 대폭 슬림화)
      scenario.py                ← wargame/scenario.py
      replan.py                  ← gradio_app.py의 자동 재계획 워커 추출
    agent/
      battlefield.py             ← agent/langgraph_agent.py + battlefield_agent.py
      mission_planner.py         ← wargame/llm_planner.py
    ontology/
      builder.py                 ← ontology/wargame_builder.py
      retrieval.py               ← ontology/retrieval.py
      writer.py                  ← ontology/writer.py
      coa_view.py                ← ontology/coa_view.py
      doctrine_rag.py            ← tools/graph_rag_tool.py의 조회 로직 (교리 온톨로지 RAG)
    harness/                     ← wargame/harness/* (controller, episode_runner, metrics, rule_*, tactical_memory)
    ports/                       # ★ 인터페이스 (의존성 역전 지점)
      llm.py                     # LLMClient 포트
      ontology_store.py          # OntologyStore 포트
      event_store.py             # EventStore 포트
      conversation_store.py      # ConversationStore 포트

  infrastructure/                # 포트 구현체 (프레임워크·IO)
    llm/
      vllm_client.py             ← agent/vllm_client.py
      model_loader.py            ← agent/model_loader.py
      langgraph_llm.py           ← agent/langgraph_llm.py
    persistence/
      sqlite_event_store.py      ← engine.py 내 SQLite 이벤트 DB 추출
      harness_db.py              ← wargame/harness/harness_db.py
      conversation_store.py      ← agent/conversation_store.py (PostgreSQL)
    ontology/
      graph_store.py             ← ontology/graph_store.py
      in_memory_store.py         ← ontology/in_memory_store.py
      factory.py                 ← ontology/factory.py
      doctrine_loader.py         ← graph_rag_tool.py의 rdflib TTL 로더 부분

  presentation/                  # 진입점·전달 계층
    tools/                       ← tools/* (LLM 툴 어댑터, ARMA3 툴 제외)
      single_tool_guard.py, wargame_*_tool.py, coa_analysis_tool.py,
      ontology_query_tool.py, graph_rag_tool.py(thin wrapper), ...
    web/
      api.py                     ← ui/web_api.py (FastAPI REST — 정식 UI 백엔드)
      static/                    ← HTML/JS 대시보드 자산
    cli/
      main.py                    ← main.py (조립 루트 진입점)

  composition/
      container.py               # DI 조립 (포트↔구현 바인딩, 진입점에서 주입)

config/    scripts/    tests/    docs/    data/     # 유지 (tests는 새 구조 미러링)
```

> `data/coha_full_ontology.ttl`은 유지(그래프 RAG). `data/arma3_*.json`은 삭제.

### 핵심 분할 결정

- **engine.py(2,029줄) 3분할**
  - 순수 전투/탐지 계산 → `domain/wargame/combat.py`
  - 틱 루프·상태 전이 오케스트레이션 → `application/simulation/engine.py`
  - SQLite 이벤트 DB → `infrastructure/persistence/sqlite_event_store.py` (EventStore 포트 구현)
- **gradio_app.py(2,502줄) 분할**
  - 자동 재계획 워커·임무 적용 흐름 → `application/simulation/replan.py`
  - 나머지 UI는 폐기(FastAPI+HTML로 대체)
- **tools/는 통째로 presentation** (ARMA3 툴 제외): 에이전트가 호출하는 인터페이스 어댑터. tools→application→domain 단방향.
- **그래프 RAG 분리**: rdflib TTL 로더 → `infrastructure/ontology/doctrine_loader.py`, 조회 서비스 → `application/ontology/doctrine_rag.py`, LLM 툴 래퍼 → `presentation/tools/graph_rag_tool.py`.

---

## 4. 포트 정의 (의존성 역전 지점)

application이 소유하는 인터페이스. infrastructure가 구현하고 조립 루트에서 주입한다.

| 포트 | 책임 | 현재 구현 후보 |
|------|------|---------------|
| `LLMClient` | 프롬프트→응답 생성(동기/스트리밍) | vLLM 클라이언트(EXAONE4) |
| `OntologyStore` | 배틀필드 KG 읽기/쓰기 | graph_store / in_memory_store |
| `EventStore` | 시뮬 이벤트 적재/조회 | SQLite 이벤트 DB |
| `ConversationStore` | 전술채팅 멀티턴 적재/조회 | PostgreSQL(+in-memory 폴백) |

---

## 5. Strangler 마이그레이션 순서 & 검증

의존성 안쪽(domain)부터 만들고, 각 조각은 **기존 모듈을 re-export 얇은 shim으로 남겨** 항상 실행 가능하게 유지한다.

| Slice | 내용 | 위험도 |
|-------|------|--------|
| **0. 안전망** | `src/c2/` 뼈대 생성 + **특성화(characterization) 테스트** 구축 + `import-linter` 의존성 규칙 계약 설정 + **ARMA3/PDF RAG 죽은 코드 선삭제** | 낮음 |
| **1. domain 추출** | Unit/terrain/coord/ontology models/mission_plan + engine 순수 전투계산 → domain. 기존 파일은 re-export shim으로 유지 | 낮음 |
| **2. ports + infra** | 포트 4개 정의 → vLLM·온톨로지 스토어·SQLite 이벤트DB·PostgreSQL·교리 TTL 로더를 infra로 이동 + 포트 구현 | 중간 |
| **3. application** | engine 틱 루프·scenario·mission_planner·ontology builder/retrieval/writer/doctrine_rag·harness·agent → application. **여기서 `wargame⇄tools` 순환 제거** | 높음 |
| **4. presentation 재배선** | tools/*(ARMA3 제외)·web_api → presentation. gradio 오케스트레이션 추출 후 **Gradio 삭제**. main.py → 조립 루트(container.py 주입) | 중간 |
| **5. 정리** | shim 제거, 옛 top-level 패키지 삭제, config·scripts·docs·CLAUDE.md 갱신 | 낮음 |

### Slice별 완료 기준 (Definition of Done)

각 slice는 다음을 모두 만족해야 병합(커밋)한다.

1. **특성화 테스트 green** — engine 틱 결과·임무 적용·`/api/state` 응답·온톨로지 빌드 스냅샷이 리팩토링 전후 동일
2. **`import-linter` 의존성 규칙 green** — 계층 방향 위반 0
3. **스모크 통과** — 엔진 1회 구동 + FastAPI `/api/state` 정상 응답 + 시나리오 실행
4. **개별 커밋** — slice 단위로 커밋, 되돌리기 가능

### 특성화 테스트 (Slice 0 핵심)

테스트 안전망이 거의 없으므로, 리팩토링 전 현재 동작을 고정하는 스냅샷/골든 테스트를 먼저 만든다.

- **엔진 결정성 테스트**: 고정 시드 시나리오로 N틱 실행 → 유닛 상태/전투 결과 스냅샷
- **임무 적용 테스트**: 대표 mission plan JSON 적용 → 부대 waypoints/target 스냅샷
- **web_api 계약 테스트**: `/api/state`, `/api/events` 응답 스키마·값 스냅샷
- **온톨로지 빌드 테스트**: 기존 `tests/test_ontology_pipeline.py` 활용·확장

---

## 6. 리스크와 완화

| 리스크 | 완화 |
|--------|------|
| 시뮬레이션은 비결정 요소(랜덤 배치)가 있어 스냅샷이 불안정 | 시드 고정 + 결정적 시나리오(`setup_bn_vs_bn`) 사용, 랜덤 시나리오는 별도 처리 |
| engine 분할 중 자동 재계획 콜백 체계 손상 (CLAUDE.md 금지사항) | 콜백 등록 블록은 마지막 Slice까지 원형 유지, 계약 테스트로 4개 콜백 검증 |
| shim 남발로 임시 복잡도 증가 | Slice 5에서 shim 전량 제거를 명시적 완료 조건에 포함 |
| Gradio 제거 시 web_api가 대체 못 하는 기능 존재 가능 | Slice 4 착수 전 gradio 기능 인벤토리 → web_api 커버리지 확인 |

---

## 7. 범위 밖 (Non-goals)

- 시뮬레이션 로직·전술 알고리즘의 기능적 변경 (동작 보존이 원칙)
- 새 UI 프론트엔드(SPA) 도입 — FastAPI+HTML 유지
- LLM 모델 교체·프롬프트 튜닝
- ARMA3/비디오/PDF RAG 재도입
