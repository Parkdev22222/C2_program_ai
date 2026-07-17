"""온톨로지(Neo4j) 기반 전장 상황 조회.

워게임 상태를 직접 읽지 않고, OntologyWriter가 실시간 적재한 Neo4j(또는 폴백
in-memory) 그래프를 검색해 아군 방책(COA) 판단용 상황을 반환한다.

이 모듈은 smolagents 툴을 노출하지 않는다. 대신 에이전트가 **매 판단(agent.run)마다**
자동으로 온톨로지를 검색해 상황을 주입받도록, 일반 함수와 주입용 텍스트 블록 포매터를
제공한다 (agent/battlefield_agent.py 의 _session_run 래퍼에서 호출).

검색 순서(아군 모든 부대 seed → neighborhood(1-hop) → edges_for_nodes →
evidence_for_edges)와 시간 윈도우 처리는 prototype-ontology-intelligence
(claude/ukraine-event-scenarios-wmre56) 브랜치의 RetrieveContextNode 와 동일하다.
"""

from __future__ import annotations

import json
import logging

from c2.application.ontology.coa_view import serialize_situation
from c2.application.ontology.retrieval import retrieve_graph_context
from c2.application.ontology.wargame_builder import WARGAME_SCENARIO_ID

logger = logging.getLogger(__name__)

_graph_store = None
_scenario_id = WARGAME_SCENARIO_ID

# 부대(seed)별 이웃을 최신 우선으로 이만큼만 조회 → 턴이 쌓여도 컨텍스트 고정
_PER_SEED_LIMIT = 5
_EDGE_LIMIT = 500
# 탐색 깊이(hop). 2 이면 아군 → 탐지한 적 → 그 적의 관측·관계까지 확장 수집.
_HOPS = 2
# 최근 전투/포격 이벤트를 별도로 확보할 개수 (관측 노드에 밀려 누락되는 것 방지)
_EVENT_LIMIT = 20


def register_graph_store(store, scenario_id: str = WARGAME_SCENARIO_ID) -> None:
    """UI에서 온톨로지 그래프 스토어를 등록."""
    global _graph_store, _scenario_id
    _graph_store = store
    _scenario_id = scenario_id


def get_ontology_situation() -> dict:
    """실시간 적재된 온톨로지(Neo4j)에서 아군 방책 판단용 전장 상황을 조회한다.

    아군(BLUFOR) 모든 부대의 entity_id 를 seed 로, 부대별 이웃을 최신 5개까지만
    2-hop(아군 → 탐지한 적 → 그 적의 관측·관계)으로 검색한다
    (턴이 누적돼도 부대당 조회량이 고정되도록 제한).

    Returns:
        dict — status / source / scenario_id / units / detections / events /
               evidence / summary. 스토어 미등록·데이터 없음·오류 시 status 로 구분.
    """
    if _graph_store is None:
        return {"status": "store_not_ready", "message": "그래프 스토어 미등록"}
    try:
        seeds = _graph_store.unit_entity_ids(scenario_id=_scenario_id, side="BLUFOR")
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
            per_seed_limit=_PER_SEED_LIMIT,
            edge_limit=_EDGE_LIMIT,
            hops=_HOPS,
        )
        # 최근 전투/포격 이벤트를 별도로 확보해 병합 (관측 노드에 밀려 이웃 검색에서 누락 방지)
        try:
            recent_events = _graph_store.recent_event_nodes(
                scenario_id=_scenario_id, limit=_EVENT_LIMIT
            )
            if recent_events:
                existing = {n.kg_node_id for n in kg_nodes}
                extra = tuple(n for n in recent_events if n.kg_node_id not in existing)
                if extra:
                    kg_nodes = tuple(kg_nodes) + extra
                    # 이벤트의 participates_in 등 엣지도 확보(관계 표기용)
                    try:
                        ev_edges = _graph_store.edges_for_nodes(
                            tuple(n.kg_node_id for n in recent_events),
                            limit=_EDGE_LIMIT,
                            scenario_id=_scenario_id,
                        )
                        seen_e = {e.kg_edge_id for e in kg_edges}
                        kg_edges = tuple(kg_edges) + tuple(
                            e for e in ev_edges if e.kg_edge_id not in seen_e
                        )
                    except Exception:
                        pass
        except Exception as _ee:
            logger.debug("최근 이벤트 병합 실패(무시): %s", _ee)
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


def ontology_situation_block() -> str:
    """에이전트 쿼리에 주입할 [현재 전장 상황] 텍스트 블록. 데이터 없으면 빈 문자열.

    battlefield_agent 의 agent.run 래퍼가 매 판단마다 호출해 쿼리 앞에 붙인다.
    """
    sit = get_ontology_situation()
    if sit.get("status") != "success":
        return ""
    try:
        payload = json.dumps(sit, ensure_ascii=False)
    except Exception:
        return ""
    return (
        "\n[현재 전장 상황 — 온톨로지(Neo4j)에서 매 판단마다 자동 조회됨]\n"
        "아래 데이터를 전장 상황(situation)으로 간주하고 방책을 판단할 것. "
        "별도의 상황 조회 툴은 존재하지 않는다.\n"
        f"```json\n{payload}\n```\n"
    )
