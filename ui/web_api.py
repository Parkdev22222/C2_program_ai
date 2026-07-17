"""[shim] FastAPI 웹 API 구현은 c2.presentation.web.api 로 이동됨 (Task 30).

이 모듈은 하위 호환을 위한 순수 재노출(shim)이며 네이티브 구현은 없다.
더 이상 `ui.gradio_app`을 참조하지 않는다 — 신규 구현(`c2.presentation.web.api`)이
`c2.composition.container.build_session()`으로 얻은 `WargameSession`을 직접 사용한다.
"""
from c2.presentation.web.api import (  # noqa: F401  [shim]
    app,
    create_app,
    start_server,
    set_agent,
    get_job_status,
    _convert_state_to_api,
    _xy_to_latlon,
)

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    start_server()
