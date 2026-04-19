# C2 군사 전략 AI

군사 영상 분석 및 전략/전술 추천을 위한 듀얼 모델 C2(지휘통제) AI 시스템입니다.

## 아키텍처

```
사용자 쿼리
    │
    ▼
┌─────────────────────────────────────────────────────┐
│              EXAONE4 (메인 에이전트)                 │
│         EXAONE-4.0-32B-AWQ / smolagents CodeAgent   │
│                                                     │
│  영상 분석 쿼리 ──→ 비디오 도구 사용 ──→ 상황 분석 응답  │
│                          │                          │
│                    situation_memory 갱신             │
│                                                     │
│  전략/전술 쿼리 ──→ strategy_advisor_tool 호출       │
│                          │                          │
└──────────────────────────┼──────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   EXAONE Deep          │
              │   EXAONE-Deep-32B      │
              │                        │
              │ 입력: [EXAONE4 상황분석 │
              │       + 사용자 쿼리]   │
              │                        │
              │ 출력: 전략/전술 권고    │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   EXAONE4 최종 응답    │
              │ 상황분석 + 전략권고    │
              │ 종합하여 최종 응답 생성 │
              └────────────────────────┘
```

## 핵심 흐름

1. **영상 분석**: 군사 영상 업로드 → EXAONE4가 객체 탐지/임베딩 결과를 바탕으로 상황 분석
2. **메모리 갱신**: EXAONE4의 상황 분석 응답 → `situation_memory` 자동 저장
3. **전략 쿼리**: 사용자가 전략/전술 추천 요청 → EXAONE4가 `strategy_advisor_tool` 호출
4. **EXAONE Deep 처리**: `[EXAONE4 상황 분석 + 사용자 쿼리]` → EXAONE Deep이 전략/전술 권고 생성
5. **최종 응답**: EXAONE4가 자신의 상황 분석 + EXAONE Deep의 권고를 종합하여 최종 응답

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
# Gradio UI 실행
python main.py ui

# 환경 확인
python main.py check-env

# 영상 분석 (CLI)
python main.py analyze --video path/to/video.mp4

# 에이전트 쿼리 (CLI)
python main.py query --query "적 기갑부대 탐지 시 전술 추천"
```

## 파일 구조

```
C2_program_ai/
├── config/
│   ├── models_config.yaml          # EXAONE4 + EXAONE Deep 설정
│   ├── agent_config.yaml           # 에이전트 및 UI 설정
│   ├── agent_custom_instructions.txt  # EXAONE4 역할 지침
│   └── videodb_config.yaml
├── core_src/                       # 비디오 분석 파이프라인
│   ├── video_analysis_system.py    # 메인 오케스트레이터
│   ├── object_detection.py         # SAM2 기반 객체 탐지
│   ├── embedding_generator.py      # CLIP 임베딩
│   ├── event_description.py        # SmolVLM2 영상 설명
│   ├── videodb_manager.py          # 로컬 비디오 DB
│   ├── collection_manager.py       # 컬렉션 관리
│   ├── context_scanner.py          # 컨텍스트 추적
│   └── model_manager.py            # ML 모델 싱글톤
├── agent/
│   ├── battlefield_agent.py        # EXAONE4 메인 에이전트 (핵심)
│   ├── model_loader.py             # EXAONE4 로더
│   └── strategy_model_loader.py    # EXAONE Deep 로더 (신규)
├── tools/
│   ├── videodb_query_tool.py       # 비디오 쿼리 도구
│   ├── pdf_rag_tool.py             # PDF RAG 도구
│   ├── wargame_query_tool.py       # 전술지도 쿼리 도구
│   └── strategy_advisor_tool.py   # EXAONE Deep 호출 도구 (신규)
├── ui/
│   └── gradio_app.py               # Gradio 웹 인터페이스
├── data/
│   └── wargame_state.json          # 전술지도 초기 데이터
├── main.py                         # CLI 진입점
└── requirements.txt
```

## 전략/전술 쿼리 키워드

다음 키워드가 포함된 쿼리는 자동으로 EXAONE Deep을 호출합니다:

**한국어**: 전략, 전술, 작전, 기동, 화력 지원, 포위, 기습, 매복, 침투, 방어, 돌격, 대응방안, 추천, 제안

**영어**: strategy, tactics, maneuver, fire support, assault, defense, COA, recommend
