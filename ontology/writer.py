"""워게임 → 온톨로지 실시간 적재기.

주기 스냅샷 스레드가 일정 간격으로 WargameEngine 상태와 전투 로그를 읽어 동일 스키마
KG로 변환·MERGE 한다. 탐지/전투/피격 등 이벤트 발생 시에는 UI 핸들러가 ``flush_now()``
를 호출해 즉시 반영할 수 있다(사용자 지정: 이벤트 + 주기 스냅샷).

그래프 스토어는 Neo4jGraphStore 또는 InMemoryGraphStore(폴백) 어느 쪽이든 동일한
``ingest`` / ``reset_demo_data`` 인터페이스를 갖는다.
"""

from __future__ import annotations

import logging
import threading

from ontology.wargame_builder import WargameOntologyBuilder

logger = logging.getLogger(__name__)


class OntologyWriter:
    def __init__(
        self,
        engine,
        graph_store,
        *,
        interval: float = 2.0,
        scenario_id: str | None = None,
        builder: WargameOntologyBuilder | None = None,
    ) -> None:
        self.engine = engine
        self.graph_store = graph_store
        self.interval = float(interval)
        self.builder = builder or (
            WargameOntologyBuilder(scenario_id)
            if scenario_id
            else WargameOntologyBuilder()
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()  # 이벤트 발생 시 즉시 스냅샷 트리거
        self._thread: threading.Thread | None = None

    # -- 스냅샷 1회 ----------------------------------------------------
    def snapshot(self) -> int:
        """현재 상태·전투로그를 KG로 변환해 적재. 적재된 (노드+엣지) 수 반환."""
        engine = self.engine
        if engine is None:
            return 0
        with self._lock:
            try:
                state = engine.get_state()
                events = []
                try:
                    # 스냅샷 간 다수의 교전/포격 이벤트가 누락되지 않도록 충분히 넓게 조회
                    # (빌더가 _seen_event_keys 로 중복 적재를 막으므로 넉넉히 가져와도 안전)
                    events = engine.db.get_recent_events(n=300)
                except Exception:
                    events = []
                nodes, edges, evidences = self.builder.build(state, events)
                self.graph_store.ingest(nodes, edges, evidences)
                return len(nodes) + len(edges)
            except Exception as e:  # 적재 실패가 시뮬레이션을 막지 않도록
                logger.warning("온톨로지 스냅샷 적재 실패: %s", e)
                return 0

    def flush_now(self) -> None:
        """즉시(동기) 반영 — 호출 스레드에서 스냅샷을 수행한다."""
        self.snapshot()

    def request_flush(self) -> None:
        """비동기 즉시 반영 — 엔진 틱 스레드 등에서 호출(논블로킹).

        writer 스레드를 깨워 스냅샷을 수행하게 하므로 호출자 스레드를 막지 않는다.
        """
        self._wake.set()

    # -- 주기 스냅샷 스레드 (주기 + 이벤트 wake) ----------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            # 주기(interval) 경과 또는 이벤트 wake 중 먼저 오는 쪽에서 스냅샷
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.snapshot()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._loop, name="OntologyWriter", daemon=True
        )
        self._thread.start()
        logger.info(
            "OntologyWriter 시작 (interval=%.1fs, store=%s)",
            self.interval,
            type(self.graph_store).__name__,
        )

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()  # 대기 중인 loop 를 즉시 깨워 종료
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1.0)
            self._thread = None

    # -- 시뮬레이션 리셋 시 그래프 초기화 -----------------------------
    def reset(self, *, wipe: bool = True) -> None:
        with self._lock:
            self.builder = WargameOntologyBuilder(self.builder.scenario_id)
            if wipe:
                try:
                    self.graph_store.reset_demo_data()
                except Exception as e:
                    logger.warning("온톨로지 그래프 초기화 실패: %s", e)
