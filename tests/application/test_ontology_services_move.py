"""Task 23/33: 온톨로지 애플리케이션 서비스 (c2.application.ontology).

- builder(wargame_builder)/retrieval/writer/coa_view 는 그래프 스토어를 생성자·함수
  인자로 주입받는 DI-friendly 모듈이라 애플리케이션 계층으로 그대로 이동 가능하다.
- 애플리케이션 온톨로지 모듈은 domain + application(.ports) + stdlib 외 아무것도
  import 하지 않는다 (infrastructure/tools/ui import 시 import-linter 의
  application-no-outward 계약 위반).
"""

import importlib
import inspect


def test_public_symbols_importable_from_application():
    wb = importlib.import_module("c2.application.ontology.wargame_builder")
    assert hasattr(wb, "WargameOntologyBuilder")
    assert hasattr(wb, "WARGAME_SCENARIO_ID")
    assert hasattr(wb, "seed_entity_ids")

    retrieval = importlib.import_module("c2.application.ontology.retrieval")
    assert hasattr(retrieval, "retrieve_graph_context")
    assert hasattr(retrieval, "GraphStore")

    writer = importlib.import_module("c2.application.ontology.writer")
    assert hasattr(writer, "OntologyWriter")

    coa_view = importlib.import_module("c2.application.ontology.coa_view")
    assert hasattr(coa_view, "serialize_situation")


def test_application_ontology_modules_have_no_outward_imports():
    for modname in (
        "c2.application.ontology.wargame_builder",
        "c2.application.ontology.retrieval",
        "c2.application.ontology.writer",
        "c2.application.ontology.coa_view",
    ):
        mod = importlib.import_module(modname)
        src = inspect.getsource(mod)
        for forbidden in (
            "c2.infrastructure",
            "c2.presentation",
            "import wargame",
            "from wargame",
            "import tools",
            "from tools",
            "import ui",
            "from ui",
            "import ontology",
            "from ontology",
        ):
            assert forbidden not in src, f"{modname} 에 금지된 import 발견: {forbidden}"


def test_writer_and_pipeline_work_with_injected_in_memory_store():
    """이동된 서비스가 주입된 store 로 여전히 동작하는지(파이프라인 축소판) 확인.

    in-memory store 는 인프라 구현이지만, 테스트에서는 인프라 임포트가 허용된다
    (DI 증명이 목적 — 애플리케이션 모듈 자체가 infra를 import하지 않음을 별도로 검증).
    """
    from c2.application.ontology.coa_view import serialize_situation
    from c2.application.ontology.retrieval import retrieve_graph_context
    from c2.application.ontology.wargame_builder import (
        WARGAME_SCENARIO_ID,
        WargameOntologyBuilder,
    )
    from c2.application.ontology.writer import OntologyWriter
    from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore

    store = InMemoryGraphStore()
    builder = WargameOntologyBuilder()

    class _FakeDB:
        def get_recent_events(self, n=300):
            return []

    class _FakeEngine:
        db = _FakeDB()

        def get_state(self):
            return {
                "tick": 1,
                "game_time": 30.0,
                "units": [
                    {"id": "Alpha", "side": "BLUFOR", "unit_type": "기계화보병",
                     "x": 5000, "y": 4000, "elevation": 120, "combat_power": 95.0,
                     "status": "active", "current_action": "move", "color": "blue"},
                    {"id": "Red1", "side": "OPFOR", "unit_type": "전차",
                     "x": 20000, "y": 21000, "elevation": 200, "combat_power": 90.0,
                     "status": "active", "current_action": "hold", "color": "red"},
                ],
                "intelligence": {
                    "BLUFOR": [{"unit_id": "Red1", "status": "detected",
                                "known_x": 20000, "known_y": 21000, "unit_type": "전차",
                                "combat_power": 90.0, "detected_by": "Alpha"}],
                    "OPFOR": [],
                },
            }

    writer = OntologyWriter(_FakeEngine(), store, builder=builder)
    loaded = writer.snapshot()
    assert loaded > 0

    seeds = store.unit_entity_ids(scenario_id=WARGAME_SCENARIO_ID, side="BLUFOR")
    assert seeds == ("Alpha",)

    kg_nodes, kg_edges, evidences = retrieve_graph_context(
        store, seeds, scenario_id=WARGAME_SCENARIO_ID, node_limit=1000, edge_limit=2000
    )
    sit = serialize_situation(kg_nodes, kg_edges, evidences)
    assert sit["summary"]["blufor_units"] == 1
    assert sit["summary"]["detected_targets"] == 1
