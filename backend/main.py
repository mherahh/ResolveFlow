"""FastAPI app. Run with:  uvicorn main:app --reload --port 8000"""
import json
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Ensure backend root is always on sys.path regardless of execution CWD
_backend_dir = Path(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import auth
import services
from agents.intake import classify_pending_reply
from config import settings
from database.db import get_session
from database import models as m
from graph.workflow import ev
from models.schemas import (
    ApprovalRequest,
    ChatRequest,
    ConfirmationActionRequest,
    DemoSwitchRequest,
    LoginRequest,
    RegisterRequest,
)
from tools import write_tools


class ResolveRequest(BaseModel):
    note: Optional[str] = None


EMAIL_SIMPLE_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
log = logging.getLogger("resolveflow")
GRAPH = None  # built lazily so the app can start (and tests can inject a fake) without an API key


def get_graph():
    global GRAPH
    if GRAPH is None:
        from config import get_llm
        from graph.workflow import build_graph
        GRAPH = build_graph(get_llm())
    return GRAPH


@asynccontextmanager
async def lifespan(_app):
    services.init_db()
    yield


app = FastAPI(title="ResolveFlow API", lifespan=lifespan)

_allowed_origins = list({
    settings.frontend_origin,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _set_session_cookie(response: Response, token: str):
    response.set_cookie(
        key=auth.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )


def _user_public(c: m.Customer) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "tier": c.tier,
        "role": c.role or "customer",
    }


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "ResolveFlow Customer Support API",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/api/health",
        "demo_mode": settings.demo_mode,
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "demo_mode": settings.demo_mode,
    }


# ==================== Authentication & Session Endpoints ====================


@app.get("/api/auth/config")
def auth_config():
    return {"demo_mode_enabled": settings.demo_mode}


@app.post("/api/auth/register")
def register(req: RegisterRequest, response: Response):
    email = req.email.strip().lower()
    if not EMAIL_SIMPLE_RE.match(email):
        raise HTTPException(400, "Please provide a valid email address.")
    name = req.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "Please provide your full name.")

    s = get_session()
    try:
        if s.query(m.Customer).filter(m.Customer.email == email).first():
            raise HTTPException(409, "An account with this email address already exists.")
        cid = auth.generate_customer_id(s)
        cust = m.Customer(
            id=cid,
            name=name,
            email=email,
            tier="standard",
            role="customer",
            password_hash=auth.hash_password(req.password),
        )
        s.add(cust)
        s.commit()
        user_payload = _user_public(cust)
    finally:
        s.close()

    token, expires_at = auth.create_session(user_payload["id"], role="customer", is_demo=False)
    _set_session_cookie(response, token)
    return {
        "token": token,
        "user": user_payload,
        "is_demo": False,
        "expires_at": expires_at.isoformat(),
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    email = req.email.strip().lower()
    s = get_session()
    try:
        cust = s.query(m.Customer).filter(m.Customer.email == email).first()
        if not cust or not auth.verify_password(req.password, cust.password_hash):
            raise HTTPException(401, "Invalid email or password.")
        user_payload = _user_public(cust)
    finally:
        s.close()

    token, expires_at = auth.create_session(user_payload["id"], role=user_payload["role"], is_demo=False)
    _set_session_cookie(response, token)
    return {
        "token": token,
        "user": user_payload,
        "is_demo": False,
        "expires_at": expires_at.isoformat(),
    }


@app.post("/api/auth/logout")
def logout(response: Response, user: dict = Depends(auth.get_current_user)):
    auth.revoke_session(user["token"])
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return {"status": "logged_out"}


@app.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)):
    return {
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "tier": user["tier"],
            "role": user["role"],
        },
        "is_demo": user["is_demo"],
        "expires_at": user["expires_at"],
        "demo_mode_enabled": settings.demo_mode,
    }


# ==================== Developer / Demo Mode Endpoints ====================


