# C2 군사 전략 AI

EXAONE4 기반 C2(지휘통제) AI 시스템입니다.  
파이썬 워게임 시뮬레이터와 연동하여 전장 영상 분석, 정찰·공격 임무계획 수립, 전략/전술 추천을 수행합니다.

---

## 시스템 아키텍처

![Agent System Architecture](docs/agent_architecture.png)

> **레이어 설명**
> - **UI Layer** (파랑): Gradio 웹 인터페이스 — AI 에이전트 채팅, 워게임 시뮬레이터, 전장 지도 탭
> - **Agent / Planner** (초록): `BattlefieldAgent` (smolagents CodeAgent, EXAONE4) + `MissionPlanner`
> - **Tools** (주황/청록): 에이전트가 코드로 호출하는 도구 레이어 — 비디오/PDF/전략/워게임/ARMA3
> - **Core Systems** (보라/청록): 실제 연산 엔진 — VideoAnalysisSystem(YOLO), EXAONE Deep, WargameEngine, ARMA3DBManager
> - **External / Data** (빨강): 외부 데이터 — 영상 파일, 군사 교리 PDF, ARMA3 게임, 시나리오 설정

### 듀얼 모델 아키텍처

| 모델 | 역할 |
|------|------|
| **EXAONE4** | 메인 CodeAgent — 영상 분석, 상황 판단, 임무계획 수립, 최종 응답 |
| **EXAONE Deep** | 전략·전술 전문 — `strategy_advisor_tool`을 통해 EXAONE4가 호출 |

---

## 빠른 시작

```bash
# 패키지 설치
pip install -r requirements.txt

# AI 시스템 기동 (Gradio UI)
python main.py ui
```

브라우저에서 출력된 Gradio URL에 접속합니다.

---

## 워게임 시뮬레이터

내장 파이썬 워게임 엔진으로 대대급 전투를 시뮬레이션합니다.

### 시나리오 편제 (기계화 보병 대대 vs 대대)

| 진영 | 부대 ID | 병종 | 역할 |
|------|---------|------|------|
| BLUFOR | `Alpha` | 기계화보병 | 정면 공격 |
| BLUFOR | `Bravo` | 기계화보병 | 측방 기동 |
| BLUFOR | `Charlie` | 전차 | 기갑 돌파 |
| BLUFOR | `Delta` | **정찰** | 적 위치 탐지 |
| BLUFOR | `Echo` | 대전차 | 기갑 저지 |
| BLUFOR | `Foxtrot` | 자주포 | 화력 지원 |
| OPFOR | `Red1~Red5` | 혼성 | 방어·반격 |

### 전장 지도 범례

| 마커 | 의미 |
|------|------|
| 실선 빨간 마커 | OPFOR — 정확한 위치 탐지됨 (`detected`) |
| 주황 빈 원 | OPFOR — 개략 위치만 파악 (`approximate`) |
| 회색 빈 원 | OPFOR — 탐지 상실, 마지막 위치 (`lost`) |
| 파란 마커 | BLUFOR — 실제 위치 |

### 정찰 우선 워크플로

```
1. 🔍 정찰 임무계획 버튼 클릭
   → assess_recon_need()로 탐지 현황 평가
   → recommend_recon_routes()로 교전 회피 경로 생성
   → Delta(정찰부대)만 기동 — 공격부대 대기 유지

2. 정찰 완료 → 적 위치 탐지 상태 확인

3. ⚔️ 공격 임무계획 버튼 클릭
   → 탐지된 OPFOR 기준으로 격멸 임무계획 생성
   → 전 부대 공격 기동
```

---

## 에이전트 도구 목록

총 **28개** 도구가 등록되어 있으며 역할별로 6개 그룹으로 분류됩니다.

---

### 1. 영상 DB 조회 도구 (`videodb_query_tool.py`)

