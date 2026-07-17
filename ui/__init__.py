"""`ui` 패키지.

`gradio_app`는 선택적 의존성(gradio)에 의존하므로 여기서 eager import하지 않는다.
`ui.web_api`(FastAPI, Task 30부터 gradio 비의존)처럼 gradio 없이도 동작해야 하는
서브모듈이 `import ui.xxx`만으로 gradio import에 실패하지 않도록 lazy 처리한다.
gradio가 설치돼 있으면 기존과 동일하게 `ui.create_app`/`ui.launch_app`을 노출한다.
"""
try:
    from .gradio_app import create_app, launch_app

    __all__ = ["create_app", "launch_app"]
except ImportError:
    __all__ = []