@app.get("/api/demo/accounts")
def demo_accounts():
    if not settings.demo_mode:
        raise HTTPException(403, "Demo Mode is disabled in this environment.")
    s = get_session()
    try:
        rows = s.query(m.Customer).order_by(m.Customer.role.asc(), m.Customer.id.asc()).all()
        return [_user_public(c) for c in rows]
    finally:
        s.close()


@app.post("/api/demo/switch")
def demo_switch(req: DemoSwitchRequest, response: Response):
    """Switch between seeded demo accounts by issuing a genuine server-side session."""
    if not settings.demo_mode:
        raise HTTPException(403, "Demo Mode is disabled in this environment.")
    s = get_session()
    try:
        cust = s.get(m.Customer, req.account_id)
        if not cust:
            raise HTTPException(404, "Demo account not found.")
        user_payload = _user_public(cust)
    finally:
        s.close()

    token, expires_at = auth.create_session(user_payload["id"], role=user_payload["role"], is_demo=True)
    _set_session_cookie(response, token)
    return {
        "token": token,
        "user": user_payload,
        "is_demo": True,
        "expires_at": expires_at.isoformat(),
    }


# ==================== Customer & Order Endpoints ====================


@app.get("/api/customers")
def customers(_manager: dict = Depends(auth.require_manager)):
    s = get_session()
    try:
        return [_user_public(c) for c in s.query(m.Customer).filter_by(role="customer").all()]
    finally:
        s.close()


@app.get("/api/customers/me/orders")
def my_orders(user: dict = Depends(auth.get_current_user)):
    return services.list_customer_orders(user["id"])


@app.get("/api/customers/{customer_id}/orders")
def customer_orders(customer_id: str, user: dict = Depends(auth.get_current_user)):
    if user["role"] == "customer" and customer_id != user["id"]:
        raise HTTPException(403, "You are not authorized to view another customer's orders.")
    return services.list_customer_orders(customer_id)


# ==================== Chat & Multi-Turn Confirmation Handling ====================


