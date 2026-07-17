# C2 군사 전략 AI

EXAONE4 기반 C2(지휘통제) AI 시스템입니다.  
Python 워게임 시뮬레이터와 연동하여 정찰·공격 임무계획 수립, 전략/전술 추천을 수행합니다.

---

## 시스템 아키텍처

![Agent System Architecture](c2_agent_architecture.png)

### 레이어 설명

| 레이어 | 색상 | 구성 요소 |
|--------|------|-----------|
| **UI Layer** | 파랑 | FastAPI 웹 대시보드 (`c2.presentation.web.api`) — AI 채팅, 워게임 시뮬레이터, Leaflet 전장 지도 |
| **Agent / Planner** | 초록 | `LangGraphBattlefieldAgent`(기본) 또는 `BattlefieldAgent`(smolagents CodeAgent, EXAONE4) + `MissionPlanner` + 자동 재계획 워커(`c2.application.simulation.replan`) |
| **Tools** | 주황·청록·보라 | 에이전트가 호출하는 LLM 툴 어댑터(`c2.presentation.tools`) — 12개 도구 모듈, 스텝당 1개 제한 |
| **Core Systems** | 보라·빨강 | WargameEngine, EXAONE4 LLM (vLLM 서빙), rdflib 온톨로지 |
| **Data / External** | 빨강 | 시나리오, SQLite DB, COHA 온톨로지 TTL |

### 모델 아키텍처

| 모델 | 역할 |
|------|------|
| **EXAONE4-32B-AWQ** | 메인 CodeAgent — 상황 판단, 전략/전술 추천, 임무계획 수립, 최종 응답 |

---

## 빠른 시작

```bash
# 패키지 설치
pip install -r requirements.txt

# 1) vLLM 서버 기동 (EXAONE4, 별도 터미널)
python scripts/launch_vllm_servers.py

# 2) AI 시스템 기동 (웹 대시보드 UI — FastAPI + Leaflet)
python main.py ui
```

브라우저에서 출력된 주소(기본 `http://localhost:7860`)에 접속합니다.

> LLM은 애플리케이션 프로세스 내부가 아닌 별도 vLLM 서버(OpenAI 호환 API)에서 동작합니다.
> 서버 주소는 `config/models_config.yaml`의 `agent_model.serving`(기본 `127.0.0.1:8000`)
> 또는 환경변수 `C2_AGENT_VLLM_BASE_URL`로 설정합니다.

### vLLM 서버 수동 실행 (nohup 백그라운드, A100 80GB 기준)

런처 스크립트 대신 서버를 직접 띄우는 방법입니다.
`nohup` + 백그라운드(`&`)로 실행하므로 SSH/터미널을 닫아도 유지되며,
로그는 `out1.log`에 기록됩니다.

```bash
# EXAONE4 (:8000) → out1.log
# 단일 모델 전용 GPU이므로 gpu-memory-utilization을 크게 잡아 KV 캐시를 확보하고,
# --enforce-eager 를 넣지 않아 CUDA 그래프로 디코드를 가속한다.
nohup vllm serve LGAI-EXAONE/EXAONE-4.0-32B-AWQ --host 127.0.0.1 --port 8000 \
  --served-model-name exaone4-agent --trust-remote-code \
  --quantization awq_marlin --dtype float16 \
  --gpu-memory-utilization 0.90 --max-model-len 32768 \
  --enable-prefix-caching --max-num-seqs 64 \
  > out1.log 2>&1 &
```

> `scripts/launch_vllm_servers.py`는 위 값을 `config/models_config.yaml`의
> `agent_model`(gpu_memory_utilization / enforce_eager / enable_prefix_caching /
> max_num_seqs / quantization)에서 읽어 동일하게 구성합니다. VRAM 사용량을 조절하려면
> `gpu_memory_utilization`을(를) 낮추세요(예: 40GB GPU는 0.90 유지 + `max_model_len` 하향).

```bash
# 로딩 진행 확인 (Ctrl+C는 tail만 종료)
tail -f out1.log

# 서버 준비 확인 (200이면 준비 완료)
curl http://127.0.0.1:8000/health
```

주의 사항:

- `2>&1`이 stderr(vLLM 로그 대부분)를 로그 파일로 합쳐주므로 반드시 포함해야 합니다.
- 재실행 시 `>`는 기존 로그를 덮어씁니다. 이어 쓰려면 `>>`로 변경하세요.
- `--served-model-name`(`exaone4-agent`)은
  `models_config.yaml`의 `serving.served_model_name`과 일치해야 합니다.

서버 종료:

```bash
pkill -f "vllm serve"                      # 종료
sleep 5 && nvidia-smi                      # GPU 메모리 반환 확인 후 재시작
```

---

## 에이전트 백엔드 선택 (LangGraph / smolagents)

동일한 툴셋·시스템 지시사항·온톨로지 자동 주입을 공유하는 두 가지 에이전트 백엔드를
환경변수 `C2_AGENT_BACKEND`로 전환할 수 있습니다.

| 값 | 백엔드 | 구현 | 도구 호출 방식 |
|----|--------|------|----------------|
| `langgraph` (기본) | LangGraph StateGraph(ReAct) | `src/c2/presentation/agent/langgraph_agent.py` | function calling (tool call) |
| `smolagents` | smolagents CodeAgent | `src/c2/presentation/agent/battlefield_agent.py` | 코드 생성형 |

```bash
# LangGraph 백엔드로 UI 실행 (기본값이라 생략 가능)
C2_AGENT_BACKEND=langgraph python main.py ui

# 기존 smolagents 백엔드로 복귀
C2_AGENT_BACKEND=smolagents python main.py ui
```

두 백엔드는 다음을 **완전히 동일하게** 공유합니다.

- 툴셋: `build_battlefield_tools()` 단일 소스 (`src/c2/presentation/agent/battlefield_agent.py`)
  → LangGraph 는 `src/c2/presentation/agent/langgraph_tools.py`가 각 smolagents 툴을 LangChain
    `StructuredTool`로 감싸 재사용하므로 워게임 엔진 연동·반환 구조가 같습니다.
