# docs/

C2 군사 AI 시스템 문서 디렉토리.

- 프로젝트 개요·아키텍처·핵심 상수·개발 규칙: 저장소 루트 `CLAUDE.md`
- 빠른 시작·vLLM 서버 기동·에이전트 백엔드 선택·도구 목록: 저장소 루트 `README.md`
- 철원 시나리오(가상) 편제·지형·규칙: `docs/scenario_cheorwon.md`

## 코드 구조 (요약)

애플리케이션 코드는 `src/c2/` 아래 4계층 클린 아키텍처로 구성된다
(`domain → application → infrastructure/presentation`, `composition`이 조립 루트).
레거시 top-level 패키지(`wargame/`, `agent/`, `tools/`, `ontology/`, 구 `ui/gradio_app.py`)는
모두 삭제되었으며 더 이상 참조하지 않는다.

UI는 FastAPI 웹 API(`c2.presentation.web.api`)와 HTML/Leaflet 대시보드(`ui/dashboard/`)로
구성된다. Gradio는 제거되었다. ARMA3 연동·PDF RAG·video 관련 기능도 제거되었으며,
COHA 군사 전술 온톨로지 기반 Graph RAG(`c2.infrastructure.ontology.doctrine_loader` +
`c2.presentation.tools.graph_rag_tool`)만 유지된다.

상세 내용은 `CLAUDE.md`의 "아키텍처 개요"/"디렉토리 구조" 절을 참고.
