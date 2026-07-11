"""온톨로지(Neo4j) 기반 전장 상황 조회 도구 (smolagents Tool).

워게임 상태를 직접 읽지 않고, OntologyWriter가 실시간 적재한 Neo4j(또는 폴백
in-memory) 그래프를 검색해 아군 방책(COA) 생성을 위한 상황을 반환한다.

검색 순서(neighborhood → edges_for_nodes → evidence_for_edges)와 시간 윈도우 처리는
prototype-ontology-intelligence(claude/ukraine-event-scenarios-wmre56) 브랜치의
RetrieveContextNode 와 동일하다(ontology.retrieval.retrieve_graph_context).
"""

from __future__ import annotations

import logging

from smolagents import tool

from ontology.coa_view import serialize_situation
from ontology.retrieval import retrieve_graph_context
from ontology.wargame_builder import WARGAME_SCENARIO_ID

logger = logging.getLogger(__name__)

_graph_store = None
_scenario_id = WARGAME_SCENARIO_ID

# 세션 내 관측이 누적되므로 최신 관측을 놓치지 않도록 넉넉히 조회
_NODE_LIMIT = 1000
_EDGE_LIMIT = 2000


def register_graph_store(store, scenario_id: str = WARGAME_SCENARIO_ID) -> None:
    """UI에서 온톨로지 그래프 스토어를 등록."""
    global _graph_store, _scenario_id
    _graph_store = store
    _scenario_id = scenario_id


@tool
def get_coa_situation_from_ontology() -> dict:
    """실시간 적재된 온톨로지(Neo4j)에서 아군 방책(COA) 생성을 위한 전장 상황을 조회한다.

    워게임 엔진을 직접 조회하지 않고, OntologyWriter가 KG로 적재한 그래프를
    neighborhood → edges_for_nodes → evidence_for_edges 순서로 검색한다.

    Returns:
        dict:
          - status: "success" | "no_data" | "store_not_ready" | "error"
          - source: 그래프 스토어 종류 (Neo4jGraphStore | InMemoryGraphStore)
          - scenario_id: 시나리오 ID
          - units: 아군/적 부대 목록 (최신 관측 좌표·전투력·상태 포함)
          - detections: 탐지 관계 (observer → target)
          - events: 최근 전투/포격/공습 이벤트 (BattleEvent 필드)
          - evidence: 이벤트 근거 텍스트
          - summary: 아군 부대 수 / 탐지 표적 수 / 최근 이벤트 수
    """
    if _graph_store is None:
        return {"status": "store_not_ready", "message": "그래프 스토어 미등록"}
    try:
        seeds = _graph_store.unit_entity_ids(
            scenario_id=_scenario_id, side="BLUFOR"
        )
        if not seeds:
            return {
                "status": "no_data",
                "source": type(_graph_store).__name__,
                "scenario_id": _scenario_id,
                "message": "온톨로지에 아군 부대가 아직 적재되지 않았습니다.",
            }
        kg_nodes, kg_edges, evidences = retrieve_graph_context(
            _graph_store,
            seeds,
            scenario_id=_scenario_id,
            node_limit=_NODE_LIMIT,
            edge_limit=_EDGE_LIMIT,
        )
        result = serialize_situation(kg_nodes, kg_edges, evidences)
        result.update(
            {
                "status": "success",
                "source": type(_graph_store).__name__,
                "scenario_id": _scenario_id,
            }
        )
        return result
    except Exception as e:
        logger.warning("온톨로지 상황 조회 실패: %s", e)
        return {"status": "error", "message": str(e)}
