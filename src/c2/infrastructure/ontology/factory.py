"""그래프 스토어 생성 팩토리.

환경변수(``OI_NEO4J_URI`` / ``OI_NEO4J_USER`` / ``OI_NEO4J_PASSWORD``)가 설정되어
있고 neo4j 드라이버로 접속 가능하면 :class:`Neo4jGraphStore` 를, 그렇지 않으면
:class:`InMemoryGraphStore` 를 반환한다(사용자 지정: 환경변수 + 연결 실패 시 폴백).

Neo4jGraphStore 와 InMemoryGraphStore 는 검색 3종(neighborhood / edges_for_nodes /
evidence_for_edges)과 적재(merge_node/merge_edge/merge_evidence/ingest/reset_demo_data)
인터페이스가 동일하므로 상위 코드는 어느 백엔드든 동일하게 사용할 수 있다.

Task 16: ontology/factory.py 에서 c2.infrastructure.ontology.factory 로 이동.
옛 경로(ontology/factory.py)는 이 모듈을 재노출하는 shim이다.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def build_graph_store():
    """환경변수 우선 Neo4j, 실패 시 in-memory 폴백으로 그래프 스토어를 만든다."""
    uri = os.environ.get("OI_NEO4J_URI")
    if uri:
        try:
            from c2.infrastructure.ontology.graph_store import Neo4jConfig, Neo4jGraphStore

            store = Neo4jGraphStore(Neo4jConfig.from_env())
            logger.info("온톨로지 그래프 스토어: Neo4j (%s)", uri)
            return store
        except Exception as e:  # 드라이버 없음/접속 실패 등
            logger.warning(
                "Neo4j 접속 실패(%s) — in-memory 그래프 스토어로 폴백합니다.", e
            )
    else:
        logger.info(
            "OI_NEO4J_URI 미설정 — in-memory 그래프 스토어를 사용합니다."
        )

    from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore

    return InMemoryGraphStore()
