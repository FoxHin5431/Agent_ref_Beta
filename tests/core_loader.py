from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
STREAMLIT_UI_MARKER = "# =========================\n# Streamlit UI"


def load_beta_core() -> types.ModuleType:
    """Load Beta validation functions without executing the Streamlit UI."""
    source = APP_PATH.read_text(encoding="utf-8")
    if STREAMLIT_UI_MARKER not in source:
        raise RuntimeError(f"Could not find Streamlit UI marker in {APP_PATH}")

    pandas_stub = types.ModuleType("pandas")
    pandas_stub.DataFrame = type("DataFrame", (), {})
    dependency_stubs = {
        "httpx": types.ModuleType("httpx"),
        "pandas": pandas_stub,
        "streamlit": types.ModuleType("streamlit"),
    }

    module = types.ModuleType("agent_ref_beta_core")
    module.__file__ = str(APP_PATH)
    core_source = source.split(STREAMLIT_UI_MARKER, 1)[0]
    with patch.dict(sys.modules, dependency_stubs):
        exec(compile(core_source, str(APP_PATH), "exec"), module.__dict__)
    return module
