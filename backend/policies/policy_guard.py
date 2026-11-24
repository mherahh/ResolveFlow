"""Policy Guard: plain Python decides whether a proposed action is allowed. The LLM cannot override it."""
from datetime import datetime, timezone

from models.schemas import ActionPlan, PolicyDecision
from policies.policy_config import POLICY


def check_action(plan: ActionPlan, order: dict | None, customer_id: str | None) -> PolicyDecision:
    if plan.action in ("none", "escalate"):
        return PolicyDecision(decision="allow", reasons=["No write action requested."])

    reasons: list[str] = []
    if not order or not order.get("found"):
        return PolicyDecision(decision="deny", reasons=["Order was not found."])

    # Ownership: a customer may only act on their own orders, and must be identified.
    if not customer_id:
        return PolicyDecision(decision="deny", reasons=["Customer is not identified."])
    if order["customer_id"] != customer_id:
        return PolicyDecision(decision="deny", reasons=["This order does not belong to this customer."])

    if plan.action == "refund":
        if order["status"] not in POLICY["refundable_statuses"]:
            reasons.append(f"Order status is '{order['status']}', so it is not refundable.")
        if order["already_refunded"]:
            reasons.append("This order has already been refunded.")
        if order["delivered_at"]:
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            days = (now_dt - datetime.strptime(order["delivered_at"], "%Y-%m-%d")).days
            if days > POLICY["return_window_days"]:
                reasons.append(f"Delivered {days} days ago, outside the {POLICY['return_window_days']}-day return window.")
        if plan.amount is None or abs(plan.amount - order["amount"]) > 0.01:
            reasons.append("Refund amount must equal the order amount.")
        if order["amount"] > POLICY["max_refund_amount"]:
            reasons.append(f"Amount is above the ${POLICY['max_refund_amount']:.0f} system limit.")
        if reasons:
            return PolicyDecision(decision="deny", reasons=reasons)
        if order["amount"] > POLICY["auto_refund_limit"]:
            return PolicyDecision(decision="needs_approval",
                                  reasons=[f"Refund is above the ${POLICY['auto_refund_limit']:.0f} automatic limit."])
        return PolicyDecision(decision="allow", reasons=["Refund meets all policy conditions."])

    if plan.action == "cancel":
        if order["status"] not in POLICY["cancellable_statuses"]:
            return PolicyDecision(decision="deny",
                                  reasons=[f"Order status is '{order['status']}', so it cannot be cancelled."])
        return PolicyDecision(decision="allow", reasons=["Order is still processing."])

    if plan.action == "change_address":
        if order["status"] not in POLICY["address_change_statuses"]:
            return PolicyDecision(decision="deny",
                                  reasons=[f"Order status is '{order['status']}', so the address cannot be changed."])
        if not plan.new_address or len(plan.new_address.strip()) < 8:
            return PolicyDecision(decision="deny", reasons=["A complete new address is required."])
        return PolicyDecision(decision="allow", reasons=["Order is still processing."])

    return PolicyDecision(decision="deny", reasons=["Unknown action."])
