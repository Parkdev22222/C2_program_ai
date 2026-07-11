"""Core domain objects for battlefield KG-backed RAG.

These dataclasses intentionally avoid any database, web framework, or model-provider
imports so the domain model stays portable across adapters.

원본: prototype-ontology-intelligence(claude/ukraine-event-scenarios-wmre56)
      src/core/domain/models.py — 스키마를 동일하게 유지하기 위해 그대로 이식한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SecurityLevel = Literal["unclassified", "restricted", "secret"]


@dataclass(frozen=True)
class UserContext:
    user_id: str
    roles: tuple[str, ...]
    clearance: SecurityLevel = "restricted"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    description: str


@dataclass(frozen=True)
class Entity:
    entity_id: str
    scenario_id: str
    name: str
    entity_type: str
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class GeoObject:
    geo_object_id: str
    scenario_id: str
    entity_id: str
    name: str
    geometry: dict[str, Any]
    symbol: str
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class Report:
    report_id: str
    scenario_id: str
    title: str
    document_id: str
    summary: str
    mentioned_entity_ids: tuple[str, ...]
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class Document:
    document_id: str
    scenario_id: str
    title: str
    source_uri: str
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    scenario_id: str
    text: str
    page: int
    mentioned_entity_ids: tuple[str, ...]
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class KnowledgeNode:
    kg_node_id: str
    scenario_id: str
    entity_id: str
    label: str
    node_type: str
    security_level: SecurityLevel = "restricted"
    # Spatiotemporal axis: where (lat/lon) and when (ISO-8601 string) this node was observed.
    # Optional so legacy/static nodes without a fix keep working.
    lat: float | None = None
    lon: float | None = None
    observed_at: str | None = None
    # Event/API-specific and other KG adapter attributes. Kept as an open map so
    # KG nodes can carry source fields without creating scenario-specific tables.
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeEdge:
    kg_edge_id: str
    scenario_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    security_level: SecurityLevel = "restricted"
    # When this relation was observed (ISO-8601 string); enables time-ordered traversal.
    observed_at: str | None = None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    scenario_id: str
    evidence_type: Literal["document_chunk", "knowledge_edge", "geo_object"]
    source_id: str
    text: str
    entity_ids: tuple[str, ...]
    geo_object_ids: tuple[str, ...] = field(default_factory=tuple)
    kg_edge_ids: tuple[str, ...] = field(default_factory=tuple)
    document_id: str | None = None
    chunk_id: str | None = None
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class Citation:
    citation_id: str
    evidence_id: str
    label: str
    document_id: str | None = None
    chunk_id: str | None = None
    geo_object_ids: tuple[str, ...] = field(default_factory=tuple)
    kg_edge_ids: tuple[str, ...] = field(default_factory=tuple)
    # Full evidence text (label is only an 80-char preview). Lets the UI show
    # the untruncated source text on demand. Defaults empty for compatibility.
    text: str = ""


@dataclass(frozen=True)
class SentenceCitation:
    """One answer sentence tied to the citations that ground it (XAI trail).

    ``citation_ids`` reference ``Citation.citation_id`` values on the same
    ``SupportedAnswer`` so the UI can render an evidence popup next to each
    sentence. An empty tuple means the sentence has no matched supporting
    evidence.
    """

    text: str
    citation_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SupportedAnswer:
    answer: str
    citations: tuple[Citation, ...]
    map_highlights: tuple[str, ...]
    related_document_ids: tuple[str, ...]
    related_kg_edge_ids: tuple[str, ...]
    # Per-sentence grounding for the inline evidence-popup UX. Optional and
    # defaulted so existing constructors/serialization stay backward compatible.
    sentence_citations: tuple[SentenceCitation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RetrievalEvidence:
    """One piece of retrieved evidence: its id and the full source text (원문)."""

    evidence_id: str
    text: str = ""


@dataclass(frozen=True)
class RetrievalSnapshot:
    """What a single turn retrieved, kept so a past answer's grounding is traceable.

    Stored alongside each conversation turn (as JSONB in the persistent backend) so
    the UI (or a future LLM coreference step) can see what a past answer was grounded
    on — including the retrieved source text, not just ids.
    """

    # Entity ids the turn surfaced (KG nodes + map nodes).
    entity_ids: tuple[str, ...] = field(default_factory=tuple)
    # KG node ids retrieved for the turn — kept for explainability / debugging.
    kg_node_ids: tuple[str, ...] = field(default_factory=tuple)
    # Retrieved evidence the answer cited, each with its id AND full source text, so
    # the RAG-retrieved 원문 is persisted alongside the turn (not only referenced).
    evidences: tuple[RetrievalEvidence, ...] = field(default_factory=tuple)
    # The query text the turn ran with (the user's question for now).
    search_query: str = ""

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """Just the evidence ids (derived from ``evidences``)."""

        return tuple(item.evidence_id for item in self.evidences)


@dataclass(frozen=True)
class ConversationTurn:
    """One question/answer exchange plus the retrieval snapshot that grounded it.

    These are the rows loaded/saved by the conversation store: the recent window is
    fed back into the next turn (history in the prompt + anaphora seed).
    """

    question: str  # the user's question for this turn
    answer: str  # the model's answer text (citations stripped)
    # What retrieval produced for this turn (see RetrievalSnapshot).
    retrieval: RetrievalSnapshot = field(default_factory=RetrievalSnapshot)
    # Kind of turn: "nl" for a natural-language assess Q/A, "situation" for a map
    # object situation lookup. Only "nl" turns feed the generation prompt, but a
    # "situation" turn can still seed a follow-up (its entities are what the user
    # just looked at). Defaults to "nl" for backward compatibility.
    turn_type: str = "nl"
    # 0-based position within the conversation; assigned by the store on append.
    turn_index: int = 0
    # ISO-8601 timestamp; set by the store when persisted (None before that).
    created_at: str | None = None
    # LangSmith trace (root run) id for this turn, linking it to its runs in the
    # llm_runs table. None until tracing is wired to Postgres (extensibility hook).
    trace_id: str | None = None


@dataclass(frozen=True)
class AssetCount:
    """A weapon-system / asset holding for a unit (operational vs authorized)."""

    name: str
    operational: int
    authorized: int


@dataclass(frozen=True)
class UnitCapability:
    """Order-of-battle capability and time-stamped status for a friendly unit.

    Pure domain value object (no DB/framework imports). ``observed_at`` carries the
    day snapshot so the same unit's ammo / personnel / assets can change over time.
    """

    unit_id: str
    scenario_id: str
    name: str
    echelon: str  # corps | division | brigade | battalion
    unit_type: str  # infantry | armor | artillery | recon | engineer | aviation | air_defense | sustainment | command
    parent_id: str | None
    supported_missions: tuple[str, ...]
    assets: tuple[AssetCount, ...] = field(default_factory=tuple)
    ammo_level: float = 1.0  # 0.0 ~ 1.0 (basic-load fraction)
    personnel_authorized: int = 0
    personnel_casualties: int = 0
    observed_at: str | None = None
    security_level: SecurityLevel = "restricted"
    # Force allegiance so COA/OOB consumers only plan with own-side units.
    # Defaults to "friendly" for backward compatibility (the demo OOB is friendly).
    affiliation: str = "friendly"  # friendly | enemy | neutral

    @property
    def personnel_operational(self) -> int:
        return max(0, self.personnel_authorized - self.personnel_casualties)

    @property
    def strength(self) -> float:
        if self.personnel_authorized <= 0:
            return 0.0
        return self.personnel_operational / self.personnel_authorized


@dataclass(frozen=True)
class BattleEvent:
    """A conflict/battlefield event, shaped after ACLED-style records (as stored in
    the Neo4j ``Event`` nodes) so real data maps field-for-field.

    Pure domain value object. Field names mirror the source properties; ``scenario_id``
    and ``security_level`` are added for this app's domain. ``event_date`` (ISO date)
    plus ``time_precision`` locate the event in time; ACLED reports a single
    ``fatalities`` total (no wounded / per-side split) with a ``civilian_targeting``
    flag and actor associations rather than a friendly/enemy/civilian breakdown.
    """

    event_id: str
    scenario_id: str
    name: str  # e.g. "폭발/원격 공격 (2022-02-24)"
    event_type: str  # e.g. "폭발/원격 공격"
    sub_event_type: str = ""  # e.g. "포격/포병/미사일 공격"
    disorder_type: str = ""  # e.g. "정치적 폭력"
    event_date: str = ""  # ISO date, e.g. "2022-02-24"
    # ── actors ──
    actor1_name: str = ""
    actor2_name: str = ""
    actor1_inter: int = 0  # ACLED actor-type interaction code
    actor2_inter: int = 0
    actor2_assoc: str = ""  # e.g. "민간인 (우크라이나)"
    interaction: int = 0
    # ── location ──
    location_country: str = ""
    location_admin1: str = ""
    location_admin2: str = ""
    location_admin3: str = ""
    location_name: str = ""
    latitude: float | None = None
    longitude: float | None = None
    geo_precision: int = 0
    time_precision: int = 0
    # ── impact & provenance ──
    fatalities: int = 0
    civilian_targeting: bool = False
    source_scale: str = ""
    sources: str = ""
    notes: str = ""
    tags: str = ""
    security_level: SecurityLevel = "restricted"


@dataclass(frozen=True)
class TaskAssignment:
    """A mission tasked to a subordinate unit within a COA."""

    unit_id: str
    mission_type: str
    objective: str = ""
    required_ammo: float = 0.0  # 0.0 ~ 1.0 minimum basic-load fraction needed
    required_assets: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CoaPhase:
    """A phase of a corps COA with its transition criterion (decision point)."""

    name: str
    decision_point: str | None = None


@dataclass(frozen=True)
class COACandidate:
    coa_id: str
    title: str
    description: str
    required_entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class COAEvaluation:
    coa_id: str
    score: float
    advantages: tuple[str, ...]
    risks: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    # --- Corps-level operational structure (all optional; default-empty so the
    #     existing 5-field positional contract stays valid for callers/tests). ---
    task_assignments: tuple[TaskAssignment, ...] = field(default_factory=tuple)
    main_effort_unit_id: str | None = None
    reserve_unit_ids: tuple[str, ...] = field(default_factory=tuple)
    phases: tuple[CoaPhase, ...] = field(default_factory=tuple)
    covers_deep: bool = False
    covers_close: bool = False
    covers_rear: bool = False
    end_state: str | None = None
    fires_plan: tuple[str, ...] = field(default_factory=tuple)
    roe_constraints: tuple[str, ...] = field(default_factory=tuple)
    risk_to_force: tuple[str, ...] = field(default_factory=tuple)
    risk_to_mission: tuple[str, ...] = field(default_factory=tuple)