SAM3 기반으로 분석된 전장 영상 세그먼트를 검색하고 조회합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_selected_contexts()` | — | 현재 선택된 비디오·PDF 컨텍스트 목록 반환 |
| `query_video_semantic(query, top_k)` | query: 자연어, top_k: 최대 결과 수(기본 5) | 자연어로 영상 세그먼트를 의미론적 검색 |
| `query_video_by_object(object_class)` | object_class: 군사 객체 유형 | 특정 객체(전차, 헬기 등)가 등장하는 세그먼트 검색 |
| `query_video_by_event(keyword)` | keyword: 이벤트 키워드 | AI 생성 설명에서 특정 이벤트 키워드로 세그먼트 검색 |
| `get_video_summary(video_id)` | video_id: 비디오 ID | 특정 비디오의 요약 통계(객체·이벤트 수 등) 반환 |
| `get_segment_details(segment_id)` | segment_id: 세그먼트 ID | 특정 세그먼트의 상세 정보(타임스탬프, 객체 목록 등) 반환 |
| `set_active_videos(video_ids)` | video_ids: 비디오 ID 리스트 | 에이전트가 쿼리할 활성 비디오 목록 설정 |

---

### 2. PDF RAG 도구 (`pdf_rag_tool.py`)

군사 교범·문서를 벡터 DB에 색인하고 검색합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `pdf_rag_search(query, top_k)` | query: 검색 쿼리, top_k: 결과 수(기본 5) | 군사 교범·PDF에서 관련 내용 시맨틱 검색 |
| `add_pdf_to_rag(pdf_path)` | pdf_path: PDF 파일 경로 | PDF를 RAG 시스템에 추가(청킹 + 임베딩 색인) |

---

### 3. 워게임 시뮬레이터 조회 도구 (`wargame_query_tool.py`)

내장 파이썬 워게임 엔진의 실시간 전장 상황을 조회합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_wargame_situation()` | — | 현재 워게임의 전체 전장 상황(부대 위치·HP·진영 등) 반환 |
| `get_intelligence_report(side)` | side: `"BLUFOR"` / `"OPFOR"` | 특정 진영의 적 탐지 인텔 보고서 반환 (FOW 상태 포함) |
| `get_wargame_unit_detail(unit_id)` | unit_id: 부대 ID | 특정 부대의 상세 정보·최근 이동 이력 반환 |
| `get_wargame_battle_log(n)` | n: 가져올 로그 수(기본 20) | 최근 전투 이벤트 로그 반환 (교전·기습·이동 기록) |

> **FOW(Fog of War) 상태값:** `"detected"` (정확) / `"approximate"` (대략) / `"lost"` (탐지 소실)

---

### 4. 워게임 임무계획 실행 도구 (`wargame_mission_tool.py`)

워게임 엔진에 임무계획 및 공중지원 명령을 직접 적용합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `apply_wargame_mission_plan(plan_json)` | plan_json: 임무계획 JSON 문자열 | BLUFOR 임무계획(이동 경로·목표)을 워게임에 적용 |
| `apply_wargame_air_support(support_json)` | support_json: 공중지원 계획 JSON | CAS(근접항공지원) 임무를 워게임 엔진에 적용 |
| `get_wargame_engine_status()` | — | 워게임 엔진 상태(실행 중 여부, 시간 배율, 현재 틱 등) 반환 |

---

### 5. 워게임 전술 분석 도구

#### 5-1. 정찰 임무 (`wargame_recon_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `assess_recon_need()` | — | OPFOR 탐지 현황 평가 — 정찰 필요 여부 및 미탐지 목표 목록 반환 |
| `recommend_recon_routes()` | — | 교전 회피 정찰 경로 자동 생성 (측방 우회 + 관측 포인트 + 복귀점) |

**정찰 경로 설계 원칙:**
- 직선 접근 금지 → 60° 측방 우회 경유지 삽입
- standoff 5 km 유지 (교전 범위 4 km 바깥)
- 목표 주변 3개 관측 포인트 (고도·엄폐율 기준 최적화)
- 관측 완료 후 안전 복귀점

#### 5-2. 전술 권고 (`wargame_strategy_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_wargame_tactical_recommendation()` | — | 병종 상성 분석 + 지형 기반 최적 기동 경로 추천 |

#### 5-3. 최적 공격 위치·수단 추천 (`wargame_attack_advisor_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_optimal_attack_positions(top_n)` | top_n: 목표별 추천 위치 수(기본 3) | 탐지된 OPFOR 위치·고도·엄폐를 분석하여 최적 공격 위치 및 수단 추천 |

**위치 후보 생성:** 각 OPFOR 목표 기준 16방향 × 4거리(1.2/2.0/3.0/4.5 km) = 64개 후보

**점수 가중치:**

| 요소 | 가중치 | 설명 |
|------|--------|------|
| 고도 우위 | 30% | 공격자가 더 높을수록 유리 |
| 공격자 엄폐 | 25% | 공격 위치의 지형 엄폐율 |
| 적 노출도 | 20% | 적의 엄폐가 낮을수록 고점수 |
| 교전 효율 | 15% | 거리별 교전 효율 (1.2 km 최적) |
| 시선 품질 | 10% | 지형 차폐 없이 적을 관측 가능한 정도 |

