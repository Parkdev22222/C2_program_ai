"""C2 온톨로지 패키지.

prototype-ontology-intelligence(claude/ukraine-event-scenarios-wmre56) 브랜치의
온톨로지 스키마와 Neo4j 검색 코드를 그대로 이식하고, 워게임 상태를 동일 스키마의
온톨로지로 변환·실시간 적재하는 모듈을 제공한다.

구성
----
- models.py          : KnowledgeNode / KnowledgeEdge / Evidence / BattleEvent 등 도메인 스키마 (원본 그대로)
- graph_store.py     : Neo4jGraphStore — Cypher 검색/적재 (원본 그대로)
- in_memory_store.py : InMemoryGraphStore — Neo4j 미접속 시 폴백 (원본 그대로)
- retrieval.py       : neighborhood→edges_for_nodes→evidence_for_edges 검색 오케스트레이션
- wargame_builder.py : 워게임 get_state()/전투로그 → 동일 스키마 온톨로지 변환
- writer.py          : 이벤트 + 주기 스냅샷 실시간 적재 (OntologyWriter)
- factory.py         : 환경변수 기반 그래프 스토어 생성 (Neo4j, 실패 시 in-memory 폴백)
"""

from ontology.models import (
    BattleEvent,
    Entity,
    Evidence,
    GeoObject,
    KnowledgeEdge,
    KnowledgeNode,
    Scenario,
    UnitCapability,
    UserContext,
)

__all__ = [
    "BattleEvent",
    "Entity",
    "Evidence",
    "GeoObject",
    "KnowledgeEdge",
    "KnowledgeNode",
    "Scenario",
    "UnitCapability",
    "UserContext",
]
