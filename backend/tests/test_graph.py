"""Graph wiring tests using the rule-based FakeLLM (no network, no API key)."""
import pytest
from database.seed import reset_and_seed
from graph.workflow import build_graph
from tests.fake_llm import FakeLLM
from tools import write_tools
from tools.read_tools import call, get_order


@pytest.fixture(autouse=True)
def fresh():
    write_tools.SIMULATE_SILENT_FAILURE.clear()
    reset_and_seed()


def run(message, customer_id, ticket_id="T-1", **extra):
    graph = build_graph(FakeLLM())
    return graph.invoke({"message": message, "customer_id": customer_id, "ticket_id": ticket_id, "trace": [], **extra})


def test_info_request_resolved():
    r = run("Where is my order ORD-1004?", "C004")
    assert r["outcome"] == "resolved" and "execute" not in [e["node"] for e in r["trace"]]


def test_eligible_refund_executed_and_verified():
    r = run("I want a refund for ORD-1001", "C001")
    assert r["outcome"] == "resolved" and r["verification"]["ok"]
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is True


def test_ineligible_refund_explained_not_executed():
    r = run("I want a refund for ORD-1002", "C002")
    assert r["outcome"] == "resolved"
    assert call(get_order, order_id="ORD-1002")["already_refunded"] is False
    assert r["attempt"] == 1  # exactly one correction attempt used


def test_high_value_refund_waits_for_approval_then_executes():
    r = run("Refund ORD-1006 please", "C001")
    assert r["outcome"] == "pending_approval"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False
    r2 = run("Refund ORD-1006 please", "C001", approved=True, plan=r["plan"], intake=r["intake"])
    assert r2["outcome"] == "resolved"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is True


def test_cancel_processing_order():
    r = run("Please cancel ORD-1003", "C003")
    assert r["outcome"] == "resolved"
    assert call(get_order, order_id="ORD-1003")["status"] == "cancelled"


def test_cancel_shipped_order_blocked():
    r = run("Cancel ORD-1004", "C004")
    assert call(get_order, order_id="ORD-1004")["status"] == "shipped"


def test_missing_order_id_asks_for_it():
    assert run("I want a refund", "C001")["outcome"] == "needs_info"


def test_other_customers_order_not_exposed():
    r = run("Cancel ORD-1003", "C001")
    assert r["outcome"] == "needs_info"
    assert call(get_order, order_id="ORD-1003")["status"] == "processing"


def test_angry_customer_escalated():
    r = run("This is a scam!!! Refund ORD-1001", "C001")
    assert r["outcome"] == "escalated" and r["handoff"]["reason_for_escalation"]
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False


def test_silent_failure_retries_once_then_escalates():
    write_tools.SIMULATE_SILENT_FAILURE.add("cancel")
    r = run("Cancel ORD-1003", "C003")
    assert r["outcome"] == "escalated"
    assert r["verify_attempts"] == 2


def test_duplicate_request_in_new_ticket_is_blocked():
    run("Refund ORD-1001", "C001", ticket_id="T-1")
    r = run("Refund ORD-1001", "C001", ticket_id="T-2")
    from tools.read_tools import get_refund_history
    assert len(call(get_refund_history, customer_id="C001")) == 1


def test_lookup_order_tool_security():
    from tools.read_tools import lookup_order
    # 1. Matching order and email -> verified
    res = call(lookup_order, order_id="ORD-1001", email="ayesha@example.com")
    assert res["verified"] is True and res["found"] is True
    assert res["item"] == "Wireless Earbuds"

    # 2. Valid order but mismatched email -> rejected, privacy protected
    bad_email = call(lookup_order, order_id="ORD-1001", email="wrong@example.com")
    assert bad_email["verified"] is False and bad_email["found"] is False

    # 3. Nonexistent order -> rejected identical shape
    bad_oid = call(lookup_order, order_id="ORD-9999", email="ayesha@example.com")
    assert bad_oid["verified"] is False and bad_oid["found"] is False

    # Both failure cases return identical signature (no information disclosure)
    assert bad_email.keys() == bad_oid.keys()


def test_order_verification_guard_blocks_unverified():
    # Customer provides order ID and wrong email in message
    r = run("I want to check order ORD-1001 for wrong@email.com", "C001")
    assert r["outcome"] == "needs_info"
    # Action nodes must be bypassed
    assert "resolve" not in [e["node"] for e in r["trace"]]
    assert "execute" not in [e["node"] for e in r["trace"]]


def test_refund_confirmation_flow():
    # Turn 1: Customer requests refund with confirmation required -> asks for confirmation
    r1 = run("I would like a refund for ORD-1001", "C001", require_confirmation=True)
    assert r1["outcome"] == "needs_confirmation"
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False

    # Turn 2: Customer confirms -> executes and verifies refund
    r2 = run("Yes please proceed with the refund", "C001", confirmed=True, require_confirmation=True,
             plan=r1["plan"], intake=r1["intake"])
    assert r2["outcome"] == "resolved"
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is True


def test_cancellation_confirmation_flow():
    # Turn 1: Asks to cancel processing order -> prompts for confirmation
    r1 = run("Can I cancel my order ORD-1003?", "C003", require_confirmation=True)
    assert r1["outcome"] == "needs_confirmation"
    assert call(get_order, order_id="ORD-1003")["status"] == "processing"

    # Turn 2: Customer confirms -> cancels
    r2 = run("Yes, please cancel it", "C003", confirmed=True, require_confirmation=True,
             plan=r1["plan"], intake=r1["intake"])
    assert r2["outcome"] == "resolved"
    assert call(get_order, order_id="ORD-1003")["status"] == "cancelled"


def test_remembers_already_refunded_order():
    # ORD-1007 is already refunded in seed database
    r = run("Please refund ORD-1007", "C002")
    assert r["outcome"] == "resolved"
    assert "execute" not in [e["node"] for e in r["trace"]]
    assert r["plan"]["action"] == "none"
    assert "already" in r["plan"]["reason"].lower()


def test_remembers_already_cancelled_order():
    # ORD-1005 is already cancelled in seed database
    r = run("Cancel order ORD-1005", "C005")
    assert r["outcome"] == "resolved"
    assert "execute" not in [e["node"] for e in r["trace"]]
    assert r["plan"]["action"] == "none"
    assert "already" in r["plan"]["reason"].lower()
