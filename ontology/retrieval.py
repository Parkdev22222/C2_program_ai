"""[shim] KG 검색 오케스트레이션은 c2.application.ontology.retrieval 로 이동됨."""
from c2.application.ontology.retrieval import (  # noqa: F401  [shim]
    GraphStore,
    retrieve_graph_context,
)
