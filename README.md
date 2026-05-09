# C2 군사 전략 AI — 완전 실행 가이드

ARMA3 기계화 보병 대대 vs 대대 시나리오와 연동되는 C2(지휘통제) AI 시스템입니다.  
EXAONE4 에이전트가 실시간 전장 데이터를 분석하고 중대 단위 임무 경로를 생성하면,  
ARMA3가 그 명령을 자동으로 수신하여 실행합니다.

---

## 전체 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Google Colab (서버)                             │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                   FastAPI 서버 (포트 8765)                        │   │
│  │   POST /arma3/report      ← 전장 데이터 수신 (ARMA3 → Colab)     │   │
│  │   GET  /arma3/orders/pending → 임무 명령 조회 (relay.py 폴링)    │   │
│  │   POST /arma3/orders/ack  ← 수신 확인                           │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                  │                          ↑                           │
│          save_report()              send_mission_orders_to_arma3()      │
│                  ↓                          │                           │
│        data/arma3_state.json       data/arma3_orders.json               │
│                  ↑                          ↓                           │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │              EXAONE4 에이전트 (BattlefieldAgent)                  │  │
│  │                                                                   │  │
│  │  get_arma3_situation()     → 전장 상황 분석                       │  │
│  │  get_arma3_enemy_units()   → 적 유닛 조회                        │  │
│  │  strategy_advisor_tool()   → EXAONE Deep 전술 생성               │  │
│  │  send_mission_orders_to_arma3(json) → 임무 명령 발령             │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                    │  ngrok 터널 (HTTPS)  │
                    ↕                      ↕
┌─────────────────────────────────────────────────────────────────────────┐
│                       로컬 PC (Windows)                                  │
│                                                                         │
│  relay.py (백그라운드 실행)                                              │
│    ① arma3_xxx.rpt 감시 → [C2AI_DATA] 추출 → POST /arma3/report        │
│    ② GET /arma3/orders/pending 폴링 → SQF 변환 → 미션폴더 저장         │
│                                                                         │
│  ARMA3 (스팀)                                                           │
│    c2_ai_reporter.sqf    → 10초마다 전장데이터 diag_log 기록            │
│    c2_order_executor.sqf → 3초마다 c2ai_order_N.sqf 감지 → execVM     │
└─────────────────────────────────────────────────────────────────────────┘
```

### 데이터 흐름 요약

| 방향 | 경로 | 내용 |
|------|------|------|
| ARMA3 → Colab | rpt 로그 → relay.py → POST /arma3/report | 유닛 위치, 전력 현황 |
| Colab → ARMA3 | GET /arma3/orders/pending → relay.py → .sqf 파일 | 중대 임무 경로 |

---

## 빠른 시작 (TL;DR)

> 상세 설명이 필요하면 아래 단계별 가이드를 참고하세요.

**Colab (셀 순서대로 실행):**
```python
# 셀 1 — 서버 기동
import sys, uvicorn, threading
sys.path.insert(0, '/content/drive/MyDrive/C2_program_ai')
from api.arma3_receiver import app, set_auth_token
set_auth_token("my_token")
threading.Thread(target=uvicorn.run, kwargs={"app":app,"host":"0.0.0.0","port":8765}, daemon=True).start()

# 셀 2 — ngrok 터널
from pyngrok import ngrok
tunnel = ngrok.connect(8765)
print("ngrok URL:", tunnel.public_url)  # ← 이 URL을 복사

# 셀 3 — AI 시스템 기동
!cd /content/drive/MyDrive/C2_program_ai && python main.py ui
```

**로컬 PC (cmd 한 줄):**
```cmd
python arma3_integration\launch.py ^
  --scenario bn_vs_bn ^
  --url https://xxxx.ngrok-free.app ^
  --token my_token
```

이 한 줄로 ARMA3 자동 실행 + relay 자동 시작이 완료됩니다.

---

## 1단계 — Colab 환경 준비

### 1-1. 드라이브 마운트 및 패키지 설치

```python
# Colab 셀 #1
from google.colab import drive
drive.mount('/content/drive')

# 저장소 클론 (최초 1회)
import os
REPO_DIR = "/content/drive/MyDrive/C2_program_ai"
if not os.path.exists(REPO_DIR):
    !git clone https://github.com/Parkdev22222/C2_program_ai.git {REPO_DIR}

