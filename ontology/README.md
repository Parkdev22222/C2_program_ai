# C2 온톨로지 파이프라인

워게임 상태를 **prototype-ontology-intelligence(`claude/ukraine-event-scenarios-wmre56`)
브랜치와 동일한 KG 스키마**의 온톨로지로 변환하여 Neo4j에 실시간 적재하고, 그 온톨로지를
검색해 아군 방책(COA)을 생성한다.

## 데이터 흐름

```
WargameEngine.get_state() / 전투로그
        │  (이벤트 발생 시 즉시 + 주기 스냅샷)
        ▼
WargameOntologyBuilder  ──►  KnowledgeNode / KnowledgeEdge / Evidence   (동일 스키마)
        │
        ▼
OntologyWriter.ingest() ──►  Neo4jGraphStore (또는 InMemoryGraphStore 폴백)
        │
        ▼   neighborhood → edges_for_nodes → evidence_for_edges  (원본과 동일한 검색)
get_coa_situation_from_ontology()  ──►  smolagents CodeAgent  ──►  방책(COA) 생성
```

## KG 스키마 (원본 그대로)

- 노드: `(:KgNode {kg_node_id, scenario_id, entity_id, label, node_type, security_level, lat, lon, observed_at, ...})`
- 엣지: `-[:REL {kg_edge_id, relation, evidence_ids, security_level, observed_at}]->`
- 근거: `(:Evidence {...})`
- `node_type` ∈ `Unit` / `Observation` / `Event`(BattleEvent 페이로드)
- `relation` ∈ `has_observation` / `observes` / `participates_in` / `engages` / `threatens`

워게임 매핑:

| 워게임 | 온톨로지 |
|--------|----------|
| 부대(unit) | `node_type="Unit"` 앵커 노드 (entity_id=부대ID) |
| 부대 시점 관측 | `node_type="Observation"` + `[has_observation]` |
| 탐지(intelligence) | 탐지자→피탐지 `[observes]` (아군→적) |
| 전투/포격/공습 이벤트 | `node_type="Event"`(BattleEvent) + `[participates_in]` + Evidence |
| 교전(전투 이벤트의 공격자↔피격자) | 공격자→피격자 `[engages]` (적↔아군 cross-side) |
| 근접 위협(탐지된 적) | 적→최근접 아군 `[threatens]` |

`observes` / `engages` / `threatens` 는 모두 **적-아군 간 관계**로, COA 상황 조회 결과의
`force_relations` 및 `summary.engagements` / `summary.threats` 로 노출된다.

## Neo4j 연결 (환경변수)

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `OI_NEO4J_URI` | (없음) | 예: `bolt://localhost:7687`. **미설정 시 in-memory 폴백** |
| `OI_NEO4J_USER` | `neo4j` | 사용자 |
| `OI_NEO4J_PASSWORD` | `""` | 비밀번호 |

- `OI_NEO4J_URI` 가 있고 접속 가능하면 Neo4j에 적재/검색.
- 미설정이거나 접속 실패 시 자동으로 `InMemoryGraphStore` 로 폴백 (검색 semantics 동일).
- Neo4j 드라이버: `pip install neo4j` (requirements.txt 에 포함).

## 스모크 확인

```bash
python - <<'PY'
from ontology.factory import build_graph_store
from ontology.wargame_builder import WargameOntologyBuilder, WARGAME_SCENARIO_ID
from ontology.retrieval import retrieve_graph_context
store = build_graph_store()   # OI_NEO4J_URI 없으면 InMemoryGraphStore
state = {"tick":1,"game_time":60.0,"units":[
  {"id":"Alpha","side":"BLUFOR","unit_type":"기계화보병","x":5000,"y":4000,"combat_power":95.0,"status":"active","current_action":"move"}],
  "intelligence":{"BLUFOR":[],"OPFOR":[]}}
n,e,ev = WargameOntologyBuilder().build(state, [])
store.ingest(n,e,ev)
seeds = store.unit_entity_ids(scenario_id=WARGAME_SCENARIO_ID, side="BLUFOR")
print(retrieve_graph_context(store, seeds, scenario_id=WARGAME_SCENARIO_ID))
PY
```
