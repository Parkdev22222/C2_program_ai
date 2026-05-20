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

### 브랜치 안내

- `main`: 안정 버전(배포/데모 기준)
- `work`: 기능 개발 브랜치

최신 기능 검증 후 `work`에서 `main`으로 병합한 뒤 README 변경사항을 함께 반영하세요.

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

총 **33개** 도구가 등록되어 있으며 역할별로 8개 그룹으로 분류됩니다.

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
> **좌표 단위:** 모든 위치 값은 미터(m) 정수 반환 (예: `x_m: 9000`, `y_m: 8000`)

---

### 4. 워게임 임무계획 실행 도구 (`wargame_mission_tool.py`)

워게임 엔진에 임무계획 및 공중지원 명령을 직접 적용합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `apply_wargame_mission_plan(plan_json, dry_run)` | plan_json: 임무계획 JSON, dry_run: 검증만(기본 True) | BLUFOR 임무계획(이동 경로·목표·공중지원)을 워게임에 적용 |
| `apply_wargame_air_support(support_json, dry_run)` | support_json: 공중지원 계획 JSON, dry_run: 검증만(기본 True) | CAS·타격·포병·헬기 지원 임무를 워게임 엔진에 적용 |
| `get_wargame_engine_status()` | — | 워게임 엔진 상태(실행 중 여부, 시간 배율, 현재 틱 등) 반환 |

#### 임무계획 2단계 승인 게이트

```
1단계: apply_wargame_mission_plan(plan_json)          # dry_run=True (기본값)
       → 검증 결과 + plan_id 반환 → 사용자에게 안내

2단계: approve_mission_plan_tool(plan_id="plan_xxx")   # 사용자 승인
       → apply_wargame_mission_plan(plan_json, dry_run=False)  # 실제 적용
```

#### 공중지원·포격 목표 좌표 강제 교정

`apply_wargame_mission_plan` / `apply_wargame_air_support` 적용 시, `air_support_plans`의 `target` 좌표를 가장 가까운 **탐지된(detected) OPFOR 부대의 정확 좌표로 자동 교정**합니다.  
탐지 OPFOR로부터 4 km 이상 벗어난 목표는 오류로 차단됩니다.

| 공중지원 유형 | 반경 | 지연 | 특징 |
|-------------|------|------|------|
| `cas` | 1,500 m | 60 s | 근접항공지원 — 지속 제압 |
| `strike` | 400 m | 120 s | 정밀타격 — 순간 고위력 |
| `artillery` | 2,500 m | 30 s | 포병 광역 지속 |
| `helicopter` | 1,000 m | 60 s | 공격헬기 |

---

### 5. 워게임 전술 분석 도구

#### 5-1. 정찰 임무 (`wargame_recon_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `assess_recon_need()` | — | OPFOR 탐지 현황 평가 — 정찰 필요 여부 및 미탐지 목표 목록 반환 |
| `recommend_recon_routes()` | — | 교전 회피 정찰 경로 자동 생성 (측방 우회 + 관측 포인트 + 복귀점), `apply_json` 포함 반환 |

**정찰 경로 설계 원칙:**
- 직선 접근 금지 → 60° 측방 우회 경유지 삽입
- standoff 5 km 유지 (교전 범위 4 km 바깥)
- 목표 주변 관측 포인트 (고도·엄폐율 기준 최적화)
- 관측 완료 후 안전 복귀점

#### 5-2. 적군 예상 기동 경로 예측 (`wargame_opfor_routes_tool.py`) ★ 신규

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `predict_opfor_routes()` | — | 탐지된 OPFOR 부대가 BLUFOR를 공격하기 위해 기동할 예상 경로 3가지(정면/우측우회/좌측우회)를 지형 기반으로 생성 |

**반환 정보:**
- 각 경로별 `waypoints`, `threat_level` (`high` / `medium` / `low`), `key_chokepoints`
- `interdict_priority`: 여러 경로가 교차하는 차단 우선 지점 상위 6개

**활용법:** 반환된 `predicted_routes`를 `json.dumps()`로 직렬화하여 `get_optimal_attack_positions(opfor_routes_json=...)`에 전달하면 경로 차단 보너스가 적용됩니다.

