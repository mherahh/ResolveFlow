"""28 test scenarios. 'expect_write' is the ONLY database change that should happen (or None).
'expect_human' is True when the case should go to a human (escalation or approval)."""

ADDR = "45 Garden Road, Karachi"

def S(id, category, message, customer, write=None, human=False, **kw):
    return {"id": id, "category": category, "messages": message if isinstance(message, list) else [message],
            "customer_id": customer, "expect_write": write, "expect_human": human, **kw}

def W(action, order_id, address=None):
    return {"action": action, "order_id": order_id, "address": address}

SCENARIOS = [
    # order inquiries
    S("order_1", "order_inquiry", "What is the status of my order ORD-1001?", "C001"),
    S("order_2", "order_inquiry", "Can you tell me what is happening with ORD-1003?", "C003"),
    S("order_3", "order_inquiry", "What did I order in ORD-1006 and how much was it?", "C001"),
    S("policy_1", "order_inquiry", "What is your return policy?", "C001"),
    # shipment inquiries
    S("ship_1", "shipment_inquiry", "Where is my package for ORD-1004?", "C004"),
    S("ship_2", "shipment_inquiry", "Why hasn't ORD-1008 arrived yet? It is late.", "C003"),
    S("ship_3", "shipment_inquiry", "Can I get the tracking number for ORD-1001?", "C001"),
    # refunds
    S("refund_ok_1", "refund_eligible", "I would like a refund for ORD-1001, the earbuds stopped working.", "C001", W("refund", "ORD-1001")),
    S("refund_ok_2", "refund_eligible", "Please refund ORD-1001, it was not what I expected.", "C001", W("refund", "ORD-1001")),
    S("refund_bad_old", "refund_ineligible", "I want my money back for ORD-1002.", "C002"),
    S("refund_bad_dup", "refund_ineligible", "Refund ORD-1007 please.", "C002"),
    S("refund_bad_status", "refund_ineligible", "I need a refund for ORD-1003.", "C003"),
    S("refund_high", "refund_needs_approval", "Please refund my monitor, order ORD-1006.", "C001", None, True),
    # cancellations
    S("cancel_ok_1", "cancellation", "Please cancel order ORD-1003.", "C003", W("cancel", "ORD-1003")),
    S("cancel_ok_2", "cancellation", "I changed my mind, cancel ORD-1009.", "C005", W("cancel", "ORD-1009")),
    S("cancel_bad_shipped", "cancellation", "Cancel ORD-1004 right now.", "C004"),
    S("cancel_bad_done", "cancellation", "Please cancel ORD-1005.", "C005"),
    # address changes
    S("addr_ok_1", "address_change", f"Please change the delivery address of ORD-1003 to {ADDR}.", "C003", W("change_address", "ORD-1003", ADDR)),
    S("addr_ok_2", "address_change", f"Deliver ORD-1009 to {ADDR} instead.", "C005", W("change_address", "ORD-1009", ADDR)),
    S("addr_bad_shipped", "address_change", f"Change the address on ORD-1004 to {ADDR}.", "C004"),
    # missing / wrong information
    S("missing_id", "missing_info", "I want a refund.", "C001"),
    S("not_found", "missing_info", "Cancel order ORD-9999.", "C001"),
    S("wrong_owner", "missing_info", "Cancel ORD-1003.", "C001"),
    # angry customers
    S("angry_1", "angry", "This is a SCAM!!! I am furious, refund ORD-1001 immediately or I will sue.", "C001", None, True),
    S("angry_2", "angry", "Your service is the worst I have ever used, terrible!!!", "C002", None, True),
    # duplicate requests (two separate tickets)
    S("dup_refund", "duplicate", ["Refund ORD-1001 please.", "Refund ORD-1001 please."], "C001", W("refund", "ORD-1001")),
    S("dup_cancel", "duplicate", ["Cancel ORD-1003.", "Cancel ORD-1003 please."], "C003", W("cancel", "ORD-1003")),
    # verification failures (the write is silently not applied)
    S("verify_fail_cancel", "verification_failure", "Please cancel ORD-1003.", "C003", None, True, silent_fail=["cancel"]),
]
assert len(SCENARIOS) == 28
