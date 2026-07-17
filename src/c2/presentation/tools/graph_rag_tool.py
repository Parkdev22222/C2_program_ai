"""COHA 군사 전술 온톨로지 Graph RAG 도구.

순수 rdflib 로딩/쿼리 로직(`_ensure_graph`, `_build_index`, `_label_of`,
`_related_triples`, `_match_uris`, `query_military_ontology`,
`get_recon_ontology_context`, `get_attack_ontology_context`)은
`c2.infrastructure.ontology.doctrine_loader` (인프라 계층)로 추출되었다.
이 파일은 smolagents `@tool` 바인딩(프레젠테이션 계층)만 담당하는 얇은 래퍼다.

사용처:
  - recommend_recon_routes()   → 정찰 ISR·지형 교리 컨텍스트
  - get_optimal_attack_positions() → 공격 기동·화력 교리 컨텍스트
  - LLM 에이전트 직접 호출 → 임의 전술 개념 조회

Task 17: tools/graph_rag_tool.py 에서 c2.infrastructure.ontology.doctrine_loader 로
rdflib 로딩/쿼리 로직 이동. 이 파일은 얇은 @tool 래퍼로 위임한다.
"""

from c2.infrastructure.ontology.doctrine_loader import (
    get_attack_ontology_context,
    get_recon_ontology_context,
    query_military_ontology,
)

__all__ = [
    "query_military_ontology",
    "get_recon_ontology_context",
    "get_attack_ontology_context",
    "graph_rag_military_query",
]


# ── smolagents 에이전트용 @tool ───────────────────────────────────────────────

def graph_rag_military_query(query: str) -> str:
    """
    COHA 군사 전술 온톨로지에서 전술 개념과 관계를 검색합니다.
    정찰·공격 임무 계획 전에 호출하여 관련 교리·부대 관계·지형 고려사항을 조회하세요.

    Args:
        query: 검색할 전술 개념 (한국어·영어 혼용 가능)
               예) "정찰 ISR 지형 경로", "기갑 도시전 보병 지원 화력"

    Returns:
        관련 전술 교리 개념과 관계 목록
    """
    return query_military_ontology(query)