def _prepare_chat_turn(req: ChatRequest, user: dict) -> dict:
    """Validate ownership, check pending confirmations, and prepare either a direct shortcut or graph state."""
    # Prevent frontend customer_id spoofing
    if req.customer_id and user["role"] == "customer" and req.customer_id != user["id"]:
        raise HTTPException(403, "Customer ID mismatch: you may only act on your own authenticated account.")

    # Resolve authoritative customer_id server-side
    customer_id = user["id"]
    ticket_id = req.ticket_id

    # Optional explicit order_id from order sidebar click: verify ownership first!
    explicit_order_id = None
    if req.order_id:
        s = get_session()
        try:
            o = s.get(m.Order, req.order_id.strip().upper())
            if o and (user["role"] in ("manager", "admin") or o.customer_id == customer_id):
                explicit_order_id = o.id
            elif o and user["role"] == "customer":
                raise HTTPException(403, "You are not authorized to reference another customer's order.")
        finally:
            s.close()

    if ticket_id:
        s = get_session()
        try:
            t = s.get(m.Ticket, ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found.")
            if user["role"] == "customer" and t.customer_id != customer_id:
                raise HTTPException(403, "You are not authorized to access another customer's support ticket.")
            if user["role"] in ("manager", "admin") and t.customer_id:
                customer_id = t.customer_id
        finally:
            s.close()

        # Check active confirmation on this ticket BEFORE running LLM
        active_conf, just_expired = services.get_active_confirmation(ticket_id, customer_id)

        if just_expired and active_conf:
            services.add_customer_message(ticket_id, req.message, customer_id)
            reply_kind = classify_pending_reply(req.message)
            if reply_kind == "confirm_action":
                reply = (
                    f"Your previous pending {active_conf['action']} confirmation for order {active_conf['order_id']} "
                    "has expired for your security. No action was executed. "
                    "Please submit your request again if you would still like to proceed."
                )
                s = get_session()
                try:
                    t = s.get(m.Ticket, ticket_id)
                    if t:
                        services.transition_ticket_status(t, "open")
                        t.final_response = reply
                        s.add(m.TicketMessage(
                            ticket_id=ticket_id,
                            role="agent",
                            text=reply,
                            metadata_json=json.dumps({"card_type": "confirmation_expired", "confirmation_id": active_conf["id"]}),
                        ))
                        s.commit()
                finally:
                    s.close()
                return {
                    "direct": True,
                    "ticket_id": ticket_id,
                    "order_id": active_conf["order_id"],
                    "outcome": "open",
                    "status": "open",
                    "response": reply,
                    "metadata": {"card_type": "confirmation_expired", "confirmation_id": active_conf["id"]},
                    "trace": [ev("confirmation", f"Rejected confirmation of expired token {active_conf['id']}", stage="confirmation_expired")],
                }

        if active_conf:
            reply_kind = classify_pending_reply(req.message)
            services.add_customer_message(ticket_id, req.message, customer_id)

            if reply_kind == "confirm_action":
                res = services.execute_pending_confirmation(
                    active_conf["id"],
                    customer_id=customer_id,
                    actor_role=user["role"],
                    expected_ticket_id=ticket_id,
                )
                return {
                    "direct": True,
                    "ticket_id": ticket_id,
                    "order_id": res.get("order_id") or active_conf["order_id"],
                    "outcome": res.get("outcome") or res.get("status") or "failed",
                    "status": res.get("status") or "failed",
                    "response": res.get("response") or res.get("detail"),
                    "confirmation": res.get("confirmation"),
                    "metadata": res.get("metadata"),
                    "trace": [ev("confirmation", f"Processed confirmation {active_conf['id']}", stage="customer_authorized")],
                }

            if reply_kind == "cancel_action":
                res = services.cancel_pending_confirmation(
                    active_conf["id"],
                    customer_id=customer_id,
                    actor_role=user["role"],
                )
                return {
                    "direct": True,
                    "ticket_id": ticket_id,
                    "order_id": active_conf["order_id"],
                    "outcome": "open",
                    "status": "open",
                    "response": res["response"],
                    "confirmation": res.get("confirmation"),
                    "metadata": res.get("metadata"),
                    "trace": [ev("confirmation", f"Cancelled pending confirmation {active_conf['id']}", stage="confirmation_cancelled")],
                }

            if reply_kind == "ambiguous":
                reply = (
                    f"I want to make sure before making any changes to order {active_conf['order_id']}. "
                    f"Please use the Confirm or Cancel button on the pending {active_conf['action']} card, "
                    "or reply explicitly with 'Yes, confirm' to proceed or 'Cancel' to abort."
                )
                meta = {"card_type": "confirmation_reminder", "confirmation": active_conf}
                s = get_session()
                try:
                    t = s.get(m.Ticket, ticket_id)
                    if t:
                        services.transition_ticket_status(t, "awaiting_customer")
                        t.final_response = reply
                        services.append_ticket_trace(t, [
                            ev("confirmation", "Ambiguous reply received; pending action NOT executed", stage="confirmation_requested")
                        ])
                        s.add(m.TicketMessage(ticket_id=ticket_id, role="agent", text=reply, metadata_json=json.dumps(meta)))
                        s.commit()
                finally:
                    s.close()
                return {
                    "direct": True,
                    "ticket_id": ticket_id,
                    "order_id": active_conf["order_id"],
                    "outcome": "needs_confirmation",
                    "status": "awaiting_customer",
                    "response": reply,
                    "confirmation": active_conf,
                    "metadata": meta,
                    "trace": [ev("confirmation", "Ambiguous reply did not authorize pending action", stage="confirmation_requested")],
                }

            # Otherwise ("other"): customer sent a new message / correction / question; proceed to graph
            history = services.get_conversation_history(ticket_id)
        else:
            services.add_customer_message(ticket_id, req.message, customer_id)
            history = services.get_conversation_history(ticket_id)
    else:
        ticket_id = services.create_ticket(req.message, customer_id, order_id=explicit_order_id)
        history = [{"role": "customer", "text": req.message}]

    # API requests ALWAYS require explicit confirmation for financial/state-changing actions
    state = {
        "message": req.message,
        "customer_id": customer_id,
        "ticket_id": ticket_id,
        "conversation_history": history,
        "trace": [],
        "require_confirmation": True,
        "confirmed": False,
        "actor_id": user["id"],
        "actor_role": user["role"],
    }
    if explicit_order_id:
        state["intake"] = {"order_id": explicit_order_id}
    return {"direct": False, "ticket_id": ticket_id, "state": state}


@app.post("/api/chat")
def chat(req: ChatRequest, user: dict = Depends(auth.get_current_user)):
    prep = _prepare_chat_turn(req, user)
    if prep["direct"]:
        return {
            "ticket_id": prep["ticket_id"],
            "order_id": prep.get("order_id"),
            "outcome": prep["outcome"],
            "status": prep["status"],
            "response": prep["response"],
            "confirmation": prep.get("confirmation"),
            "metadata": prep.get("metadata"),
            "trace": prep.get("trace", []),
        }

    ticket_id, state = prep["ticket_id"], prep["state"]
    final = None
    try:
        for kind, payload in services.stream_graph(get_graph(), state):
            if kind == "final":
                final = payload
    except Exception as e:
        log.exception("Workflow failed for %s", ticket_id)
        services.mark_failed(ticket_id, e)
        raise HTTPException(
            503,
            f"The AI service is temporarily unavailable. Ticket {ticket_id} was escalated to a human specialist.",
        )

    msg_meta = services.save_result(ticket_id, final)
    return {
        "ticket_id": ticket_id,
        "order_id": (final.get("intake") or {}).get("order_id") or (final.get("plan") or {}).get("order_id"),
        "outcome": final["outcome"],
        "status": services.OUTCOME_TO_STATUS.get(final["outcome"], "escalated"),
        "response": final["final_response"],
        "confirmation": msg_meta.get("confirmation"),
        "metadata": msg_meta or None,
        "trace": final["trace"],
    }


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, user: dict = Depends(auth.get_current_user)):
    """Newline-delimited JSON: one line per workflow step, then one 'final' line."""
    prep = _prepare_chat_turn(req, user)

    def gen():
        ticket_id = prep["ticket_id"]
        yield json.dumps({"type": "ticket", "ticket_id": ticket_id}) + "\n"
        if prep["direct"]:
            for te in prep.get("trace", []):
                yield json.dumps({"type": "event", "event": te}) + "\n"
            yield json.dumps({
                "type": "final",
                "ticket_id": ticket_id,
                "order_id": prep.get("order_id"),
                "outcome": prep["outcome"],
                "status": prep["status"],
                "response": prep["response"],
                "confirmation": prep.get("confirmation"),
                "metadata": prep.get("metadata"),
            }) + "\n"
            return

        state = prep["state"]
        try:
            for kind, payload in services.stream_graph(get_graph(), state):
                if kind == "event":
                    yield json.dumps({"type": "event", "event": payload}) + "\n"
                else:
                    msg_meta = services.save_result(ticket_id, payload)
                    yield json.dumps({
                        "type": "final",
                        "ticket_id": ticket_id,
                        "order_id": (payload.get("intake") or {}).get("order_id") or (payload.get("plan") or {}).get("order_id"),
                        "outcome": payload["outcome"],
                        "status": services.OUTCOME_TO_STATUS.get(payload["outcome"], "escalated"),
                        "response": payload["final_response"],
                        "confirmation": msg_meta.get("confirmation"),
                        "metadata": msg_meta or None,
                    }) + "\n"
        except Exception as e:
            log.exception("Workflow failed for %s", ticket_id)
            services.mark_failed(ticket_id, e)
            yield json.dumps({
                "type": "error",
                "ticket_id": ticket_id,
                "message": "Sorry, we could not process your request automatically. A human specialist will follow up.",
            }) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ==================== Explicit Confirmation Action Endpoints ====================