- 시스템 지시사항: `config/agent_custom_instructions.txt`
- 매 판단마다 Neo4j 온톨로지 상황 자동 주입 (`ontology_situation_block()`)
- 공개 인터페이스: `run()` / `agent.agent.run()` / `reset_memory()` /
  `get_situation_memory()` / `reload_instructions()`

### LangGraph 사용 시 vLLM 서버 요구사항

LangGraph 백엔드는 **function calling(tool call)** 으로 도구를 호출하므로, vLLM 서버를
tool-calling 활성화 옵션으로 기동해야 합니다. 위 `vllm serve` 명령에 다음 플래그를
추가하세요(파서는 모델에 맞춰 선택 — EXAONE4 계열은 `hermes` 파서를 사용).

```bash
nohup vllm serve LGAI-EXAONE/EXAONE-4.0-32B-AWQ --host 127.0.0.1 --port 8000 \
  --served-model-name exaone4-agent --trust-remote-code \
  --quantization awq_marlin --dtype float16 \
  --gpu-memory-utilization 0.90 --max-model-len 32768 \
  --enable-prefix-caching --max-num-seqs 64 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  > out1.log 2>&1 &
```

> 서버 주소는 `config/models_config.yaml`의 `agent_model.serving.base_url`
> 또는 환경변수 `C2_AGENT_VLLM_BASE_URL`(예: `http://127.0.0.1:8000/v1`)로 지정합니다.
> smolagents 백엔드는 위 tool-calling 플래그 없이도 동작합니다.

### LLM 프로바이더 선택 — 직접 서빙한 EXAONE4 vs. Gemini API

LangGraph 백엔드(기본)는 LLM을 **직접 서빙한 EXAONE4(vLLM)** 대신 **Google Gemini API**로도
쓸 수 있습니다. GPU/서버 기동 없이 API 키만 있으면 됩니다. 프로바이더는 환경변수
`C2_LLM_PROVIDER`(또는 `config/models_config.yaml`의 최상위 `llm_provider`)로 전환합니다.

| `C2_LLM_PROVIDER` | LLM | 필요 조건 |
|-------------------|-----|-----------|
| `vllm` (기본) | 직접 서빙한 EXAONE4 | vLLM 서버 기동(위) |
| `gemini` | Google Gemini API | `GOOGLE_API_KEY` 환경변수 |

