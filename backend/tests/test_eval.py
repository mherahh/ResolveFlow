"""Smoke tests for the evaluation harness itself, plus the ReAct baseline wiring with a scripted tool-calling model."""
from typing import Any
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from database.seed import reset_and_seed
from evaluation import metrics as mt
from evaluation.run_eval import WorkflowSystem, run_trial, summarize
from evaluation.scenarios import SCENARIOS
from tests.fake_llm import FakeLLM


def test_scenario_count_and_shape():
    assert 20 <= len(SCENARIOS) <= 30
    assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS)


def test_ungrounded_detector():
    assert mt.ungrounded_tokens("Your order ORD-1001 cost $45.", [], '[["ORD-1001", 45.0]]', "") == []
    assert mt.ungrounded_tokens("Refund of $999 sent for ORD-1001", [], '[["ORD-1001", 45.0]]', "") == ["$999"]


def test_pass_hat_k():
    assert mt.pass_hat_k([[True, True], [True, False]], 2) == 0.5


def test_harness_runs_end_to_end_with_stub():
    system = WorkflowSystem(FakeLLM())
    rows = [run_trial(system, sc, 0) for sc in SCENARIOS[:6]]
    s = summarize(rows, 1)
    assert s["runs"] == 6 and 0 <= s["task_success_rate"] <= 1


class ScriptedToolModel(BaseChatModel):
    """Calls cancel_order once, then answers. Only used to check the baseline agent is wired correctly."""
    calls: int = 0

    @property
    def _llm_type(self): return "scripted"

    def bind_tools(self, tools, **kwargs: Any): return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            msg = AIMessage(content="", tool_calls=[{"name": "cancel_order", "args": {"order_id": "ORD-1003"}, "id": "c1"}])
        else:
            msg = AIMessage(content="Done.")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_baseline_agent_wiring():
    from agents.react_baseline import run_baseline
    from tools.read_tools import call, get_order
    reset_and_seed()
    out = run_baseline(ScriptedToolModel(), "cancel ORD-1003", "C003")
    assert out["response"] == "Done."
    assert call(get_order, order_id="ORD-1003")["status"] == "cancelled"
