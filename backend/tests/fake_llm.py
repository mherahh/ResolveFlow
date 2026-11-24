"""A tiny rule-based stand-in for the LLM, used ONLY to test graph wiring without an API key.
It is not an AI model, and results from it say nothing about real model quality."""
import json
import re
from types import SimpleNamespace

from models.schemas import ActionPlan, IntakeResult


def _text(msgs) -> str:
    return "\n".join(m.content for m in msgs)


class _Structured:
    def __init__(self, schema):
        self.schema = schema

    def invoke(self, msgs):
        t = _text(msgs)
        if self.schema is IntakeResult:
            human_text = msgs[-1].content if msgs else t
            last_line = human_text.split("\n")[-1].strip()
            low = last_line.lower()
            full_low = human_text.lower()
            intent = (
                "refund_request" if "refund" in low or "money back" in low else
                "cancellation" if "cancel" in low else
                "address_change" if "address" in low or "deliver" in low else
                "shipment_tracking" if "track" in low or "arrive" in low or "where" in low else
                "order_inquiry" if "order" in low else
                "refund_request" if "refund" in full_low or "money back" in full_low else
                "cancellation" if "cancel" in full_low else
                "address_change" if "address" in full_low or "deliver" in full_low else
                "shipment_tracking" if "track" in full_low or "arrive" in full_low or "where" in full_low else
                "order_inquiry" if "order" in full_low else "general_inquiry"
            )
            sent = "angry" if any(w in full_low for w in ("furious", "scam", "terrible", "worst", "!!!")) else "calm"
            addr = re.search(r"to ([0-9][^.?]*?)(?: instead)?[.?]?$", last_line) or re.search(r"to ([0-9][^.?]*?)(?: instead)?[.?]?", t)
            em_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", last_line)
            email = em_match.group(0).lower() if em_match else None
            is_confirm = bool(re.match(r"^\s*(yes|confirm|proceed|go ahead)", low))
            mtype = (
                "confirm_action" if is_confirm else
                "action_request" if intent in ("refund_request", "cancellation", "address_change") else
                "inquiry"
            )
            return IntakeResult(
                intent=intent,
                sentiment=sent,
                message_type=mtype,
                order_id=None,
                email=email,
                confirmed=is_confirm,
                new_address=addr.group(1).strip() if addr else None,
            )
        if self.schema is ActionPlan:
            intent = re.search(r"Detected intent: (\w+)", t).group(1)
            mtype_match = re.search(r"Message type: (\w+)", t)
            mtype = mtype_match.group(1) if mtype_match else "action_request"
            order = json.loads(re.search(r"Order data: (.*)", t).group(1))
            addr = re.search(r"New address given by customer: (.*)", t).group(1)
            fb = re.search(r"checker feedback on your previous plan: (.*)", t).group(1)
            oid = order.get("id") if order else None
            if fb != "none":  # after a policy rejection: just explain
                return ActionPlan(action="none", reason="Policy rejected the request.", confidence=0.9)
            if mtype == "inquiry":
                return ActionPlan(action="none", order_id=oid, reason="Customer is asking for information or eligibility.", confidence=0.95)
            m = {
                "refund_request": ("refund", order.get("amount") if order else None),
                "cancellation": ("cancel", None),
                "address_change": ("change_address", None),
            }
            if intent in m:
                act, amt = m[intent]
                return ActionPlan(
                    action=act,
                    order_id=oid,
                    amount=amt,
                    new_address=None if addr == "None" else addr,
                    reason="Customer request.",
                    confidence=0.9,
                )
            return ActionPlan(action="none", reason="Information request.", confidence=0.9)
        raise ValueError("unsupported schema")


class FakeLLM:
    def with_structured_output(self, schema):
        return _Structured(schema)

    def invoke(self, msgs):
        t = _text(msgs)
        outcome = re.search(r"Outcome: (\w+)", t).group(1)
        facts_m = re.search(r"Verified facts: (.*)", t, re.DOTALL)
        facts = {}
        if facts_m:
            try:
                facts = json.loads(facts_m.group(1).strip())
            except Exception:
                facts = {}
        if facts.get("eligibility_summary"):
            return SimpleNamespace(content=facts["eligibility_summary"])
        if facts.get("order_already_refunded"):
            return SimpleNamespace(content=f"Order {facts.get('order', {}).get('id', '')} has already been refunded and cannot be refunded again.")
        if facts.get("order_already_cancelled"):
            return SimpleNamespace(content=f"Order {facts.get('order', {}).get('id', '')} is already cancelled.")
        if facts.get("request_not_allowed_because"):
            return SimpleNamespace(content=f"Sorry, this request cannot be processed: {facts['request_not_allowed_because']}")
        if outcome == "pending_approval":
            return SimpleNamespace(content="Your request has been submitted for human manager review. Nothing has been processed yet.")
        if outcome == "needs_info" and facts.get("order_verification_failed"):
            return SimpleNamespace(content="I could not find a matching order on your account. Please verify your Order ID.")
        if facts.get("shipment") and facts.get("order"):
            sh = facts["shipment"]
            o = facts["order"]
            return SimpleNamespace(
                content=f"Order {o['id']} ({o['item']}) is currently {o['status']}. "
                        f"Shipment via {sh['carrier']} ({sh['tracking_number']}): {sh['last_update']}."
            )
        if facts.get("order") and not facts.get("verified_action"):
            o = facts["order"]
            return SimpleNamespace(
                content=f"Order {o['id']} ({o['item']}, ${o['amount']:.2f}) is currently {o['status']}."
            )
        return SimpleNamespace(content=f"[stub reply: {outcome}]")