**API 키를 어디에 넣나요?** — 코드나 설정 파일이 아니라 **환경변수**에 넣습니다(키 유출 방지).
[Google AI Studio](https://aistudio.google.com/apikey)에서 발급한 키를 다음처럼 주입하세요.

```bash
# 1) Gemini API 키를 환경변수로 주입 (필수)
export GOOGLE_API_KEY="여기에_발급받은_키"     # 또는 GEMINI_API_KEY

# 2) 프로바이더를 gemini 로 전환 후 UI 실행 (vLLM 서버 불필요)
export C2_LLM_PROVIDER=gemini
python main.py ui
```

- 사용 모델은 `config/models_config.yaml`의 `gemini_model.model`에서 지정합니다
  (기본 `gemini-2.5-flash`, 필요 시 `gemini-2.5-pro` 등으로 변경).
- `models_config.yaml`의 `api_key_env`는 **키 값이 아니라 키가 담긴 환경변수 이름**입니다.
  기본값 `GOOGLE_API_KEY`를 쓰면 위 `export`만으로 연동됩니다.
- Gemini는 tool-calling을 기본 지원하므로 LangGraph 그래프에서 EXAONE4와 **동일한 툴셋·
  동일한 동작**으로 실행됩니다. (`pip install langchain-google-genai` 필요 — requirements 포함)
- EXAONE4로 되돌리려면 `unset C2_LLM_PROVIDER`(또는 `C2_LLM_PROVIDER=vllm`).

### 선택 서비스 Docker로 기동 (PostgreSQL / Neo4j)

전술채팅 멀티턴 메모리는 **PostgreSQL**, 온톨로지(지식그래프)는 **Neo4j**를 사용합니다.
둘 다 **선택 사항**이며, 미설정 시 각각 in-memory로 자동 폴백하므로 없어도 앱은 동작합니다.
아래는 Docker로 로컬에 띄우는 방법입니다. (Docker 설치 필요)

**① PostgreSQL — 전술채팅 멀티턴 메모리**

```bash
# 컨테이너 기동 (db=c2 / user=postgres / password=c2password)
docker run -d --name c2-postgres \
  -e POSTGRES_DB=c2 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=c2password \
  -p 5432:5432 \
  postgres:16

# 앱이 접속하도록 환경변수 주입 (테이블 c2_chat_turns는 앱이 자동 생성)
export C2_PG_HOST=127.0.0.1
export C2_PG_PORT=5432
export C2_PG_DB=c2
export C2_PG_USER=postgres
export C2_PG_PASSWORD=c2password
# (또는 한 줄 DSN) export C2_PG_DSN="postgresql://postgres:c2password@127.0.0.1:5432/c2"
```

**② Neo4j — 온톨로지 지식그래프**

```bash
# 컨테이너 기동 (user=neo4j / password=c2password — 8자 이상 필수)
# 7474: 브라우저 UI(http://localhost:7474), 7687: bolt(앱 접속)
docker run -d --name c2-neo4j \
  -e NEO4J_AUTH=neo4j/c2password \
  -p 7474:7474 -p 7687:7687 \
  neo4j:5

# 앱이 접속하도록 환경변수 주입 (OI_NEO4J_URI 미설정 시 in-memory 폴백)
export OI_NEO4J_URI="bolt://127.0.0.1:7687"
export OI_NEO4J_USER=neo4j
export OI_NEO4J_PASSWORD=c2password
```

**기동 확인 / 정리**

```bash
docker ps                                   # 두 컨테이너 실행 확인
curl http://localhost:7474                   # Neo4j 브라우저 응답 확인(200)

# 위 환경변수를 준 뒤 앱 실행 → 채팅 멀티턴/온톨로지 활성화
python main.py ui

# 중지·삭제
docker rm -f c2-postgres c2-neo4j            # 컨테이너 삭제 (데이터도 함께 소멸)
```

> 데이터 영속화가 필요하면 `-v` 볼륨을 추가하세요
> (예: PostgreSQL `-v c2-pgdata:/var/lib/postgresql/data`,
> Neo4j `-v c2-neo4jdata:/data`).

### 전술채팅 멀티턴 대화 메모리 (PostgreSQL / in-memory)

전술채팅은 **이전 2턴**(사용자 쿼리 + 툴 호출 내역 + 툴 실행 결과 + 최종 응답)을 저장소에서
적재해 현재 질문 앞에 붙여 멀티턴 대화를 지원합니다. 저장소는 **PostgreSQL** 또는
**in-memory 폴백** 두 가지이며, 온톨로지 그래프 스토어와 동일한 폴백 패턴을 씁니다.
(공격·정찰·COA 계획 경로는 무상태로 두어 이전 대화가 섞이지 않습니다.)

| 환경변수 | 설명 |
|----------|------|
| `C2_CHAT_STORE` | `postgres` / `inmemory` 강제 선택 (미설정 시 접속정보 있으면 postgres) |
| `C2_PG_DSN` | PostgreSQL 접속 문자열 (예: `postgresql://user:pw@host:5432/c2`) |
| `C2_PG_HOST` / `C2_PG_PORT` / `C2_PG_DB` / `C2_PG_USER` / `C2_PG_PASSWORD` | DSN 대신 분리 지정 |
| `C2_CHAT_SESSION_ID` | 대화 세션 ID (기본 `wargame_chat`) |

```bash
# PostgreSQL 사용 (접속 실패 시 자동으로 in-memory 폴백)
export C2_PG_DSN="postgresql://postgres:pw@127.0.0.1:5432/c2"
python main.py ui
```

- 접속정보가 없으면 자동으로 **in-memory**로 동작합니다(별도 설정 불필요, 프로세스 종료 시 소멸).
- 대화 턴은 `c2_chat_turns` 테이블에 적재되며, 각 턴은 LangChain 메시지(Human/AI/Tool)를
  직렬화해 저장합니다. `pip install psycopg2-binary` 필요(requirements 포함).
- 유지 턴 수는 `src/c2/presentation/agent/langgraph_agent.py`의 `_MEMORY_TURNS`(기본 2)로 조정합니다.

---

## Google Colab에서 실행

EXAONE4-32B(AWQ)를 **별도 vLLM 서버(OpenAI 호환 API)**로 띄우고, 앱은 그 서버에 접속합니다.
따라서 Colab에서는 **① vLLM 서버**(포트 8000)와 **② 웹 UI**(포트 7860) 두 프로세스를
백그라운드로 함께 띄웁니다. 32B 모델이므로 **A100 GPU가 필요**합니다.

| 런타임 | 가능 여부 | 비고 |
|--------|-----------|------|
| **A100 80GB** (Colab Pro+) | ✅ 권장 | 기본 설정 그대로 사용 |
| **A100 40GB** (Colab Pro) | ✅ | `config/models_config.yaml`의 `agent_model.gpu_memory_utilization`/`max_model_len`를 낮춰 조정 |
| T4 / L4 (무료·기본) | ❌ 불가 | 16~24GB로 32B AWQ 로딩 불가 |

> 런타임 설정: **런타임 → 런타임 유형 변경 → 하드웨어 가속기: A100 GPU**

### 1) 저장소 클론 (비공개 저장소 → 토큰 필요)

```python
# GitHub Personal Access Token (repo 권한) 사용
GH_TOKEN = "ghp_..."   # 본인 토큰으로 교체
!git clone -b claude/repo-access-check-lo2915 https://{GH_TOKEN}@github.com/Parkdev22222/C2_program_ai.git
%cd C2_program_ai
```

### 2) 패키지 설치 (설치 순서 준수 — vLLM/transformers 버전 고정)

```python
# Step 1: 충돌 패키지 제거
!pip uninstall vllm transformers torchaudio -y -q

# Step 2: vLLM 고정 설치 (torch는 vLLM이 자동 설치)
!pip install "vllm==0.6.6.post1" -q

# Step 3: Colab 전용 의존성 설치 (openai 클라이언트·neo4j 등 포함)
!pip install -r requirements-colab.txt -q
```

> ⚠️ 설치 후 **런타임을 재시작**하세요(런타임 → 세션 다시 시작). 재시작 후 `%cd C2_program_ai`로 다시 이동합니다.
> `transformers 4.48+` / `vllm ≥ 0.7.0`은 Colab에서 커널 ABI 충돌을 일으키므로 위 버전을 반드시 고정합니다.

### 3) (선택) 온톨로지 Neo4j 연결

워게임 상태는 동일 스키마 온톨로지로 변환되어 실시간 적재됩니다. **환경변수를 설정하지 않으면
자동으로 in-memory 그래프로 폴백**되므로 Neo4j 없이도 동작합니다. 원격 Neo4j(예: Neo4j Aura)를
쓰려면 서버 기동 전에 설정하세요.

```python
import os
os.environ["OI_NEO4J_URI"]      = "neo4j+s://<your-db>.databases.neo4j.io"
os.environ["OI_NEO4J_USER"]     = "neo4j"
os.environ["OI_NEO4J_PASSWORD"] = "<password>"
```

### 4) vLLM 서버 기동 (백그라운드, 포트 8000)

`scripts/launch_vllm_servers.py`가 `config/models_config.yaml`을 읽어 EXAONE4를
OpenAI 호환 vLLM 서버로 띄웁니다. Colab에서는 백그라운드 프로세스로 실행하고
`/health`가 200이 될 때까지 대기합니다(가중치 다운로드 포함 수~수십 분 소요).

```python
import subprocess, time, urllib.request

# EXAONE4 vLLM 서버를 백그라운드로 기동 (로그: logs/vllm_*.log)
vllm_proc = subprocess.Popen(["python", "scripts/launch_vllm_servers.py"])

# 서버 준비(/health=200) 대기
ready = False
for _ in range(360):  # 최대 ~30분
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as r:
            if r.status == 200:
                ready = True
                break
    except Exception:
        pass
    time.sleep(5)
print("vLLM 서버 준비 완료" if ready else "아직 준비 안 됨 — logs/vllm_*.log 확인")
```

> 진행 로그 확인: `!tail -n 40 logs/vllm_exaone4-agent.log`

### 5) 웹 UI 기동 + Colab 포트 노출 (포트 7860)

앱은 위 vLLM 서버(:8000)에 접속하는 클라이언트만 만들므로 자체 모델 로딩은 없습니다.
FastAPI 서버를 백그라운드 스레드로 띄운 뒤 Colab 포트를 노출합니다.

```python
import threading, time
from main import init_agent
from c2.presentation.web.api import start_server

# vLLM 서버(:8000)에 연결하는 에이전트 생성
agent = init_agent()

# FastAPI 대시보드를 백그라운드 스레드로 기동 (포트 7860)
threading.Thread(
    target=lambda: start_server(agent=agent, host="0.0.0.0", port=7860),
    daemon=True,
).start()
time.sleep(5)

# Colab 인라인 창으로 대시보드 열기
from google.colab.output import serve_kernel_port_as_window
serve_kernel_port_as_window(7860)
```

> 새 탭 대신 인라인 프레임으로 열려면 `serve_kernel_port_as_window` 자리에
> `from google.colab.output import serve_kernel_port_as_iframe; serve_kernel_port_as_iframe(7860)`
> 를 사용하거나, 출력된 링크를 클릭하세요.

### 자주 겪는 문제

- **CUDA OOM**: `config/models_config.yaml`의 `agent_model.gpu_memory_utilization`을 낮추거나 `max_model_len`을 줄이세요(GPU별 권장값은 파일 주석 참고). A100 40GB는 특히 조정이 필요합니다.
- **`/health`가 계속 실패**: `logs/vllm_exaone4-agent.log`의 마지막 부분을 확인하세요(OOM·다운로드 지연 등). 서버가 죽었으면 4)를 다시 실행합니다.
- **`No module named 'vllm'` / ABI 오류**: 2)의 버전 고정과 **런타임 재시작**을 다시 확인하세요.
- **모델 다운로드 지연**: HuggingFace에서 32B 가중치를 받으므로 시간이 걸립니다. 필요 시 `huggingface-cli login`으로 토큰을 등록하세요.
- **서버 종료**: `vllm_proc.terminate()` 또는 `!pkill -f "vllm serve"` 후 `!nvidia-smi`로 GPU 메모리 반환을 확인하세요.