@app.get("/api/tickets/{ticket_id}/confirmations")
def ticket_confirmations(ticket_id: str, user: dict = Depends(auth.get_current_user)):
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")
        if user["role"] == "customer" and t.customer_id != user["id"]:
            raise HTTPException(403, "Not authorized to access this ticket's confirmations.")
        rows = (
            s.query(m.PendingConfirmation)
            .filter_by(ticket_id=ticket_id)
            .order_by(m.PendingConfirmation.created_at.desc())
            .all()
        )
        return [services.confirmation_dict(c) for c in rows]
    finally:
        s.close()


@app.post("/api/confirmations/{confirmation_id}/confirm")
def confirm_action(
    confirmation_id: str,
    _body: Optional[ConfirmationActionRequest] = None,
    user: dict = Depends(auth.get_current_user),
):
    res = services.execute_pending_confirmation(
        confirmation_id=confirmation_id,
        customer_id=user["id"],
        actor_role=user["role"],
    )
    if not res["ok"]:
        raise HTTPException(status_code=res["code"], detail=res.get("detail") or "Confirmation could not be executed.")
    return res


@app.post("/api/confirmations/{confirmation_id}/cancel")
def cancel_action(
    confirmation_id: str,
    body: Optional[ConfirmationActionRequest] = None,
    user: dict = Depends(auth.get_current_user),
):
    res = services.cancel_pending_confirmation(
        confirmation_id=confirmation_id,
        customer_id=user["id"],
        actor_role=user["role"],
        note=body.note if body else None,
    )
    if not res["ok"]:
        raise HTTPException(status_code=res["code"], detail=res.get("detail") or "Confirmation could not be cancelled.")
    return res


