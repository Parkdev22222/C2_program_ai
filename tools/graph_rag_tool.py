"""[shim] Graph RAG 도구는 c2.presentation.tools.graph_rag_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.graph_rag_tool import (  # noqa: F401  [shim]
    query_military_ontology,
    get_recon_ontology_context,
    get_attack_ontology_context,
    graph_rag_military_query,
)

__all__ = [
    "query_military_ontology",
    "get_recon_ontology_context",
    "get_attack_ontology_context",
    "graph_rag_military_query",
]
