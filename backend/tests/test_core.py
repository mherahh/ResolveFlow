"""Tests for the deterministic parts: tools, policy guard, write tools, idempotency, verifier. No LLM needed."""
import pytest
from database.seed import reset_and_seed
from models.schemas import ActionPlan
from policies.policy_guard import check_action
from tools import write_tools
from tools.read_tools import get_order, track_shipment, get_refund_history, get_policy, call
from agents.verifier import verify


@pytest.fixture(autouse=True)
def fresh_db():
    write_tools.SIMULATE_SILENT_FAILURE.clear()
    reset_and_seed()


def plan(action, order_id, amount=None, addr=None):
    return ActionPlan(action=action, order_id=order_id, amount=amount, new_address=addr, reason="test", confidence=0.9)


def test_read_tools():
    assert call(get_order, order_id="ORD-1001")["item"] == "Wireless Earbuds"
    assert call(get_order, order_id="ORD-9999")["found"] is False
    assert call(track_shipment, order_id="ORD-1004")["status"] == "in_transit"
    assert call(get_refund_history, customer_id="C002")[0]["order_id"] == "ORD-1007"
    assert "30 days" in call(get_policy, topic="refund")["policy"]


def test_refund_allowed_small_recent():
    o = call(get_order, order_id="ORD-1001")
    assert check_action(plan("refund", "ORD-1001", 45.0), o, "C001").decision == "allow"


def test_refund_denied_old_order():
    o = call(get_order, order_id="ORD-1002")
    assert check_action(plan("refund", "ORD-1002", 80.0), o, "C002").decision == "deny"


def test_refund_high_value_needs_approval():
    o = call(get_order, order_id="ORD-1006")
    assert check_action(plan("refund", "ORD-1006", 350.0), o, "C001").decision == "needs_approval"


def test_refund_denied_already_refunded_and_wrong_owner():
    o = call(get_order, order_id="ORD-1007")
    assert check_action(plan("refund", "ORD-1007", 25.0), o, "C002").decision == "deny"
    o = call(get_order, order_id="ORD-1001")
    assert check_action(plan("refund", "ORD-1001", 45.0), o, "C002").decision == "deny"


def test_cancel_rules():
    assert check_action(plan("cancel", "ORD-1003"), call(get_order, order_id="ORD-1003"), "C003").decision == "allow"
    assert check_action(plan("cancel", "ORD-1004"), call(get_order, order_id="ORD-1004"), "C004").decision == "deny"


def test_address_rules():
    ok = check_action(plan("change_address", "ORD-1003", addr="22 Garden Road, Karachi"),
                      call(get_order, order_id="ORD-1003"), "C003")
    assert ok.decision == "allow"
    bad = check_action(plan("change_address", "ORD-1004", addr="22 Garden Road, Karachi"),
                       call(get_order, order_id="ORD-1004"), "C004")
    assert bad.decision == "deny"


def test_cancel_executes_and_verifies():
    r = write_tools.cancel_order("ORD-1003", "key-1")
    assert r["executed"]
    assert verify("cancel", "ORD-1003").ok


def test_idempotency_blocks_duplicate_refund():
    first = write_tools.issue_refund("ORD-1001", 45.0, "defective", "key-r1")
    second = write_tools.issue_refund("ORD-1001", 45.0, "defective", "key-r1")
    assert first["executed"] and not second["executed"] and second["duplicate"]
    assert len(call(get_refund_history, customer_id="C001")) == 1


def test_verifier_catches_silent_failure():
    write_tools.SIMULATE_SILENT_FAILURE.add("cancel")
    assert write_tools.cancel_order("ORD-1003", "key-2")["executed"]
    assert verify("cancel", "ORD-1003").ok is False