%cd {REPO_DIR}
!git checkout military-strategy-ai-sim_connect
!git pull
```

```python
# Colab 셀 #2 — 패키지 설치
!pip install -r requirements.txt -q

# FastAPI 서버 의존성
!pip install fastapi uvicorn pyngrok python-multipart -q
```

### 1-2. SAM3 가중치 확인

```python
# Colab 셀 #3
SAM3_WEIGHTS = "/content/drive/MyDrive/multi-source-intelligent-system-claude-realtime-report-ui-update/sam3_weights"
import os
print("SAM3 가중치 존재:", os.path.exists(SAM3_WEIGHTS))
```

가중치가 없으면 HuggingFace에서 자동 다운로드됩니다 (`facebook/sam3`).

---

## 2단계 — Colab FastAPI 서버 기동

### 2-1. 인증 토큰 설정 및 서버 시작

```python
# Colab 셀 #4 — 서버 기동 (반드시 main.py 실행 전에 실행)
import sys
sys.path.insert(0, '/content/drive/MyDrive/C2_program_ai')

import uvicorn, threading
from api.arma3_receiver import app, set_auth_token

# ★ 토큰을 원하는 문자열로 변경하세요 (relay.py --token 과 동일해야 함)
MY_TOKEN = "my_secret_token_2024"
set_auth_token(MY_TOKEN)

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")

server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()
print("FastAPI 서버 기동 완료 (포트 8765)")
```

### 2-2. ngrok 터널 오픈

```python
# Colab 셀 #5 — ngrok 터널
!pip install pyngrok -q
from pyngrok import ngrok

# ngrok 인증 (https://dashboard.ngrok.com 에서 토큰 발급)
# 최초 1회만 필요
# ngrok.set_auth_token("YOUR_NGROK_AUTH_TOKEN")

tunnel = ngrok.connect(8765)
NGROK_URL = tunnel.public_url
print(f"\n★ ngrok URL: {NGROK_URL}")
print(f"★ relay.py 실행 명령:")
print(f'  python relay.py --url {NGROK_URL} --token {MY_TOKEN} --mission-dir "C:\\...\\미션폴더경로"')
```

> **중요**: 셀을 다시 실행할 때마다 ngrok URL이 바뀝니다.  
> 무료 플랜은 세션당 1개의 터널만 허용됩니다.

### 2-3. 서버 동작 확인

```python
# Colab 셀 #6 — 헬스 체크
import requests
resp = requests.get(f"{NGROK_URL}/health")
print(resp.json())  # {"status": "healthy"}

# 전장 상태 확인 (ARMA3 데이터 수신 전에는 빈 데이터)
resp = requests.get(
    f"{NGROK_URL}/arma3/status",
    headers={"Authorization": f"Bearer {MY_TOKEN}"}
)
print(resp.json())
```

---

## 3단계 — C2 AI 메인 시스템 기동

```python
# Colab 셀 #7 — 메인 시스템 실행 (ML 모델 로딩, 10~20분 소요)
%cd /content/drive/MyDrive/C2_program_ai
!python main.py ui
```

또는 Gradio 앱을 직접 실행:

```python
# Colab 셀 #7 (대안) — Gradio 직접 실행
import subprocess
proc = subprocess.Popen(
    ["python", "main.py", "ui"],
    cwd="/content/drive/MyDrive/C2_program_ai"
)
# Gradio public URL이 출력됩니다
```

> Gradio URL(`https://xxxx.gradio.live`)은 별도 터널로  
> FastAPI ngrok URL과 다릅니다. 두 URL 모두 메모해 두세요.

---

## 4단계 — 로컬 PC 환경 준비 (Windows)

### 4-1. Python 설치 확인

```cmd
python --version   # 3.9 이상 권장
pip install requests
```

### 4-2. relay.py 다운로드

`arma3_integration/relay.py` 파일을 로컬 PC 어디에나 저장합니다.  
(예: `C:\C2AI\relay.py`)

---

## 5단계 — ARMA3 미션 설정

### 5-1. 예제 미션 배포 (권장 — 한 번에 복사)

저장소에 완성된 예제 미션이 포함되어 있습니다.  
아래 폴더를 통째로 ARMA3 `mpmissions` 폴더에 복사합니다.