#### 5-3. 최적 공격 위치·수단 추천 (`wargame_attack_advisor_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_optimal_attack_positions(top_n, opfor_routes_json)` | top_n: 목표별 추천 위치 수(기본 3), opfor_routes_json: `predict_opfor_routes()` 결과 JSON(선택) | 탐지된 OPFOR 위치·고도·엄폐를 분석하여 최적 공격 위치 및 수단 추천 |

**위치 후보 생성:** 각 OPFOR 목표 기준 16방향 × 4거리(1.2/2.0/3.0/4.5 km) = 64개 후보

**점수 가중치:**

| 요소 | 가중치 | 설명 |
|------|--------|------|
| 고도 우위 | 30% | 공격자가 더 높을수록 유리 |
| 공격자 엄폐 | 25% | 공격 위치의 지형 엄폐율 |
| 적 노출도 | 20% | 적의 엄폐가 낮을수록 고점수 |
| 교전 효율 | 15% | 거리별 교전 효율 (1.2 km 최적) |
| 시선 품질 | 10% | 지형 차폐 없이 적을 관측 가능한 정도 |
| 경로 차단 보너스 | 최대 +25점 | `opfor_routes_json` 제공 시, LOS + 사거리 내 예상 경로 경유지를 차단할 수 있는 위치에 가산 |

#### 5-4. 전술 권고 (`wargame_strategy_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_wargame_tactical_recommendation()` | — | 병종 상성 분석 + 지형 기반 최적 기동 경로 추천 |

#### 5-5. COA(행동 방책) 분석 (`coa_analysis_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `analyze_coa_wargame(coa_list, objective)` | coa_list: COA 목록(dict 배열), objective: 작전 목표(선택) | 복수의 행동 방책을 현재 워게임 상태에 대입하여 점수·위험도·권장 순위 반환 |

**COA 점수 산정 (0~100):**

| 항목 | 점수 변화 | 조건 |
|------|-----------|------|
| 기본 | 50 | — |
| 스키마 검증 실패 | −30 | 임무계획 오류 |
| 경고 1건당 | −5 | 검증 경고 |
| 참여 부대 비율 | +0~+10 | 가용 BLUFOR 대비 참여율 |
| 정찰 임무 포함 | +5 | `"recon"` in mission_types |
| 공격 임무 포함 | +5 | `"attack"` or `"flank"` |
| 측방 기동 포함 | +8 | `"flank"` in mission_types |
| 미탐지 OPFOR (정찰 없음) | −15 | approximate/lost OPFOR 존재 |
| 공격 부대 우세 (≥1.5:1) | +10 | 공격 부대 수 / OPFOR 수 |
| 공격 부대 열세 (<0.5:1) | −10 | 위 반대 |

**위험도 분류:** `low` (≥70) / `medium` (≥45) / `high` (<45)

#### 5-6. 임무계획 검증·승인 도구 (`mission_plan_validator_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `validate_mission_plan_tool(plan_json)` | plan_json: JSON 문자열 | 임무계획 유효성 검증 — 오류/경고 반환, 실제 적용 없음 |
| `approve_mission_plan_tool(plan_id)` | plan_id: 승인할 계획 ID | plan_id를 승인하여 `dry_run=False` 실행 허가 |
| `get_pending_plan_tool()` | — | 현재 승인 대기 중인 임무계획 및 세션 상태 조회 |

---

### 6. EXAONE Deep 어드바이저 도구 (`strategy_advisor_tool.py`)

EXAONE Deep 모델을 호출하여 전략·전술 권고를 생성합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `strategy_advisor_tool(query, additional_context)` | query: 전략/전술 질문, additional_context: 보완 정보(선택) | EXAONE4의 상황 분석 + 사용자 쿼리를 EXAONE Deep에 전달하여 전략·전술 권고 생성 |
| `recon_advisor_tool(recon_routes_json, recon_summary)` | recon_routes_json: `recommend_recon_routes()`의 apply_json, recon_summary: 경로 요약(선택) | 제안된 정찰 경로를 EXAONE Deep에 전술 검토 요청 → 수정 의견 + 최종 확정 JSON 반환 |

---

### 7. ARMA3 연동 도구

실제 ARMA3 게임과 relay.py를 통해 실시간 전장 데이터를 주고받습니다.