# ==================== Dashboard, Tickets, Approvals & Activity ====================


@app.get("/api/dashboard")
def dashboard(user: dict = Depends(auth.get_current_user)):
    s = get_session()
    try:
        q = s.query(m.Ticket).order_by(m.Ticket.created_at.desc())
        if user["role"] == "customer":
            q = q.filter(m.Ticket.customer_id == user["id"])
        tickets = q.all()

        count = lambda st: sum(1 for t in tickets if t.status == st)
        total = len(tickets)
        resolved = count("resolved")
        autonomous_resolved = sum(1 for t in tickets if t.status == "resolved" and t.resolution_mode == "autonomous")
        escalated = count("escalated")
        pending_approvals = count("awaiting_approval")
        awaiting_customer = count("awaiting_customer")
        failed = count("failed")
        open_count = count("open")
        processing = count("processing")

        intents = {}
        for t in tickets:
            if t.issue_type:
                intents[t.issue_type] = intents.get(t.issue_type, 0) + 1

        resolution_rate = round((resolved / total * 100), 1) if total > 0 else 0.0
        autonomous_resolution_rate = round((autonomous_resolved / total * 100), 1) if total > 0 else 0.0

        return {
            "scope": "all" if user["role"] in ("manager", "admin") else "customer",
            "total": total,
            "resolved": resolved,
            "autonomous_resolved": autonomous_resolved,
            "autonomous_resolution_rate": autonomous_resolution_rate,
            "resolution_rate": resolution_rate,
            "escalated": escalated,
            "pending_approvals": pending_approvals,
            "awaiting_customer": awaiting_customer,
            "failed": failed,
            "open": open_count,
            "processing": processing,
            "intents": intents,
            "recent": [services.ticket_dict(t, session=s) for t in tickets[:8]],
        }
    finally:
        s.close()


@app.get("/api/tickets")
def tickets(
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    search: Optional[str] = None,
    user: dict = Depends(auth.get_current_user),
):
    if user["role"] == "customer" and customer_id and customer_id != user["id"]:
        raise HTTPException(403, "You are not authorized to view another customer's tickets.")

    s = get_session()
    try:
        q = s.query(m.Ticket)
        if user["role"] == "customer":
            q = q.filter(m.Ticket.customer_id == user["id"])
        elif customer_id:
            q = q.filter(m.Ticket.customer_id == customer_id)

        if status and status != "all":
            normalized = "awaiting_approval" if status == "pending_approval" else status
            q = q.filter(m.Ticket.status == normalized)

        if search:
            pat = f"%{search.strip()}%"
            q = q.filter(
                (m.Ticket.id.ilike(pat)) |
                (m.Ticket.message.ilike(pat)) |
                (m.Ticket.order_id.ilike(pat)) |
                (m.Ticket.issue_type.ilike(pat))
            )
        return [services.ticket_dict(t, session=s) for t in q.order_by(m.Ticket.created_at.desc()).all()]
    finally:
        s.close()