```
[복사 원본]
  arma3_integration/mission_template/C2AI_BN_VS_BN.Altis/

[복사 대상]
  C:\Users\유저명\Documents\Arma 3\mpmissions\C2AI_BN_VS_BN.Altis\
```

복사 후 폴더 구조:
```
mpmissions\
└── C2AI_BN_VS_BN.Altis\
    ├── mission.sqm           ← Eden 에디터 미션 파일 (플레이어 1명)
    ├── description.ext       ← 미션 설명 및 설정
    ├── init.sqf              ← 유닛 스폰 + C2AI 초기화
    ├── c2_ai_reporter.sqf    ← 전장 데이터 수집
    └── c2_order_executor.sqf ← 임무 명령 자동 실행
```

**예제 미션 편제:**

| 진영 | 중대 | groupId | APC | 보병 |
|------|------|---------|-----|------|
| BLUFOR | Alpha 중대 | `Alpha` | Pandur II × 8 | 80명 |
| BLUFOR | Bravo 중대 | `Bravo` | Pandur II × 8 | 80명 |
| OPFOR  | Red1 중대  | `Red1`  | Marid × 8    | 80명 |
| OPFOR  | Red2 중대  | `Red2`  | Marid × 8    | 80명 |

> **총전력:** 양측 합계 AI 384명 + 차량 32대 / 양측 초기 거리 약 15km

> 자체 init.sqf에 이미 `c2_ai_reporter.sqf`와 `c2_order_executor.sqf` 호출이 포함되어 있어  
> 추가 설정 없이 바로 사용 가능합니다.

---

### 5-2. 커스텀 미션에 C2AI 연동하기 (선택)

기존 미션이 있는 경우, 아래 파일들을 미션 폴더에 복사하고 `init.sqf`를 수정합니다.

```
arma3_integration/c2_ai_reporter.sqf     → 미션폴더/c2_ai_reporter.sqf
arma3_integration/c2_order_executor.sqf  → 미션폴더/c2_order_executor.sqf
```

```sqf
// init.sqf 맨 아래에 추가
execVM "c2_ai_reporter.sqf";
execVM "c2_order_executor.sqf";
```

### 5-3. 부대 그룹 ID 설정 (핵심)

에이전트가 발행한 임무 명령의 `company_id`와 ARMA3 그룹의 `groupId`가  
**정확히 일치해야** 웨이포인트가 적용됩니다.

**에디터에서 그룹 ID 설정 방법:**

```sqf
// 미션 에디터 Init 박스 또는 init.sqf에서 각 지휘관 유닛에 설정
// BLUFOR
[group AlphaLeader,  "Alpha"]  call BIS_fnc_setGroupID;
[group BravoLeader,  "Bravo"]  call BIS_fnc_setGroupID;
[group CharlieLeader,"Charlie"] call BIS_fnc_setGroupID;
[group DeltaLeader,  "Delta"]  call BIS_fnc_setGroupID;

// OPFOR
[group Red1Leader, "Red1"] call BIS_fnc_setGroupID;
[group Red2Leader, "Red2"] call BIS_fnc_setGroupID;
[group Red3Leader, "Red3"] call BIS_fnc_setGroupID;
[group Red4Leader, "Red4"] call BIS_fnc_setGroupID;
```

그룹 ID 확인 방법 (게임 내 디버그 콘솔):

```sqf
hint str (groupId (group player));
```

### 5-4. 권장 그룹 ID 명명 규칙 (기계화 보병 대대 vs 대대)

| 진영 | 그룹 ID | 역할 |
|------|---------|------|
| BLUFOR | `Alpha` | 1중대 (공격) |
| BLUFOR | `Bravo` | 2중대 (측방 기동) |
| BLUFOR | `Charlie` | 3중대 (예비) |
| BLUFOR | `Delta` | 중화기 중대 (화력지원) |
| BLUFOR | `HQ` | 대대 본부 |
| OPFOR | `Red1` | 1중대 |
| OPFOR | `Red2` | 2중대 |
| OPFOR | `Red3` | 3중대 |
| OPFOR | `Red4` | 예비 중대 |
| OPFOR | `RedHQ` | 대대 본부 |

---

