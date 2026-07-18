"""검색된 KG(노드/엣지/근거)를 COA 생성용 상황 dict로 직렬화.

smolagents 등 무거운 의존성이 없어 단독 테스트가 가능하다. tools.ontology_query_tool
이 이 함수를 사용해 에이전트에 상황을 반환한다.
"""

from __future__ import annotations


def serialize_situation(kg_nodes, kg_edges, evidences) -> dict:
    """Unit/Observation/Event 노드와 observes/participates_in 엣지를 상황 dict로 변환."""
    unit_nodes = {n.entity_id: n for n in kg_nodes if n.node_type == "Unit"}

    # 부대별 최신 Observation
    latest_obs: dict = {}
    for n in kg_nodes:
        if n.node_type != "Observation":
            continue
        cur = latest_obs.get(n.entity_id)
        if cur is None or (n.observed_at or "") >= (cur.observed_at or ""):
            latest_obs[n.entity_id] = n

    units = []
    for eid, un in unit_nodes.items():
        obs = latest_obs.get(eid)
        p = un.properties           # Unit 앵커: 매 스냅샷 최신 상태로 MERGE 갱신됨
        op = obs.properties if obs else {}

        def _cur(key):
            # 앵커(현재값) 우선, 없으면 최신 Observation 값으로 폴백
            v = p.get(key)
            return v if v is not None else op.get(key)

        units.append(
            {
                "unit_id": eid,
                "side": p.get("side", ""),
                "affiliation": p.get("affiliation", ""),
                "unit_type": p.get("unit_type", ""),
                "lat": un.lat if un.lat is not None else (obs.lat if obs else None),
                "lon": un.lon if un.lon is not None else (obs.lon if obs else None),
                "x_m": _cur("x_m"),
                "y_m": _cur("y_m"),
                "combat_power": _cur("combat_power"),
                "status": _cur("status") or "",
                "current_action": _cur("current_action") or "",
                # 이동 방향(8방위)·속도 — 적 부대 기동 방향 인지용
                "heading": _cur("heading"),
                "speed_mps": _cur("speed_mps"),
                "observed_at": (obs.observed_at if obs else un.observed_at),
            }
        )

    def _uid(node_id: str) -> str:
        return node_id.replace("KGN-UNIT-", "")

    # 부대ID → 병종/진영 조회 (관계에 적 부대 type 을 인라인 반영하기 위함)
    _type_by_id = {u["unit_id"]: u.get("unit_type", "") for u in units}
    _side_by_id = {u["unit_id"]: u.get("side", "") for u in units}

    # 적↔아군 관계 (observes=탐지 / engages=교전 / threatens=위협).
    # 틱마다 같은 (source,target,relation) 쌍이 반복 적재되므로 중복은 최신 1건만 유지
    # → 관계 목록이 턴 수에 비례해 늘지 않도록 압축.
    # observes=탐지 / engages=교전 / threatens=위협 / friendly_fire=아군 오사 / advances_toward=적 접근
    _FORCE_RELS = {"observes", "engages", "threatens", "friendly_fire", "advances_toward"}
    _latest_rel: dict = {}
    for e in kg_edges:
        if e.relation not in _FORCE_RELS:
            continue
        src, tgt = _uid(e.source_node_id), _uid(e.target_node_id)
        key = (src, tgt, e.relation)
        cur = _latest_rel.get(key)
        if cur is None or (e.observed_at or "") >= (cur["observed_at"] or ""):
            _latest_rel[key] = {
                "source": src,
                "source_type": _type_by_id.get(src, ""),
                "source_side": _side_by_id.get(src, ""),
                "target": tgt,
                "target_type": _type_by_id.get(tgt, ""),   # 적 부대 병종(자주포/기계화보병 등)
                "target_side": _side_by_id.get(tgt, ""),
                "relation": e.relation,
                "observed_at": e.observed_at,
            }
    force_relations = sorted(
        _latest_rel.values(), key=lambda r: r.get("observed_at") or ""
    )

    # 탐지 관계 (observes) — 위 압축 결과에서 파생 (탐지 대상 병종 포함)
    detections = [
        {
            "observer": r["source"],
            "target": r["target"],
            "target_type": r["target_type"],   # 탐지된 적 부대 병종
            "target_side": r["target_side"],
            "observed_at": r["observed_at"],
        }
        for r in force_relations
        if r["relation"] == "observes"
    ]

    # 전투 이벤트 (Event 노드 — BattleEvent 페이로드)
    events = []
    for n in kg_nodes:
        if n.node_type != "Event":
            continue
        p = n.properties
        events.append(
            {
                "event_id": n.entity_id,
                "name": p.get("title") or n.label,
                "event_type": p.get("event_type", ""),
                "sub_event_type": p.get("sub_event_type", ""),
                "actor1": p.get("actor1_name", ""),
                "actor2": p.get("actor2_name", ""),
                "fatalities": p.get("fatalities", 0),
                "lat": n.lat,
                "lon": n.lon,
                "observed_at": n.observed_at,
                "notes": p.get("notes", ""),
            }
        )
    events.sort(key=lambda e: e.get("observed_at") or "")

    evidence = [{"evidence_id": ev.evidence_id, "text": ev.text} for ev in evidences]

    blufor = [u for u in units if u["side"] == "BLUFOR"]
    detected_targets = {d["target"] for d in detections}
    engagements = [r for r in force_relations if r["relation"] == "engages"]
    threats = [r for r in force_relations if r["relation"] == "threatens"]
    friendly_fire = [r for r in force_relations if r["relation"] == "friendly_fire"]
    advancing = [r for r in force_relations if r["relation"] == "advances_toward"]
    # 탐지된 적 부대 요약 (병종 포함) — 아군 방책 판단 시 적 편성 인지용
    detected_enemies = [
        {
            "unit_id": u["unit_id"],
            "unit_type": u.get("unit_type", ""),
            "combat_power": u.get("combat_power"),
            "status": u.get("status", ""),
            "lat": u.get("lat"),
            "lon": u.get("lon"),
        }
        for u in units
        if u["unit_id"] in detected_targets and u["side"] != "BLUFOR"
    ]
    return {
        "units": units,
        "detections": detections,
        "detected_enemies": detected_enemies,
        # 적↔아군 관계: observes(탐지) / engages(교전) / threatens(위협)
        "force_relations": force_relations[-60:],
        "events": events[-30:],
        "evidence": evidence[-30:],
        "summary": {
            "blufor_units": len(blufor),
            "detected_targets": len(detected_targets),
            "recent_events": len(events),
            "engagements": len(engagements),
            "threats": len(threats),
            # 아군 오사(아군 화력에 의한 아군 피해) / 접근 중인 적 부대 수
            "friendly_fire_incidents": len(friendly_fire),
            "advancing_enemies": len(advancing),
        },
    }
