from pathlib import Path
import types


def load_beta_core():
    """Fresh core instance for isolated lookup mocks, with no Streamlit startup."""
    path = Path(__file__).resolve().parents[1] / "core.py"
    module = types.ModuleType("agent_ref_test_core")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module