---

### 6. 전략 어드바이저 도구 (`strategy_advisor_tool.py`)

EXAONE Deep 모델을 호출하여 전략·전술 권고를 생성합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `strategy_advisor_tool(query)` | query: 전략/전술 질문 | EXAONE4의 상황 분석 + 사용자 쿼리를 EXAONE Deep에 전달하여 전술 권고 생성 |

> 전략/전술 쿼리가 감지되면 에이전트가 자동으로 이 도구를 사용합니다.  
> `agent_config.yaml`의 `strategy_keywords`를 기준으로 판별합니다.

---

### 도구 그룹 요약

| 그룹 | 파일 | 도구 수 | 주요 용도 |
|------|------|---------|-----------|
| 영상 DB 조회 | `videodb_query_tool.py` | 7 | SAM3 분석 영상 세그먼트 검색 |
| PDF RAG | `pdf_rag_tool.py` | 2 | 군사 교범 문서 검색 |
| 워게임 조회 | `wargame_query_tool.py` | 4 | 시뮬레이터 실시간 전장 상황 |
| 워게임 실행 | `wargame_mission_tool.py` | 3 | 임무계획·공중지원 적용 |
| 전술 분석 | `wargame_recon_tool.py` + `wargame_strategy_tool.py` + `wargame_attack_advisor_tool.py` | 4 | 정찰·전술 권고·최적 공격 위치 |
| 전략 어드바이저 | `strategy_advisor_tool.py` | 1 | EXAONE Deep 전략·전술 권고 |

---

## 파일 구조

```
C2_program_ai/
├── agent/
│   └── battlefield_agent.py       # EXAONE4 메인 에이전트
├── wargame/
│   ├── engine.py                  # 워게임 시뮬레이션 엔진 (FOW, 교전, 기동)
│   ├── models.py                  # Unit, AirSupport, WargameDB 데이터 모델
│   ├── scenario.py                # 대대 vs 대대 초기 편제
│   ├── terrain.py                 # 지형 고도·엄폐 맵
│   └── llm_planner.py             # LLM 기반 임무계획 생성기
├── tools/
│   ├── videodb_query_tool.py      # 영상 DB 조회 (7개 도구)
│   ├── pdf_rag_tool.py            # PDF RAG (2개 도구)
│   ├── wargame_query_tool.py      # 워게임 조회 (4개 도구)
│   ├── wargame_mission_tool.py    # 임무계획 실행 (3개 도구)
│   ├── wargame_recon_tool.py      # 정찰 임무 (2개 도구)
│   ├── wargame_strategy_tool.py   # 전술 권고 (1개 도구)
│   ├── wargame_attack_advisor_tool.py  # 최적 공격 위치 (1개 도구)
│   └── strategy_advisor_tool.py   # EXAONE Deep 전략 어드바이저 (1개 도구)
├── core_src/
│   ├── video_analysis_system.py   # SAM3 영상 분석
│   ├── object_detection.py        # SAM3 객체 탐지·추적
│   ├── embedding_generator.py     # MobileCLIP 임베딩
│   └── event_description.py       # SmolVLM2 이벤트 설명
├── ui/
│   └── gradio_app.py              # Gradio 웹 인터페이스
├── config/
│   ├── agent_config.yaml          # 에이전트 설정 (max_steps, strategy_keywords 등)
│   ├── agent_custom_instructions.txt  # 에이전트 시스템 프롬프트
│   └── models_config.yaml         # ML 모델 설정
├── main.py
└── requirements.txt
```

---

## 설정

### `config/agent_config.yaml` 주요 설정

```yaml
code_agent:
  max_steps: 20          # 에이전트 최대 추론 스텝 (줄이면 응답 빠름)
  planning_interval: 3   # N스텝마다 플래닝 실행
  stream_outputs: false  # true로 변경 시 스트리밍 출력

strategy_keywords:
  korean: [전략, 전술, 작전, 정찰, 기습, ...]
  english: [strategy, tactics, reconnaissance, ...]
```

### 권장 추론 속도 개선

| 방법 | 효과 |
|------|------|
| `max_steps: 5~8`로 감소 | 응답 시간 비례 단축 |
| vLLM / Ollama 서빙 | 로컬 추론 3~5× 빠름 |
| `load_in_4bit: true` | GPU 메모리 75% 절감, 2~3× 빠름 |
| API 모델 교체 (Claude Haiku 등) | 10~30× 빠름 |