---

## 워게임 시뮬레이터

내장 Python 워게임 엔진으로 대대급 전투를 시뮬레이션합니다.

### 시나리오 편제 (기계화 보병 대대 vs 대대)

| 진영 | 부대 ID | 병종 | 역할 |
|------|---------|------|------|
| BLUFOR | `Alpha` | 기계화보병 | 정면 공격 |
| BLUFOR | `Bravo` | 기계화보병 | 측방 기동 |
| BLUFOR | `Charlie` | 전차 | 기갑 돌파 |
| BLUFOR | `Delta` | **정찰** | 적 위치 탐지 (탐지 반경 8 km) |
| BLUFOR | `Echo` | 대전차 | 기갑 저지 |
| BLUFOR | `Foxtrot` | 자주포 | 화력 지원 |
| OPFOR | `Red1~Red5` | 혼성 | 방어·반격 |

### 탐지 반경 (Fog of War)

| 병종 | 기본 탐지 반경 | 확정 탐지 (50%) |
|------|--------------|----------------|
| 정찰 (Delta) | 8,000 m | 4,000 m |
| 전차 | 4,000 m | 2,000 m |
| 기계화보병·대전차 | 3,000 m | 1,500 m |
| 자주포 | 2,000 m | 1,000 m |

탐지 상태: `approximate` (초기 ±4 km 오차) → `detected` (정확 위치) → `lost` (Dead Reckoning)

**대포병 탐지 (counter-battery)**: 자주포가 간접사격하면 그 순간 위치가 적에게 노출됩니다.
기본은 음향표정 수준의 `approximate`(오차 반경 700 m), 확률 35%로 대포병 레이더가 정확
위치(`detected`)를 포착합니다. 사격을 멈추면 Dead Reckoning 감쇠로 `lost` 처리되므로,
쏘고 즉시 진지변환(shoot-and-scoot)하지 않으면 대포병 사격의 표적이 됩니다.
(파라미터: `c2.application.simulation.engine:_COUNTER_BATTERY_DETECT_PROB` / `_COUNTER_BATTERY_APPROX_RADIUS`)

**포병 화력 감쇠**: 자주포가 피해를 입어 전투력이 낮아질수록 간접사격 위력이 **초선형(제곱)**으로
약해집니다. CP 100%→100%, 75%→56%, 50%→25%, 25%→6% 수준. 즉 피격당한 포병은 화력지원
효율이 급격히 떨어지므로 보호가 중요합니다. (파라미터: `c2.application.simulation.engine:_SPG_FIRE_DEGRADE_EXP`)

### 전장 지도 범례

| 마커 | 의미 |
|------|------|
| 실선 빨간 마커 | OPFOR — 정확한 위치 탐지됨 (`detected`) |
| 주황 빈 원 | OPFOR — 개략 위치만 파악 (`approximate`) |
| 회색 빈 원 | OPFOR — 탐지 상실, 마지막 위치 (`lost`) |
| 파란 마커 | BLUFOR — 실제 위치 |
| 공중지원 아이콘 | `pending` → `active` → `completed` 상태 표시 |

### 자동 재계획 이벤트

다음 이벤트 발생 시 `_detection_worker`가 자동으로 공격 임무계획을 재수립합니다.

| 이벤트 | 트리거 조건 |
|--------|------------|
| `detection` | BLUFOR가 OPFOR를 신규 탐지 |
| `cp_threshold` | BLUFOR 부대 전투력 70% / 30% 이하 |
| `air_hit` | 공중지원이 OPFOR에 명중 |

30틱 쿨다운: 마지막 재계획 후 30틱 이내 이벤트는 무시

---

## 에이전트 도구 목록

`SingleToolGuard`에 의해 **스텝당 1개 도구만 호출 가능**합니다.

---

### 1. 워게임 시뮬레이터 조회 도구 (`wargame_query_tool.py`)

