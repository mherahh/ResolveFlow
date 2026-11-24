"""Glue between the API, LangGraph workflow, confirmation engine, and database."""
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.responder import format_confirmation_summary, format_verified_receipt
from agents.verifier import verify
from config import settings
from database.db import get_session
from database import models as m
from database.migrations import migrate_schema
from graph.workflow import build_facts, ev
from models.schemas import ActionPlan
from policies.policy_guard import check_action
from tools import write_tools
from tools.read_tools import call, get_order

OUTCOME_TO_STATUS = {
    "resolved": "resolved",
    "escalated": "escalated",
    "pending_approval": "awaiting_approval",
    "awaiting_approval": "awaiting_approval",
    "needs_info": "awaiting_customer",
    "needs_confirmation": "awaiting_customer",
    "awaiting_customer": "awaiting_customer",
    "failed": "failed",
    "open": "open",
    "processing": "processing",
}

VALID_STATUSES = {
    "open",
    "awaiting_customer",
    "awaiting_approval",
    "processing",
    "resolved",
    "escalated",
    "failed",
    "rejected",
}

ALLOWED_TRANSITIONS = {
    "open": {"open", "processing", "awaiting_customer", "awaiting_approval", "resolved", "escalated", "failed"},
    "processing": {"open", "processing", "awaiting_customer", "awaiting_approval", "resolved", "escalated", "failed"},
    "awaiting_customer": {"open", "processing", "awaiting_customer", "awaiting_approval", "resolved", "escalated", "failed"},
    "awaiting_approval": {"processing", "awaiting_approval", "resolved", "rejected", "escalated", "failed"},
    "failed": {"open", "processing", "escalated", "resolved", "failed"},
    "escalated": {"open", "processing", "resolved", "escalated"},
    "resolved": {"open", "processing", "awaiting_customer", "resolved"},
    "rejected": {"open", "processing", "escalated", "rejected"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def transition_ticket_status(ticket: m.Ticket, target_status: str) -> str:
    """Validate and apply a ticket status transition."""
    if target_status not in VALID_STATUSES:
        raise ValueError(f"Invalid ticket status: {target_status}")
    current = ticket.status or "open"
    if current == "pending_approval":
        current = "awaiting_approval"
    allowed = ALLOWED_TRANSITIONS.get(current, VALID_STATUSES)
    if target_status not in allowed:
        raise ValueError(f"Invalid status transition from '{current}' to '{target_status}'")
    ticket.status = target_status
    ticket.updated_at = utc_now()
    return target_status


def init_db():
    migrate_schema()
    s = get_session()
    try:
        empty = s.query(m.Customer).count() == 0
    finally:
        s.close()
    if empty:
        from database.seed import reset_and_seed
        reset_and_seed()


def generate_ticket_id(s) -> str:
    """Generate a collision-safe incremental ticket ID like T-1001, T-1002."""
    existing_ids = s.query(m.Ticket.id).all()
    max_num = 1000
    for (tid,) in existing_ids:
        if tid and tid.startswith("T-"):
            num_part = tid[2:]
            if num_part.isdigit():
                max_num = max(max_num, int(num_part))
    return f"T-{max_num + 1}"


def create_ticket(message: str, customer_id: str | None, order_id: str | None = None) -> str:
    s = get_session()
    try:
        tid = generate_ticket_id(s)
        ticket = m.Ticket(
            id=tid,
            customer_id=customer_id,
            order_id=order_id,
            message=message,
            issue_type="unknown",
            status="open",
        )
        s.add(ticket)
        transition_ticket_status(ticket, "processing")
        s.add(m.TicketMessage(ticket_id=tid, role="customer", text=message))
        s.commit()
        return tid
    finally:
        s.close()


def add_customer_message(ticket_id: str, message: str, customer_id: str | None = None) -> bool:
    """Add a customer follow-up message to an existing ticket."""
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t:
            return False
        if customer_id and not t.customer_id:
            t.customer_id = customer_id
        transition_ticket_status(t, "processing")
        s.add(m.TicketMessage(ticket_id=ticket_id, role="customer", text=message))
        s.commit()
        return True
    finally:
        s.close()


def append_ticket_trace(ticket: m.Ticket, new_events: list[dict]):
    existing = json.loads(ticket.trace or "[]")
    ticket.trace = json.dumps(existing + new_events)


def get_conversation_history(ticket_id: str) -> list[dict]:
    """Retrieve chronologically ordered messages for a ticket."""
    s = get_session()
    try:
        msgs = (
            s.query(m.TicketMessage)
            .filter_by(ticket_id=ticket_id)
            .order_by(m.TicketMessage.created_at.asc(), m.TicketMessage.id.asc())
            .all()
        )
        if msgs:
            return [{"role": msg.role, "text": msg.text} for msg in msgs]
        t = s.get(m.Ticket, ticket_id)
        if not t:
            return []
        res = []
        if t.message:
            res.append({"role": "customer", "text": t.message})
        if t.final_response:
            res.append({"role": "agent", "text": t.final_response})
        return res
    finally:
        s.close()


def get_ticket_order_id(ticket_id: str) -> Optional[str]:
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        return t.order_id if t else None
    finally:
        s.close()


def stream_graph(graph, state: dict):
    """Yield ('event', trace_event) as nodes finish, then ('final', full_state)."""
    state = {**state, "trace": list(state.get("trace", []))}
    for update in graph.stream(state, stream_mode="updates"):
        for _node, changes in update.items():
            for key, val in (changes or {}).items():
                if key == "trace":
                    state["trace"] = state["trace"] + val
                    for e in val:
                        yield "event", e
                else:
                    state[key] = val
    yield "final", state


def confirmation_dict(c: m.PendingConfirmation) -> dict:
    snap = {}
    try:
        snap = json.loads(c.order_snapshot or "{}")
    except Exception:
        snap = {}
    is_expired = c.status == "pending" and c.expires_at <= utc_now()
    eff_status = "expired" if is_expired else c.status
    if c.policy_decision == "needs_approval":
        elig_label = "Requires human manager approval (exceeds $100 automatic limit)"
        proc_info = "Confirming submits this request to a support manager for review. No refund is processed until approved."
    elif c.action == "refund":
        elig_label = "Eligible for automatic refund"
        proc_info = "Refund will be returned to your original payment method within 3 to 5 business days."
    elif c.action == "cancel":
        elig_label = "Eligible for cancellation (order is still processing)"
        proc_info = "Once cancelled, processing stops immediately and the order cannot be reinstated."
    else:
        elig_label = "Eligible for shipping address update (order is still processing)"
        proc_info = "The delivery address will be updated before dispatch."
    return {
        "id": c.id,
        "ticket_id": c.ticket_id,
        "customer_id": c.customer_id,
        "order_id": c.order_id,
        "item": snap.get("item", "Item"),
        "action": c.action,
        "amount": c.amount if c.amount is not None else snap.get("amount"),
        "current_address": snap.get("shipping_address"),
        "new_address": c.new_address,
        "order_status": snap.get("status"),
        "reason": c.reason,
        "policy_decision": c.policy_decision,
        "eligibility_label": elig_label,
        "processing_info": proc_info,
        "status": eff_status,
        "created_at": c.created_at.isoformat(),
        "expires_at": c.expires_at.isoformat(),
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
    }


def create_pending_confirmation(
    s,
    ticket_id: str,
    customer_id: str,
    order: dict,
    plan: dict,
    policy_decision: str,
    reason: str | None = None,
    ttl_minutes: int | None = None,
) -> m.PendingConfirmation:
    """Create a single-use, time-bound, state-snapshotted confirmation record."""
    # Supersede any prior pending confirmations on this ticket
    prior_pending = (
        s.query(m.PendingConfirmation)
        .filter_by(ticket_id=ticket_id, status="pending")
        .all()
    )
    now = utc_now()
    for p in prior_pending:
        p.status = "superseded"
        p.resolved_at = now

    conf_id = f"CONF-{secrets.token_hex(6).upper()}"
    ttl = ttl_minutes if ttl_minutes is not None else settings.confirmation_ttl_minutes
    snapshot = {
        "id": order.get("id"),
        "item": order.get("item"),
        "amount": order.get("amount"),
        "status": order.get("status"),
        "refunded": bool(order.get("already_refunded", order.get("refunded", False))),
        "shipping_address": order.get("shipping_address"),
        "delivered_at": order.get("delivered_at"),
    }
    conf = m.PendingConfirmation(
        id=conf_id,
        ticket_id=ticket_id,
        customer_id=customer_id,
        order_id=order["id"],
        action=plan["action"],
        amount=plan.get("amount") if plan.get("amount") is not None else order.get("amount"),
        new_address=plan.get("new_address"),
        reason=reason or plan.get("reason"),
        policy_decision=policy_decision,
        order_snapshot=json.dumps(snapshot),
        status="pending",
        idempotency_key=f"conf:{conf_id}:{plan['action']}:{order['id']}",
        created_at=now,
        expires_at=now + timedelta(minutes=ttl),
    )
    s.add(conf)
    return conf


def get_active_confirmation(ticket_id: str, customer_id: str | None = None) -> tuple[Optional[dict], bool]:
    """Return (confirmation_dict_or_None, just_expired_bool) for a ticket."""
    s = get_session()
    try:
        q = s.query(m.PendingConfirmation).filter_by(ticket_id=ticket_id, status="pending")
        if customer_id:
            q = q.filter_by(customer_id=customer_id)
        conf = q.order_by(m.PendingConfirmation.created_at.desc()).first()
        if not conf:
            return None, False
        now = utc_now()
        if conf.expires_at <= now:
            conf.status = "expired"
            conf.resolved_at = now
            t = s.get(m.Ticket, ticket_id)
            if t:
                t.pending_action = None
                if t.status == "awaiting_customer":
                    transition_ticket_status(t, "open")
                append_ticket_trace(t, [ev("confirmation", f"Confirmation {conf.id} expired before authorization", stage="confirmation_expired")])
            s.commit()
            write_tools.record_audit_event(
                action="confirmation_expired",
                status="expired",
                detail=f"Pending {conf.action} confirmation {conf.id} expired.",
                ticket_id=ticket_id,
                order_id=conf.order_id,
                customer_id=conf.customer_id,
                actor_id="system",
                actor_role="system",
                confirmation_id=conf.id,
            )
            return confirmation_dict(conf), True
        return confirmation_dict(conf), False
    finally:
        s.close()


def supersede_pending_confirmations(ticket_id: str, customer_id: str | None = None):
    s = get_session()
    try:
        q = s.query(m.PendingConfirmation).filter_by(ticket_id=ticket_id, status="pending")
        if customer_id:
            q = q.filter_by(customer_id=customer_id)
        now = utc_now()
        for p in q.all():
            p.status = "superseded"
            p.resolved_at = now
        s.commit()
    finally:
        s.close()


def cancel_pending_confirmation(
    confirmation_id: str,
    customer_id: str,
    actor_role: str = "customer",
    note: str | None = None,
) -> dict:
    """Cancel a pending confirmation without executing any database mutation."""
    s = get_session()
    try:
        conf = s.get(m.PendingConfirmation, confirmation_id)
        if not conf:
            return {"ok": False, "code": 404, "detail": "Confirmation request not found."}
        if actor_role == "customer" and conf.customer_id != customer_id:
            return {"ok": False, "code": 403, "detail": "Not authorized to access this confirmation."}
        if conf.status != "pending":
            return {"ok": False, "code": 409, "detail": f"Confirmation is already {conf.status}."}
        if conf.expires_at <= utc_now():
            conf.status = "expired"
            conf.resolved_at = utc_now()
            s.commit()
            return {"ok": False, "code": 410, "detail": "This confirmation has already expired."}

        conf.status = "cancelled"
        conf.resolved_at = utc_now()
        t = s.get(m.Ticket, conf.ticket_id)
        action_word = {"refund": "refund", "cancel": "cancellation", "change_address": "address update"}.get(conf.action, conf.action)
        reply = f"No problem — I have cancelled your pending {action_word} request for order {conf.order_id}. No changes were made to your order."
        meta = {"card_type": "action_cancelled", "confirmation_id": conf.id, "order_id": conf.order_id, "action": conf.action}
        if t:
            t.pending_action = None
            transition_ticket_status(t, "open")
            t.final_response = reply
            append_ticket_trace(t, [
                ev("confirmation", f"Customer cancelled pending {conf.action} ({conf.id})", stage="confirmation_cancelled"),
                ev("respond", "Informed customer no changes were made", stage="response_sent"),
            ])
            s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps(meta)))
        s.commit()

        write_tools.record_audit_event(
            action="confirmation_cancelled",
            status="cancelled",
            detail=f"Customer cancelled pending {conf.action} for {conf.order_id}" + (f": {note}" if note else ""),
            ticket_id=conf.ticket_id,
            order_id=conf.order_id,
            customer_id=conf.customer_id,
            actor_id=customer_id,
            actor_role=actor_role,
            confirmation_id=conf.id,
        )
        return {
            "ok": True,
            "code": 200,
            "ticket_id": conf.ticket_id,
            "confirmation": confirmation_dict(conf),
            "status": "open",
            "outcome": "open",
            "response": reply,
            "metadata": meta,
        }
    finally:
        s.close()


