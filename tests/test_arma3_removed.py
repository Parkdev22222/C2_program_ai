from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

def test_arma3_paths_gone():
    for rel in [
        "arma3_integration", "api/arma3_receiver.py", "core_src",
        "tools/arma3_order_tool.py", "tools/arma3_query_tool.py",
        "data/arma3_orders.json", "data/arma3_state.json",
    ]:
        assert not (_ROOT / rel).exists(), f"still exists: {rel}"

def test_no_arma3_imports_in_agent():
    # 레거시 agent/battlefield_agent.py shim은 Task 34에서 삭제됨 —
    # 실제 구현이 이전된 canonical 경로(src/c2)를 검사한다.
    src = (_ROOT / "src" / "c2" / "presentation" / "agent" / "battlefield_agent.py").read_text(
        encoding="utf-8"
    )
    assert "arma3" not in src.lower()
