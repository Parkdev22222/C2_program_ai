"""Task 15: 대화 저장소 이동 — c2.infrastructure.persistence.conversation_store + shim 검증.

- 새 경로(c2.infrastructure.persistence.conversation_store)에서 구현체가 임포트 가능한지 확인.
- 옛 경로(agent.conversation_store)가 새 경로와 동일 객체(identity)를 재노출하는 shim인지 확인.
- ConversationStore Protocol이 c2.application.ports.conversation_store의 정본과 통일됐는지 확인.
- 실제 동작(round-trip) 및 psycopg 미설치 시 in-memory 폴백 확인.
"""


# ── 새 경로에서 임포트 가능 ────────────────────────────────────────────
def test_conversation_store_importable_from_new_path():
    from c2.infrastructure.persistence.conversation_store import (
        ConversationStore,
        InMemoryConversationStore,
        PostgresConversationStore,
        build_conversation_store,
    )

    assert ConversationStore is not None
    assert InMemoryConversationStore is not None
    assert PostgresConversationStore is not None
    assert callable(build_conversation_store)


def test_conversation_store_protocol_unified_with_canonical_port():
    """새 인프라 모듈은 Protocol을 재정의하지 않고 정본 포트를 그대로 사용해야 한다."""
    from c2.application.ports.conversation_store import ConversationStore as PortProtocol
    from c2.infrastructure.persistence.conversation_store import (
        ConversationStore as InfraProtocol,
    )

    assert InfraProtocol is PortProtocol


# ── 옛 경로(agent.conversation_store)는 shim ──────────────────────────
def test_conversation_store_shim_identity():
    import agent.conversation_store as old
    import c2.infrastructure.persistence.conversation_store as new

    assert old.ConversationStore is new.ConversationStore
    assert old.InMemoryConversationStore is new.InMemoryConversationStore
    assert old.PostgresConversationStore is new.PostgresConversationStore
    assert old.build_conversation_store is new.build_conversation_store


def test_shim_conversation_store_is_canonical_port():
    """agent.conversation_store.ConversationStore가 정본 포트와 동일 객체여야 한다."""
    from c2.application.ports.conversation_store import ConversationStore as PortProtocol
    import agent.conversation_store as old

    assert old.ConversationStore is PortProtocol


# ── 포트 정합성 + 기능 round-trip ──────────────────────────────────────
def test_in_memory_conversation_store_satisfies_port_and_round_trips():
    from c2.application.ports.conversation_store import ConversationStore
    from c2.infrastructure.persistence.conversation_store import InMemoryConversationStore

    store = InMemoryConversationStore()
    assert isinstance(store, ConversationStore)

    session_id = "s1"
    turn = [{"role": "human", "content": "hello"}, {"role": "ai", "content": "hi"}]
    store.append_turn(session_id, turn)
    store.append_turn(session_id, [{"role": "human", "content": "again"}])

    recent = store.recent_turns(session_id, 5)
    assert len(recent) == 2
    assert recent[0] == turn

    store.clear(session_id)
    assert store.recent_turns(session_id, 5) == []


# ── psycopg/postgres 미설정 시 in-memory 폴백 ──────────────────────────
def test_build_conversation_store_falls_back_to_in_memory(monkeypatch):
    from c2.infrastructure.persistence.conversation_store import (
        InMemoryConversationStore,
        build_conversation_store,
    )

    for key in ("C2_CHAT_STORE", "C2_PG_DSN", "C2_PG_HOST"):
        monkeypatch.delenv(key, raising=False)

    store = build_conversation_store()
    assert isinstance(store, InMemoryConversationStore)

    # 기능 smoke: 폴백 스토어도 정상 동작
    store.append_turn("x", [{"role": "human", "content": "test"}])
    assert len(store.recent_turns("x", 1)) == 1
