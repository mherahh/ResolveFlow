"""Comprehensive security, authentication, confirmation, concurrency, and failure-mode tests.

Covers all 20 required production scenarios plus auth/registration/demo-mode/address/cancellation safeguards.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import main
import services
from config import settings
from database.db import get_session
from database import models as m
from database.seed import DEMO_CUSTOMER_PASSWORD, DEMO_MANAGER_PASSWORD, reset_and_seed
from graph.workflow import build_graph
from tests.fake_llm import FakeLLM
from tools import write_tools
from tools.read_tools import call, get_order, get_refund_history


@pytest.fixture()
def client():
    write_tools.SIMULATE_SILENT_FAILURE.clear()
    write_tools.SIMULATE_DB_ERROR.clear()
    settings.demo_mode = True
    reset_and_seed()
    main.GRAPH = build_graph(FakeLLM())
    return TestClient(main.app)


def login(client: TestClient, email: str, password: str = DEMO_CUSTOMER_PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- Scenario 1: Customer requests a refund without specifying an order ---
def test_01_refund_without_specifying_order(client):
    c1 = login(client, "ayesha@example.com")
    r = client.post("/api/chat", json={"message": "Refund my order."}, headers=c1)
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "needs_info"
    assert body["status"] == "awaiting_customer"
    assert "ORD-1001" in body["response"]
    assert "ORD-1006" in body["response"]
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False


# --- Scenario 2 & 3: Eligible refund proposes confirmation and NEVER executes before confirmation ---
def test_02_03_eligible_refund_requires_confirmation_and_does_not_execute_early(client):
    c1 = login(client, "ayesha@example.com")
    r = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1)
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "needs_confirmation"
    assert body["status"] == "awaiting_customer"
    assert body["confirmation"] is not None
    assert body["confirmation"]["order_id"] == "ORD-1001"
    assert body["confirmation"]["amount"] == 45.0
    assert body["confirmation"]["status"] == "pending"
    assert "ORD-1001" in body["response"]
    assert "Wireless Earbuds" in body["response"]
    assert "$45.00" in body["response"]
    # Database MUST NOT be mutated yet
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False
    assert len(call(get_refund_history, customer_id="C001")) == 0


# --- Scenario 4: Customer rejects the refund confirmation ---
def test_04_customer_rejects_refund_confirmation(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    cancel_res = client.post(f"/api/confirmations/{conf_id}/cancel", headers=c1)
    assert cancel_res.status_code == 200
    assert cancel_res.json()["confirmation"]["status"] == "cancelled"
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False
    assert len(call(get_refund_history, customer_id="C001")) == 0


# --- Scenario 5: Customer confirms the refund -> verified DB state + final receipt ---
def test_05_customer_confirms_refund(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "I want a refund for ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    r2 = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1)
    assert r2.status_code == 200
    data = r2.json()
    assert data["status"] == "resolved"
    assert data["verified"] is True
    assert data["reference_id"] and data["reference_id"].startswith("REF-")
    assert "ORD-1001" in data["response"]
    assert "Wireless Earbuds" in data["response"]
    assert "$45.00" in data["response"]
    assert "Refund Processed" in data["response"]
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is True
    assert len(call(get_refund_history, customer_id="C001")) == 1


# --- Scenario 6: Customer submits the same confirmation twice ---
def test_06_duplicate_confirmation_submission_blocked(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    first = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1)
    assert first.status_code == 200
    second = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1)
    assert second.status_code == 409
    assert len(call(get_refund_history, customer_id="C001")) == 1


# --- Scenario 7: Customer requests a refund for an already refunded order ---
def test_07_already_refunded_order_blocked(client):
    c2 = login(client, "bilal@example.com")
    r = client.post("/api/chat", json={"message": "Refund ORD-1007"}, headers=c2)
    assert r.status_code == 200
    body = r.json()
    assert body["confirmation"] is None
    assert "already" in body["response"].lower()
    assert len(call(get_refund_history, customer_id="C002")) == 1


# --- Scenario 8: Customer requests a refund outside the 30-day eligibility window ---
def test_08_refund_outside_eligibility_window(client):
    c2 = login(client, "bilal@example.com")
    r = client.post("/api/chat", json={"message": "Refund ORD-1002"}, headers=c2)
    assert r.status_code == 200
    body = r.json()
    assert body["confirmation"] is None
    assert call(get_order, order_id="ORD-1002")["already_refunded"] is False
    assert "30-day" in body["response"] or "window" in body["response"].lower()


# --- Scenario 9: Customer requests a refund exceeding automatic approval threshold ---
def test_09_high_value_refund_requires_confirmation_then_manager_approval(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1006"}, headers=c1).json()
    assert r1["outcome"] == "needs_confirmation"
    assert r1["confirmation"]["policy_decision"] == "needs_approval"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False

    # Customer confirms submitting for human review
    r2 = client.post(f"/api/confirmations/{r1['confirmation']['id']}/confirm", headers=c1).json()
    assert r2["status"] == "awaiting_approval"
    assert "manager review" in r2["response"].lower() or "pending manager approval" in r2["response"].lower()
    assert "successfully processed" not in r2["response"].lower()
    # Still NOT refunded until manager approves
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False


# --- Scenario 10: Customer attempts to access another customer's order ---
def test_10_customer_cannot_access_another_customers_order_or_spoof_id(client):
    c1 = login(client, "ayesha@example.com")
    # Direct orders endpoint blocked
    assert client.get("/api/customers/C003/orders", headers=c1).status_code == 403
    # Spoofing customer_id in chat body blocked
    assert client.post("/api/chat", json={"message": "Cancel ORD-1003", "customer_id": "C003"}, headers=c1).status_code == 403
    # Referencing another customer's order via order_id field blocked
    assert client.post("/api/chat", json={"message": "Cancel ORD-1003", "order_id": "ORD-1003"}, headers=c1).status_code == 403
    # Mentioning another customer's order in text does not expose or modify it
    r = client.post("/api/chat", json={"message": "Cancel ORD-1003"}, headers=c1)
    assert r.status_code == 200
    assert r.json()["outcome"] == "needs_info"
    assert call(get_order, order_id="ORD-1003")["status"] == "processing"


# --- Scenario 11: Customer requests cancellation of a shipped order ---
def test_11_cancel_shipped_order_denied(client):
    c4 = login(client, "usman@example.com")
    r = client.post("/api/chat", json={"message": "Cancel ORD-1004"}, headers=c4)
    assert r.status_code == 200
    assert r.json()["confirmation"] is None
    assert call(get_order, order_id="ORD-1004")["status"] == "shipped"


# --- Scenario 12: Customer attempts to modify the address of a delivered order ---
def test_12_modify_address_of_delivered_order_denied(client):
    c1 = login(client, "ayesha@example.com")
    orig_addr = call(get_order, order_id="ORD-1001")["shipping_address"]
    r = client.post(
        "/api/chat",
        json={"message": "Change shipping address of ORD-1001 to 99 Clifton Block 4, Karachi"},
        headers=c1,
    )
    assert r.status_code == 200
    assert r.json()["confirmation"] is None
    assert call(get_order, order_id="ORD-1001")["shipping_address"] == orig_addr


# --- Scenario 13: Customer changes their mind before confirming ---
def test_13_customer_changes_mind_in_chat_before_confirming(client):
    c3 = login(client, "sara@example.com")
    r1 = client.post("/api/chat", json={"message": "Can you cancel ORD-1003?"}, headers=c3).json()
    assert r1["outcome"] == "needs_confirmation"
    tid = r1["ticket_id"]

    r2 = client.post("/api/chat", json={"message": "Actually, never mind.", "ticket_id": tid}, headers=c3).json()
    assert r2["status"] == "open"
    assert r2["confirmation"]["status"] == "cancelled"
    assert call(get_order, order_id="ORD-1003")["status"] == "processing"


# --- Scenario 14: Customer sends an ambiguous confirmation ---
def test_14_ambiguous_confirmation_does_not_execute(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    tid = r1["ticket_id"]

    for vague in ("okay", "hmm", "maybe"):
        r2 = client.post("/api/chat", json={"message": vague, "ticket_id": tid}, headers=c1).json()
        assert r2["outcome"] == "needs_confirmation"
        assert r2["status"] == "awaiting_customer"
        assert call(get_order, order_id="ORD-1001")["already_refunded"] is False

    # Explicit confirmation afterwards DOES work
    r3 = client.post("/api/chat", json={"message": "Yes, proceed.", "ticket_id": tid}, headers=c1).json()
    assert r3["status"] == "resolved"
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is True


# --- Scenario 15: The database operation fails ---
def test_15_database_operation_failure_reports_failed_never_success(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    write_tools.SIMULATE_DB_ERROR.add("refund")
    try:
        res = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1)
        assert res.status_code == 500
    finally:
        write_tools.SIMULATE_DB_ERROR.clear()

    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False
    t = client.get(f"/api/tickets/{r1['ticket_id']}", headers=c1).json()
    assert t["status"] == "failed"
    assert "successfully processed" not in (t["final_response"] or "").lower()


# --- Scenario 16: Database operation logs success but verification fails ---
def test_16_verification_failure_marks_ticket_failed_and_never_claims_success(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    write_tools.SIMULATE_SILENT_FAILURE.add("refund")
    try:
        res = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1)
        assert res.status_code == 500
    finally:
        write_tools.SIMULATE_SILENT_FAILURE.clear()

    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False
    t = client.get(f"/api/tickets/{r1['ticket_id']}", headers=c1).json()
    assert t["status"] == "failed"
    assert "uncertain" in (t["final_response"] or "").lower() or "could not be verified" in (t["final_response"] or "").lower()


# --- Scenario 17: A pending confirmation expires ---
def test_17_expired_confirmation_cannot_be_executed(client):
    c1 = login(client, "ayesha@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    # Force expiry in DB
    s = get_session()
    try:
        c = s.get(m.PendingConfirmation, conf_id)
        c.expires_at = services.utc_now() - timedelta(minutes=5)
        s.commit()
    finally:
        s.close()

    res = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1)
    assert res.status_code == 410
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False


# --- Scenario 18: Two simultaneous requests attempt to refund the same order ---
def test_18_concurrent_refund_requests_execute_only_once(client):
    c1 = login(client, "ayesha@example.com")
    # Create two separate tickets/confirmations for the same order ORD-1001
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    r2 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    cid1, cid2 = r1["confirmation"]["id"], r2["confirmation"]["id"]

    def run_confirm(conf_id):
        return services.execute_pending_confirmation(conf_id, customer_id="C001", actor_role="customer")

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(run_confirm, cid1)
        f2 = pool.submit(run_confirm, cid2)
        res1, res2 = f1.result(), f2.result()

    # Exactly ONE refund row in database, never two!
    refunds = call(get_refund_history, customer_id="C001")
    assert len(refunds) == 1
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is True
    assert sum(1 for r in (res1, res2) if r.get("ok") and r.get("verified")) == 1


# --- Scenario 19: An unauthorized user attempts to approve a high-value refund ---
def test_19_unauthorized_customer_cannot_approve_high_value_refund(client):
    c1 = login(client, "ayesha@example.com")
    c2 = login(client, "bilal@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1006"}, headers=c1).json()
    client.post(f"/api/confirmations/{r1['confirmation']['id']}/confirm", headers=c1)

    # Neither the requester nor another customer may approve!
    assert client.post(f"/api/approvals/{r1['ticket_id']}", json={"approve": True}, headers=c1).status_code == 403
    assert client.post(f"/api/approvals/{r1['ticket_id']}", json={"approve": True}, headers=c2).status_code == 403
    assert client.get("/api/approvals", headers=c1).status_code == 403
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False


# --- Scenario 20: Customer logs out and attempts to access an existing session ---
def test_20_logout_invalidates_session_token(client):
    c1 = login(client, "ayesha@example.com")
    assert client.get("/api/auth/me", headers=c1).status_code == 200

    out = client.post("/api/auth/logout", headers=c1)
    assert out.status_code == 200

    # Existing session token is immediately rejected everywhere
    assert client.get("/api/auth/me", headers=c1).status_code == 401
    assert client.get("/api/customers/me/orders", headers=c1).status_code == 401
    assert client.post("/api/chat", json={"message": "Hi"}, headers=c1).status_code == 401


# --- Additional Production Tests: Auth, Eligibility Inquiries, Address Change, State Drift, Demo Mode ---


def test_registration_and_login_flow(client):
    reg = client.post("/api/auth/register", json={
        "name": "Zainab Noor",
        "email": "zainab@example.com",
        "password": "StrongPassword123!",
    })
    assert reg.status_code == 200
    data = reg.json()
    assert data["user"]["email"] == "zainab@example.com"
    assert data["user"]["role"] == "customer"
    headers = {"Authorization": f"Bearer {data['token']}"}

    # Duplicate email rejected
    dup = client.post("/api/auth/register", json={
        "name": "Zainab 2",
        "email": "zainab@example.com",
        "password": "StrongPassword123!",
    })
    assert dup.status_code == 409

    # Wrong password rejected
    assert client.post("/api/auth/login", json={"email": "zainab@example.com", "password": "wrong"}).status_code == 401

    # New user cannot access C001's orders
    assert client.get("/api/customers/C001/orders", headers=headers).status_code == 403


def test_eligibility_inquiry_never_executes_or_asks_confirmation(client):
    c1 = login(client, "ayesha@example.com")
    r = client.post("/api/chat", json={"message": "I want to know if I can get a refund."}, headers=c1)
    assert r.status_code == 200
    body = r.json()
    assert body["confirmation"] is None
    assert body["outcome"] == "resolved"
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False


def test_address_change_multi_turn_confirmation_flow(client):
    c3 = login(client, "sara@example.com")
    # Turn 1: Start address change without new address -> shows current address, asks for new address
    r1 = client.post("/api/chat", json={"message": "I want to change my address for ORD-1003"}, headers=c3).json()
    assert r1["outcome"] == "needs_info"
    assert r1["status"] == "awaiting_customer"
    assert "House 12, Block 5, Karachi" in r1["response"]
    tid = r1["ticket_id"]

    # Turn 2: Provide new address -> presents old vs new confirmation card (DOES NOT mutate yet!)
    new_addr = "House 88, Clifton Block 2, Karachi"
    r2 = client.post("/api/chat", json={"message": new_addr, "ticket_id": tid}, headers=c3).json()
    assert r2["outcome"] == "needs_confirmation"
    assert r2["confirmation"]["action"] == "change_address"
    assert r2["confirmation"]["current_address"] == "House 12, Block 5, Karachi"
    assert r2["confirmation"]["new_address"] == new_addr
    assert call(get_order, order_id="ORD-1003")["shipping_address"] == "House 12, Block 5, Karachi"

    # Turn 3: Confirm -> executes & verifies
    r3 = client.post(f"/api/confirmations/{r2['confirmation']['id']}/confirm", headers=c3).json()
    assert r3["status"] == "resolved" and r3["verified"] is True
    assert call(get_order, order_id="ORD-1003")["shipping_address"] == new_addr


def test_order_state_change_before_confirmation_blocks_execution(client):
    c3 = login(client, "sara@example.com")
    r1 = client.post("/api/chat", json={"message": "Can you cancel ORD-1003?"}, headers=c3).json()
    conf_id = r1["confirmation"]["id"]

    # Simulate order shipping before customer clicks Confirm
    s = get_session()
    try:
        o = s.get(m.Order, "ORD-1003")
        o.status = "shipped"
        s.commit()
    finally:
        s.close()

    res = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c3)
    assert res.status_code == 409
    assert call(get_order, order_id="ORD-1003")["status"] == "shipped"


def test_cross_customer_confirmation_rejected(client):
    c1 = login(client, "ayesha@example.com")
    c2 = login(client, "bilal@example.com")
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    conf_id = r1["confirmation"]["id"]

    # Customer 2 tries to confirm Customer 1's confirmation -> 403 Forbidden
    assert client.post(f"/api/confirmations/{conf_id}/confirm", headers=c2).status_code == 403
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False


def test_demo_mode_disabled_blocks_demo_endpoints(client):
    settings.demo_mode = False
    try:
        assert client.get("/api/demo/accounts").status_code == 403
        assert client.post("/api/demo/switch", json={"account_id": "C001"}).status_code == 403
    finally:
        settings.demo_mode = True


def test_high_value_refund_never_short_circuits_to_escalation_when_llm_proposes_escalate(client):
    """Regression test: even if an LLM prematurely proposes action='escalate' for a >$100 refund,
    resolve_node deterministically routes it through PolicyGuard -> Confirmation -> Awaiting Approval
    -> Manager Approve & Execute so the database refund actually executes and updates the order."""
    from models.schemas import ActionPlan

    class EscalatingFakeLLM(FakeLLM):
        def with_structured_output(self, schema):
            inner = super().with_structured_output(schema)
            if schema is ActionPlan:
                class _Esc:
                    def invoke(self, msgs):
                        return ActionPlan(
                            action="escalate",
                            order_id="ORD-1006",
                            reason="Amount exceeds $100 automatic limit and needs human approval.",
                            confidence=0.9,
                        )
                return _Esc()
            return inner

    main.GRAPH = build_graph(EscalatingFakeLLM())
    c1 = login(client, "ayesha@example.com")
    mgr = login(client, "manager@resolveflow.io", DEMO_MANAGER_PASSWORD)

    # 1. Customer requests high-value refund -> MUST produce a Confirmation Card (NOT escalated!)
    r1 = client.post("/api/chat", json={"message": "refund my order 1006"}, headers=c1).json()
    assert r1["outcome"] == "needs_confirmation"
    assert r1["confirmation"] is not None
    assert r1["confirmation"]["policy_decision"] == "needs_approval"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False

    # 2. Customer confirms -> transitions to awaiting_approval (NOT yet refunded)
    r2 = client.post(f"/api/confirmations/{r1['confirmation']['id']}/confirm", headers=c1).json()
    assert r2["status"] == "awaiting_approval"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False

    # 3. Manager approves -> executes atomic refund & updates DB!
    r3 = client.post(f"/api/approvals/{r1['ticket_id']}", json={"approve": True}, headers=mgr).json()
    assert r3["status"] == "resolved"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is True
    assert len(call(get_refund_history, customer_id="C001")) == 1

    # 4. Customer checks orders -> ORD-1006 is now refunded!
    orders = client.get("/api/customers/me/orders", headers=c1).json()
    ord_1006 = next(o for o in orders if o["id"] == "ORD-1006")
    assert ord_1006["refunded"] is True
    assert ord_1006["refund"]["reference_id"].startswith("REF-")