## 6단계 — launch.py 실행 (ARMA3 자동 실행 + relay 통합)

**`launch.py` 한 줄로 ARMA3 자동 실행과 relay를 동시에 시작합니다.**

### 6-1. 기본 실행 (자동 탐색)

```cmd
cd C:\C2AI
python arma3_integration\launch.py ^
  --scenario bn_vs_bn ^
  --url https://xxxx-xx-xx-xx.ngrok-free.app ^
  --token my_secret_token_2024
```

`launch.py`가 자동으로 처리하는 항목:
1. `scenarios.yaml`에서 `bn_vs_bn` 시나리오 설정 로드
2. ARMA3 실행 파일 (`arma3_x64.exe`) 레지스트리/일반 경로 자동 탐색
3. 미션 폴더 (`Documents\Arma 3\mpmissions\C2AI_BN_VS_BN.Altis`) 자동 탐색
4. ARMA3 프로세스 실행 (`-host -world=Altis -mission=... -skipIntro`)
5. 새 `.rpt` 파일 생성 대기 (최대 120초)
6. `relay.py` 자동 시작 (전장 데이터 업로드 + 임무 명령 다운로드)

### 6-2. 시나리오 목록 확인

```cmd
python arma3_integration\launch.py --list-scenarios
```

출력 예시:
```
등록된 시나리오 목록:
------------------------------------------------------------
  bn_vs_bn            기계화 보병 대대 vs 대대
                      BLUFOR vs OPFOR 기계화 보병 대대급 전투 시나리오
                      맵: Altis

  company_attack      중대급 공격 작전
                      단일 중대 공격 훈련 시나리오
                      맵: Stratis

  custom              커스텀 시나리오
                      사용자 정의 미션
                      맵: Altis
```

### 6-3. 자동 탐색 실패 시 경로 직접 지정

```cmd
python arma3_integration\launch.py ^
  --scenario bn_vs_bn ^
  --url https://xxxx.ngrok-free.app ^
  --token my_token ^
  --exe "D:\SteamLibrary\steamapps\common\Arma 3\arma3_x64.exe" ^
  --mission-dir "D:\...\mpmissions\C2AI_BN_VS_BN.Altis"
```

### 6-4. 커스텀 미션 실행

```cmd
python arma3_integration\launch.py ^
  --scenario custom ^
  --mission-name MyCustomMission ^
  --url https://xxxx.ngrok-free.app ^
  --token my_token ^
  --mission-dir "C:\...\mpmissions\MyCustomMission.Altis"
```

### 6-5. ARMA3가 이미 실행 중인 경우 (relay만 시작)

```cmd
python arma3_integration\launch.py ^
  --scenario bn_vs_bn ^
  --url https://xxxx.ngrok-free.app ^
  --token my_token ^
  --no-launch
```

### 6-6. launch.py 전체 파라미터

| 파라미터 | 설명 | 기본값 |
|----------|------|--------|
| `--scenario` | 시나리오 이름 (`--list-scenarios`로 확인) | 필수 |
| `--url` | Colab ngrok URL | 필수 |
| `--token` | 인증 토큰 | 필수 |
| `--exe` | ARMA3 exe 경로 직접 지정 | 자동 탐색 |
| `--mission-dir` | 미션 폴더 절대 경로 직접 지정 | 자동 탐색 |
| `--mission-name` | 미션 이름 직접 지정 (custom 시나리오) | 시나리오 설정 |
| `--rpt` | .rpt 파일 경로 직접 지정 | 자동 감지 |
| `--rpt-wait` | .rpt 파일 대기 최대 시간(초) | 120 |
| `--poll` | RPT 폴링 간격(초) | 0.5 |
| `--order-poll` | 임무 명령 폴링 간격(초) | 5.0 |
| `--no-launch` | ARMA3 실행 없이 relay만 시작 | false |
| `--list-scenarios` | 시나리오 목록 출력 후 종료 | - |

### 6-7. scenarios.yaml — 새 시나리오 등록

`arma3_integration/scenarios.yaml`을 열어 새 항목 추가:

```yaml
scenarios:
  my_scenario:
    display_name: "내 커스텀 시나리오"
    description: "설명"
    mission_name: "MyMission"    # mpmissions\MyMission.Altis 폴더명
    world: "Altis"
    multiplayer: true
    mission_dir: ""              # 비우면 자동 탐색
    agent_context: >
      그룹 ID 정보: Alpha, Bravo, Charlie
```

