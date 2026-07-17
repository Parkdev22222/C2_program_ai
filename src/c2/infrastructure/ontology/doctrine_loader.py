"""COHA 군사 전술 온톨로지(교리 그래프 RAG) rdflib 로더.

coha_full_ontology.ttl (OWL/Turtle)을 rdflib로 로드하여
rdfs:label 기반 키워드 매칭 + 1-hop 그래프 탐색으로
전술 쿼리와 관련된 개념·관계를 반환하는 순수 인프라 로직.

rdflib/TTL 파일 IO만 다루며 smolagents 등 프레젠테이션 계층에는 의존하지 않는다.
`tools/graph_rag_tool.py`의 `@tool graph_rag_military_query` 가 이 모듈에 위임한다.

Task 17: tools/graph_rag_tool.py 에서 순수 rdflib 로딩/쿼리 로직을
c2.infrastructure.ontology.doctrine_loader 로 추출 — 원문 로직 그대로 유지.
"""
from __future__ import annotations

import logging
from pathlib import Path
from c2._paths import data_path

logger = logging.getLogger(__name__)

# 이 파일: <repo>/src/c2/infrastructure/ontology/doctrine_loader.py
# parents[0]=ontology, [1]=infrastructure, [2]=c2, [3]=src, [4]=<repo>
_ONTOLOGY_PATH = data_path("coha_full_ontology.ttl")

# 모듈 레벨 캐시 (프로세스 내 1회 로드)
_graph = None
_label_index: dict = {}   # label_lower → [uri_str, ...]


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _ensure_graph():
    """rdflib Graph를 로드하고 레이블 인덱스를 구축한다."""
    global _graph, _label_index
    if _graph is not None:
        return _graph
    try:
        import rdflib
        g = rdflib.Graph()
        g.parse(str(_ONTOLOGY_PATH), format="turtle")
        _graph = g
        _build_index(g)
        logger.info(f"[GraphRAG] 온톨로지 로드 완료: {len(g)} 트리플, {len(_label_index)} 레이블")
        return g
    except ImportError:
        logger.error("[GraphRAG] rdflib 미설치 — pip install rdflib")
        return None
    except Exception as e:
        logger.error(f"[GraphRAG] 온톨로지 로드 실패: {e}")
        return None


def _build_index(g):
    """rdfs:label → URI 매핑 인덱스 생성."""
    import rdflib
    RDFS_LABEL = rdflib.namespace.RDFS.label
    for s, _, o in g.triples((None, RDFS_LABEL, None)):
        key = str(o).lower().strip()
        _label_index.setdefault(key, [])
        if str(s) not in _label_index[key]:
            _label_index[key].append(str(s))


def _label_of(g, uri) -> str:
    import rdflib
    for o in g.objects(uri, rdflib.namespace.RDFS.label):
        return str(o)
    return str(uri).split("#")[-1]


def _related_triples(g, uri_str: str, depth: int = 1) -> list:
    """
    uri_str을 중심으로 depth 홉까지의 (subject_label, prop_label, object_label) 수집.
    나가는 방향(predicate_objects)과 들어오는 방향(subject_predicates) 모두 탐색.
    """
    import rdflib
    RDF_TYPE  = rdflib.RDF.type
    RDFS_LBL  = rdflib.namespace.RDFS.label
    OWL_THING = rdflib.OWL.Thing
    OWL_CLASS = rdflib.OWL.Class
    OWL_ONT   = rdflib.OWL.Ontology

    skip_nodes = {str(OWL_CLASS), str(OWL_THING), str(OWL_ONT)}

    results = []
    visited = set()
    queue = [(uri_str, 0)]

    while queue:
        cur, d = queue.pop(0)
        if cur in visited or d > depth:
            continue
        visited.add(cur)

        node = rdflib.URIRef(cur)

        # ① 나가는 방향: node --[p]--> o
        for p, o in g.predicate_objects(node):
            if p in (RDFS_LBL, RDF_TYPE):
                continue
            o_str = str(o)
            if o_str in skip_nodes:
                continue
            s_lbl = _label_of(g, node)
            p_lbl = _label_of(g, p) or str(p).split("#")[-1]
            o_lbl = _label_of(g, o) if isinstance(o, rdflib.URIRef) else str(o)
            if s_lbl and o_lbl and s_lbl != o_lbl:
                results.append((s_lbl, p_lbl, o_lbl))
            if d < depth and isinstance(o, rdflib.URIRef) and o_str not in skip_nodes:
                queue.append((o_str, d + 1))

        # ② 들어오는 방향: s --[p]--> node  (rdfs:domain/range 등 역방향 관계)
        if d == 0:   # 루트 노드에서만 역방향 탐색 (무한 확장 방지)
            for s, p in g.subject_predicates(node):
                if p in (RDFS_LBL, RDF_TYPE):
                    continue
                s_str = str(s)
                if s_str in skip_nodes:
                    continue
                s_lbl = _label_of(g, s)
                p_lbl = _label_of(g, p) or str(p).split("#")[-1]
                o_lbl = _label_of(g, node)
                if s_lbl and o_lbl and s_lbl != o_lbl:
                    results.append((s_lbl, p_lbl, o_lbl))

    return results


