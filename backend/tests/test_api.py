"""API tests with the fake LLM injected (no network)."""
import json
import pytest
from fastapi.testclient import TestClient

import main
from database.seed import DEMO_CUSTOMER_PASSWORD, DEMO_MANAGER_PASSWORD, reset_and_seed
from graph.workflow import build_graph
from tests.fake_llm import FakeLLM
from tools import write_tools
from tools.read_tools import call, get_order


@pytest.fixture()
def client():
    write_tools.SIMULATE_SILENT_FAILURE.clear()
    write_tools.SIMULATE_DB_ERROR.clear()
    reset_and_seed()
    main.GRAPH = build_graph(FakeLLM())
    return TestClient(main.app)


def login_headers(client: TestClient, email: str, password: str = DEMO_CUSTOMER_PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_chat_refund_and_dashboard(client):
    c1 = login_headers(client, "ayesha@example.com")
    mgr = login_headers(client, "manager@resolveflow.io", DEMO_MANAGER_PASSWORD)

    # Step 1: Request refund -> must ask confirmation, NEVER execute immediately
    r1 = client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1).json()
    assert r1["outcome"] == "needs_confirmation"
    assert r1["ticket_id"].startswith("T-") and r1["order_id"] == "ORD-1001"
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is False

    # Step 2: Explicitly confirm via dedicated confirmation endpoint
    conf_id = r1["confirmation"]["id"]
    c_res = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1).json()
    assert c_res["status"] == "resolved" and c_res["verified"] is True
    assert call(get_order, order_id="ORD-1001")["already_refunded"] is True

    # Dashboard reflects real DB metrics
    d = client.get("/api/dashboard", headers=mgr).json()
    assert d["total"] == 1 and d["resolved"] == 1 and d["autonomous_resolved"] == 1
    assert d["autonomous_resolution_rate"] == 100.0


def test_stream_emits_steps_then_final(client):
    c4 = login_headers(client, "usman@example.com")
    with client.stream("POST", "/api/chat/stream", json={"message": "Where is ORD-1004?"}, headers=c4) as resp:
        lines = [json.loads(l) for l in resp.iter_lines() if l]
    types = [l["type"] for l in lines]
    assert types[0] == "ticket" and types[-1] == "final" and "event" in types


def test_approval_flow(client):
    c1 = login_headers(client, "ayesha@example.com")
    mgr = login_headers(client, "manager@resolveflow.io", DEMO_MANAGER_PASSWORD)

    # Customer requests high-value refund -> confirmation first
    r = client.post("/api/chat", json={"message": "Refund ORD-1006"}, headers=c1).json()
    assert r["outcome"] == "needs_confirmation"
    conf_id = r["confirmation"]["id"]

    # Customer confirms submission -> awaiting_approval (NOT processed yet!)
    sub = client.post(f"/api/confirmations/{conf_id}/confirm", headers=c1).json()
    assert sub["status"] == "awaiting_approval"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False

    # Manager views approvals and approves
    assert len(client.get("/api/approvals", headers=mgr).json()) == 1
    out = client.post(f"/api/approvals/{r['ticket_id']}", json={"approve": True}, headers=mgr).json()
    assert out["status"] == "resolved"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is True
    t = client.get(f"/api/tickets/{r['ticket_id']}", headers=mgr).json()
    assert t["status"] == "resolved"
    assert t["resolution_mode"] == "human_approved"


def test_rejection_leaves_order_unchanged(client):
    c1 = login_headers(client, "ayesha@example.com")
    mgr = login_headers(client, "manager@resolveflow.io", DEMO_MANAGER_PASSWORD)

    r = client.post("/api/chat", json={"message": "Refund ORD-1006"}, headers=c1).json()
    client.post(f"/api/confirmations/{r['confirmation']['id']}/confirm", headers=c1)
    out = client.post(f"/api/approvals/{r['ticket_id']}", json={"approve": False}, headers=mgr).json()
    assert out["status"] == "rejected"
    assert call(get_order, order_id="ORD-1006")["already_refunded"] is False


def test_llm_failure_escalates_ticket_instead_of_hanging(client):
    c1 = login_headers(client, "ayesha@example.com")

    class Broken:
        def stream(self, *a, **k):
            raise RuntimeError("model down")

    main.GRAPH = Broken()
    with client.stream("POST", "/api/chat/stream", json={"message": "Hi"}, headers=c1) as resp:
        lines = [json.loads(l) for l in resp.iter_lines() if l]
    assert lines[-1]["type"] == "error"
    t = client.get("/api/tickets", headers=c1).json()[0]
    assert t["status"] == "escalated"
    assert client.post("/api/chat", json={"message": "Hi"}, headers=c1).status_code == 503


def test_multi_turn_conversation_preserves_ticket_and_messages(client):
    c4 = login_headers(client, "usman@example.com")
    r1 = client.post("/api/chat", json={"message": "Where is my order ORD-1004?"}, headers=c4).json()
    ticket_id = r1["ticket_id"]
    assert ticket_id.startswith("T-")

    r2 = client.post("/api/chat", json={"message": "Thank you for the update!", "ticket_id": ticket_id}, headers=c4).json()
    assert r2["ticket_id"] == ticket_id

    td = client.get(f"/api/tickets/{ticket_id}", headers=c4).json()
    assert "messages" in td
    assert len(td["messages"]) >= 3
    assert td["messages"][0]["role"] == "customer"
    assert td["messages"][0]["text"] == "Where is my order ORD-1004?"


def test_tickets_filtering_and_search(client):
    c1 = login_headers(client, "ayesha@example.com")
    c4 = login_headers(client, "usman@example.com")
    mgr = login_headers(client, "manager@resolveflow.io", DEMO_MANAGER_PASSWORD)

    client.post("/api/chat", json={"message": "Refund ORD-1001"}, headers=c1)
    client.post("/api/chat", json={"message": "Where is ORD-1004?"}, headers=c4)

    # Customer sees only their own tickets
    c1_own = client.get("/api/tickets", headers=c1).json()
    assert len(c1_own) == 1 and all(t["customer_id"] == "C001" for t in c1_own)

    # Manager can filter by customer and search
    c1_tickets = client.get("/api/tickets?customer_id=C001", headers=mgr).json()
    assert all(t["customer_id"] == "C001" for t in c1_tickets)

    search_res = client.get("/api/tickets?search=1004", headers=mgr).json()
    assert len(search_res) >= 1
    assert any("1004" in t["message"] or t["order_id"] == "ORD-1004" for t in search_res)


def test_customer_orders_endpoint(client):
    c1 = login_headers(client, "ayesha@example.com")
    orders = client.get("/api/customers/me/orders", headers=c1).json()
    assert len(orders) >= 1
    assert "id" in orders[0] and "status" in orders[0] and "actions" in orders[0]


def test_resolve_ticket_with_note(client):
    c1 = login_headers(client, "ayesha@example.com")
    mgr = login_headers(client, "manager@resolveflow.io", DEMO_MANAGER_PASSWORD)

    r = client.post("/api/chat", json={"message": "Refund ORD-1006"}, headers=c1).json()
    client.post(f"/api/confirmations/{r['confirmation']['id']}/confirm", headers=c1)
    ticket_id = r["ticket_id"]
    res = client.post(f"/api/tickets/{ticket_id}/resolve", json={"note": "Handled manually via phone call"}, headers=mgr).json()
    assert res["status"] == "resolved"
    t = client.get(f"/api/tickets/{ticket_id}", headers=mgr).json()
    assert t["status"] == "resolved"
    assert t["resolution_mode"] == "human_manual"