등록 후 `--scenario my_scenario`로 바로 사용 가능합니다.

### 6-8. 정상 실행 출력 예시

```
12:00:00 [INFO] ============================================================
12:00:00 [INFO] C2AI 통합 런처 시작
12:00:00 [INFO] 시나리오: 기계화 보병 대대 vs 대대
12:00:00 [INFO] Colab URL: https://xxxx.ngrok-free.app
12:00:00 [INFO] 미션 폴더: C:\Users\...\mpmissions\C2AI_BN_VS_BN.Altis
12:00:00 [INFO] ============================================================
12:00:00 [INFO] 레지스트리에서 ARMA3 발견: C:\...\arma3_x64.exe
12:00:00 [INFO] 미션 폴더 발견: C:\...\mpmissions\C2AI_BN_VS_BN.Altis
12:00:00 [INFO] ARMA3 실행됨 (PID: 12345)
12:00:00 [INFO] ARMA3 프로세스 실행 완료. .rpt 파일 생성 대기 중...
12:00:08 [INFO] .rpt 파일 감지됨: C:\...\arma3_20240101_120000.rpt
12:00:08 [INFO] relay 시작: rpt=C:\...\arma3_20240101_120000.rpt
12:00:08 [INFO]   전장 데이터 업로드: 활성화
12:00:08 [INFO]   임무 명령 수신: 활성화
12:00:08 [INFO] 임무 명령 폴링 시작 → C:\...\mpmissions\C2AI_BN_VS_BN.Altis
12:00:08 [INFO] 기존 로그 건너뜀 — 새 데이터부터 감시 시작
```

---

## 7단계 — ARMA3 미션 확인

launch.py가 ARMA3를 자동으로 실행했으면, ARMA3 로딩 완료 후:

1. **미션이 자동으로 시작됩니다** (`-host -mission=...` 파라미터로 자동 로드)
2. **디버그 콘솔에서 초기화 확인:**

```sqf
// 디버그 콘솔 (~ 키 → 오른쪽 패널)에서 실행
// Reporter 상태 확인
hint str C2AI_REPORT_INTERVAL;   // 10이 출력되면 정상

// Executor 상태 확인
hint str C2AI_ORDER_EXEC_RUNNING; // true가 출력되면 정상
hint str C2AI_LAST_ORDER_SEQ;    // 0이 출력되면 정상 (명령 수신 전)
```

5. **relay.py 콘솔에서 데이터 수신 확인:**

```
2024-01-01 12:05:10 [INFO] 전송 성공: mission_time=10  units=87
2024-01-01 12:05:20 [INFO] 전송 성공: mission_time=20  units=87
```

---

## 8단계 — AI 에이전트 사용 및 임무 명령 발령

### 8-1. Gradio UI 접속

Colab에서 출력된 Gradio URL로 브라우저 접속합니다.

### 8-2. 전장 상황 조회 쿼리 예시

```
현재 ARMA3 전장의 적군 전력 현황을 분석해줘.
```

```
OPFOR 전차 위치와 보병 배치를 기반으로 현재 방어 취약 지점을 분석해줘.
```

### 8-3. 임무 명령 발령 쿼리 예시

```
현재 전장 상황을 바탕으로 기계화 보병 대대 공격 작전을 수립하고
Alpha, Bravo, Charlie 중대의 임무 경로를 JSON으로 생성한 뒤
ARMA3로 전송해줘.
```

에이전트 동작 순서:
1. `get_arma3_situation()` — 전장 상황 조회
2. `get_arma3_enemy_units("armor")` — 적 전차 위치 파악
3. `strategy_advisor_tool()` — EXAONE Deep 전술 생성
4. `send_mission_orders_to_arma3(json)` — 임무 명령 ARMA3 전송

### 8-4. 임무 명령 수신 확인 (ARMA3 내부)

relay.py 콘솔:
```
2024-01-01 12:10:05 [INFO] 명령 SQF 저장: c2ai_order_1.sqf  companies=3
```

