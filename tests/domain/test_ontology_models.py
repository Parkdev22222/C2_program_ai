"""도메인 이관 검증: c2.domain.ontology.models ↔ ontology.models (shim) 동일성."""
import importlib


def test_ontology_models_from_domain_match_shim():
    new = importlib.import_module("c2.domain.ontology.models")
    shim = importlib.import_module("ontology.models")
    public = [n for n in dir(new) if not n.startswith("_")]
    assert public, "공개 심볼이 있어야 함"
    for name in public:
        if isinstance(getattr(new, name), type):
            assert getattr(new, name) is getattr(shim, name, None), f"{name} 불일치"
