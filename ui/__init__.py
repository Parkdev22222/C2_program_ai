"""`ui` 패키지.

`ui.web_api`(FastAPI, Task 30부터 gradio 비의존)가 이 패키지의 실행 진입점이다.
과거 gradio 기반 UI(`ui/gradio_app.py`)는 Task 31에서 삭제됐다 — 세션
오케스트레이션이 `c2.application`으로 완전히 이관됐기 때문이다.
"""
__all__: list[str] = []
