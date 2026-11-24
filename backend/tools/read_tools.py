"""Read-only tools. The Resolution step may call these; none of them can change data."""
from typing import Optional
from langchain_core.tools import tool

from database.db import get_session
from database import models as m
from policies.policy_config import POLICY, POLICY_TEXT


def _iso(dt):
    return dt.strftime("%Y-%m-%d") if dt else None


@tool
def get_customer(customer_id: str) -> dict:
    """Look up a customer by ID (for example C001)."""
    s = get_session()
    try:
        c = s.get(m.Customer, customer_id)
        if not c:
            return {"found": False}
        return {"found": True, "id": c.id, "name": c.name, "email": c.email, "tier": c.tier}
    finally:
        s.close()


@tool
def lookup_order(order_id: str, email: str) -> dict:
    """Look up and verify an order using the order ID and customer email address.
    Returns order data only if BOTH match. If either is missing or invalid, returns
    verified=False and found=False without revealing whether the order ID exists.
    """
    if not order_id or not email:
        return {"verified": False, "found": False}
    clean_oid = order_id.strip().upper()
    clean_email = email.strip().lower()

    s = get_session()
    try:
        o = s.get(m.Order, clean_oid)
        if not o:
            return {"verified": False, "found": False}
        c = s.get(m.Customer, o.customer_id)
        if not c or c.email.strip().lower() != clean_email:
            # Privacy guard: do not reveal whether order_id exists
            return {"verified": False, "found": False}
        return {
            "verified": True,
            "found": True,
            "id": o.id,
            "customer_id": o.customer_id,
            "customer_name": c.name,
            "customer_email": c.email,
            "item": o.item,
            "amount": o.amount,
            "status": o.status,
            "placed_at": _iso(o.placed_at),
            "delivered_at": _iso(o.delivered_at),
            "shipping_address": o.shipping_address,
            "already_refunded": bool(o.refunded),
        }
    finally:
        s.close()


@tool
def get_order(order_id: str) -> dict:
    """Look up an order by ID (for example ORD-1001)."""
    s = get_session()
    try:
        o = s.get(m.Order, order_id)
        if not o:
            return {"found": False, "verified": False}
        return {"found": True, "verified": True, "id": o.id, "customer_id": o.customer_id, "item": o.item, "amount": o.amount,
                "status": o.status, "placed_at": _iso(o.placed_at), "delivered_at": _iso(o.delivered_at),
                "shipping_address": o.shipping_address, "already_refunded": bool(o.refunded)}
    finally:
        s.close()


@tool
def track_shipment(order_id: str) -> dict:
    """Get the shipment and tracking status for an order."""
    s = get_session()
    try:
        sh = s.query(m.Shipment).filter_by(order_id=order_id).first()
        if not sh:
            return {"found": False}
        return {"found": True, "carrier": sh.carrier, "tracking_number": sh.tracking_number,
                "status": sh.status, "last_update": sh.last_update, "eta": sh.eta}
    finally:
        s.close()


@tool
def get_refund_history(customer_id: str) -> list:
    """List the refunds a customer has received before."""
    s = get_session()
    try:
        rows = s.query(m.Refund).filter_by(customer_id=customer_id).all()
        return [
            {
                "order_id": r.order_id,
                "amount": r.amount,
                "reason": r.reason,
                "reference_id": r.reference_id,
                "date": _iso(r.created_at),
            }
            for r in rows
        ]
    finally:
        s.close()


@tool
def get_customer_orders(customer_id: str) -> list:
    """List all orders belonging to an authenticated customer, newest first."""
    s = get_session()
    try:
        rows = s.query(m.Order).filter_by(customer_id=customer_id).order_by(m.Order.placed_at.desc()).all()
        return [
            {
                "id": o.id,
                "customer_id": o.customer_id,
                "item": o.item,
                "amount": o.amount,
                "status": o.status,
                "placed_at": _iso(o.placed_at),
                "delivered_at": _iso(o.delivered_at),
                "shipping_address": o.shipping_address,
                "already_refunded": bool(o.refunded),
            }
            for o in rows
        ]
    finally:
        s.close()


@tool
def get_policy(topic: str) -> dict:
    """Get the company policy text. Topic is one of: refund, cancellation, address_change, general."""
    return {"topic": topic, "policy": POLICY_TEXT.get(topic, POLICY_TEXT["general"]), "limits": POLICY}


READ_TOOLS = [get_customer, get_customer_orders, get_order, lookup_order, track_shipment, get_refund_history, get_policy]


def call(tool_obj, **kwargs):
    """Small helper so deterministic code can call a tool directly."""
    return tool_obj.invoke(kwargs)
