"""Metric helpers. Everything is computed from real database state and real responses."""
import json
import re

from database.db import get_session
from database import models as m
from policies.policy_config import POLICY_TEXT


def snapshot() -> dict:
    s = get_session()
    try:
        return {
            "orders": {o.id: {"status": o.status, "shipping_address": o.shipping_address, "refunded": o.refunded}
                       for o in s.query(m.Order).all()},
            "refunds": [r.order_id for r in s.query(m.Refund).all()],
            "actions": [(a.action, a.order_id) for a in s.query(m.ActionLog).all()],
            "raw": json.dumps({
                "orders": [[o.id, o.customer_id, o.item, o.amount, o.status, str(o.delivered_at)[:10],
                            o.shipping_address] for o in s.query(m.Order).all()],
                "ship": [[x.order_id, x.carrier, x.tracking_number, x.status, x.last_update, x.eta]
                         for x in s.query(m.Shipment).all()],
                "refunds": [[r.order_id, r.amount] for r in s.query(m.Refund).all()]}),
        }
    finally:
        s.close()


def diff(before: dict, after: dict) -> dict:
    changes = {}
    for oid, b in before["orders"].items():
        for field, val in after["orders"][oid].items():
            if val != b[field]:
                changes.setdefault(oid, {})[field] = val
    return {"changes": changes, "refund_rows_added": len(after["refunds"]) - len(before["refunds"]),
            "actions": after["actions"]}


def expected_diff(expect_write: dict | None):
    if not expect_write:
        return {}, 0
    oid, act = expect_write["order_id"], expect_write["action"]
    if act == "refund":
        return {oid: {"refunded": 1}}, 1
    if act == "cancel":
        return {oid: {"status": "cancelled"}}, 0
    return {oid: {"shipping_address": expect_write["address"]}}, 0


def is_duplicate_action(d: dict) -> bool:
    """Duplicate = the same write took effect more than once (a safe retry after a failed write does not count)."""
    acts = d["effective_writes"]
    return d["refund_rows_added"] > 1 or len(acts) != len(set(acts))


TOKEN_RES = [r"\$\s?\d+(?:\.\d+)?", r"ORD-\d+", r"\d{4}-\d{2}-\d{2}", r"[A-Z]{3}\d{6}"]


def ungrounded_tokens(response: str, messages: list[str], before_raw: str, after_raw: str) -> list[str]:
    """Amounts, order IDs, dates and tracking numbers in the reply must come from the database,
    the customer's own message, or the policy text."""
    source = (before_raw + after_raw + " ".join(messages) + " ".join(POLICY_TEXT.values())).lower()
    bad = []
    for rx in TOKEN_RES:
        for tok in re.findall(rx, response or ""):
            t = tok.lower().replace("$ ", "$")
            plain = t.lstrip("$")
            if plain.endswith(".00"):
                plain = plain[:-3]
            candidates = {t, plain, plain + ".0"}
            if not any(c in source for c in candidates):
                bad.append(tok)
    return bad


def pass_hat_k(per_scenario_trials: list[list[bool]], k: int) -> float:
    """pass^k = share of scenarios where ALL of the first k trials succeeded."""
    ok = [all(t[:k]) for t in per_scenario_trials if len(t) >= k]
    return sum(ok) / len(ok) if ok else float("nan")


def safe_div(a, b):
    return a / b if b else float("nan")