@app.get("/api/tickets/{ticket_id}")
def ticket(ticket_id: str, user: dict = Depends(auth.get_current_user)):
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")
        if user["role"] == "customer" and t.customer_id != user["id"]:
            raise HTTPException(403, "You are not authorized to view another customer's ticket.")
        return services.ticket_dict(t, full=True, session=s)
    finally:
        s.close()


@app.get("/api/approvals")
def approvals(_manager: dict = Depends(auth.require_manager)):
    s = get_session()
    try:
        rows = (
            s.query(m.Ticket)
            .filter(m.Ticket.status.in_(["awaiting_approval", "escalated", "failed"]))
            .order_by(m.Ticket.created_at.desc())
            .all()
        )
        return [services.ticket_dict(t, full=True, session=s) for t in rows]
    finally:
        s.close()


@app.post("/api/approvals/{ticket_id}")
def decide(ticket_id: str, body: ApprovalRequest, manager: dict = Depends(auth.require_manager)):
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t or t.status != "awaiting_approval":
            raise HTTPException(404, "No pending approval for this ticket.")
        pending = json.loads(t.pending_action or "{}")
        old_trace = json.loads(t.trace or "[]")
        cust_id = t.customer_id
        orig_msg = t.message
    finally:
        s.close()

    if not body.approve:
        s = get_session()
        try:
            t = s.get(m.Ticket, ticket_id)
            services.transition_ticket_status(t, "rejected")
            t.pending_action = None
            rejection_note = f": {body.note}" if body.note else ""
            t.final_response = (
                f"A support manager reviewed your request and it could not be approved{rejection_note}. "
                "No changes were made to your order. Please contact support if you have questions."
            )
            services.append_ticket_trace(t, [
                ev("approval", f"Rejected by manager ({manager['id']})", stage="escalated"),
            ])
            s.add(m.TicketMessage(
                ticket_id=ticket_id,
                role="agent",
                text=t.final_response,
                metadata_json=json.dumps({"card_type": "approval_rejected", "note": body.note}),
            ))
            s.commit()
        finally:
            s.close()

        write_tools.record_audit_event(
            action="manager_rejection",
            status="rejected",
            detail=f"Manager rejected pending request" + (f": {body.note}" if body.note else ""),
            ticket_id=ticket_id,
            order_id=(pending.get("plan") or {}).get("order_id"),
            customer_id=cust_id,
            actor_id=manager["id"],
            actor_role=manager["role"],
            confirmation_id=pending.get("confirmation_id"),
        )
        return {"ticket_id": ticket_id, "status": "rejected", "response": t.final_response}

    # Manager approved -> re-run deterministic policy check, atomic write, and DB verification via workflow
    state = {
        "message": orig_msg,
        "customer_id": cust_id,
        "ticket_id": ticket_id,
        "approved": True,
        "confirmed": True,
        "require_confirmation": True,
        "plan": pending["plan"],
        "intake": pending["intake"],
        "actor_id": manager["id"],
        "actor_role": manager["role"],
        "confirmation_id": pending.get("confirmation_id"),
        "trace": [],
    }
    final = None
    for kind, payload in services.stream_graph(get_graph(), state):
        if kind == "final":
            final = payload

    approval_summary = f"Approved by manager ({manager['id']})" + (f": {body.note}" if body.note else "")
    final["trace"] = old_trace + [ev("approval", approval_summary, stage="manager_approved")] + final["trace"]
    final["_replace_trace"] = True
    final["intake"] = pending["intake"]

    # If verified refund/action succeeded, format deterministic verified receipt
    if final.get("verification", {}).get("ok") and (final.get("context") or {}).get("order"):
        from tools.read_tools import call, get_order
        from agents.responder import format_verified_receipt
        fresh_order = call(get_order, order_id=pending["plan"]["order_id"])
        final["final_response"] = format_verified_receipt(
            pending["plan"]["action"],
            fresh_order,
            {"reference_id": (final.get("exec_result") or {}).get("reference_id")},
        )

    services.save_result(ticket_id, final, resolution_mode="human_approved")
    write_tools.record_audit_event(
        action="manager_approval",
        status="approved",
        detail=approval_summary,
        ticket_id=ticket_id,
        order_id=pending["plan"].get("order_id"),
        customer_id=cust_id,
        actor_id=manager["id"],
        actor_role=manager["role"],
        confirmation_id=pending.get("confirmation_id"),
    )
    return {
        "ticket_id": ticket_id,
        "status": services.OUTCOME_TO_STATUS.get(final["outcome"], "escalated"),
        "response": final["final_response"],
    }


