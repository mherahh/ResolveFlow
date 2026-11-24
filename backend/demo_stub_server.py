"""Run the API with a rule-based stub instead of a real LLM, so you can click through the UI without an API key.
This is NOT AI: it only understands a few keywords. Use `uvicorn main:app` for the real system.

    uvicorn demo_stub_server:app --port 8000
"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import main
from graph.workflow import build_graph
from tests.fake_llm import FakeLLM

main.GRAPH = build_graph(FakeLLM())
app = main.app
