"""The three write operations. Only the Action Executor / confirmation service imports this file.

Every write requires an idempotency key and enforces atomic conditional updates plus database-level
unique constraints so retries or simultaneous concurrent requests can never refund or cancel twice.
"""
import json
import secrets
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database.db import get_session
from database import models as m

# For evaluation/testing only:
# - SIMULATE_SILENT_FAILURE: write is logged but silently not applied (caught by Verifier)
# - SIMULATE_DB_ERROR: database write fails with an operational error
# - EFFECTIVE_WRITES: every write that really changed the database, as (action, order_id)
SIMULATE_SILENT_FAILURE: set[str] = set()
SIMULATE_DB_ERROR: set[str] = set()
EFFECTIVE_WRITES: list[tuple[str, str]] = []


def _already_done(s, key: str):
    return s.query(m.ActionLog).filter_by(idempotency_key=key).first()


def _log(
    s,
    ticket_id: str | None,
    action: str,
    order_id: str | None,
    key: str,
    status: str,
    detail: str,
    customer_id: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
    confirmation_id: str | None = None,
    previous_state: dict | None = None,
    new_state: dict | None = None,
):
    s.add(m.ActionLog(
        ticket_id=ticket_id,
        customer_id=customer_id,
        actor_id=actor_id,
        actor_role=actor_role,
        confirmation_id=confirmation_id,
        action=action,
        order_id=order_id,
        idempotency_key=key,
        status=status,
        detail=detail,
        previous_state=json.dumps(previous_state) if previous_state is not None else None,
        new_state=json.dumps(new_state) if new_state is not None else None,
    ))


def record_audit_event(
    action: str,
    status: str,
    detail: str,
    ticket_id: str | None = None,
    order_id: str | None = None,
    customer_id: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
    confirmation_id: str | None = None,
    idempotency_key: str | None = None,
    previous_state: dict | None = None,
    new_state: dict | None = None,
):
    """Append an immutable audit log entry for non-write lifecycle events (confirm/cancel/expire/approve/reject)."""
    s = get_session()
    try:
        key = idempotency_key or f"audit:{action}:{ticket_id or 'none'}:{secrets.token_hex(8)}"
        if _already_done(s, key):
            return
        _log(
            s,
            ticket_id=ticket_id,
            action=action,
            order_id=order_id,
            key=key,
            status=status,
            detail=detail,
            customer_id=customer_id,
            actor_id=actor_id,
            actor_role=actor_role,
            confirmation_id=confirmation_id,
            previous_state=previous_state,
            new_state=new_state,
        )
        s.commit()
    except SQLAlchemyError:
        s.rollback()
    finally:
        s.close()