def execute_pending_confirmation(
    confirmation_id: str,
    customer_id: str,
    actor_role: str = "customer",
    expected_ticket_id: str | None = None,
) -> dict:
    """Atomically validate, execute, and verify an explicitly confirmed customer action.

    Steps:
    1. Verify ownership, ticket binding, single-use status, and TTL expiry.
    2. Atomically claim the confirmation row (pending -> processing) to prevent concurrent replay.
    3. Re-read live order state and detect any changes since confirmation was created.
    4. Re-run deterministic PolicyGuard validation.
    5. If needs_approval: transition ticket to awaiting_approval (never claim processed).
    6. If allow: execute atomic idempotent write tool, then re-read DB via Verifier.
    7. Only after verified DB state succeeds, mark ticket resolved and return receipt.
    """
    s = get_session()
    try:
        conf = s.get(m.PendingConfirmation, confirmation_id)
        if not conf:
            return {"ok": False, "code": 404, "detail": "Confirmation request not found."}
        if actor_role == "customer" and conf.customer_id != customer_id:
            return {"ok": False, "code": 403, "detail": "Not authorized to confirm another customer's request."}
        if expected_ticket_id and conf.ticket_id != expected_ticket_id:
            return {"ok": False, "code": 403, "detail": "Confirmation does not belong to this conversation."}

        t = s.get(m.Ticket, conf.ticket_id)
        if not t or (actor_role == "customer" and t.customer_id != customer_id):
            return {"ok": False, "code": 403, "detail": "Not authorized for this support ticket."}

        if conf.status == "confirmed":
            return {
                "ok": False,
                "code": 409,
                "already_processed": True,
                "detail": "This confirmation has already been processed.",
                "confirmation": confirmation_dict(conf),
                "ticket_id": conf.ticket_id,
                "status": t.status,
            }
        if conf.status == "processing":
            return {
                "ok": False,
                "code": 409,
                "already_processed": True,
                "detail": "This confirmation is currently being processed.",
                "confirmation": confirmation_dict(conf),
                "ticket_id": conf.ticket_id,
                "status": t.status,
            }
        if conf.status in ("cancelled", "superseded", "failed"):
            return {
                "ok": False,
                "code": 409,
                "detail": f"This confirmation is no longer active (status: {conf.status}).",
                "confirmation": confirmation_dict(conf),
            }

        now = utc_now()
        if conf.status == "expired" or conf.expires_at <= now:
            conf.status = "expired"
            conf.resolved_at = now
            t.pending_action = None
            if t.status == "awaiting_customer":
                transition_ticket_status(t, "open")
            reply = (
                f"Your pending confirmation for order {conf.order_id} has expired for your security. "
                "No changes were made. Please submit your request again if you would still like to proceed."
            )
            t.final_response = reply
            append_ticket_trace(t, [ev("confirmation", f"Confirmation {conf.id} expired", stage="confirmation_expired")])
            s.add(m.TicketMessage(
                ticket_id=t.id,
                role="agent",
                text=reply,
                metadata_json=json.dumps({"card_type": "confirmation_expired", "confirmation_id": conf.id}),
            ))
            s.commit()
            write_tools.record_audit_event(
                action="confirmation_expired",
                status="expired",
                detail=f"Attempted confirmation of expired token {conf.id}",
                ticket_id=conf.ticket_id,
                order_id=conf.order_id,
                customer_id=conf.customer_id,
                actor_id=customer_id,
                actor_role=actor_role,
                confirmation_id=conf.id,
            )
            return {
                "ok": False,
                "code": 410,
                "expired": True,
                "detail": "Confirmation has expired. Please initiate a new request.",
                "response": reply,
                "ticket_id": conf.ticket_id,
                "confirmation": confirmation_dict(conf),
            }

        # Atomic claim: pending -> processing
        claimed = (
            s.query(m.PendingConfirmation)
            .filter(m.PendingConfirmation.id == confirmation_id, m.PendingConfirmation.status == "pending")
            .update({m.PendingConfirmation.status: "processing"}, synchronize_session=False)
        )
        if claimed == 0:
            s.rollback()
            return {
                "ok": False,
                "code": 409,
                "already_processed": True,
                "detail": "This confirmation was already submitted.",
            }
        transition_ticket_status(t, "processing")
        append_ticket_trace(t, [ev("confirmation", f"Customer explicitly authorized {conf.action} ({conf.id})", stage="customer_authorized")])
        s.commit()

        # Re-read live order and check against snapshot
        live_order = call(get_order, order_id=conf.order_id)
        snapshot = json.loads(conf.order_snapshot or "{}")
        if not live_order.get("found") or live_order.get("customer_id") != conf.customer_id:
            conf.status = "failed"
            conf.resolved_at = utc_now()
            t.pending_action = None
            transition_ticket_status(t, "failed")
            reply = "We could not verify ownership of this order before execution. No changes were made."
            t.final_response = reply
            append_ticket_trace(t, [ev("policy", "Order ownership verification failed before execution", stage="action_failed")])
            s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps({"card_type": "execution_failed"})))
            s.commit()
            return {"ok": False, "code": 403, "detail": reply, "status": "failed", "response": reply}

        state_changed = (
            live_order.get("status") != snapshot.get("status")
            or bool(live_order.get("already_refunded")) != bool(snapshot.get("refunded"))
            or abs((live_order.get("amount") or 0) - (snapshot.get("amount") or 0)) > 0.01
            or live_order.get("shipping_address") != snapshot.get("shipping_address")
        )

        # Deterministic policy re-validation against LIVE database state
        plan = ActionPlan(
            action=conf.action,
            order_id=conf.order_id,
            amount=live_order["amount"] if conf.action == "refund" else None,
            new_address=conf.new_address,
            reason=conf.reason or "Customer confirmed request",
            confidence=1.0,
        )
        decision = check_action(plan, live_order, conf.customer_id)
        append_ticket_trace(t, [ev(
            "policy",
            f"Pre-execution policy re-validation: {decision.decision}",
            stage="policy_validated",
            detail="; ".join(decision.reasons),
        )])

        if decision.decision == "deny":
            conf.status = "superseded" if state_changed else "failed"
            conf.resolved_at = utc_now()
            t.pending_action = None
            transition_ticket_status(t, "open" if state_changed else "failed")
            reason_txt = "; ".join(decision.reasons)
            prefix = "Your order status changed before we could execute this action: " if state_changed else "This action is no longer eligible under policy: "
            reply = f"{prefix}{reason_txt} No changes were made."
            t.final_response = reply
            s.add(m.TicketMessage(
                ticket_id=t.id,
                role="agent",
                text=reply,
                metadata_json=json.dumps({"card_type": "execution_failed", "reason": reason_txt, "state_changed": state_changed}),
            ))
            s.commit()
            write_tools.record_audit_event(
                action=f"{conf.action}_blocked",
                status="failed",
                detail=reason_txt,
                ticket_id=t.id,
                order_id=conf.order_id,
                customer_id=conf.customer_id,
                actor_id=customer_id,
                actor_role=actor_role,
                confirmation_id=conf.id,
            )
            return {
                "ok": False,
                "code": 409,
                "state_changed": state_changed,
                "detail": reason_txt,
                "status": t.status,
                "outcome": "failed" if not state_changed else "open",
                "response": reply,
                "ticket_id": t.id,
                "confirmation": confirmation_dict(conf),
            }

        # If underlying order state changed (e.g. amount or address changed) while still eligible, require fresh confirmation!
        if state_changed:
            conf.status = "superseded"
            conf.resolved_at = utc_now()
            new_conf = create_pending_confirmation(
                s,
                ticket_id=t.id,
                customer_id=conf.customer_id,
                order=live_order,
                plan=plan.model_dump(),
                policy_decision=decision.decision,
                reason=conf.reason,
            )
            new_conf_dict = confirmation_dict(new_conf)
            transition_ticket_status(t, "awaiting_customer")
            facts = {
                "action_to_confirm": conf.action,
                "eligibility_label": new_conf_dict["eligibility_label"],
                "processing_info": new_conf_dict["processing_info"],
                "order_summary": {
                    "id": live_order["id"],
                    "item": live_order["item"],
                    "amount": live_order["amount"],
                    "status": live_order["status"],
                    "current_address": live_order["shipping_address"],
                    "new_address": conf.new_address,
                    "reason": conf.reason,
                },
            }
            reply = (
                "The details of your order changed since your previous confirmation. "
                "Please review the updated details and confirm again:\n\n"
                + format_confirmation_summary(facts)
            )
            t.final_response = reply
            t.pending_action = json.dumps({
                "plan": plan.model_dump(),
                "intake": {"intent": t.issue_type, "order_id": live_order["id"]},
                "policy": decision.model_dump(),
                "confirmation_id": new_conf.id,
            })
            append_ticket_trace(t, [ev("confirmation", f"Order state changed; created fresh confirmation {new_conf.id}", stage="confirmation_requested")])
            meta = {"card_type": "confirmation", "confirmation": new_conf_dict, "state_changed": True}
            s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps(meta)))
            s.commit()
            return {
                "ok": True,
                "code": 200,
                "reconfirmation_required": True,
                "ticket_id": t.id,
                "status": "awaiting_customer",
                "outcome": "needs_confirmation",
                "response": reply,
                "confirmation": new_conf_dict,
                "metadata": meta,
            }

        # High-value refund / manager approval path: submit for review, DO NOT execute or claim processed!
        if decision.decision == "needs_approval":
            conf.status = "confirmed"
            conf.resolved_at = utc_now()
            transition_ticket_status(t, "awaiting_approval")
            t.pending_action = json.dumps({
                "plan": plan.model_dump(),
                "intake": {"intent": t.issue_type, "order_id": live_order["id"]},
                "policy": decision.model_dump(),
                "confirmation_id": conf.id,
            })
            reply = (
                f"Your refund request for order {live_order['id']} ({live_order['item']}, ${live_order['amount']:.2f}) "
                "has been submitted for human manager review because it exceeds the $100.00 automatic refund limit.\n\n"
                "Status: Pending Manager Approval\n"
                "No refund has been processed yet. We will notify you as soon as a manager completes the review."
            )
            t.final_response = reply
            append_ticket_trace(t, [
                ev("approval", "Submitted to manager approval queue after customer confirmation", stage="approval_pending"),
                ev("respond", "Notified customer request is pending human approval", stage="response_sent"),
            ])
            meta = {
                "card_type": "approval_pending",
                "order_id": live_order["id"],
                "item": live_order["item"],
                "amount": live_order["amount"],
                "action": conf.action,
                "confirmation_id": conf.id,
                "reasons": decision.reasons,
            }
            s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps(meta)))
            s.commit()
            write_tools.record_audit_event(
                action="approval_submitted",
                status="approved",
                detail=f"Customer confirmed {conf.action} for {conf.order_id}; routed to manager approval.",
                ticket_id=t.id,
                order_id=conf.order_id,
                customer_id=conf.customer_id,
                actor_id=customer_id,
                actor_role=actor_role,
                confirmation_id=conf.id,
            )
            return {
                "ok": True,
                "code": 200,
                "ticket_id": t.id,
                "order_id": live_order["id"],
                "status": "awaiting_approval",
                "outcome": "pending_approval",
                "response": reply,
                "confirmation": confirmation_dict(conf),
                "metadata": meta,
            }

        # Policy allowed -> execute atomic database mutation using confirmation idempotency key
        s.commit()  # release read/write lock cleanly before write_tools opens its own atomic transaction

        if conf.action == "refund":
            exec_res = write_tools.issue_refund(
                conf.order_id,
                live_order["amount"],
                conf.reason or "Customer confirmed refund",
                conf.idempotency_key,
                ticket_id=t.id,
                actor_id=customer_id,
                actor_role=actor_role,
                confirmation_id=conf.id,
            )
        elif conf.action == "cancel":
            exec_res = write_tools.cancel_order(
                conf.order_id,
                conf.idempotency_key,
                ticket_id=t.id,
                actor_id=customer_id,
                actor_role=actor_role,
                confirmation_id=conf.id,
            )
        else:
            exec_res = write_tools.change_address(
                conf.order_id,
                conf.new_address,
                conf.idempotency_key,
                ticket_id=t.id,
                actor_id=customer_id,
                actor_role=actor_role,
                confirmation_id=conf.id,
            )

        # Re-load ticket & confirmation for final state update
        conf = s.get(m.PendingConfirmation, confirmation_id)
        t = s.get(m.Ticket, conf.ticket_id)

        if exec_res.get("error") or not exec_res.get("executed"):
            is_dup = bool(exec_res.get("duplicate"))
            conf.status = "superseded" if is_dup else "failed"
            conf.resolved_at = utc_now()
            t.pending_action = None
            transition_ticket_status(t, "open" if is_dup else "failed")
            err_detail = exec_res.get("detail") or "The database operation could not be completed."
            reply = (
                f"This {conf.action} request for order {conf.order_id} was not executed because "
                f"{err_detail.lower()}" if is_dup else
                f"We were unable to complete your {conf.action} request for order {conf.order_id}. "
                f"Reason: {err_detail} No confirmed change has been applied."
            )
            t.final_response = reply
            append_ticket_trace(t, [
                ev("execute", f"{conf.action} not executed: {err_detail}", stage="action_failed", tool=conf.action),
                ev("respond", "Communicated execution outcome to customer", stage="response_sent"),
            ])
            meta = {"card_type": "execution_failed", "order_id": conf.order_id, "action": conf.action, "detail": err_detail, "duplicate": is_dup}
            s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps(meta)))
            s.commit()
            return {
                "ok": False,
                "code": 500 if exec_res.get("error") else 409,
                "ticket_id": t.id,
                "order_id": conf.order_id,
                "status": t.status,
                "outcome": "open" if is_dup else "failed",
                "verified": False,
                "duplicate": is_dup,
                "detail": err_detail,
                "response": reply,
                "confirmation": confirmation_dict(conf),
                "metadata": meta,
            }

        append_ticket_trace(t, [ev("execute", f"{conf.action}: {exec_res['detail']}", stage="action_executed", tool=conf.action)])

        # Step 5: Re-read database to verify actual result before EVER claiming success
        ver = verify(conf.action, conf.order_id, conf.new_address)
        if not ver.ok:
            conf.status = "failed"
            conf.resolved_at = utc_now()
            t.pending_action = None
            transition_ticket_status(t, "failed")
            reply = (
                f"Your {conf.action} request for order {conf.order_id} could not be verified in our database "
                f"({ver.detail}). Its status is currently uncertain—please do not resubmit. "
                "Our support team has been alerted to verify and resolve your request."
            )
            t.final_response = reply
            t.handoff = json.dumps({
                "issue": t.message,
                "customer": {"id": t.customer_id},
                "order": {"id": conf.order_id, "amount": live_order.get("amount"), "status": live_order.get("status")},
                "proposed_action": conf.action,
                "reason_for_escalation": f"Post-execution verification failed: {ver.detail}",
            })
            append_ticket_trace(t, [
                ev("verify", f"Verification failed: {ver.detail}", stage="action_failed"),
                ev("respond", "Communicated uncertain/unverified status to customer", stage="response_sent"),
            ])
            meta = {
                "card_type": "verification_failed",
                "order_id": conf.order_id,
                "action": conf.action,
                "detail": ver.detail,
            }
            s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps(meta)))
            s.commit()
            write_tools.record_audit_event(
                action=f"{conf.action}_verify_failed",
                status="failed",
                detail=ver.detail,
                ticket_id=t.id,
                order_id=conf.order_id,
                customer_id=conf.customer_id,
                actor_id="system",
                actor_role="system",
                confirmation_id=conf.id,
            )
            return {
                "ok": False,
                "code": 500,
                "ticket_id": t.id,
                "order_id": conf.order_id,
                "status": "failed",
                "outcome": "failed",
                "verified": False,
                "detail": ver.detail,
                "response": reply,
                "confirmation": confirmation_dict(conf),
                "metadata": meta,
            }

        # Verified success! Re-read fresh order for receipt
        fresh_order = call(get_order, order_id=conf.order_id)
        ref_id = exec_res.get("reference_id")
        if not ref_id and conf.action == "refund":
            ref_row = s.query(m.Refund).filter_by(order_id=conf.order_id).first()
            ref_id = ref_row.reference_id if ref_row else None

        conf.status = "confirmed"
        conf.resolved_at = utc_now()
        t.pending_action = None
        transition_ticket_status(t, "resolved")
        t.resolution_mode = "autonomous"
        reply = format_verified_receipt(
            conf.action,
            fresh_order,
            {"reference_id": ref_id, "new_address": conf.new_address},
        )
        t.final_response = reply
        append_ticket_trace(t, [
            ev("verify", ver.detail, stage="action_verified"),
            ev("respond", "Delivered verified execution receipt to customer", stage="response_sent"),
        ])
        meta = {
            "card_type": "receipt",
            "action": conf.action,
            "order_id": fresh_order["id"],
            "item": fresh_order["item"],
            "amount": fresh_order["amount"],
            "order_status": fresh_order["status"],
            "refunded": fresh_order["already_refunded"],
            "shipping_address": fresh_order["shipping_address"],
            "reference_id": ref_id,
            "confirmation_id": conf.id,
            "verified": True,
        }
        s.add(m.TicketMessage(ticket_id=t.id, role="agent", text=reply, metadata_json=json.dumps(meta)))
        s.commit()

        return {
            "ok": True,
            "code": 200,
            "ticket_id": t.id,
            "order_id": fresh_order["id"],
            "status": "resolved",
            "outcome": "resolved",
            "verified": True,
            "reference_id": ref_id,
            "response": reply,
            "confirmation": confirmation_dict(conf),
            "metadata": meta,
        }
    finally:
        s.close()


