"""Task 12: 애플리케이션 포트 4종 정의 — 구조적 적합성(conformance) 테스트.

각 포트(typing.Protocol, @runtime_checkable)를 실제 콘크리트 클래스가
"구현 선언 없이" 구조적으로 만족하는지 검증한다.

- 로컬 자원만 필요한 클래스(InMemoryGraphStore, InMemoryConversationStore,
  WargameDB(tmp sqlite))는 실제로 인스턴스화해 isinstance() 로 검증한다.
- 외부 자원(Neo4j, PostgreSQL, vLLM 서버)이 필요한 클래스는 인스턴스화하지
  않고, 클래스 자체에 대해 issubclass() 로 메서드 존재 여부를 검증한다.
  (메서드만 있는 "non-data protocol"은 issubclass() 가 각 프로토콜 메서드명이
  클래스에 정의돼 있는지를 구조적으로 확인해준다.)
"""
import ast
import inspect
from pathlib import Path

import pytest

PORTS_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "c2" / "application" / "ports"

FORBIDDEN_IMPORT_ROOTS = {"agent", "tools", "wargame", "ontology", "ui"}


def _port_module_files():
    return sorted(p for p in PORTS_DIR.glob("*.py") if p.name != "__init__.py")


def test_port_modules_exist():
    files = _port_module_files()
    names = {p.name for p in files}
    assert names == {
        "llm.py",
        "ontology_store.py",
        "event_store.py",
        "conversation_store.py",
    }


def test_port_modules_import_only_domain_and_stdlib():
    """소스를 파싱해 최상위 import 문에 금지된 인프라 패키지가 없는지 확인."""
    for path in _port_module_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:  # relative "from . import x"
                    continue
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                assert root not in FORBIDDEN_IMPORT_ROOTS, (
                    f"{path.name} imports forbidden root '{root}' "
                    f"(application layer may only import domain/stdlib)"
                )


# ── LLMClient ──────────────────────────────────────────────────────────
def test_llm_client_port_methods():
    from c2.application.ports.llm import LLMClient

    assert set(dir(LLMClient)) >= {"chat"}


def test_vllm_server_client_satisfies_llm_client_port():
    """VLLMServerClient는 openai 패키지 없이도 import는 가능하다(지연 import).
    실제 연결이 필요하므로 인스턴스화 없이 클래스 단위 구조 검사만 수행."""
    from c2.application.ports.llm import LLMClient
    from agent.vllm_client import VLLMServerClient

    assert issubclass(VLLMServerClient, LLMClient)


# ── OntologyStore ─────────────────────────────────────────────────────
def test_ontology_store_port_methods():
    from c2.application.ports.ontology_store import OntologyStore

    expected = {
        "neighborhood", "edges_for_nodes", "evidence_for_edges",
        "merge_node", "merge_edge", "merge_evidence", "ingest",
        "unit_entity_ids", "recent_event_nodes", "reset_demo_data", "close",
    }
    assert expected <= set(dir(OntologyStore))


def test_in_memory_graph_store_isinstance_ontology_store():
    from c2.application.ports.ontology_store import OntologyStore
    from ontology.in_memory_store import InMemoryGraphStore

    store = InMemoryGraphStore()
    assert isinstance(store, OntologyStore)


def test_neo4j_graph_store_satisfies_ontology_store_port():
    """Neo4j driver/서버가 필요하므로 인스턴스화 없이 클래스 단위 구조 검사만 수행."""
    from c2.application.ports.ontology_store import OntologyStore
    from ontology.graph_store import Neo4jGraphStore

    assert issubclass(Neo4jGraphStore, OntologyStore)


# ── EventStore ─────────────────────────────────────────────────────────
def test_event_store_port_methods():
    from c2.application.ports.event_store import EventStore

    expected = {
        "save_units", "load_units", "update_unit", "save_snapshot",
        "save_unit_realtime", "get_latest_unit_states", "get_unit_history",
        "log_event", "get_recent_events", "clear",
    }
    assert expected <= set(dir(EventStore))


def test_wargame_db_isinstance_event_store(tmp_path):
    from c2.application.ports.event_store import EventStore
    from wargame.models import WargameDB

    db = WargameDB(db_path=tmp_path / "test_wargame.db")
    assert isinstance(db, EventStore)


# ── ConversationStore ─────────────────────────────────────────────────
def test_conversation_store_port_methods():
    from c2.application.ports.conversation_store import ConversationStore

    expected = {"append_turn", "recent_turns", "clear"}
    assert expected <= set(dir(ConversationStore))


def test_in_memory_conversation_store_isinstance_conversation_store():
    from c2.application.ports.conversation_store import ConversationStore
    from agent.conversation_store import InMemoryConversationStore

    store = InMemoryConversationStore()
    assert isinstance(store, ConversationStore)


def test_postgres_conversation_store_satisfies_conversation_store_port():
    """PostgreSQL 접속이 필요하므로 인스턴스화 없이 클래스 단위 구조 검사만 수행."""
    from c2.application.ports.conversation_store import ConversationStore
    from agent.conversation_store import PostgresConversationStore

    assert issubclass(PostgresConversationStore, ConversationStore)


# ── __init__.py re-export ───────────────────────────────────────────────
def test_ports_reexported_from_package_init():
    from c2.application.ports import (
        LLMClient,
        OntologyStore,
        EventStore,
        ConversationStore,
    )

    assert all(
        inspect.isclass(p) for p in (LLMClient, OntologyStore, EventStore, ConversationStore)
    )
