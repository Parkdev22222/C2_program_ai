"""그래프 스토어 생성 팩토리 — shim.

이 모듈은 하위 호환을 위한 순수 재노출(shim)이며 네이티브 구현은 없다.
실제 구현은 `c2.infrastructure.ontology.factory` 로 이전되었다.
"""
from c2.infrastructure.ontology.factory import build_graph_store  # noqa: F401  [shim]