내장 워게임 엔진의 실시간 전장 상황을 조회합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_wargame_situation()` | — | 전체 전장 상황(BLUFOR 실위치, OPFOR 인텔) 반환 |
| `get_intelligence_report(side)` | side: `"BLUFOR"` / `"OPFOR"` | 탐지 인텔 보고서 반환 (FOW 상태 포함) |
| `get_wargame_unit_detail(unit_id)` | unit_id: 부대 ID | 특정 부대의 상세 정보·최근 이동 이력 반환 |
| `get_wargame_battle_log(n)` | n: 가져올 로그 수(기본 20) | 최근 전투 이벤트 로그 반환 |

> **좌표 단위:** 모든 위치 값은 미터(m) 정수 (`x_m`, `y_m`)와 WGS84 위경도(`lat`, `lon`) 함께 반환  
> 임무계획 적용 시에는 반드시 미터 좌표(`x_m`, `y_m`) 사용

---

### 4. 워게임 임무계획 실행 도구 (`wargame_mission_tool.py`)

워게임 엔진에 임무계획 및 공중지원 명령을 **즉시(dry_run=False)** 적용합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `apply_wargame_mission_plan(plan_json, dry_run)` | plan_json: 임무계획 JSON, dry_run: 기본 False | BLUFOR 임무계획(이동 경로·목표·공중지원)을 워게임에 즉시 적용 |
| `apply_wargame_air_support(support_json, dry_run)` | support_json: 공중지원 계획 JSON | CAS·타격·포병·헬기 지원 임무를 워게임 엔진에 즉시 적용 |
| `get_wargame_engine_status()` | — | 워게임 엔진 상태(실행 중 여부, 시간 배율, 현재 틱 등) 반환 |

> **즉시 적용 원칙:** `apply_wargame_mission_plan`은 항상 `dry_run=False`로 호출합니다.  
> 호출 성공 시 `FinalAnswerException`을 발생시켜 에이전트를 즉시 종료합니다.

#### 공중지원 유형 및 파라미터

| 유형 | 반경 | 게임 내 지연 | 특징 |
|------|------|------------|------|
| `cas` | 1,500 m | 6 s | 근접항공지원 — 지속 제압 (~40% 피해) |
| `strike` | 400 m | 12 s | 정밀타격 — 순간 고위력 (~33% 피해) |
| `artillery` | 2,500 m | 30 s | 포병 광역 지속 — 클러스터 적에 효과적 |
| `helicopter` | 1,000 m | 60 s | 공격헬기 — 기갑 목표 우선 |

> 시뮬레이터는 60× 배속 실행 (실제 1초 = 게임 60초)

#### 공중지원 목표 좌표 자동 교정

`air_support_plans`의 `target` 좌표를 **탐지된(detected) OPFOR의 정확 좌표로 자동 스냅**합니다.  
탐지 OPFOR 최근접점으로부터 4 km 이상 벗어난 좌표는 거부됩니다.

---

### 5. 워게임 전술 분석 도구

#### 5-1. 정찰 임무 (`wargame_recon_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `assess_recon_need()` | — | OPFOR 탐지 현황 평가 — 탐지 상태별 부대 목록 및 정찰 필요 여부 반환 |
| `recommend_recon_routes()` | — | 교전 회피 정찰 경로 자동 생성, `apply_json`(미터 좌표) + `ontology_context`(COHA 교리) 포함 반환 |

**정찰 경로 설계 원칙:**
- 직선 접근 금지 → 60° 측방 우회 경유지 삽입
- Standoff 5 km 유지 (교전 범위 4 km 바깥)
- 고도·엄폐율 기준 최적 관측 포인트 배치
- 관측 완료 후 안전 복귀점으로 이동

#### 5-2. 적군 예상 기동 경로 예측 (`wargame_opfor_routes_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `predict_opfor_routes()` | — | 탐지된 OPFOR 부대의 예상 기동 경로 3가지(정면/우측우회/좌측우회)를 지형 기반으로 생성 |

**반환 정보:**
- 각 경로별 `waypoints_xy`, `threat_level` (`high` / `medium` / `low`)
- `interdict_priority`: 경로 교차 차단 우선 지점 상위 3개

**활용법:** 반환된 `predicted_routes`를 `json.dumps()`로 직렬화하여 `get_optimal_attack_positions(opfor_routes_json=...)`에 전달하면 경로 차단 보너스(최대 +25점)가 적용됩니다.

#### 5-3. 최적 공격 위치·수단 추천 (`wargame_attack_advisor_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_optimal_attack_positions(top_n, opfor_routes_json)` | top_n: 목표별 추천 위치 수(기본 3), opfor_routes_json: 경로 JSON(선택) | 탐지된 OPFOR 위치·고도·엄폐를 분석하여 최적 공격 위치·수단 추천 |

반환값에 `ontology_context` 필드로 COHA 온톨로지 교리 컨텍스트가 포함됩니다.

**위치 후보 생성:** 각 OPFOR 목표 기준 16방향 × 4거리(1.2/2.0/3.0/4.5 km) = 64개 후보

**점수 가중치:**

| 요소 | 가중치 | 설명 |
|------|--------|------|
| 고도 우위 | 30% | 공격자가 더 높을수록 유리 |
| 공격자 엄폐 | 25% | 공격 위치의 지형 엄폐율 |
| 적 노출도 | 20% | 적의 엄폐가 낮을수록 고점수 |
| 교전 효율 | 15% | 거리별 교전 효율 (1.2 km 최적) |
| 시선 품질 | 10% | 지형 차폐 없이 적을 관측 가능한 정도 |
| 경로 차단 보너스 | 최대 +25점 | `opfor_routes_json` 제공 시 가산 |
| 공중지원 가용 보너스 | 최대 +20점 | 잔여 공중지원 횟수 × 5점 |

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
| 측방 기동 포함 | +8 | `"flank"` in mission_types |
| 미탐지 OPFOR (정찰 없음) | −15 | approximate/lost OPFOR 존재 |
| 공격 부대 우세 (≥1.5:1) | +10 | 공격 부대 수 / OPFOR 수 |

**위험도 분류:** `low` (≥70) / `medium` (≥45) / `high` (<45)