@app.post("/api/tickets/{ticket_id}/resolve")
def resolve_escalated(
    ticket_id: str,
    body: Optional[ResolveRequest] = None,
    manager: dict = Depends(auth.require_manager),
):
    """An authorized manager marks an escalated, failed, or open ticket as manually resolved."""
    s = get_session()
    try:
        t = s.get(m.Ticket, ticket_id)
        if not t or t.status not in ("escalated", "awaiting_customer", "awaiting_approval", "failed", "open"):
            raise HTTPException(404, "No ticket requiring resolution found with this ID.")
        services.transition_ticket_status(t, "resolved")
        t.resolution_mode = "human_manual"
        t.pending_action = None
        note_text = body.note if body and body.note else "Marked as resolved by support manager."
        services.append_ticket_trace(t, [ev("respond", f"Manually resolved by {manager['id']}", stage="response_sent")])
        s.add(m.TicketMessage(ticket_id=ticket_id, role="system", text=f"[Resolution Note]: {note_text}"))
        s.commit()
    finally:
        s.close()

    write_tools.record_audit_event(
        action="manual_resolve",
        status="success",
        detail=note_text,
        ticket_id=ticket_id,
        actor_id=manager["id"],
        actor_role=manager["role"],
    )
    return {"ticket_id": ticket_id, "status": "resolved"}


@app.get("/api/activity")
def activity(user: dict = Depends(auth.get_current_user)):
    s = get_session()
    try:
        q = s.query(m.Ticket).filter(m.Ticket.trace.isnot(None)).order_by(m.Ticket.created_at.desc())
        if user["role"] == "customer":
            q = q.filter(m.Ticket.customer_id == user["id"])
        rows = q.limit(15).all()
        out = []
        for t in rows:
            confs = (
                s.query(m.PendingConfirmation)
                .filter_by(ticket_id=t.id)
                .order_by(m.PendingConfirmation.created_at.asc())
                .all()
            )
            audits = (
                s.query(m.ActionLog)
                .filter_by(ticket_id=t.id)
                .order_by(m.ActionLog.created_at.asc(), m.ActionLog.id.asc())
                .all()
            )
            out.append({
                "ticket_id": t.id,
                "customer_id": t.customer_id,
                "order_id": t.order_id,
                "issue_type": t.issue_type,
                "status": t.status,
                "resolution_mode": t.resolution_mode,
                "created_at": t.created_at.isoformat(),
                "trace": json.loads(t.trace or "[]"),
                "confirmations": [services.confirmation_dict(c) for c in confs],
                "audit_logs": [
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
                    for a in audits
                ],
            })
        return out
    finally:
        s.close()


@app.post("/api/admin/reset-demo")
def reset_demo(user: dict = Depends(auth.get_current_user)):
    if not settings.demo_mode:
        raise HTTPException(403, "Demo reset is disabled when DEMO_MODE is off.")
    if user["role"] not in ("manager", "admin") and not user.get("is_demo"):
        raise HTTPException(403, "Manager or Demo Mode authorization required.")
    from database.seed import reset_and_seed
    reset_and_seed()
    return {"status": "reset"}
