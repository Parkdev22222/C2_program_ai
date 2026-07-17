"""In-memory 그래프 스토어 — shim.

이 모듈은 하위 호환을 위한 순수 재노출(shim)이며 네이티브 클래스 정의는 없다.
실제 구현은 `c2.infrastructure.ontology.in_memory_store` 로 이전되었다.
"""
from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore  # noqa: F401  [shim]