def issue_refund(
    order_id: str,
    amount: float,
    reason: str,
    idempotency_key: str,
    ticket_id: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
    confirmation_id: str | None = None,
) -> dict:
    s = get_session()
    try:
        prior = _already_done(s, idempotency_key)
        if prior:
            existing_ref = s.query(m.Refund).filter_by(order_id=order_id).first()
            return {
                "executed": False,
                "duplicate": True,
                "error": False,
                "reference_id": existing_ref.reference_id if existing_ref else None,
                "detail": "Same request already processed.",
            }
        o = s.get(m.Order, order_id)
        if not o:
            return {"executed": False, "duplicate": False, "error": False, "detail": "Order not found."}

        if "refund" in SIMULATE_DB_ERROR:
            raise SQLAlchemyError("Simulated database failure during refund execution")

        prev_state = {"status": o.status, "refunded": bool(o.refunded), "amount": o.amount}

        if "refund" in SIMULATE_SILENT_FAILURE:
            _log(
                s, ticket_id, "refund", order_id, idempotency_key, "success", "simulated silent failure",
                customer_id=o.customer_id, actor_id=actor_id or o.customer_id,
                actor_role=actor_role or "system", confirmation_id=confirmation_id,
                previous_state=prev_state, new_state=prev_state,
            )
            s.commit()
            return {"executed": True, "duplicate": False, "error": False, "detail": "Refund submitted."}

        # Atomic conditional update prevents concurrent double-refunds even across distinct keys
        updated = (
            s.query(m.Order)
            .filter(m.Order.id == order_id, m.Order.refunded == 0, m.Order.status == "delivered")
            .update({m.Order.refunded: 1}, synchronize_session=False)
        )
        if updated == 0:
            s.rollback()
            return {
                "executed": False,
                "duplicate": True,
                "error": False,
                "detail": "Order has already been refunded or is no longer eligible.",
            }

        ref_id = f"REF-{secrets.token_hex(4).upper()}"
        s.add(m.Refund(
            order_id=order_id,
            customer_id=o.customer_id,
            amount=amount,
            reason=reason or "Customer requested refund",
            reference_id=ref_id,
            idempotency_key=idempotency_key,
        ))
        new_state = {"status": o.status, "refunded": True, "amount": amount, "reference_id": ref_id}
        _log(
            s, ticket_id, "refund", order_id, idempotency_key, "success",
            f"Refunded ${amount:.2f} (ref {ref_id})",
            customer_id=o.customer_id, actor_id=actor_id or o.customer_id,
            actor_role=actor_role or "customer", confirmation_id=confirmation_id,
            previous_state=prev_state, new_state=new_state,
        )
        s.commit()
        EFFECTIVE_WRITES.append(("refund", order_id))
        return {
            "executed": True,
            "duplicate": False,
            "error": False,
            "reference_id": ref_id,
            "detail": f"Refund of ${amount:.2f} recorded (Reference: {ref_id}).",
        }
    except IntegrityError:
        s.rollback()
        return {
            "executed": False,
            "duplicate": True,
            "error": False,
            "detail": "Duplicate refund prevented by database constraint.",
        }
    except SQLAlchemyError:
        s.rollback()
        record_audit_event(
            action="refund",
            status="failed",
            detail="Database transaction failed during refund execution.",
            ticket_id=ticket_id,
            order_id=order_id,
            actor_id=actor_id,
            actor_role=actor_role,
            confirmation_id=confirmation_id,
        )
        return {
            "executed": False,
            "duplicate": False,
            "error": True,
            "detail": "Database transaction failed while processing the refund.",
        }
    finally:
        s.close()


def cancel_order(
    order_id: str,
    idempotency_key: str,
    ticket_id: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
    confirmation_id: str | None = None,
) -> dict:
    s = get_session()
    try:
        if _already_done(s, idempotency_key):
            return {"executed": False, "duplicate": True, "error": False, "detail": "Same request already processed."}
        o = s.get(m.Order, order_id)
        if not o:
            return {"executed": False, "duplicate": False, "error": False, "detail": "Order not found."}

        if "cancel" in SIMULATE_DB_ERROR:
            raise SQLAlchemyError("Simulated database failure during cancellation")

        prev_state = {"status": o.status}

        if "cancel" in SIMULATE_SILENT_FAILURE:
            _log(
                s, ticket_id, "cancel", order_id, idempotency_key, "success", "Order cancelled",
                customer_id=o.customer_id, actor_id=actor_id or o.customer_id,
                actor_role=actor_role or "system", confirmation_id=confirmation_id,
                previous_state=prev_state, new_state=prev_state,
            )
            s.commit()
            return {"executed": True, "duplicate": False, "error": False, "detail": "Cancellation submitted."}

        updated = (
            s.query(m.Order)
            .filter(m.Order.id == order_id, m.Order.status == "processing")
            .update({m.Order.status: "cancelled"}, synchronize_session=False)
        )
        if updated == 0:
            s.rollback()
            return {
                "executed": False,
                "duplicate": False,
                "error": False,
                "detail": f"Order status is '{o.status}' and can no longer be cancelled.",
            }

        new_state = {"status": "cancelled"}
        _log(
            s, ticket_id, "cancel", order_id, idempotency_key, "success", "Order cancelled",
            customer_id=o.customer_id, actor_id=actor_id or o.customer_id,
            actor_role=actor_role or "customer", confirmation_id=confirmation_id,
            previous_state=prev_state, new_state=new_state,
        )
        s.commit()
        EFFECTIVE_WRITES.append(("cancel", order_id))
        return {"executed": True, "duplicate": False, "error": False, "detail": "Order cancelled."}
    except IntegrityError:
        s.rollback()
        return {"executed": False, "duplicate": True, "error": False, "detail": "Duplicate cancellation prevented."}
    except SQLAlchemyError:
        s.rollback()
        record_audit_event(
            action="cancel",
            status="failed",
            detail="Database transaction failed during order cancellation.",
            ticket_id=ticket_id,
            order_id=order_id,
            actor_id=actor_id,
            actor_role=actor_role,
            confirmation_id=confirmation_id,
        )
        return {
            "executed": False,
            "duplicate": False,
            "error": True,
            "detail": "Database transaction failed while cancelling the order.",
        }
    finally:
        s.close()


