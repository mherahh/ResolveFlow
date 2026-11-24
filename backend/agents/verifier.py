"""Verifier: re-reads the database and checks the expected change really happened."""
from models.schemas import VerificationResult
from tools.read_tools import get_order, call


def verify(action: str, order_id: str, new_address: str | None = None) -> VerificationResult:
    o = call(get_order, order_id=order_id)
    if not o.get("found"):
        return VerificationResult(ok=False, detail="Order could not be re-read.")
    if action == "refund":
        ok = o["already_refunded"]
        return VerificationResult(ok=ok, detail="Refund is recorded on the order." if ok else "Refund is not recorded.")
    if action == "cancel":
        ok = o["status"] == "cancelled"
        return VerificationResult(ok=ok, detail="Order status is cancelled." if ok else "Order is not cancelled.")
    if action == "change_address":
        ok = o["shipping_address"] == new_address
        return VerificationResult(ok=ok, detail="Address updated." if ok else "Address was not updated.")
    return VerificationResult(ok=False, detail="Unknown action.")