ARMA3 .rpt 로그:
```
[C2AI Executor] 명령 파일 감지: c2ai_order_1.sqf  (seq=1)
[C2AI] Alpha 임무 적용 완료: 북방 우회 기동
[C2AI] Bravo 임무 적용 완료: 정면 견제
[C2AI] Charlie 임무 적용 완료: 예비대 집결
[C2AI] 임무 명령 #1 적용 완료
```

---

## 에이전트 도구 목록

총 **26개** 도구가 등록되어 있으며 역할별로 7개 그룹으로 분류됩니다.

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

상성·지형·고도 기반으로 전술 권고와 최적 공격 위치를 분석합니다.

#### 5-1. 전술 권고 (`wargame_strategy_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_wargame_tactical_recommendation()` | — | 현재 전장 상황의 병종 상성 분석 + 지형 기반 최적 기동 경로 추천 |

#### 5-2. 최적 공격 위치·수단 추천 (`wargame_attack_advisor_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_optimal_attack_positions(top_n)` | top_n: 적 목표별 추천 위치 수(기본 3) | 탐지된 OPFOR 위치·고도·엄폐를 분석하여 아군 피해 최소·적 피해 최대 공격 위치 및 수단 추천 |

**위치 후보 생성 방식:** 각 OPFOR 목표 기준 16방향 × 4거리(1.2/2.0/3.0/4.5 km) = 64개 후보

**점수 가중치:**

| 요소 | 가중치 | 설명 |
|------|--------|------|
| 고도 우위 (elevation) | 30% | 공격자가 더 높을수록 유리 |
| 공격자 엄폐 (atk_cover) | 25% | 공격 위치의 지형 엄폐율 |
| 적 노출도 (target_exposure) | 20% | 적의 엄폐가 낮을수록 고점수 |
| 교전 효율 (engagement) | 15% | 거리별 교전 효율(1.2 km 최적) |
| 시선 품질 (LOS) | 10% | 지형 차폐 없이 적을 관측 가능한 정도 |

**추천 공격 수단:** 직접 지상화력 / 측방 포위기동 / 포병 간접사격 / 공중지원(CAS) / 정찰 후 확인사격

---

### 6. 전략 어드바이저 도구 (`strategy_advisor_tool.py`)

EXAONE Deep 모델을 호출하여 전략·전술 권고를 생성합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `strategy_advisor_tool(query)` | query: 전략/전술 질문 | EXAONE4의 상황 분석 + 사용자 쿼리를 EXAONE Deep에 전달하여 전술 권고 생성 |

> 에이전트가 전략/전술 쿼리를 감지하면 자동으로 이 도구를 사용합니다.  
> `is_strategy_query()` 함수가 `agent_config.yaml`의 `strategy_keywords`를 기준으로 판별합니다.

---

### 7. ARMA3 연동 도구

실제 ARMA3 게임과 직접 연동하여 전장 데이터를 수신하고 임무 명령을 발령합니다.

#### 7-1. 전장 조회 (`arma3_query_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_arma3_situation()` | — | 전체 전장 요약 (미션 시간, 진영별 병력·차량 수) |
| `get_arma3_enemy_units(category)` | category: `"armor"` / `"infantry"` / `"helicopter"` 등 | OPFOR 유닛 목록 |
| `get_arma3_friendly_units(category)` | category: 유닛 카테고리 | BLUFOR 유닛 목록 |
| `get_arma3_units_by_category(category)` | category: 유닛 카테고리 | 전 진영 특정 카테고리 유닛 목록 |
| `get_arma3_groups(side)` | side: `"OPFOR"` / `"BLUFOR"` | 그룹(분대·중대) 목록 |

#### 7-2. 임무 명령 (`arma3_order_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `send_mission_orders_to_arma3(mission_orders_json)` | mission_orders_json: 임무 경로 JSON 문자열 | 중대 임무 경로 JSON → ARMA3 전송 |
| `get_arma3_order_status()` | — | 최근 임무 명령 전달 현황 확인 |

---

### 도구 그룹 요약