def _match_uris(query: str, max_uris: int = 15) -> list:
    """쿼리 키워드를 레이블 인덱스와 매칭하여 관련 URI 목록 반환."""
    # 한국어 → 영어 매핑 (온톨로지가 영문 레이블만 보유)
    kor_to_eng = {
        "정찰": ["recon", "isr", "sensor", "surveillance", "observation", "collection", "intelligence"],
        "탐지": ["detection", "sensor", "observation", "gmti", "sigint", "humint", "imint"],
        "경로": ["avenue", "approach", "movement", "maneuver", "route"],
        "지형": ["terrain", "forest", "urban", "mountain", "key terrain"],
        "공격": ["attack", "offensive", "assault", "maneuver", "form of maneuver"],
        "기갑": ["armor", "armored", "armor unit"],
        "보병": ["infantry", "infantry unit"],
        "화력": ["fire support", "artillery", "fscm", "fire support asset",
                 "coordinated fire line", "fire support coordination"],
        "임무": ["mission", "task", "offensive mission", "defensive mission"],
        "우선순위": ["priority", "hvt", "hpt", "high value", "high priority"],
        "지휘": ["command", "commander", "echelon", "command echelon", "command post"],
        "항공": ["aviation", "uav", "air", "aviation unit"],
        "통신": ["communication", "radio", "c2 network"],
        "방어": ["defense", "defensive", "area defense", "mobile defense", "protect"],
        "측방": ["flank", "maneuver"],
        "부대": ["unit", "battalion", "brigade", "company"],
        "도시": ["urban", "urban terrain"],
    }

    expanded = []   # 순서 유지 (우선순위 높은 키워드 먼저)
    seen_exp = set()
    q_lower = query.lower()

    # 한국어 키워드 먼저 확장 (명시적 매핑 우선)
    for kor, engs in kor_to_eng.items():
        if kor in q_lower:
            for e in engs:
                if e not in seen_exp:
                    seen_exp.add(e)
                    expanded.append(e)

    # 원본 영어 단어도 추가 (중복 제외)
    for w in q_lower.split():
        if len(w) > 2 and w not in seen_exp:
            seen_exp.add(w)
            expanded.append(w)

    matched_uris = []
    seen_uris = set()
    for keyword in expanded:
        for lbl, uris in _label_index.items():
            if keyword in lbl or lbl in keyword:
                for u in uris:
                    if u not in seen_uris:
                        seen_uris.add(u)
                        matched_uris.append(u)
                        if len(matched_uris) >= max_uris:
                            return matched_uris
    return matched_uris


# ── 공개 쿼리 함수 ───────────────────────────────────────────────────────────

def query_military_ontology(query: str, max_triples: int = 25) -> str:
    """
    군사 전술 온톨로지에서 쿼리 관련 개념·관계를 검색한다.

    Args:
        query:       검색 쿼리 (한국어/영어 혼용 가능)
        max_triples: 반환할 최대 트리플 수

    Returns:
        관련 전술 교리 개념과 관계를 서술한 텍스트 (빈 결과 시 안내 메시지)
    """
    g = _ensure_graph()
    if g is None:
        return "[GraphRAG] 온톨로지를 사용할 수 없습니다."

    matched_uris = _match_uris(query)
    if not matched_uris:
        return f"[GraphRAG] '{query}'에 매칭되는 온톨로지 개념이 없습니다."

    all_triples = []
    seen = set()
    for uri in matched_uris:
        for triple in _related_triples(g, uri, depth=1):
            if triple not in seen:
                seen.add(triple)
                all_triples.append(triple)

    if not all_triples:
        return f"[GraphRAG] '{query}' — 매칭 URI {len(matched_uris)}개 찾았으나 관계 없음."

    lines = [f"[군사 전술 온톨로지 — '{query}' 관련 교리 개념]"]
    for s, p, o in all_triples[:max_triples]:
        lines.append(f"  {s}  --[{p}]-->  {o}")

    return "\n".join(lines)


# ── 정찰/공격 특화 쿼리 헬퍼 ─────────────────────────────────────────────────

def get_recon_ontology_context() -> str:
    """정찰 임무 계획에 필요한 온톨로지 컨텍스트를 반환한다."""
    recon_query = "정찰 ISR 탐지 지형 경로 탐지 sensor observation terrain avenue approach"
    return query_military_ontology(recon_query, max_triples=20)


def get_attack_ontology_context() -> str:
    """공격 임무 계획에 필요한 온톨로지 컨텍스트를 반환한다."""
    attack_query = "공격 기갑 보병 화력 지형 maneuver fire support armor infantry terrain"
    return query_military_ontology(attack_query, max_triples=20)
