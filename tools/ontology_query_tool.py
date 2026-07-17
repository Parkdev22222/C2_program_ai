"""[shim] 온톨로지 기반 전장 상황 조회는 c2.presentation.tools.ontology_query_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.ontology_query_tool import (  # noqa: F401  [shim]
    register_graph_store,
    get_ontology_situation,
    ontology_situation_block,
)
