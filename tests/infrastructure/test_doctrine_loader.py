"""Task 17: 교리 온톨로지(그래프 RAG) rdflib 로더 → c2.infrastructure.ontology.doctrine_loader.

- 순수 rdflib 로딩/쿼리 로직이 c2.infrastructure.ontology.doctrine_loader 에서
  임포트 가능한지 확인.
- TTL 경로가 새 모듈 위치(src/c2/infrastructure/ontology/)에서도 여전히
  <repo>/data/coha_full_ontology.ttl 로 해석되는지 확인.
- doctrine_loader 가 smolagents 를 임포트하지 않는지(소스 검사) 확인 — 인프라 계층은
  프레젠테이션(smolagents @tool) 의존성을 가지면 안 된다.
- functional: rdflib 가 설치돼 있으면 실제 TTL을 로드해 알려진 레이블 쿼리가
  비어있지 않은 관련 트리플을 반환하는지 확인. 설치돼 있지 않으면 예외 없이
  빈 결과로 우아하게 저하되는지 확인.
- tools.graph_rag_tool.graph_rag_military_query 가 여전히 동일한 방식으로
  호출 가능하고 동일한 형태(str)의 결과를 반환하는지 확인 (소비자 영향 없음).
"""
import inspect
from pathlib import Path

import pytest

try:
    import rdflib  # noqa: F401
    _RDFLIB_AVAILABLE = True
except ImportError:
    _RDFLIB_AVAILABLE = False


def test_query_fn_importable_from_infra_module():
    from c2.infrastructure.ontology.doctrine_loader import query_military_ontology

    assert callable(query_military_ontology)


def test_ttl_path_resolves_to_repo_data_dir():
    from c2.infrastructure.ontology import doctrine_loader

    ttl_path = doctrine_loader._ONTOLOGY_PATH
    repo_root = Path(__file__).resolve().parent.parent.parent
    expected = repo_root / "data" / "coha_full_ontology.ttl"

    assert Path(ttl_path).resolve() == expected.resolve()
    assert Path(ttl_path).exists()


def test_doctrine_loader_does_not_import_smolagents():
    from c2.infrastructure.ontology import doctrine_loader

    source = inspect.getsource(doctrine_loader)
    assert "import smolagents" not in source
    assert "from smolagents" not in source


def test_functional_query_returns_related_concepts_or_degrades_gracefully():
    from c2.infrastructure.ontology.doctrine_loader import query_military_ontology

    # "Armor Unit" 은 TTL 내 실재하는 rdfs:label (`:ArmorUnit rdfs:label "Armor Unit"`).
    result = query_military_ontology("Armor Unit")

    assert isinstance(result, str)
    if _RDFLIB_AVAILABLE:
        assert "매칭되는 온톨로지 개념이 없습니다" not in result
        assert "사용할 수 없습니다" not in result
        assert "Armor" in result or "armor" in result.lower()
    else:
        # rdflib 미설치 시 예외 없이 "사용할 수 없습니다" 안내로 우아하게 저하되어야 한다.
        assert "사용할 수 없습니다" in result


def test_tool_wrapper_still_callable_with_same_shape():
    from tools.graph_rag_tool import graph_rag_military_query

    result = graph_rag_military_query("Armor Unit")
    assert isinstance(result, str)


def test_tool_wrapper_delegates_to_infra_loader():
    """tools/graph_rag_tool.py 는 이제 c2.infrastructure.ontology.doctrine_loader 에 위임한다."""
    import tools.graph_rag_tool as tool_module
    from c2.infrastructure.ontology import doctrine_loader

    assert tool_module.query_military_ontology is doctrine_loader.query_military_ontology
