"""워게임 상태 → 온톨로지(동일 스키마) 변환기.

WargameEngine.get_state() 스냅샷과 전투 이벤트 로그를, prototype-ontology-intelligence
(claude/ukraine-event-scenarios-wmre56) 브랜치와 동일한 KG 스키마
(KnowledgeNode / KnowledgeEdge / Evidence, node_type ∈ Unit/Observation/Event,
Event 노드는 BattleEvent 페이로드)로 변환한다.

매핑 개요
---------
- 부대(unit)             → node_type="Unit" 앵커 노드(entity_id=부대ID)
- 부대 시점 관측         → node_type="Observation" 노드 + [has_observation] 엣지
- 탐지(intelligence)     → 탐지자→피탐지 부대 [observes] 엣지
- 전투/포격/공습 이벤트  → node_type="Event" 노드(BattleEvent 페이로드)
                           + 관여 부대 [participates_in] 엣지 + Evidence(근거)

좌표는 tools.coord_utils.xy_to_latlon 으로 위경도(WGS84)로 변환한다(원본이 lat/lon 기반).
observed_at 은 시나리오 에폭 + game_time(초) 로 만든 ISO-8601 타임스탬프.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from ontology.models import BattleEvent, Evidence, KnowledgeEdge, KnowledgeNode

# 좌표 변환(엔진 미터 → WGS84 위경도) — tools.coord_utils 와 동일한 기준점.
# ontology 패키지가 tools 패키지(무거운 __init__)에 의존하지 않도록 인라인한다.
_REF_LAT = 38.0   # 철원 지역 (DMZ 인근)
_REF_LON = 127.0
_METERS_PER_DEG_LAT = 111000.0
_METERS_PER_DEG_LON = 111000.0 * math.cos(math.radians(_REF_LAT))


def xy_to_latlon(x_m: float, y_m: float) -> tuple:
    """(x_m, y_m) 미터 → (lat, lon) 위경도. tools.coord_utils.xy_to_latlon 과 동일."""
    lat = round(_REF_LAT + (y_m or 0) / _METERS_PER_DEG_LAT, 6)
    lon = round(_REF_LON + (x_m or 0) / _METERS_PER_DEG_LON, 6)
    return lat, lon


WARGAME_SCENARIO_ID = "SCN-WARGAME-C2"

# game_time(게임초)를 절대 시각으로 환산할 때 기준이 되는 에폭.
_EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)

_SIDE_TO_AFFILIATION = {"BLUFOR": "friendly", "OPFOR": "enemy"}

# 워게임 한글 병과 → ACLED 스타일 branch (Event branch 추론용)
_UNIT_TYPE_BRANCH = {
    "기계화보병": "infantry",
    "전차": "armor",
    "정찰": "recon",
    "대전차": "infantry",
    "자주포": "artillery",
}


def _iso(game_time: float) -> str:
    """게임초 → ISO-8601 UTC 타임스탬프(초 단위)."""
    ts = _EPOCH + timedelta(seconds=float(game_time or 0))
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_node_properties(event: BattleEvent) -> dict:
    """KG Event 노드 페이로드 — 원본 _event_node_properties 와 동일 규칙."""
    props = asdict(event)
    props["node_type"] = "Event"
    props["title"] = event.name
    props["latitude"] = event.latitude
    props["longitude"] = event.longitude
    return props


# ---------------------------------------------------------------------------
# 이벤트 메시지 파서 (tools/wargame_query_tool 의 포맷과 동일)
# ---------------------------------------------------------------------------
def _parse_event_actors(event_type: str, msg: str) -> tuple[str | None, str | None]:
    """전투 이벤트 메시지에서 (공격자, 피격자) 부대ID 추출. 없으면 None."""
    if event_type in ("COMBAT", "SURPRISE"):
        # "{attacker}({type})→{defender}({type}): ..."
        m = re.search(r"(\w+)\([^)]*\)\s*→\s*(\w+)\(", msg)
        if m:
            return m.group(1), m.group(2)
    elif event_type == "INDIRECT":
        # "{spg}(자주포) 간접사격 → {defender}: ..."
        m = re.search(r"(\w+)\([^)]*\)\s*간접사격\s*→\s*(\w+)", msg)
        if m:
            return m.group(1), m.group(2)
    elif event_type == "AIR_STRIKE":
        # "[SIDE] {call_sign}→{unit_id}: ..."
        m = re.search(r"(\S+)\s*→\s*(\w+)", msg)
        if m:
            return m.group(1), m.group(2)
    elif event_type == "DESTROYED":
        # "{unit_id}(...) 파괴/제거 ..." — 피격자만
        m = re.search(r"(\w+)", msg)
        if m:
            return None, m.group(1)
    return None, None


_EVENT_TYPE_MAP = {
    "COMBAT": ("전투", "무력 충돌"),
    "SURPRISE": ("전투", "기습/조우전"),
    "INDIRECT": ("폭발/원격 공격", "포격/포병/미사일 공격"),
    "AIR_STRIKE": ("폭발/원격 공격", "공습/드론 공격"),
    "DESTROYED": ("전투", "장비/부대 파괴"),
}


class WargameOntologyBuilder:
    """워게임 상태를 KG 노드/엣지/근거로 변환한다.

    부대 앵커(Unit) 노드는 세션 내 안정적이므로 매 스냅샷 재-MERGE 되고,
    Observation/Event 노드는 tick·event 별로 고유 ID를 갖는다.
    """

    def __init__(self, scenario_id: str = WARGAME_SCENARIO_ID) -> None:
        self.scenario_id = scenario_id
        self._seen_event_keys: set[str] = set()

    # -- 부대 앵커 노드 ------------------------------------------------
    def _unit_node(self, u: dict) -> KnowledgeNode:
        uid = u["id"]
        lat, lon = xy_to_latlon(u.get("x", 0), u.get("y", 0))
        return KnowledgeNode(
            kg_node_id=f"KGN-UNIT-{uid}",
            scenario_id=self.scenario_id,
            entity_id=uid,
            label=f"{uid} ({u.get('unit_type', '')})".strip(),
            node_type="Unit",
            lat=lat,
            lon=lon,
            observed_at=None,  # 안정적 앵커 (시간 필터에서 항상 유지)
            properties={
                "side": u.get("side", ""),
                "affiliation": _SIDE_TO_AFFILIATION.get(u.get("side", ""), "neutral"),
                "unit_type": u.get("unit_type", ""),
                "branch": _UNIT_TYPE_BRANCH.get(u.get("unit_type", ""), "infantry"),
                "color": u.get("color", ""),
            },
        )

    # -- 부대 시점 관측 노드 + has_observation 엣지 -------------------
    def _observation(
        self, u: dict, tick: int, iso: str
    ) -> tuple[KnowledgeNode, KnowledgeEdge]:
        uid = u["id"]
        lat, lon = xy_to_latlon(u.get("x", 0), u.get("y", 0))
        obs_id = f"KGN-OBS-{uid}-{tick}"
        node = KnowledgeNode(
            kg_node_id=obs_id,
            scenario_id=self.scenario_id,
            entity_id=uid,
            label=f"{uid} 관측 @tick {tick}",
            node_type="Observation",
            lat=lat,
            lon=lon,
            observed_at=iso,
            properties={
                "side": u.get("side", ""),
                "unit_type": u.get("unit_type", ""),
                "combat_power": u.get("combat_power"),
                "status": u.get("status", ""),
                "current_action": u.get("current_action", ""),
                "x_m": u.get("x"),
                "y_m": u.get("y"),
                "elevation": u.get("elevation"),
                "tick": tick,
            },
        )
        edge = KnowledgeEdge(
            kg_edge_id=f"KGE-OBS-{uid}-{tick}",
            scenario_id=self.scenario_id,
            source_node_id=f"KGN-UNIT-{uid}",
            target_node_id=obs_id,
            relation="has_observation",
            observed_at=iso,
        )
        return node, edge

    # -- 탐지 → observes 엣지 -----------------------------------------
    def _detection_edges(self, state: dict, iso: str) -> list[KnowledgeEdge]:
        edges: list[KnowledgeEdge] = []
        intel = state.get("intelligence", {}) or {}
        for observer_side, entries in intel.items():
            for e in entries or []:
                detected_by = e.get("detected_by")
                target = e.get("unit_id")
                if not detected_by or not target:
                    continue
                edges.append(
                    KnowledgeEdge(
                        kg_edge_id=f"KGE-OBSV-{detected_by}-{target}-{state.get('tick', 0)}",
                        scenario_id=self.scenario_id,
                        source_node_id=f"KGN-UNIT-{detected_by}",
                        target_node_id=f"KGN-UNIT-{target}",
                        relation="observes",
                        observed_at=iso,
                    )
                )
        return edges

    # -- 전투 이벤트 → Event 노드 + participates_in 엣지 + Evidence ----
    def _event_records(
        self, events: list[dict], units_by_id: dict[str, dict]
    ) -> tuple[list[KnowledgeNode], list[KnowledgeEdge], list[Evidence]]:
        nodes: list[KnowledgeNode] = []
        edges: list[KnowledgeEdge] = []
        evidences: list[Evidence] = []

        for ev in events:
            etype = ev.get("event_type", "")
            if etype not in _EVENT_TYPE_MAP:
                continue
            tick = ev.get("tick", 0)
            msg = ev.get("message", "")
            key = str(ev.get("id", f"{tick}-{hash(msg) & 0xFFFF}"))
            if key in self._seen_event_keys:
                continue
            self._seen_event_keys.add(key)

            iso = _iso(ev.get("game_time", 0))
            event_type_ko, sub_ko = _EVENT_TYPE_MAP[etype]
            attacker, defender = _parse_event_actors(etype, msg)
            involved = [
                uid for uid in (attacker, defender) if uid and uid in units_by_id
            ]

            # 이벤트 좌표는 피격자(없으면 공격자) 위치 사용
            anchor_uid = defender if defender in units_by_id else attacker
            anchor = units_by_id.get(anchor_uid or "", {})
            lat, lon = (
                xy_to_latlon(anchor.get("x", 0), anchor.get("y", 0))
                if anchor
                else (None, None)
            )

            att_side = units_by_id.get(attacker or "", {}).get("side", "")
            def_side = units_by_id.get(defender or "", {}).get("side", "")

            event_id = f"EVT-{key}"
            date_str = _iso(ev.get("game_time", 0))[:10]
            battle_event = BattleEvent(
                event_id=event_id,
                scenario_id=self.scenario_id,
                name=f"{event_type_ko} ({date_str})",
                event_type=event_type_ko,
                sub_event_type=sub_ko,
                disorder_type="정치적 폭력",
                event_date=date_str,
                actor1_name=attacker or "",
                actor2_name=defender or "",
                actor2_assoc=def_side,
                location_name=anchor_uid or "",
                latitude=lat,
                longitude=lon,
                geo_precision=1,
                time_precision=1,
                notes=msg,
                sources="wargame-simulator",
                tags=f"{att_side}->{def_side}" if att_side or def_side else "",
            )
            node_id = f"KGN-EVT-{key}"
            nodes.append(
                KnowledgeNode(
                    kg_node_id=node_id,
                    scenario_id=self.scenario_id,
                    entity_id=event_id,
                    label=battle_event.name,
                    node_type="Event",
                    lat=lat,
                    lon=lon,
                    observed_at=iso,
                    properties=_event_node_properties(battle_event),
                )
            )

            # participates_in 엣지 + 근거(Evidence)
            edge_ids: list[str] = []
            for uid in involved:
                edge_id = f"KGE-PART-{key}-{uid}"
                edge_ids.append(edge_id)
                edges.append(
                    KnowledgeEdge(
                        kg_edge_id=edge_id,
                        scenario_id=self.scenario_id,
                        source_node_id=f"KGN-UNIT-{uid}",
                        target_node_id=node_id,
                        relation="participates_in",
                        evidence_ids=(f"EVD-{key}",),
                        observed_at=iso,
                    )
                )
            evidences.append(
                Evidence(
                    evidence_id=f"EVD-{key}",
                    scenario_id=self.scenario_id,
                    evidence_type="document_chunk",
                    source_id=node_id,
                    text=msg,
                    entity_ids=tuple(involved) + (event_id,),
                    kg_edge_ids=tuple(edge_ids),
                )
            )

        return nodes, edges, evidences

    # -- 공개 진입점 ---------------------------------------------------
    def build(
        self, state: dict, events: list[dict] | None = None
    ) -> tuple[list[KnowledgeNode], list[KnowledgeEdge], list[Evidence]]:
        """상태 스냅샷(+선택적 전투로그)을 (노드, 엣지, 근거)로 변환한다."""
        nodes: list[KnowledgeNode] = []
        edges: list[KnowledgeEdge] = []
        evidences: list[Evidence] = []

        tick = state.get("tick", 0)
        iso = _iso(state.get("game_time", 0))
        units = state.get("units", []) or []
        units_by_id = {u["id"]: u for u in units}

        for u in units:
            nodes.append(self._unit_node(u))
            obs_node, obs_edge = self._observation(u, tick, iso)
            nodes.append(obs_node)
            edges.append(obs_edge)

        edges.extend(self._detection_edges(state, iso))

        if events:
            ev_nodes, ev_edges, ev_evidences = self._event_records(events, units_by_id)
            nodes.extend(ev_nodes)
            edges.extend(ev_edges)
            evidences.extend(ev_evidences)

        return nodes, edges, evidences


def seed_entity_ids(state: dict, *, side: str = "BLUFOR") -> tuple[str, ...]:
    """COA 검색 seed용 entity_id — 지정 측 부대 + 그 측이 탐지한 적 부대."""
    ids: list[str] = []
    for u in state.get("units", []) or []:
        if u.get("side") == side:
            ids.append(u["id"])
    for e in (state.get("intelligence", {}) or {}).get(side, []) or []:
        uid = e.get("unit_id")
        if uid:
            ids.append(uid)
    return tuple(dict.fromkeys(ids))