#### 5-6. 임무계획 검증 도구 (`mission_plan_validator_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `validate_mission_plan_tool(plan)` | plan: 임무계획 JSON | 좌표 범위·부대 ID·스키마 검증 후 결과 반환 |
| `approve_mission_plan_tool(plan_id)` | plan_id: 계획 ID | 승인 대기 중인 임무계획을 승인 처리 |
| `get_pending_plan_tool()` | — | 현재 승인 대기 중인 임무계획 및 세션 상태 조회 |

#### 5-7. 화력지원 타격 우선순위 도구 (`wargame_fire_priority_tool.py`)

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_fire_priority_schedule()` | — | 적 병종·현황을 반영해 화력지원(포병/공중지원) 타격 우선순위 스케줄 반환 (자주포 등 고가치 자산 우선) |

---

### 6. Graph RAG 온톨로지 도구 (`graph_rag_tool.py`)

COHA(Command and Ontology for Hostile Action) 군사 전술 온톨로지를 rdflib로 파싱하여 교리 개념을 검색합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `graph_rag_military_query(query)` | query: 검색 쿼리 (한국어·영어 혼용) | COHA 온톨로지에서 전술 개념·관계를 검색하여 교리 컨텍스트 반환 |

**내부 동작:**
1. `coha_full_ontology.ttl` (OWL/Turtle)을 rdflib로 로드 (프로세스 내 1회 캐시)
2. `rdfs:label` 기반 레이블 인덱스 구축
3. 한국어↔영어 키워드 확장 → 레이블 매칭 → 관련 URI 수집
4. 양방향(나가는 + 들어오는) 1-hop 그래프 탐색
5. `Subject --[Predicate]--> Object` 형식의 교리 관계 목록 반환

**자동 주입:** `recommend_recon_routes()` 및 `get_optimal_attack_positions()` 반환값의 `ontology_context` 필드에 관련 교리 컨텍스트가 자동으로 포함됩니다.

---

### 7. 온톨로지 상황 조회 도구 (`ontology_query_tool.py`)

전장 지식그래프(KG)에서 현재 상황 컨텍스트를 조회합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `get_ontology_situation()` | — | 지식그래프 기반 전장 상황 컨텍스트(부대·이벤트·관계) 반환 |
| `ontology_situation_block()` | — | 프롬프트 주입용으로 정리된 온톨로지 상황 텍스트 블록 반환 |

---

### 8. 상황분석 메모리 도구 (`strategy_advisor_tool.py`)

에이전트의 상황 분석 응답을 세션 메모리로 누적·조회합니다.

| 도구 | 파라미터 | 설명 |
|------|----------|------|
| `update_situation_memory(text)` | text: 상황 분석 응답 | 상황 분석 결과를 세션 메모리에 반영 |
| `get_situation_memory()` | — | 누적된 상황 분석 메모리 조회 |
| `clear_situation_memory()` | — | 상황 분석 메모리 초기화 |

---

## 임무계획 수립 흐름

### 공격 임무계획

```
1. assess_recon_need()
   → OPFOR 탐지 현황 확인 (detected / approximate / lost)
   → detected OPFOR만 공격 대상, approximate/lost는 공격 제외

2. predict_opfor_routes()           [선택]
   → 탐지된 OPFOR 예상 기동 경로 3방향 분석

3. get_optimal_attack_positions(
     opfor_routes_json=json.dumps(routes["predicted_routes"])
   )
   → 경로 차단 보너스 + 공중지원 보너스 반영 최적 공격 위치 추천
   → ontology_context: COHA 기동·화력 교리 자동 포함

4. 최종 JSON 생성 (get_optimal_attack_positions 결과 기반 직접 결정)
   → apply_wargame_mission_plan(plan_json, dry_run=False)
   → detected OPFOR에 공중지원(cas/strike/artillery/helicopter) 적극 배치
```

### 정찰 임무계획

```
1. assess_recon_need()
   → 정찰 필요 여부 평가

2. recommend_recon_routes()
   → 교전 회피 정찰 경로 생성
   → ontology_context: COHA ISR·지형 교리 자동 포함

3. apply_wargame_mission_plan(plan_json, dry_run=False)
   → Delta(정찰부대)만 임무 부여
```

---

## 에이전트 실행 규칙

### 임무 분리 원칙

| 규칙 | 내용 |
|------|------|
| 정찰 임무 | `unit_type='정찰'`인 Delta 부대만 `recon` 임무 부여 |
| 공격 임무 | Alpha/Bravo/Charlie/Echo/Foxtrot에 공격 임무 부여, Delta는 측방 경계 |
| 동시 금지 | 정찰 임무계획과 공격 임무계획을 같은 응답에 동시 생성 금지 |

### 공중지원 규칙

- `detected` 상태 OPFOR에만 공중지원 배치
- `approximate` / `lost` OPFOR에는 공중지원 금지 (정찰 후 위치 확인 필수)
- 교전 초반에 위치가 확인된 적에 대해 CAS/Strike를 선제 활용하여 전투력 조기 약화

### SingleToolGuard (스텝당 1 도구 제한)

에이전트는 하나의 코드 블록에서 **1개의 도구만 호출**할 수 있습니다.  
2개 이상 호출 시 `RuntimeError`가 발생하며 에이전트가 다음 블록에서 재시도합니다.

---

## 도구 그룹 요약

모든 툴은 `src/c2/presentation/tools/` 아래에 있습니다 (아래 12개 모듈 + 스텝당 1툴 제한을 강제하는 `single_tool_guard.py`).

| 그룹 | 파일 | 함수 수 | 주요 용도 |
|------|------|---------|----------|
| 워게임 조회 | `wargame_query_tool.py` | 4 | 실시간 전장 상황·인텔·부대상세·전투로그 |
| 워게임 실행 | `wargame_mission_tool.py` | 3 | 임무계획·공중지원 즉시 적용·엔진상태 |
| 정찰 임무 | `wargame_recon_tool.py` | 2 | 정찰 필요 평가 + 경로 생성 |
| 적군 경로 예측 | `wargame_opfor_routes_tool.py` | 1 | OPFOR 예상 기동 경로 분석 |
| 최적 공격 위치 | `wargame_attack_advisor_tool.py` | 1 | 공격 위치 추천 + 온톨로지 컨텍스트 |
| 화력 우선순위 | `wargame_fire_priority_tool.py` | 1 | 적 병종·현황 반영 타격 우선순위 스케줄 |
| 전술 권고 | `wargame_strategy_tool.py` | 1 | 병종 상성 + 기동 경로 추천 |
| COA 분석 | `coa_analysis_tool.py` | 1 | 행동 방책 비교 평가 |
| 임무계획 검증 | `mission_plan_validator_tool.py` | 3 | 임무계획 검증·승인·대기 조회 |
| 온톨로지 상황 | `ontology_query_tool.py` | 2 | 지식그래프 기반 상황 컨텍스트 |
| 상황분석 메모리 | `strategy_advisor_tool.py` | 3 | 상황 분석 응답 세션 메모리 |
| Graph RAG | `graph_rag_tool.py` | 1 | COHA 군사 온톨로지 교리 검색 |
| **합계** | **12개 모듈** | **23** | |

---

## 파일 구조

`src/c2/` 아래 **실용형 4계층 클린 아키텍처**로 구성됩니다. 모든 의존성은 안쪽(도메인)을
향하며, 바깥 계층은 안쪽 계층이 정의한 **포트(인터페이스)** 를 통해서만 연결됩니다.
이 의존성 규칙은 `import-linter` 계약 3종으로 **자동 강제**됩니다
(`PYTHONPATH=src lint-imports` → 3 kept, 0 broken).

```
presentation ─┐
              ├─▶ application ─▶ domain
