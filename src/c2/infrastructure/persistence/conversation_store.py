"""전술채팅 멀티턴 대화 저장소.

이전 N턴의 대화(사용자 쿼리 + 툴 호출/실행 결과 + 최종 응답)를 저장·조회한다.
한 '턴' = LangChain 메시지 리스트(HumanMessage → AIMessage(tool_calls) → ToolMessage →
AIMessage(final))를 messages_to_dict 로 직렬화한 dict 리스트.

두 가지 백엔드를 제공한다 (온톨로지 그래프 스토어와 동일한 폴백 패턴):
  - PostgresConversationStore : PostgreSQL 에 적재/조회 (환경변수로 접속)
  - InMemoryConversationStore : 프로세스 메모리 폴백 (DB 미설정/접속 실패 시)

백엔드 선택 우선순위:
  환경변수 C2_CHAT_STORE=inmemory|postgres 가 최우선.
  없으면 PG 접속정보(C2_PG_DSN 또는 C2_PG_HOST 등)가 있고 드라이버가 설치돼 있으면 Postgres,
  아니면 in-memory.

원래 `agent/conversation_store.py`에 있던 코드를 이동한 것 — `agent/conversation_store.py`는
이제 이 모듈의 구현체를 shim re-export한다. `ConversationStore` Protocol은 이 모듈에서
재정의하지 않고 `c2.application.ports.conversation_store`의 정본을 그대로 사용한다.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List

from c2.application.ports.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

_TABLE = "c2_chat_turns"


# ── In-Memory 폴백 ────────────────────────────────────────────────────
class InMemoryConversationStore:
    """프로세스 메모리 기반 대화 저장소 (세션별 턴 리스트)."""

    def __init__(self) -> None:
        self._turns: dict[str, List[list[dict]]] = {}

    def append_turn(self, session_id: str, messages: list[dict]) -> None:
        self._turns.setdefault(session_id, []).append(list(messages))

    def recent_turns(self, session_id: str, n_turns: int) -> list[list[dict]]:
        turns = self._turns.get(session_id, [])
        return [list(t) for t in turns[-n_turns:]] if n_turns > 0 else []

    def clear(self, session_id: str) -> None:
        self._turns.pop(session_id, None)


# ── PostgreSQL ────────────────────────────────────────────────────────
def _import_pg():
    """psycopg(v3) 우선, 없으면 psycopg2. 둘 다 없으면 (None, 0)."""
    try:
        import psycopg  # type: ignore
        return psycopg, 3
    except Exception:
        pass
    try:
        import psycopg2  # type: ignore
        return psycopg2, 2
    except Exception:
        return None, 0


def _pg_dsn() -> str | None:
    """환경변수에서 PostgreSQL DSN 을 구성한다. 접속정보 없으면 None."""
    dsn = os.environ.get("C2_PG_DSN")
    if dsn:
        return dsn
    host = os.environ.get("C2_PG_HOST")
    if not host:
        return None
    parts = [f"host={host}"]
    parts.append(f"port={os.environ.get('C2_PG_PORT', '5432')}")
    parts.append(f"dbname={os.environ.get('C2_PG_DB', 'c2')}")
    parts.append(f"user={os.environ.get('C2_PG_USER', 'postgres')}")
    pw = os.environ.get("C2_PG_PASSWORD")
    if pw:
        parts.append(f"password={pw}")
    return " ".join(parts)


class PostgresConversationStore:
    """PostgreSQL 기반 대화 저장소. 턴을 행 단위로 적재하고 최신 N턴을 조회한다."""

    def __init__(self, dsn: str) -> None:
        driver, ver = _import_pg()
        if driver is None:
            raise RuntimeError("psycopg / psycopg2 미설치 — PostgreSQL 사용 불가")
        self._driver = driver
        self._dsn = dsn
        self._conn = driver.connect(dsn)
        self._conn.autocommit = True
        self._ensure_table()
        logger.warning("[대화메모리] PostgreSQL 연결 완료 (psycopg v%d)", ver)

    def _cursor(self):
        # 끊긴 연결이면 재연결 시도
        try:
            if getattr(self._conn, "closed", False):
                self._conn = self._driver.connect(self._dsn)
                self._conn.autocommit = True
        except Exception:
            self._conn = self._driver.connect(self._dsn)
            self._conn.autocommit = True
        return self._conn.cursor()

    def _ensure_table(self) -> None:
        with self._cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
                "  id BIGSERIAL PRIMARY KEY,"
                "  session_id TEXT NOT NULL,"
                "  turn_index INTEGER NOT NULL,"
                "  created_at TIMESTAMPTZ DEFAULT now(),"
                "  messages TEXT NOT NULL"
                ")"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_session "
                f"ON {_TABLE} (session_id, turn_index)"
            )

    def append_turn(self, session_id: str, messages: list[dict]) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(
                    f"SELECT COALESCE(MAX(turn_index), -1) + 1 FROM {_TABLE} WHERE session_id = %s",
                    (session_id,),
                )
                next_idx = cur.fetchone()[0]
                cur.execute(
                    f"INSERT INTO {_TABLE} (session_id, turn_index, messages) VALUES (%s, %s, %s)",
                    (session_id, next_idx, json.dumps(messages, ensure_ascii=False)),
                )
        except Exception as e:
            logger.warning("[대화메모리] append_turn 실패(무시): %s", e)

    def recent_turns(self, session_id: str, n_turns: int) -> list[list[dict]]:
        if n_turns <= 0:
            return []
        try:
            with self._cursor() as cur:
                cur.execute(
                    f"SELECT messages FROM {_TABLE} WHERE session_id = %s "
                    f"ORDER BY turn_index DESC LIMIT %s",
                    (session_id, n_turns),
                )
                rows = cur.fetchall()
            # 최신 우선 조회 → 시간순(오래된 것 먼저)으로 되돌림
            return [json.loads(r[0]) for r in reversed(rows)]
        except Exception as e:
            logger.warning("[대화메모리] recent_turns 실패(무시): %s", e)
            return []

    def clear(self, session_id: str) -> None:
        try:
            with self._cursor() as cur:
                cur.execute(f"DELETE FROM {_TABLE} WHERE session_id = %s", (session_id,))
        except Exception as e:
            logger.warning("[대화메모리] clear 실패(무시): %s", e)


# ── 팩토리 ────────────────────────────────────────────────────────────
def build_conversation_store() -> ConversationStore:
    """환경설정에 따라 Postgres 또는 in-memory 대화 저장소를 만든다."""
    backend = (os.environ.get("C2_CHAT_STORE") or "").strip().lower()
    dsn = _pg_dsn()

    if backend == "inmemory":
        logger.warning("[대화메모리] in-memory 저장소 사용 (C2_CHAT_STORE=inmemory)")
        return InMemoryConversationStore()

    if backend == "postgres" or (backend == "" and dsn):
        if not dsn:
            logger.warning("[대화메모리] C2_CHAT_STORE=postgres 지만 접속정보 없음 → in-memory 폴백")
            return InMemoryConversationStore()
        try:
            return PostgresConversationStore(dsn)
        except Exception as e:
            logger.warning("[대화메모리] PostgreSQL 연결 실패 → in-memory 폴백: %s", e)
            return InMemoryConversationStore()

    logger.warning("[대화메모리] in-memory 저장소 사용 (PostgreSQL 접속정보 없음)")
    return InMemoryConversationStore()