| 그룹 | 파일 | 도구 수 | 주요 용도 |
|------|------|---------|----------|
| 영상 DB 조회 | `videodb_query_tool.py` | 7 | SAM3 분석 영상 세그먼트 검색 |
| PDF RAG | `pdf_rag_tool.py` | 2 | 군사 교범 문서 검색 |
| 워게임 조회 | `wargame_query_tool.py` | 4 | 시뮬레이터 실시간 전장 상황 |
| 워게임 실행 | `wargame_mission_tool.py` | 3 | 임무계획·공중지원 적용 |
| 전술 분석 | `wargame_strategy_tool.py` + `wargame_attack_advisor_tool.py` | 2 | 상성·지형 기반 전술 권고 및 최적 공격 위치 |
| 전략 어드바이저 | `strategy_advisor_tool.py` | 1 | EXAONE Deep 전략·전술 권고 |
| ARMA3 연동 | `arma3_query_tool.py` + `arma3_order_tool.py` | 7 | 실제 ARMA3 게임 연동 |

---

## 임무 명령 JSON 스키마 상세

에이전트가 `send_mission_orders_to_arma3`에 전달하는 JSON 형식입니다.

```json
{
  "scenario": "기계화 보병 대대 vs 대대 공격 작전",
  "friendly_side": "BLUFOR",
  "tactical_intent": "우측 측방 포위 기동으로 적 주방어선 붕괴",
  "companies": [
    {
      "company_id": "Alpha",
      "side": "BLUFOR",
      "mission_type": "attack",
      "formation": "wedge",
      "speed": "combat",
      "waypoints": [
        {
          "seq": 1,
          "x": 5420.0,
          "y": 3810.0,
          "action": "move",
          "radius": 50,
          "hold_time_sec": 0,
          "notes": "집결지 이동"
        },
        {
          "seq": 2,
          "x": 6100.0,
          "y": 4300.0,
          "action": "attack",
          "radius": 100,
          "hold_time_sec": 60,
          "notes": "적 주방어선 공격"
        }
      ],
      "notes": "정면 공격으로 적 견제"
    },
    {
      "company_id": "Bravo",
      "side": "BLUFOR",
      "mission_type": "flank",
      "formation": "column",
      "speed": "combat",
      "waypoints": [
        {
          "seq": 1,
          "x": 5200.0,
          "y": 4100.0,
          "action": "move",
          "radius": 50,
          "hold_time_sec": 0,
          "notes": "우측방 기동로 진입"
        },
        {
          "seq": 2,
          "x": 6400.0,
          "y": 3900.0,
          "action": "assault",
          "radius": 80,
          "hold_time_sec": 0,
          "notes": "적 측방 강습"
        }
      ],
      "notes": "우측 포위 기동"
    }
  ]
}
```

**`mission_type` 값:**  
`attack` | `defend` | `flank` | `support` | `withdrawal` | `recon`

**`formation` 값:**  
`wedge` | `line` | `column` | `echelon_left` | `echelon_right` | `vee` | `diamond`

**`speed` 값:**  
`safe` | `aware` | `combat` | `stealth`

**`action` (웨이포인트) 값:**  
`move` | `attack` | `defend` | `hold` | `support_by_fire` | `assault` | `recon` | `withdrawal`

---

## 좌표 확인 방법 (ARMA3 내부)

임무 계획 시 특정 지점의 ARMA3 ASL 좌표가 필요합니다.

```sqf
// 마우스 커서 위치 좌표 확인 (디버그 콘솔 반복 실행)
hint str (screenToWorld (getMousePosition));

// 플레이어 현재 위치 좌표
hint str (getPosASL player);

// 마커 위치 좌표 (에디터에서 마커 배치 후)
hint str (getMarkerPos "marker_1");
```

출력 형식: `[x, y, z]` → x(동쪽), y(북쪽) 값을 임무 명령 JSON에 사용합니다.

---

## 파일 구조

