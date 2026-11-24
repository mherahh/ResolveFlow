"""Responder: writes the customer reply from verified facts only. Falls back to structured templates if the LLM fails."""
import json
from prompts.prompts import RESPONDER_PROMPT

FALLBACKS = {
    "escalated": "Thanks for contacting us. I have passed your request, with all the details, to a human support "
                 "specialist who will follow up with you.",
    "pending_approval": "Your request has been submitted for human manager review. No refund or account change has "
                        "been processed yet, and we will update you once a manager completes the review.",
    "needs_info": "Could you please share which order you need help with so I can look it up on your account?",
    "needs_confirmation": "Please review the details of your request and confirm if you would like me to proceed.",
    "failed": "We were unable to complete or verify your request due to a system error. No unverified change has "
              "been confirmed, and our team has been notified.",
    "resolved": "Your request has been handled.",
}


def format_confirmation_summary(facts: dict) -> str:
    """Deterministic, unambiguous confirmation prompt for financial / state-changing actions."""
    act = facts.get("action_to_confirm")
    summary = facts.get("order_summary") or {}
    oid = summary.get("id", "Unknown")
    item = summary.get("item", "Item")
    amount = summary.get("amount", 0.0)
    reason = summary.get("reason")
    elig = facts.get("eligibility_label") or "Eligible"
    proc = facts.get("processing_info") or ""

    if act == "refund":
        lines = [
            "Please review your refund request:",
            "",
            f"Order: {oid}",
            f"Product: {item}",
            f"Refund Amount: ${amount:.2f}",
        ]
        if reason:
            lines.append(f"Reason: {reason}")
        lines.append(f"Status: {elig}")
        if proc:
            lines.append(proc)
        lines.extend(["", "Would you like me to proceed? Use the Confirm Refund or Cancel button below."])
        return "\n".join(lines)

    if act == "cancel":
        lines = [
            "Please review your order cancellation request:",
            "",
            f"Order: {oid}",
            f"Product: {item}",
            f"Amount: ${amount:.2f}",
            f"Current Status: {summary.get('status', 'processing')}",
            "Note: Once cancelled, this order will stop processing and cannot be reinstated.",
            "",
            "Would you like me to cancel this order? Use the Confirm Cancellation or Cancel button below.",
        ]
        return "\n".join(lines)

    if act == "change_address":
        lines = [
            "Please review your shipping address update:",
            "",
            f"Order: {oid}",
            f"Product: {item}",
            f"Current Address: {summary.get('current_address', 'N/A')}",
            f"New Address: {summary.get('new_address', 'N/A')}",
            f"Status: {elig}",
            "",
            "Would you like me to update the shipping address? Use the Confirm Address Change or Cancel button below.",
        ]
        return "\n".join(lines)

    return FALLBACKS["needs_confirmation"]


def format_verified_receipt(action: str, order: dict, extra: dict | None = None) -> str:
    """Deterministic verified-execution confirmation message after DB write + DB re-read verification."""
    extra = extra or {}
    oid = order.get("id", "")
    item = order.get("item", "")
    amount = order.get("amount", 0.0)
    ref = extra.get("reference_id")

    if action == "refund":
        lines = [
            "Your refund has been successfully processed!",
            "",
            f"Order: {oid}",
            f"Product: {item}",
            f"Refund Amount: ${amount:.2f}",
            "Status: Refund Processed",
        ]
        if ref:
            lines.append(f"Transaction Reference: {ref}")
        lines.extend([
            "",
            "The refund will be returned to your original payment method. Please allow 3 to 5 business days for processing.",
        ])
        return "\n".join(lines)

    if action == "cancel":
        lines = [
            "Your order has been successfully cancelled!",
            "",
            f"Order: {oid}",
            f"Product: {item}",
            f"Amount: ${amount:.2f}",
            "Status: Cancelled",
            "",
            "This order will not be shipped. If any payment hold was placed, it will be released automatically.",
        ]
        return "\n".join(lines)

    if action == "change_address":
        lines = [
            "Your shipping address has been successfully updated!",
            "",
            f"Order: {oid}",
            f"Product: {item}",
            f"Updated Shipping Address: {order.get('shipping_address', extra.get('new_address', ''))}",
            f"Order Status: {order.get('status', 'processing')}",
        ]
        return "\n".join(lines)

    return FALLBACKS["resolved"]


def run_responder(llm, message: str, outcome: str, facts: dict) -> str:
    # For safety-critical confirmation summaries, return the exact deterministic summary so amounts/IDs are never altered
    if outcome == "needs_confirmation" and facts.get("action_to_confirm"):
        return format_confirmation_summary(facts)
    if outcome == "failed":
        reason = facts.get("failure_detail") or FALLBACKS["failed"]
        return f"We could not complete your request: {reason} Your order has not been confirmed as updated."
    if facts.get("order_selection_needed") and facts.get("available_orders"):
        orders = facts["available_orders"]
        action_label = facts.get("requested_action_label", "help with")
        lines = [f"I can help you with that. Could you confirm which order you'd like to {action_label}?", ""]
        for o in orders:
            flag = f" — {o['eligibility_note']}" if o.get("eligibility_note") else ""
            lines.append(f"- {o['id']}: {o['item']} (${o['amount']:.2f}, {o['status']}){flag}")
        return "\n".join(lines)
    if facts.get("address_needed") and facts.get("order"):
        o = facts["order"]
        return (
            f"I can help update the shipping address for order {o['id']} ({o['item']}).\n"
            f"Current shipping address: {o.get('shipping_address', 'Unavailable')}\n"
            "Please provide the full new shipping address you would like us to use."
        )

    try:
        msgs = RESPONDER_PROMPT.format_messages(message=message, outcome=outcome, facts_json=json.dumps(facts))
        text = llm.invoke(msgs).content
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass
    return FALLBACKS.get(outcome, FALLBACKS["resolved"])