#### 7-1. ARMA3 전장 조회 (`arma3_query_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_arma3_situation()` | — | ARMA3 현재 전장 상황 요약 (미션 경과 시간, 진영별 병력 현황) 반환 |
| `get_arma3_enemy_units(category)` | category: 유닛 카테고리 필터(선택) | ARMA3 수신 적군(OPFOR) 유닛 목록 반환 |
| `get_arma3_friendly_units(category)` | category: 유닛 카테고리 필터(선택) | ARMA3 수신 아군(BLUFOR) 유닛 목록 반환 |
| `get_arma3_units_by_category(category)` | category: 유닛 카테고리 | 특정 카테고리(전차, 헬기 등)의 전체 유닛(아군+적군) 반환 |

#### 7-2. ARMA3 임무 명령 (`arma3_order_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `send_mission_orders_to_arma3(mission_orders_json)` | mission_orders_json: 임무 명령 JSON | 전술·전략 임무 경로를 ARMA3로 전송 — relay.py가 SQF 파일로 저장하여 게임 내 자동 실행 |
| `get_arma3_order_status()` | — | 최근 ARMA3 임무 명령 목록과 전달 상태 반환 |

---

### 공격 임무계획 수립 흐름

```
1. get_wargame_situation()          → 전장 상황 파악
2. assess_recon_need()              → OPFOR 탐지 현황 확인
3. predict_opfor_routes()           → 탐지된 OPFOR 예상 기동 경로 분석 (★ 신규)
4. get_optimal_attack_positions(    → 경로 차단 보너스 반영 최적 공격 위치 추천
     opfor_routes_json=json.dumps(routes["predicted_routes"])
   )
5. strategy_advisor_tool(...)       → EXAONE Deep 전술 검토
6. 최종 JSON 생성 → apply_wargame_mission_plan(dry_run=False)
```

### 정찰 임무계획 수립 흐름

```
1. assess_recon_need()              → 정찰 필요 여부 평가
2. recommend_recon_routes()         → 교전 회피 정찰 경로 생성
3. recon_advisor_tool(...)          → EXAONE Deep 경로 전술 검토 (선택)
4. apply_wargame_mission_plan(dry_run=False)
```

---

### 도구 그룹 요약

| 그룹 | 파일 | 도구 수 | 주요 용도 |
|------|------|---------|----------|
| 영상 DB 조회 | `videodb_query_tool.py` | 7 | SAM3 분석 영상 세그먼트 검색 |
| PDF RAG | `pdf_rag_tool.py` | 2 | 군사 교범 문서 검색 |
| 워게임 조회 | `wargame_query_tool.py` | 4 | 시뮬레이터 실시간 전장 상황 |
| 워게임 실행 | `wargame_mission_tool.py` | 3 | 임무계획·공중지원 적용 |
| 정찰 임무 | `wargame_recon_tool.py` | 2 | 정찰 필요 평가 + 경로 생성 |
| 적군 경로 예측 | `wargame_opfor_routes_tool.py` | 1 | OPFOR 예상 기동 경로 분석 ★ |
| 최적 공격 위치 | `wargame_attack_advisor_tool.py` | 1 | 경로 차단 보너스 반영 공격 위치 추천 |
| 전술 권고 | `wargame_strategy_tool.py` | 1 | 병종 상성 + 기동 경로 추천 |
| COA 분석 | `coa_analysis_tool.py` | 1 | 행동 방책 비교 평가 |
| 임무계획 검증·승인 | `mission_plan_validator_tool.py` | 3 | 검증·승인·pending 조회 |
| EXAONE Deep 어드바이저 | `strategy_advisor_tool.py` | 2 | 전략·전술 권고 / 정찰 경로 전술 검토 |
| ARMA3 조회 | `arma3_query_tool.py` | 4 | 실제 ARMA3 전장 데이터 조회 |
| ARMA3 명령 | `arma3_order_tool.py` | 2 | ARMA3로 임무 명령 전송 |

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
│   ├── coa_analysis_tool.py       # COA 분석 (1개 도구)
│   ├── mission_plan_validator.py  # 임무계획 검증 엔진 + 2단계 게이트
│   ├── mission_plan_validator_tool.py  # 검증·승인·pending 조회 (3개 도구)
│   └── strategy_advisor_tool.py   # EXAONE Deep 어드바이저 (2개 도구: strategy + recon)
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
├── tests/
│   └── tool_trace_eval.py         # 27-케이스 도구 추적 평가 스위트
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