```
C2_program_ai/
├── api/
│   └── arma3_receiver.py          # FastAPI 서버 (ARMA3 데이터 수신 + 임무 명령 발행)
├── arma3_integration/
│   ├── c2_ai_reporter.sqf         # ARMA3 전장 데이터 수집 (디버그 로그 출력)
│   ├── c2_order_executor.sqf      # ARMA3 임무 명령 자동 수신·실행
│   └── relay.py                   # 로컬 PC 양방향 릴레이 (전장데이터↑ / 임무명령↓)
├── core_src/
│   ├── arma3_db_manager.py        # 전장 상태 JSON DB
│   ├── arma3_order_manager.py     # 임무 명령 JSON DB
│   ├── video_analysis_system.py   # SAM3 영상 분석
│   ├── object_detection.py        # SAM3 객체 탐지·추적
│   ├── embedding_generator.py     # MobileCLIP 임베딩
│   ├── event_description.py       # SmolVLM2 이벤트 설명
│   └── model_manager.py           # ML 모델 싱글톤
├── agent/
│   └── battlefield_agent.py       # EXAONE4 메인 에이전트
├── tools/
│   ├── arma3_query_tool.py        # ARMA3 전장 조회 도구 (5개)
│   ├── arma3_order_tool.py        # ARMA3 임무 명령 도구 (2개)
│   ├── videodb_query_tool.py      # 영상 DB 조회
│   ├── pdf_rag_tool.py            # PDF RAG
│   ├── wargame_query_tool.py      # 전술지도 조회
│   └── strategy_advisor_tool.py   # EXAONE Deep 전술 생성
├── data/
│   ├── arma3_state.json           # ARMA3 전장 상태 DB
│   └── arma3_orders.json          # 임무 명령 DB
├── config/
│   ├── models_config.yaml         # ML 모델 설정
│   └── agent_config.yaml          # 에이전트 설정
├── main.py
└── requirements.txt
```

---

## 트러블슈팅

### ARMA3 실행 파일을 찾을 수 없음

```
FileNotFoundError: ARMA3 실행 파일(arma3_x64.exe)을 찾을 수 없습니다.
```

→ `scenarios.yaml`의 `arma3.exe_path`에 절대 경로 입력:

```yaml
arma3:
  exe_path: "D:\\SteamLibrary\\steamapps\\common\\Arma 3\\arma3_x64.exe"
```

또는 `--exe` 옵션으로 직접 지정:

```cmd
python arma3_integration\launch.py --scenario bn_vs_bn --exe "D:\...\arma3_x64.exe" ...
```

### 미션 폴더를 찾을 수 없음

```
FileNotFoundError: 미션 폴더를 찾을 수 없습니다: C2AI_BN_VS_BN.Altis
```

→ `Documents\Arma 3\mpmissions\C2AI_BN_VS_BN.Altis` 폴더가 있는지 확인  
→ `scenarios.yaml`의 `mission_dir`에 절대 경로 입력 또는 `--mission-dir` 옵션 사용

### relay — `.rpt` 파일을 찾을 수 없음

```
FileNotFoundError: ARMA3 .rpt 파일을 찾을 수 없습니다.
```

→ `--rpt` 옵션으로 직접 경로 지정:

```cmd
python arma3_integration\launch.py ... --rpt "C:\Users\유저명\AppData\Local\Arma 3\arma3_20240101_120000.rpt"
```

### 서버 연결 실패

```
[ERROR] 서버 연결 실패: https://xxxx.ngrok-free.app/arma3/report
```

→ Colab에서 2단계(FastAPI 서버 + ngrok)가 실행 중인지 확인  
→ ngrok URL이 바뀌었으면 `--url` 값 업데이트 후 launch.py 재시작

### ARMA3 — 임무 명령이 적용되지 않음

1. 그룹 ID 불일치 확인:
```sqf
{diag_log format ["그룹: %1", groupId _x]} forEach allGroups;
```
2. `.rpt` 파일에서 경고 메시지 확인:
```
[C2AI] 경고: Alpha 그룹을 찾을 수 없습니다
```
3. relay.py 콘솔에서 SQF 파일 생성 로그 확인:
```
[INFO] 명령 SQF 저장: c2ai_order_1.sqf  companies=3
```
4. `c2ai_order_1.sqf` 파일이 미션 폴더에 실제로 있는지 확인

### ARMA3 — c2_order_executor.sqf가 중복 실행됨

```sqf
// 디버그 콘솔에서 현재 상태 확인
hint str C2AI_ORDER_EXEC_RUNNING;
```

`true`가 출력되면 이미 실행 중이므로 `init.sqf`에서 중복 `execVM` 호출 제거

### Colab — 세션 만료 후 재시작

Colab 세션이 끊기면 **2단계부터 다시 실행**해야 합니다.  
ngrok URL이 바뀌므로 relay.py도 새 URL로 재시작이 필요합니다.