infrastructure┘   (ports)  ▲
                           └── infrastructure가 포트를 구현 (의존성 역전, DI 주입)
composition ─────────────────▶ 모든 계층을 조립 (조립 루트)
```

| 계층 | 역할 | 프레임워크·IO |
|------|------|--------------|
| **domain** | 순수 도메인 규칙·값 객체 — 부대/전투/지형/좌표, 온톨로지 엔티티, 임무계획 스키마. 어떤 계층도 import하지 않음 | ❌ 표준 라이브러리(+numpy/pydantic) |
| **application** | 유스케이스·오케스트레이션 — 시뮬 엔진·세션·자동재계획·임무계획·온톨로지 서비스·하네스. **포트 5종** 정의 | ❌ 포트로 IO 추상화 |
| **infrastructure** | 포트 구현체 — vLLM 클라이언트, SQLite, 온톨로지 스토어, PostgreSQL, rdflib | ✅ |
| **presentation** | 전달 계층 — FastAPI web_api·HTML 대시보드, LLM 툴 어댑터, 에이전트 런타임 | ✅ |
| **composition** | 조립 루트 — 포트↔구현 바인딩, 전 계층 DI 주입 | ✅ |

> 이전의 레거시 top-level 패키지(`wargame/`·`agent/`·`ontology/`·`tools/`·`ui/gradio_app.py`)와
> ARMA3 연동·PDF RAG·Gradio UI는 모두 **삭제**되었습니다. 상세 매핑은 `CLAUDE.md` 참고.

```
C2_program_ai/
├── src/c2/
│   ├── domain/                    # 순수 값 객체/규칙 (표준 라이브러리만 의존)
│   │   ├── wargame/               # unit.py, combat.py, coordinates.py, terrain.py
│   │   ├── planning/               # mission_plan.py (MAP_MAX, Pydantic 스키마)
│   │   └── ontology/                # models.py (KnowledgeNode/Edge/Evidence)
│   ├── application/                # 유스케이스/오케스트레이션 (domain + 포트만 의존)
│   │   ├── ports/                   # LLMClient/EventStore/OntologyStore/ConversationStore/HarnessStore
│   │   ├── simulation/               # engine.py, scenario.py, session.py, replan.py
│   │   ├── agent/                     # mission_planner.py
│   │   ├── ontology/                   # wargame_builder.py, writer.py, retrieval.py, coa_view.py
│   │   ├── harness/                     # 학습/평가 하네스
│   │   └── planning/                     # mission_session.py (의도분류/pending-plan)
│   ├── infrastructure/              # 포트 구현체
│   │   ├── llm/                       # vllm_client.py, model_loader.py, langgraph_llm.py
│   │   ├── ontology/                   # doctrine_loader.py(Graph RAG), graph_store.py, in_memory_store.py
│   │   └── persistence/                 # sqlite_event_store.py(WargameDB), harness_db.py, conversation_store.py
│   ├── presentation/                 # 에이전트 바인딩 + 웹 API
│   │   ├── agent/                      # battlefield_agent.py, langgraph_agent.py, langgraph_tools.py
│   │   ├── tools/                        # wargame_query/mission/recon/strategy/attack_advisor/fire_priority/opfor_routes_tool.py, coa_analysis_tool.py, graph_rag_tool.py, ontology_query_tool.py, mission_plan_validator_tool.py, single_tool_guard.py
│   │   └── web/api.py                     # FastAPI 앱(create_app/start_server) — HTML 대시보드 REST API
│   └── composition/
│       └── container.py                   # build_session() — 전 계층 wiring (조립 루트)
├── ui/
│   └── dashboard/index.html          # HTML/Leaflet 대시보드 (FastAPI가 정적 서빙)
├── scripts/
│   └── launch_vllm_servers.py        # vLLM 서버 기동 (EXAONE4 :8000)
├── config/
│   ├── agent_config.yaml             # 에이전트 설정
│   ├── agent_custom_instructions.txt  # 에이전트 시스템 프롬프트
│   └── models_config.yaml            # ML 모델 설정
├── data/
│   ├── coha_full_ontology.ttl        # COHA 군사 전술 온톨로지 (OWL/Turtle)
│   └── wargame_state.db              # SQLite 워게임 상태 DB
├── tests/                             # pytest 스위트
├── c2_agent_architecture.png          # 시스템 아키텍처 다이어그램
├── main.py                            # 진입점 (ui / query / check-env)
└── requirements.txt
```

### 계층별 기능 구성

핵심 컴포넌트를 기능 영역별로 정리하면 다음과 같습니다.

**① 워게임 시뮬레이션 — `application/simulation/`**
- `engine.py` — `WargameEngine`: 틱 루프(2Hz), 전투·탐지·공중지원 처리, 부대 상태 전이.
  네 가지 이벤트 콜백(신규 탐지 / CP 임계값 / 공중지원 피격 / 표적 이동)을 발동한다.
  `WargameDB`(SQLite)를 직접 알지 않고 **`EventStore` 포트**에 의존하며, 기본 구현은
  조립 루트가 DI factory로 주입한다.
- `scenario.py` — 초기 부대 배치(`setup_bn_vs_bn`, 철원 시나리오, 커스텀 시나리오).
- `session.py` — `WargameSession`: 엔진 생명주기·탐지 워커 스레드·세션 조작(시작/정지,
  리셋, 배속, 정찰/공격 계획, 채팅, 평가/학습, 시나리오 적용)을 소유하고 **데이터(dict)** 를
  반환한다. UI는 이 데이터를 받아 렌더링만 한다.
- `replan.py` — 자동 재계획 워커: 위 4종 이벤트를 큐로 받아 에이전트로 공격/정찰 임무를
  재수립한다.

**② 에이전트 & 임무계획 — `application/agent/`, `presentation/agent/`**
- `application/agent/mission_planner.py` — `build_mission_query()` / `MissionPlanner`:
  전장 상황을 프롬프트로 구성. 정찰·공격·화력 advisor는 **주입 레지스트리**로 받아
  (application이 tools를 import하지 않도록) `wargame↔tools` 순환을 제거했다.
- `presentation/agent/langgraph_agent.py` — LangGraph StateGraph(ReAct) 오케스트레이터(기본).
- `presentation/agent/battlefield_agent.py` — smolagents CodeAgent 오케스트레이터(대안).
- `presentation/agent/langgraph_tools.py` — smolagents 툴을 LangChain `StructuredTool`로 래핑.

**③ LLM 툴 어댑터(12종) — `presentation/tools/`**
상황조회·임무적용·정찰·공격위치·화력우선순위·적경로예측·전략추천·COA분석·온톨로지질의·
그래프RAG(교리)·임무계획검증·단일툴가드. 에이전트가 호출하는 인터페이스 어댑터로,
`application`/`domain` 유스케이스를 감싼다.

**④ 온톨로지(지식그래프 + 교리 RAG) — `*/ontology/`**
- `domain/ontology/models.py` — KG 엔티티(`KnowledgeNode/Edge/Evidence` 등).
- `application/ontology/` — `wargame_builder`(전장→KG), `writer`(이벤트·스냅샷 적재),
  `retrieval`(GraphRAG 질의), `coa_view`(상황 직렬화).
- `infrastructure/ontology/` — `graph_store`(Neo4j)·`in_memory_store`(폴백)·`factory`,
  `doctrine_loader`(COHA 교리 온톨로지 TTL을 rdflib로 조회하는 Graph RAG).

**⑤ 학습/평가 하네스 — `application/harness/`**
`episode_runner`·`metrics`·`rule_extractor`·`rule_manager`·`tactical_memory` —
반복 시뮬로 전술 규칙을 추출·평가·누적. `HarnessDB`는 `HarnessStore` 포트로 주입.

**⑥ 포트 & 조립 루트 — `application/ports/`, `composition/`**
- 포트 5종: `LLMClient`·`EventStore`·`OntologyStore`·`ConversationStore`·`HarnessStore`
  (application이 정의, infrastructure가 구현).
- `composition/container.py` — `build_session()`: EventStore/HarnessStore factory,
  정찰/공격/화력 advisor, 8개 툴의 엔진 등록, 온톨로지 스토어, 에이전트를 **한곳에서 wiring**.
  애플리케이션의 유일한 의존성 주입 지점.

**⑦ 인프라 어댑터 — `infrastructure/`**
- `llm/` — `vllm_client`(EXAONE4 OpenAI 호환), `model_loader`, `langgraph_llm`(vLLM/Gemini).
- `persistence/` — `sqlite_event_store`(WargameDB), `harness_db`, `conversation_store`(PostgreSQL/in-memory).

**⑧ UI — `presentation/web/`, `ui/dashboard/`**
- `web/api.py` — FastAPI REST(`create_app`/`start_server`): 엔진 제어·상태·이벤트·채팅·
  임무(정찰/공격/평가)·시나리오·자동계획상태 엔드포인트. `WargameSession`을 조립 루트에서
  받아 사용하며 Gradio에 의존하지 않는다.
- `ui/dashboard/index.html` — Leaflet 전장 지도 대시보드(FastAPI가 정적 서빙).

### 요청 흐름 (예: `POST /api/mission/attack`)
```
브라우저(대시보드) → FastAPI web/api.py → WargameSession.request_attack_plan()
   → MissionPlanner.build_mission_query() + 에이전트(LLM) → 임무계획 JSON
   → WargameEngine.apply_mission_plan()  (EventStore 포트로 이벤트 기록)
   → 상태 dict 반환 → 대시보드 지도 갱신
```
엔진의 탐지/피격 이벤트는 `replan.py` 워커가 큐로 받아 자동으로 재계획을 수행한다.

---

## 설정

### `config/agent_config.yaml` 주요 설정

```yaml
code_agent:
  max_steps: 10          # 에이전트 최대 추론 스텝
  planning_interval: null  # 비활성화 (플래닝 재시작으로 인한 워크플로 방해 방지)
  stream_outputs: false

strategy_keywords:
  korean: [전략, 전술, 작전, 기동, 화력지원, ...]
  english: [strategy, tactics, maneuver, fire support, ...]
```

### 핵심 상수

| 항목 | 값 |
|------|-----|
| 맵 크기 | 30,000 × 30,000 m |
| 기본 배속 | 60× (실제 1초 = 게임 60초) |
| 틱 간격 | 0.5초 (2 Hz) |
| 자동 재계획 타임아웃 | 900초 |
| 재계획 쿨다운 | 30틱 |
| BLUFOR 배치 구역 | x 2,000~13,000 / y 1,500~12,000 m |
| OPFOR 배치 구역 | x 17,000~28,000 / y 17,000~28,500 m |