def change_address(
    order_id: str,
    new_address: str,
    idempotency_key: str,
    ticket_id: str | None = None,
    actor_id: str | None = None,
    actor_role: str | None = None,
    confirmation_id: str | None = None,
) -> dict:
    s = get_session()
    try:
        if _already_done(s, idempotency_key):
            return {"executed": False, "duplicate": True, "error": False, "detail": "Same request already processed."}
        o = s.get(m.Order, order_id)
        if not o:
            return {"executed": False, "duplicate": False, "error": False, "detail": "Order not found."}

        if "change_address" in SIMULATE_DB_ERROR:
            raise SQLAlchemyError("Simulated database failure during address update")

        prev_state = {"status": o.status, "address_changed": False}

        if "change_address" in SIMULATE_SILENT_FAILURE:
            _log(
                s, ticket_id, "change_address", order_id, idempotency_key, "success", "Address update submitted",
                customer_id=o.customer_id, actor_id=actor_id or o.customer_id,
                actor_role=actor_role or "system", confirmation_id=confirmation_id,
                previous_state=prev_state, new_state=prev_state,
            )
            s.commit()
            return {"executed": True, "duplicate": False, "error": False, "detail": "Address change submitted."}

        updated = (
            s.query(m.Order)
            .filter(m.Order.id == order_id, m.Order.status == "processing")
            .update({m.Order.shipping_address: new_address.strip()}, synchronize_session=False)
        )
        if updated == 0:
            s.rollback()
            return {
                "executed": False,
                "duplicate": False,
                "error": False,
                "detail": f"Order status is '{o.status}' and shipping address can no longer be changed.",
            }

        new_state = {"status": o.status, "address_changed": True}
        _log(
            s, ticket_id, "change_address", order_id, idempotency_key, "success",
            "Shipping address updated",
            customer_id=o.customer_id, actor_id=actor_id or o.customer_id,
            actor_role=actor_role or "customer", confirmation_id=confirmation_id,
            previous_state=prev_state, new_state=new_state,
        )
        s.commit()
        EFFECTIVE_WRITES.append(("change_address", order_id))
        return {"executed": True, "duplicate": False, "error": False, "detail": "Shipping address updated."}
    except IntegrityError:
        s.rollback()
        return {"executed": False, "duplicate": True, "error": False, "detail": "Duplicate address update prevented."}
    except SQLAlchemyError:
        s.rollback()
        record_audit_event(
            action="change_address",
            status="failed",
            detail="Database transaction failed during shipping address update.",
            ticket_id=ticket_id,
            order_id=order_id,
            actor_id=actor_id,
            actor_role=actor_role,
            confirmation_id=confirmation_id,
        )
        return {
            "executed": False,
            "duplicate": False,
            "error": True,
            "detail": "Database transaction failed while updating the shipping address.",
        }
    finally:
        s.close()
