"""Escalation: build a short, useful handoff for a human agent. Plain Python, no LLM."""


def build_handoff(state: dict, reason: str) -> dict:
    ctx = state.get("context") or {}
    customer = ctx.get("customer") or {}
    order = ctx.get("order") or {}
    previous_actions = [
        f"{e['node']}: {e['summary']}" for e in state.get("trace", [])
        if e["node"] in ("execute", "verify", "policy")
    ]
    return {
        "issue": state.get("message"),
        "customer": {"id": customer.get("id") or state.get("customer_id"), "name": customer.get("name")},
        "order": {"id": order.get("id"), "status": order.get("status"), "amount": order.get("amount")} if order.get("found") else None,
        "sentiment": (state.get("intake") or {}).get("sentiment"),
        "proposed_action": (state.get("plan") or {}).get("action"),
        "previous_actions": previous_actions,
        "reason_for_escalation": reason,
    }
