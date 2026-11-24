"""Create the database and insert synthetic sample data.  Run:  python -m database.seed"""
from datetime import datetime, timedelta, timezone

from auth import hash_password
from database.db import Base, engine, get_session
from database import models as m

DEMO_CUSTOMER_PASSWORD = "Password123!"
DEMO_MANAGER_PASSWORD = "Manager123!"

# Cached hashes so repeated test resets stay fast while still using strong PBKDF2 hashes
_CACHED_CUSTOMER_HASH = hash_password(DEMO_CUSTOMER_PASSWORD)
_CACHED_MANAGER_HASH = hash_password(DEMO_MANAGER_PASSWORD)


def reset_and_seed():
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def d(days_ago: int) -> datetime:
        return now - timedelta(days=days_ago)

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = get_session()

    s.add_all([
        m.Customer(id="C001", name="Ayesha Khan", email="ayesha@example.com", tier="standard",
                   role="customer", password_hash=_CACHED_CUSTOMER_HASH),
        m.Customer(id="C002", name="Bilal Ahmed", email="bilal@example.com", tier="standard",
                   role="customer", password_hash=_CACHED_CUSTOMER_HASH),
        m.Customer(id="C003", name="Sara Malik", email="sara@example.com", tier="premium",
                   role="customer", password_hash=_CACHED_CUSTOMER_HASH),
        m.Customer(id="C004", name="Usman Ali", email="usman@example.com", tier="standard",
                   role="customer", password_hash=_CACHED_CUSTOMER_HASH),
        m.Customer(id="C005", name="Hina Raza", email="hina@example.com", tier="standard",
                   role="customer", password_hash=_CACHED_CUSTOMER_HASH),
        # Seeded Support Manager account for approvals & triage
        m.Customer(id="M001", name="Tariq Mehmood", email="manager@resolveflow.io", tier="staff",
                   role="manager", password_hash=_CACHED_MANAGER_HASH),
    ])

    addr = "House 12, Block 5, Karachi"
    s.add_all([
        # 1001: delivered 5 days ago, small amount -> refund eligible (automatic)
        m.Order(id="ORD-1001", customer_id="C001", item="Wireless Earbuds", amount=45.0, status="delivered",
                placed_at=d(12), delivered_at=d(5), shipping_address=addr),
        # 1002: delivered 45 days ago -> outside the 30 day return window
        m.Order(id="ORD-1002", customer_id="C002", item="Running Shoes", amount=80.0, status="delivered",
                placed_at=d(50), delivered_at=d(45), shipping_address=addr),
        # 1003: processing -> can be cancelled, address can change
        m.Order(id="ORD-1003", customer_id="C003", item="Desk Lamp", amount=30.0, status="processing",
                placed_at=d(1), delivered_at=None, shipping_address=addr),
        # 1004: shipped, in transit -> cannot cancel
        m.Order(id="ORD-1004", customer_id="C004", item="Backpack", amount=60.0, status="shipped",
                placed_at=d(4), delivered_at=None, shipping_address=addr),
        # 1005: already cancelled
        m.Order(id="ORD-1005", customer_id="C005", item="Phone Case", amount=15.0, status="cancelled",
                placed_at=d(8), delivered_at=None, shipping_address=addr),
        # 1006: delivered recently, high value -> above automatic limit, needs human approval
        m.Order(id="ORD-1006", customer_id="C001", item="Gaming Monitor", amount=350.0, status="delivered",
                placed_at=d(10), delivered_at=d(3), shipping_address=addr),
        # 1007: already refunded -> a second refund must be blocked
        m.Order(id="ORD-1007", customer_id="C002", item="Yoga Mat", amount=25.0, status="delivered",
                placed_at=d(15), delivered_at=d(9), shipping_address=addr, refunded=1),
        # 1008: shipped but delayed
        m.Order(id="ORD-1008", customer_id="C003", item="Bluetooth Speaker", amount=55.0, status="shipped",
                placed_at=d(14), delivered_at=None, shipping_address=addr),
        # 1009: processing, second cancellable order
        m.Order(id="ORD-1009", customer_id="C005", item="Water Bottle", amount=12.0, status="processing",
                placed_at=d(0), delivered_at=None, shipping_address=addr),
    ])

    def day(offset: int) -> str:
        return (now + timedelta(days=offset)).strftime("%Y-%m-%d")

    s.add_all([
        m.Shipment(order_id="ORD-1001", carrier="TCS", tracking_number="TCS100100", status="delivered",
                   last_update="Delivered to recipient", eta=None),
        m.Shipment(order_id="ORD-1002", carrier="Leopards", tracking_number="LEO200200", status="delivered",
                   last_update="Delivered to recipient", eta=None),
        m.Shipment(order_id="ORD-1004", carrier="TCS", tracking_number="TCS400400", status="in_transit",
                   last_update="Arrived at Karachi sorting hub", eta=day(2)),
        m.Shipment(order_id="ORD-1006", carrier="TCS", tracking_number="TCS600600", status="delivered",
                   last_update="Delivered to recipient", eta=None),
        m.Shipment(order_id="ORD-1007", carrier="Leopards", tracking_number="LEO700700", status="delivered",
                   last_update="Delivered to recipient", eta=None),
        m.Shipment(order_id="ORD-1008", carrier="Leopards", tracking_number="LEO800800", status="delayed",
                   last_update="Delayed at Lahore hub", eta=day(-3)),
    ])

    s.add(m.Refund(order_id="ORD-1007", customer_id="C002", amount=25.0, reason="Changed mind",
                   reference_id="REF-SEED-1007", idempotency_key="seed-refund-1007", created_at=d(2)))
    s.commit()
    s.close()


if __name__ == "__main__":
    reset_and_seed()
    print("Database created and seeded.")
