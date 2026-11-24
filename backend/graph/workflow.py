r"""The one LangGraph StateGraph.

  intake -> retrieve -> resolve -> policy -> (ask_confirmation) -> respond
                                     |  \-> approval -> respond            (needs human approval)
                                     |  \-> execute -> verify -> respond   (only after authorization)
                                     |  \-> resolve (one correction)       (policy said no)
  any step can route to escalate -> respond
"""
import hashlib
from datetime import datetime, timezone
from langgraph.graph import StateGraph, END

from agents.escalation import build_handoff
from agents.intake import run_intake
from agents.resolution import run_resolution
from agents.responder import run_responder
from agents.verifier import verify
from graph.state import SupportState
from models.schemas import ActionPlan
from policies.policy_guard import check_action
from policies.policy_config import POLICY
from tools import write_tools
from tools.read_tools import (
    call,
    get_customer,
    get_customer_orders,
    get_order,
    lookup_order,
    get_policy,
    get_refund_history,
    track_shipment,
)

WRITE_ACTIONS = ("refund", "cancel", "change_address")
TOPIC_FOR_INTENT = {
    "refund_request": "refund",
    "cancellation": "cancellation",
    "address_change": "address_change",
    "shipment_tracking": "shipping",
    "order_inquiry": "order",
}
ACTION_LABEL_FOR_INTENT = {
    "refund_request": "refund",
    "cancellation": "cancel",
    "address_change": "update the shipping address for",
    "shipment_tracking": "track",
    "order_inquiry": "check",
}