def save_result(ticket_id: str, st: dict, resolution_mode: str | None = None) -> dict:
    """Persist workflow output, create PendingConfirmation if needed, and return extra UI metadata."""
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t:
            return {}
        t.issue_type = (st.get("intake") or {}).get("intent", t.issue_type)
        oid = (st.get("intake") or {}).get("order_id") or (st.get("plan") or {}).get("order_id")
        if oid:
            t.order_id = oid

        outcome = st.get("outcome") or "resolved"
        target_status = OUTCOME_TO_STATUS.get(outcome, "escalated")
        transition_ticket_status(t, target_status)

        if target_status == "resolved":
            t.resolution_mode = resolution_mode or t.resolution_mode or "autonomous"

        resp = st.get("final_response")
        t.final_response = resp
        # Append new trace events onto existing ticket trace (preserving multi-turn history)
        existing_trace = json.loads(t.trace or "[]")
        new_trace = st.get("trace", [])
        if st.get("_replace_trace"):
            t.trace = json.dumps(new_trace)
        else:
            t.trace = json.dumps(existing_trace + new_trace)

        t.handoff = json.dumps(st["handoff"]) if st.get("handoff") else t.handoff

        msg_meta: dict = {}
        order = (st.get("context") or {}).get("order")
        if order and order.get("found"):
            msg_meta["order"] = {
                "id": order.get("id"),
                "item": order.get("item"),
                "amount": order.get("amount"),
                "status": order.get("status"),
                "refunded": bool(order.get("already_refunded")),
            }

        if outcome == "needs_confirmation":
            plan = st["plan"]
            policy_dec = (st.get("policy") or {}).get("decision", "allow")
            reason = (st.get("intake") or {}).get("reason") or plan.get("reason")
            conf = create_pending_confirmation(
                s,
                ticket_id=ticket_id,
                customer_id=t.customer_id or order.get("customer_id"),
                order=order,
                plan=plan,
                policy_decision=policy_dec,
                reason=reason,
            )
            conf_d = confirmation_dict(conf)
            t.pending_action = json.dumps({
                "plan": plan,
                "intake": st["intake"],
                "policy": st.get("policy"),
                "confirmation_id": conf.id,
            })
            msg_meta["card_type"] = "confirmation"
            msg_meta["confirmation"] = conf_d
            write_tools.record_audit_event(
                action="confirmation_requested",
                status="success",
                detail=f"Requested customer confirmation ({conf.id}) for {plan['action']} on {order['id']}",
                ticket_id=ticket_id,
                order_id=order["id"],
                customer_id=t.customer_id,
                actor_id="system",
                actor_role="system",
                confirmation_id=conf.id,
            )
        elif outcome == "pending_approval":
            t.pending_action = json.dumps({
                "plan": st["plan"],
                "intake": st["intake"],
                "policy": st.get("policy"),
            })
            msg_meta["card_type"] = "approval_pending"
        elif outcome == "resolved":
            t.pending_action = None
            ver = st.get("verification")
            if ver and ver.get("ok"):
                msg_meta["card_type"] = "receipt"
                msg_meta["reference_id"] = (st.get("exec_result") or {}).get("reference_id")
                msg_meta["verified"] = True
        elif outcome == "failed":
            t.pending_action = None
            msg_meta["card_type"] = "execution_failed"
        elif st.get("order_selection_needed"):
            facts = build_facts(st)
            msg_meta["card_type"] = "order_selection"
            msg_meta["available_orders"] = facts.get("available_orders", [])
            msg_meta["requested_action_label"] = facts.get("requested_action_label", "help with")

        if resp:
            s.add(m.TicketMessage(
                ticket_id=ticket_id,
                role="agent",
                text=resp,
                metadata_json=json.dumps(msg_meta) if msg_meta else None,
            ))
        s.commit()
        return msg_meta
    finally:
        s.close()


