"""Company policies live here, separate from code logic. Change a number and behaviour changes."""
POLICY = {
    "return_window_days": 30,      # refunds only for delivered orders within this many days of delivery
    "auto_refund_limit": 100.0,    # refunds above this need human approval
    "max_refund_amount": 500.0,    # refunds above this are never processed by the system
    "cancellable_statuses": ["processing"],
    "address_change_statuses": ["processing"],
    "refundable_statuses": ["delivered"],
}

POLICY_TEXT = {
    "refund": "Refunds are allowed for delivered orders within 30 days of delivery. "
              "Refunds up to $100 are processed automatically; larger refunds need human approval. "
              "An order can only be refunded once.",
    "cancellation": "Orders can be cancelled only while they are still 'processing'. "
                    "Shipped or delivered orders cannot be cancelled.",
    "address_change": "The delivery address can be changed only while the order is 'processing'. "
                      "Address changes cannot be made once an order has shipped or delivered.",
    "shipping": "Orders are shipped via TCS or Leopards Courier. Standard delivery takes 2 to 5 business days. "
                "Tracking numbers are provided once the package is dispatched.",
    "order": "Orders remain in 'processing' status while being prepared. Once dispatched, the status changes to "
             "'shipped'. Customers can cancel or change delivery address only during processing.",
    "general": "ResolveFlow AI handles order lookups, shipment tracking, refund processing (within 30 days of delivery), "
               "order cancellations (for processing orders), and shipping address updates (for processing orders). "
               "Complex inquiries or distressed customer requests are escalated to human support agents.",
}