def ev(node: str, summary: str, stage: str | None = None, **extra) -> dict:
    """One trace event, shown in the Agent Activity timeline. Never include PII (emails/tokens)."""
    payload = {
        "node": node,
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    if stage:
        payload["stage"] = stage
    payload.update(extra)
    return payload


def _order_eligibility_note(intent: str, order: dict, customer_id: str | None) -> str:
    if intent == "refund_request":
        dec = check_action(
            ActionPlan(action="refund", order_id=order["id"], amount=order["amount"], reason="check", confidence=1.0),
            order,
            customer_id,
        )
        if dec.decision == "allow":
            return "Eligible for automatic refund"
        if dec.decision == "needs_approval":
            return "Eligible (requires manager approval)"
        return f"Not eligible ({'; '.join(dec.reasons)})"
    if intent == "cancellation":
        dec = check_action(
            ActionPlan(action="cancel", order_id=order["id"], reason="check", confidence=1.0),
            order,
            customer_id,
        )
        return "Eligible for cancellation" if dec.decision == "allow" else f"Not cancellable ({'; '.join(dec.reasons)})"
    if intent == "address_change":
        dec = check_action(
            ActionPlan(action="change_address", order_id=order["id"], new_address="123 Valid Street", reason="check", confidence=1.0),
            order,
            customer_id,
        )
        return "Address editable" if dec.decision == "allow" else f"Address locked ({'; '.join(dec.reasons)})"
    return order.get("status", "")


def build_graph(llm):
    # ---------- nodes ----------
    def intake_node(state: SupportState):
        try:
            import re as _re
            res = run_intake(llm, state["message"], state.get("conversation_history"))
            asks_all = bool(_re.search(r"\b(any\s+order|all\s+(?:of\s+)?my\s+orders|my\s+orders|which\s+orders?)\b", state["message"].lower()))
            if not res.order_id and not res.reference_latest_order and not asks_all:
                if state.get("plan", {}).get("order_id"):
                    res.order_id = state["plan"]["order_id"]
                elif state.get("intake", {}).get("order_id"):
                    res.order_id = state["intake"]["order_id"]
                elif state.get("ticket_id"):
                    import services
                    existing_oid = services.get_ticket_order_id(state["ticket_id"])
                    if existing_oid and res.intent in ("refund_request", "cancellation", "address_change", "order_inquiry", "shipment_tracking"):
                        res.order_id = existing_oid
        except Exception as e:
            return {
                "error": f"intake failed: {type(e).__name__}",
                "trace": [ev("intake", "Could not understand the request", stage="intent_detected")],
            }
        return {
            "intake": res.model_dump(),
            "trace": [ev(
                "intake",
                f"intent={res.intent}, type={res.message_type}, sentiment={res.sentiment}, order={res.order_id or 'unspecified'}",
                stage="intent_detected",
            )],
        }

    def retrieve_node(state: SupportState):
        intake = dict(state.get("intake") or {})
        cid = state.get("customer_id")
        ctx: dict = {}
        events = []
        cust_record = None
        customer_orders: list[dict] = []

        if cid:
            cust_record = call(get_customer, customer_id=cid)
            ctx["customer"] = cust_record
            ctx["refunds"] = call(get_refund_history, customer_id=cid)
            customer_orders = call(get_customer_orders, customer_id=cid)
            ctx["customer_orders"] = customer_orders
            events.append(ev(
                "retrieve",
                f"Loaded authenticated account context ({len(customer_orders)} order(s))",
                stage="order_retrieved",
                tool="get_customer",
            ))

        oid = intake.get("order_id") or state.get("plan", {}).get("order_id")

        # Handle "my last order" / "latest order" reference safely
        if not oid and intake.get("reference_latest_order") and customer_orders:
            if len(customer_orders) == 1:
                oid = customer_orders[0]["id"]
                intake["order_id"] = oid
                events.append(ev(
                    "retrieve",
                    f"Resolved latest order reference to {oid}",
                    stage="order_retrieved",
                    tool="get_customer_orders",
                ))
            else:
                events.append(ev(
                    "retrieve",
                    f"Latest order reference is ambiguous across {len(customer_orders)} orders; clarification needed",
                    stage="order_retrieved",
                    tool="get_customer_orders",
                ))

        if oid:
            msg_email = intake.get("email")
            session_email = cust_record.get("email") if cust_record and cust_record.get("found") else None
            verify_email = msg_email or session_email

            if verify_email:
                order = call(lookup_order, order_id=oid, email=verify_email)
                if order.get("verified"):
                    events.append(ev(
                        "retrieve",
                        f"Order {oid} ownership verified",
                        stage="order_retrieved",
                        tool="lookup_order",
                    ))
                else:
                    events.append(ev(
                        "retrieve",
                        f"Order verification failed for {oid}",
                        stage="order_retrieved",
                        tool="lookup_order",
                    ))
            else:
                order = {"found": False, "verified": False}
                events.append(ev(
                    "retrieve",
                    f"Order verification blocked for {oid}: account authentication required",
                    stage="order_retrieved",
                    tool="lookup_order",
                ))

            # Strict ownership check: authenticated customer may ONLY access their own orders
            if order.get("found") and cid and order["customer_id"] != cid:
                order = {"found": False, "verified": False}
                events.append(ev(
                    "retrieve",
                    "Order ownership check rejected cross-account access",
                    stage="order_retrieved",
                ))

            ctx["order"] = order
            if order.get("verified") and order.get("found"):
                ctx["shipment"] = call(track_shipment, order_id=oid)
                events.append(ev("retrieve", f"Loaded shipment status for {oid}", stage="order_retrieved", tool="track_shipment"))
                if order.get("already_refunded"):
                    events.append(ev("retrieve", f"Order {oid} is already refunded", stage="order_retrieved"))
                if order.get("status") == "cancelled":
                    events.append(ev("retrieve", f"Order {oid} is already cancelled", stage="order_retrieved"))

        topic = TOPIC_FOR_INTENT.get(intake.get("intent", ""), "general")
        ctx["policy"] = call(get_policy, topic=topic)
        events.append(ev("retrieve", f"Loaded {topic} policy", stage="order_retrieved", tool="get_policy"))
        return {"intake": intake, "context": ctx, "trace": events}

    def resolve_node(state: SupportState):
        intake = state["intake"]
        ctx = state["context"]
        order = ctx.get("order") or {}
        intent = intake["intent"]
        mtype = intake.get("message_type", "action_request")
        cid = state.get("customer_id")

        # 1. Pure eligibility / policy inquiry -> evaluate deterministically, NEVER propose a write action
        if mtype == "inquiry" and intent in ("refund_request", "cancellation", "address_change"):
            if order.get("found"):
                note = _order_eligibility_note(intent, order, cid)
                summary = f"Order {order['id']} ({order['item']}, ${order['amount']:.2f}): {note}. " + (ctx.get("policy") or {}).get("policy", "")
                ctx = {**ctx, "eligibility_summary": summary}
                return {
                    "context": ctx,
                    "plan": {"action": "none", "order_id": order.get("id"), "reason": note, "confidence": 1.0},
                    "error": None,
                    "trace": [ev("resolve", f"Eligibility inquiry checked (read-only): {note}", stage="policy_validated")],
                }
            # No specific order ID: summarize eligibility across customer's orders + return policy
            orders = ctx.get("customer_orders") or []
            policy_txt = (ctx.get("policy") or {}).get("policy", "")
            if orders:
                lines = [policy_txt, "", "Here is the current eligibility of your orders:"]
                for o in orders:
                    lines.append(f"- {o['id']} ({o['item']}, ${o['amount']:.2f}): {_order_eligibility_note(intent, o, cid)}")
                summary = "\n".join(lines)
            else:
                summary = policy_txt
            ctx = {**ctx, "eligibility_summary": summary}
            return {
                "context": ctx,
                "plan": {"action": "none", "reason": "Answered eligibility inquiry without executing any action.", "confidence": 1.0},
                "error": None,
                "trace": [ev("resolve", "Answered eligibility inquiry (read-only)", stage="policy_validated")],
            }

        # 2. Database state awareness: already refunded
        if order.get("already_refunded") and intent == "refund_request":
            return {
                "plan": {"action": "none", "order_id": order.get("id"), "reason": "This order has already been refunded in the database.", "confidence": 1.0},
                "error": None,
                "policy_feedback": "This order has already been refunded and cannot be refunded again.",
                "trace": [ev("resolve", f"Order {order.get('id')} was already refunded", stage="policy_validated")],
            }

        # 3. Database state awareness: already cancelled
        if order.get("status") == "cancelled" and intent in ("cancellation", "refund_request", "address_change"):
            return {
                "plan": {"action": "none", "order_id": order.get("id"), "reason": "This order is already cancelled in the database.", "confidence": 1.0},
                "error": None,
                "policy_feedback": "This order is already cancelled.",
                "trace": [ev("resolve", f"Order {order.get('id')} is already cancelled", stage="policy_validated")],
            }

        # 4. Address change without new address yet -> check order status first, then collect address
        if intent == "address_change" and order.get("found") and not intake.get("new_address"):
            if order.get("status") not in POLICY["address_change_statuses"]:
                reason = f"Order status is '{order['status']}', so the shipping address cannot be changed."
                return {
                    "plan": {"action": "none", "order_id": order.get("id"), "reason": reason, "confidence": 1.0},
                    "error": None,
                    "policy_feedback": reason,
                    "trace": [ev("resolve", f"Address change blocked: order is '{order['status']}'", stage="policy_validated")],
                }
            return {
                "plan": {"action": "none", "order_id": order.get("id"), "reason": "Need new shipping address from customer.", "confidence": 1.0},
                "address_needed": True,
                "outcome": "needs_info",
                "error": None,
                "trace": [ev("resolve", f"Order {order.get('id')} is eligible for address change; awaiting new address", stage="action_proposed")],
            }

        try:
            plan = run_resolution(
                llm,
                state["message"],
                intent,
                ctx,
                intake.get("new_address"),
                state.get("policy_feedback"),
                message_type=mtype,
            )
        except Exception as e:
            return {
                "error": f"resolution failed: {type(e).__name__}",
                "trace": [ev("resolve", "Could not create a plan", stage="action_proposed")],
            }

        # Deterministic guardrail: never let the LLM prematurely bypass PolicyGuard + Confirmation
        # by returning 'escalate' on a supported action (e.g., high-value refunds > $100 MUST go
        # through PolicyGuard -> Confirmation Card -> Awaiting Approval -> Manager Approve & Execute).
        if (
            plan.action == "escalate"
            and order.get("found")
            and not state.get("policy_feedback")
            and mtype in ("action_request", "provide_info", "correction")
        ):
            if intent == "refund_request":
                plan = ActionPlan(
                    action="refund",
                    order_id=order["id"],
                    amount=order["amount"],
                    reason=plan.reason or "Customer requested a refund.",
                    confidence=max(plan.confidence, 0.9),
                )
            elif intent == "cancellation":
                plan = ActionPlan(
                    action="cancel",
                    order_id=order["id"],
                    reason=plan.reason or "Customer requested order cancellation.",
                    confidence=max(plan.confidence, 0.9),
                )
            elif intent == "address_change" and intake.get("new_address"):
                plan = ActionPlan(
                    action="change_address",
                    order_id=order["id"],
                    new_address=intake.get("new_address"),
                    reason=plan.reason or "Customer requested shipping address update.",
                    confidence=max(plan.confidence, 0.9),
                )

        # The plan may only target the order we actually retrieved and verified.
        if plan.action in WRITE_ACTIONS:
            plan.order_id = order.get("id") or intake.get("order_id")
            if plan.action == "refund" and order.get("found"):
                plan.amount = order["amount"]
            if plan.action == "change_address" and not plan.new_address:
                plan.new_address = intake.get("new_address")
        return {
            "plan": plan.model_dump(),
            "error": None,
            "trace": [ev(
                "resolve",
                f"Proposed action: {plan.action} (confidence {plan.confidence:.2f})",
                stage="action_proposed",
                detail=plan.reason,
            )],
        }

    def policy_node(state: SupportState):
        plan = ActionPlan(**state["plan"])
        order = state["context"].get("order")
        cust_id = state.get("customer_id") or (order.get("customer_id") if order and order.get("verified") else None)
        dec = check_action(plan, order, cust_id)
        if dec.decision == "needs_approval" and state.get("approved"):
            dec.decision, dec.reasons = "allow", dec.reasons + ["Approved by an authorized manager."]
        out = {
            "policy": dec.model_dump(),
            "trace": [ev("policy", f"Decision: {dec.decision}", stage="policy_validated", detail="; ".join(dec.reasons))],
        }
        if dec.decision == "deny":
            out["attempt"] = state.get("attempt", 0) + 1
            out["policy_feedback"] = "; ".join(dec.reasons)
        return out

    def execute_node(state: SupportState):
        plan, order = state["plan"], state["context"]["order"]
        retry = state.get("verify_attempts", 0)
        key = f"{state['ticket_id']}:{plan['action']}:{plan['order_id']}" + (f":retry{retry}" if retry else "")
        tid = state["ticket_id"]
        actor_id = state.get("actor_id") or state.get("customer_id")
        actor_role = state.get("actor_role") or ("manager" if state.get("approved") else "customer")
        conf_id = state.get("confirmation_id")

        if plan["action"] == "refund":
            res = write_tools.issue_refund(
                plan["order_id"], order["amount"], plan["reason"], key, tid,
                actor_id=actor_id, actor_role=actor_role, confirmation_id=conf_id,
            )
        elif plan["action"] == "cancel":
            res = write_tools.cancel_order(
                plan["order_id"], key, tid,
                actor_id=actor_id, actor_role=actor_role, confirmation_id=conf_id,
            )
        else:
            res = write_tools.change_address(
                plan["order_id"], plan["new_address"], key, tid,
                actor_id=actor_id, actor_role=actor_role, confirmation_id=conf_id,
            )
        stage = "action_failed" if res.get("error") else "action_executed"
        return {
            "exec_result": res,
            "trace": [ev("execute", f"{plan['action']}: {res['detail']}", stage=stage, tool=plan["action"])],
        }

    def verify_node(state: SupportState):
        plan = state["plan"]
        exec_res = state.get("exec_result") or {}
        if exec_res.get("error"):
            return {
                "verification": {"ok": False, "detail": exec_res.get("detail", "Database execution failed.")},
                "outcome": "failed",
                "trace": [ev("verify", "Skipped verification because database write failed", stage="action_failed")],
            }
        v = verify(plan["action"], plan["order_id"], plan.get("new_address"))
        out = {
            "verification": v.model_dump(),
            "trace": [ev("verify", v.detail, stage="action_verified" if v.ok else "action_failed")],
        }
        if not v.ok:
            out["verify_attempts"] = state.get("verify_attempts", 0) + 1
        return out

    def approval_node(state: SupportState):
        return {
            "outcome": "pending_approval",
            "trace": [ev("approval", "Submitted for human manager approval; no mutation executed yet", stage="approval_pending")],
        }

    def ask_confirmation_node(state: SupportState):
        plan = state["plan"]
        order = state["context"].get("order") or {}
        policy_dec = (state.get("policy") or {}).get("decision", "allow")
        label = "requires manager approval after confirmation" if policy_dec == "needs_approval" else "eligible for automatic execution"
        return {
            "outcome": "needs_confirmation",
            "trace": [ev(
                "confirmation",
                f"Order {order.get('id')} ({plan['action']}) {label}; awaiting explicit customer confirmation",
                stage="confirmation_requested",
            )],
        }

    def verification_failed_node(state: SupportState):
        ctx = state.get("context") or {}
        intake = state.get("intake") or {}
        orders = ctx.get("customer_orders") or []
        oid = intake.get("order_id")
        if not oid and orders:
            return {
                "outcome": "needs_info",
                "order_selection_needed": True,
                "trace": [ev(
                    "verify_order",
                    f"Order ID not specified; presenting {len(orders)} account order(s) for selection",
                    stage="order_retrieved",
                    tool="get_customer_orders",
                )],
            }
        return {
            "outcome": "needs_info",
            "trace": [ev(
                "verify_order",
                "Order verification guard: order could not be verified on this account",
                stage="order_retrieved",
                tool="lookup_order",
            )],
        }

    def escalate_node(state: SupportState):
        reason = state.get("escalation_reason") or "Escalated"
        return {
            "outcome": "escalated",
            "escalation_reason": reason,
            "handoff": build_handoff(state, reason),
            "trace": [ev("escalate", reason, stage="escalated")],
        }

    def respond_node(state: SupportState):
        outcome = state.get("outcome") or "resolved"
        facts = build_facts(state)
        text = run_responder(llm, state["message"], outcome, facts)
        return {
            "outcome": outcome,
            "final_response": text,
            "trace": [ev("respond", f"Reply ready ({outcome})", stage="response_sent")],
        }

    # ---------- routing ----------
    def entry(state: SupportState):
        if state.get("approved") or (state.get("confirmed") and state.get("plan")):
            return "retrieve"
        return "intake"

    def after_intake(state: SupportState):
        if state.get("error"):
            return "escalate_error"
        if state["intake"]["sentiment"] == "angry":
            return "escalate_angry"
        return "retrieve"

    def after_retrieve(state: SupportState):
        if state.get("approved") or (state.get("confirmed") and state.get("plan")):
            return "policy"
        intake = state["intake"]
        intent = intake["intent"]
        mtype = intake.get("message_type", "action_request")
        order = state["context"].get("order")

        # Pure eligibility inquiries can proceed to resolve even without a specific order ID
        if mtype == "inquiry" and intent in ("refund_request", "cancellation", "address_change") and not intake.get("order_id"):
            return "resolve"

        if intent != "general_inquiry":
            if not (order and order.get("verified", order.get("found"))):
                return "verification_failed"
        return "resolve"

    def after_resolve(state: SupportState):
        if state.get("error"):
            return "escalate_error"
        if state.get("address_needed"):
            return "respond"
        plan = state["plan"]
        if plan["action"] == "escalate" or plan["confidence"] < 0.6:
            return "escalate_unsure"
        if plan["action"] == "none":
            return "respond"
        return "policy"

    def after_policy(state: SupportState):
        d = state["policy"]["decision"]
        act = state["plan"]["action"]
        req_conf = state.get("require_confirmation", False)
        is_confirmed = bool(state.get("approved") or state.get("confirmed"))

        if d == "allow":
            if act in WRITE_ACTIONS:
                if req_conf and not is_confirmed:
                    return "ask_confirmation"
                return "execute"
            return "respond"
        if d == "needs_approval":
            if req_conf and not is_confirmed:
                return "ask_confirmation"
            return "approval"
        if "already" in (state.get("policy_feedback") or "").lower():
            return "respond"
        return "resolve" if state.get("attempt", 0) <= 1 else "escalate_policy"

    def after_verify(state: SupportState):
        if state.get("outcome") == "failed":
            return "respond"
        if state["verification"]["ok"]:
            return "respond"
        return "policy" if state.get("verify_attempts", 0) <= 1 else "escalate_verify"

    # ---------- wiring ----------
    g = StateGraph(SupportState)
    for name, fn in [
        ("intake", intake_node),
        ("retrieve", retrieve_node),
        ("resolve", resolve_node),
        ("policy", policy_node),
        ("execute", execute_node),
        ("verify", verify_node),
        ("approval", approval_node),
        ("ask_confirmation", ask_confirmation_node),
        ("verification_failed", verification_failed_node),
        ("escalate", escalate_node),
        ("respond", respond_node),
    ]:
        g.add_node(name, fn)

    reasons = {
        "escalate_error": "The system could not reliably understand or plan this request.",
        "escalate_angry": "The customer appears highly distressed or angry.",
        "escalate_unsure": "The proposed resolution was uncertain or needs a human.",
        "escalate_policy": "Policy does not allow automatic processing after one correction attempt.",
        "escalate_verify": "The action could not be verified after one retry.",
    }
    for name, reason in reasons.items():
        g.add_node(name, lambda s, r=reason: {"escalation_reason": r})
        g.add_edge(name, "escalate")

    g.set_conditional_entry_point(entry, {"retrieve": "retrieve", "intake": "intake"})
    g.add_conditional_edges("intake", after_intake, {
        "escalate_error": "escalate_error",
        "escalate_angry": "escalate_angry",
        "retrieve": "retrieve",
    })
    g.add_conditional_edges("retrieve", after_retrieve, {
        "policy": "policy",
        "verification_failed": "verification_failed",
        "resolve": "resolve",
    })
    g.add_edge("verification_failed", "respond")
    g.add_conditional_edges("resolve", after_resolve, {
        "escalate_error": "escalate_error",
        "escalate_unsure": "escalate_unsure",
        "respond": "respond",
        "policy": "policy",
    })
    g.add_conditional_edges("policy", after_policy, {
        "execute": "execute",
        "respond": "respond",
        "approval": "approval",
        "ask_confirmation": "ask_confirmation",
        "resolve": "resolve",
        "escalate_policy": "escalate_policy",
    })
    g.add_edge("ask_confirmation", "respond")
    g.add_edge("execute", "verify")
    g.add_conditional_edges("verify", after_verify, {
        "respond": "respond",
        "policy": "policy",
        "escalate_verify": "escalate_verify",
    })
    g.add_edge("approval", "respond")
    g.add_edge("escalate", "respond")
    g.add_edge("respond", END)
    return g.compile()


def build_facts(state: dict) -> dict:
    """Only verified information goes to the Responder."""
    facts: dict = {}
    ctx = state.get("context") or {}
    order = ctx.get("order")
    intake = state.get("intake") or {}
    outcome = state.get("outcome")
    plan = state.get("plan") or {}
    policy_dec = (state.get("policy") or {}).get("decision")
    cid = state.get("customer_id")

    if order and order.get("found"):
        facts["order"] = {
            k: order[k]
            for k in ("id", "item", "amount", "status", "delivered_at", "shipping_address", "already_refunded")
            if k in order
        }
        if order.get("already_refunded"):
            facts["order_already_refunded"] = True
            facts["policy_explanation"] = "This order has already been refunded in our system. Each order can only be refunded once."
        if order.get("status") == "cancelled":
            facts["order_already_cancelled"] = True
            facts["policy_explanation"] = "This order has already been cancelled in our system."

    if ctx.get("eligibility_summary"):
        facts["eligibility_summary"] = ctx["eligibility_summary"]

    if state.get("order_selection_needed"):
        intent = intake.get("intent", "order_inquiry")
        orders = ctx.get("customer_orders") or []
        facts["order_selection_needed"] = True
        facts["requested_action_label"] = ACTION_LABEL_FOR_INTENT.get(intent, "help with")
        facts["available_orders"] = [
            {
                "id": o["id"],
                "item": o["item"],
                "amount": o["amount"],
                "status": o["status"],
                "already_refunded": o["already_refunded"],
                "eligibility_note": _order_eligibility_note(intent, o, cid),
            }
            for o in orders
        ]
    elif not (order and order.get("verified", order.get("found"))) and intake.get("intent") != "general_inquiry" and not ctx.get("eligibility_summary"):
        facts["order_verification_failed"] = True
        facts["verification_requirement"] = "Please verify your Order ID (e.g. ORD-1001) on your authenticated account."

    if state.get("address_needed"):
        facts["address_needed"] = True

    if ctx.get("shipment", {}).get("found"):
        facts["shipment"] = ctx["shipment"]
    if ctx.get("policy"):
        facts["policy"] = ctx["policy"]["policy"]

    ver = state.get("verification")
    exec_res = state.get("exec_result") or {}
    if ver and ver.get("ok"):
        facts["verified_action"] = {
            "action": plan.get("action"),
            "detail": ver["detail"],
            "reference_id": exec_res.get("reference_id"),
        }
    if outcome == "failed":
        facts["failure_detail"] = (ver or {}).get("detail") or exec_res.get("detail") or "Operation could not be verified."
    if state.get("policy_feedback") and plan.get("action") in (None, "none"):
        facts["request_not_allowed_because"] = state["policy_feedback"]
    if outcome == "pending_approval":
        facts["proposed_action"] = plan.get("action")
    if outcome == "needs_confirmation":
        facts["requires_customer_confirmation"] = True
        facts["action_to_confirm"] = plan.get("action")
        if policy_dec == "needs_approval":
            facts["eligibility_label"] = "Eligible — Requires human manager approval (above $100 automatic limit)."
            facts["processing_info"] = "If you confirm, your request will be submitted to a support manager for review before any refund is processed."
        elif plan.get("action") == "refund":
            facts["eligibility_label"] = "Eligible for automatic refund."
            facts["processing_info"] = "Refunds are returned to your original payment method within 3 to 5 business days."
        elif plan.get("action") == "cancel":
            facts["eligibility_label"] = "Eligible for immediate cancellation (order is still processing)."
        elif plan.get("action") == "change_address":
            facts["eligibility_label"] = "Eligible for address update (order is still processing)."
        facts["order_summary"] = {
            "id": order.get("id") if order else plan.get("order_id"),
            "item": order.get("item") if order else "Item",
            "amount": plan.get("amount") or (order.get("amount") if order else 0),
            "status": order.get("status") if order else None,
            "current_address": order.get("shipping_address") if order else None,
            "new_address": plan.get("new_address"),
            "reason": intake.get("reason") or plan.get("reason"),
        }
    return facts


def new_idempotency_key(*parts: str) -> str:
    return hashlib.sha1(":".join(parts).encode()).hexdigest()[:16]