def ticket_dict(t: m.Ticket, full: bool = False, session=None) -> dict:
    d = {
        "id": t.id,
        "customer_id": t.customer_id,
        "order_id": t.order_id,
        "issue_type": t.issue_type,
        "status": t.status,
        "resolution_mode": t.resolution_mode,
        "message": t.message,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else t.created_at.isoformat(),
    }

    close_session = False
    s = session
    if s is None:
        s = get_session()
        close_session = True
    try:
        msg_rows = (
            s.query(m.TicketMessage)
            .filter_by(ticket_id=t.id)
            .order_by(m.TicketMessage.created_at.asc(), m.TicketMessage.id.asc())
            .all()
        )
        if msg_rows:
            d["messages"] = [
                {
                    "id": r.id,
                    "role": r.role,
                    "text": r.text,
                    "metadata": json.loads(r.metadata_json) if r.metadata_json else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in msg_rows
            ]
        else:
            msgs = []
            if t.message:
                msgs.append({"id": 1, "role": "customer", "text": t.message, "metadata": None, "created_at": t.created_at.isoformat()})
            if t.final_response:
                msgs.append({"id": 2, "role": "agent", "text": t.final_response, "metadata": None, "created_at": t.created_at.isoformat()})
            d["messages"] = msgs

        confs = (
            s.query(m.PendingConfirmation)
            .filter_by(ticket_id=t.id)
            .order_by(m.PendingConfirmation.created_at.desc())
            .all()
        )
        d["confirmations"] = [confirmation_dict(c) for c in confs]
        active = next((c for c in d["confirmations"] if c["status"] == "pending"), None)
        d["active_confirmation"] = active

        if full:
            audit_rows = (
                s.query(m.ActionLog)
                .filter_by(ticket_id=t.id)
                .order_by(m.ActionLog.created_at.asc(), m.ActionLog.id.asc())
                .all()
            )
            d.update(
                final_response=t.final_response,
                pending_action=json.loads(t.pending_action) if t.pending_action else None,
                handoff=json.loads(t.handoff) if t.handoff else None,
                trace=json.loads(t.trace) if t.trace else [],
                audit_logs=[
                    {
                        "id": a.id,
                        "action": a.action,
                        "order_id": a.order_id,
                        "actor_id": a.actor_id,
                        "actor_role": a.actor_role,
                        "confirmation_id": a.confirmation_id,
                        "status": a.status,
                        "detail": a.detail,
                        "created_at": a.created_at.isoformat(),
                    }
                    for a in audit_rows
                ],
            )
    finally:
        if close_session:
            s.close()

    return d


def mark_failed(ticket_id: str, error: Exception, escalate: bool = True):
    """If the workflow crashes, mark ticket cleanly without exposing internal stack traces or secrets."""
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t:
            return
        target = "escalated" if escalate else "failed"
        transition_ticket_status(t, target)
        error_msg = "Sorry, we could not process your request automatically. A human support specialist will follow up."
        t.final_response = error_msg
        t.handoff = json.dumps({
            "issue": t.message,
            "customer": {"id": t.customer_id},
            "reason_for_escalation": f"System error before completion: {type(error).__name__}",
        })
        append_ticket_trace(t, [ev("escalate" if escalate else "execute", f"Workflow interrupted ({type(error).__name__})", stage="action_failed")])
        s.add(m.TicketMessage(
            ticket_id=ticket_id,
            role="agent",
            text=error_msg,
            metadata_json=json.dumps({"card_type": "execution_failed"}),
        ))
        s.commit()
    finally:
        s.close()


def list_customer_orders(customer_id: str) -> list[dict]:
    """Retrieve full order details, shipment, refund history, and backend-computed action eligibility."""
    s = get_session()
    try:
        orders = s.query(m.Order).filter_by(customer_id=customer_id).order_by(m.Order.placed_at.desc()).all()
        result = []
        for o in orders:
            ship = s.query(m.Shipment).filter_by(order_id=o.id).first()
            refund = s.query(m.Refund).filter_by(order_id=o.id).first()

            order_dict = {
                "found": True,
                "id": o.id,
                "customer_id": o.customer_id,
                "item": o.item,
                "amount": o.amount,
                "status": o.status,
                "delivered_at": o.delivered_at.strftime("%Y-%m-%d") if o.delivered_at else None,
                "shipping_address": o.shipping_address,
                "already_refunded": bool(o.refunded),
            }

            ref_dec = check_action(
                ActionPlan(action="refund", order_id=o.id, amount=o.amount, reason="eligibility", confidence=1.0),
                order_dict,
                customer_id,
            )
            can_dec = check_action(
                ActionPlan(action="cancel", order_id=o.id, reason="eligibility", confidence=1.0),
                order_dict,
                customer_id,
            )
            addr_dec = check_action(
                ActionPlan(action="change_address", order_id=o.id, new_address="123 Example Street", reason="eligibility", confidence=1.0),
                order_dict,
                customer_id,
            )

            actions = [
                {
                    "action": "refund",
                    "label": "Request Refund",
                    "available": ref_dec.decision in ("allow", "needs_approval"),
                    "requires_approval": ref_dec.decision == "needs_approval",
                    "reason": "; ".join(ref_dec.reasons),
                },
                {
                    "action": "cancel",
                    "label": "Cancel Order",
                    "available": can_dec.decision == "allow",
                    "requires_approval": False,
                    "reason": "; ".join(can_dec.reasons),
                },
                {
                    "action": "change_address",
                    "label": "Update Address",
                    "available": addr_dec.decision == "allow",
                    "requires_approval": False,
                    "reason": "; ".join(addr_dec.reasons),
                },
            ]

            result.append({
                "id": o.id,
                "item": o.item,
                "amount": o.amount,
                "status": o.status,
                "placed_at": o.placed_at.strftime("%Y-%m-%d"),
                "delivered_at": o.delivered_at.strftime("%Y-%m-%d") if o.delivered_at else None,
                "shipping_address": o.shipping_address,
                "refunded": bool(o.refunded),
                "refund": {
                    "amount": refund.amount,
                    "reason": refund.reason,
                    "reference_id": refund.reference_id,
                    "created_at": refund.created_at.strftime("%Y-%m-%d"),
                } if refund else None,
                "shipment": {
                    "carrier": ship.carrier,
                    "tracking_number": ship.tracking_number,
                    "status": ship.status,
                    "last_update": ship.last_update,
                    "eta": ship.eta,
                } if ship else None,
                "actions": actions,
            })
        return result
    finally:
        s.close()
