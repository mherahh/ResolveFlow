"""Authentication, password hashing, and server-side session management.

Uses Python standard library PBKDF2-HMAC-SHA256 for password hashing and opaque
cryptographically random bearer tokens stored as SHA-256 hashes in SQLite/PostgreSQL.
No external auth providers or third-party auth dependencies required.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request

from config import settings
from database.db import get_session
from database import models as m

PBKDF2_ITERATIONS = 200_000
SESSION_COOKIE_NAME = "rf_session"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a random 16-byte salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash in constant time."""
    if not password or not stored_hash:
        return False
    try:
        algo, iter_str, salt, expected_hex = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return hmac.compare_digest(dk.hex(), expected_hex)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(customer_id: str, role: str = "customer", is_demo: bool = False,
                   ttl_hours: Optional[int] = None) -> tuple[str, datetime]:
    """Create a persistent server-side session and return (raw_token, expires_at)."""
    raw_token = secrets.token_urlsafe(32)
    token_digest = hash_token(raw_token)
    hours = ttl_hours if ttl_hours is not None else settings.session_ttl_hours
    expires_at = utc_now() + timedelta(hours=hours)

    s = get_session()
    try:
        sess = m.UserSession(
            token_hash=token_digest,
            customer_id=customer_id,
            role=role,
            is_demo=1 if is_demo else 0,
            expires_at=expires_at,
            revoked=0,
        )
        s.add(sess)
        s.commit()
        return raw_token, expires_at
    finally:
        s.close()


def revoke_session(raw_token: str) -> bool:
    """Revoke an active session token so it can no longer be used."""
    if not raw_token:
        return False
    digest = hash_token(raw_token)
    s = get_session()
    try:
        sess = s.get(m.UserSession, digest)
        if not sess:
            return False
        sess.revoked = 1
        s.commit()
        return True
    finally:
        s.close()


def resolve_session_token(raw_token: str | None) -> Optional[dict]:
    """Validate a raw session token against the database and return user payload or None."""
    if not raw_token:
        return None
    digest = hash_token(raw_token.strip())
    s = get_session()
    try:
        sess = s.get(m.UserSession, digest)
        if not sess:
            return None
        if sess.revoked:
            return None
        now = utc_now()
        if sess.expires_at <= now:
            sess.revoked = 1
            s.commit()
            return None
        cust = s.get(m.Customer, sess.customer_id)
        if not cust:
            return None
        return {
            "id": cust.id,
            "name": cust.name,
            "email": cust.email,
            "tier": cust.tier,
            "role": cust.role or "customer",
            "is_demo": bool(sess.is_demo),
            "expires_at": sess.expires_at.isoformat(),
            "token": raw_token.strip(),
        }
    finally:
        s.close()


def extract_token(
    authorization: Optional[str] = Header(default=None),
    rf_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
        if tok:
            return tok
    if rf_session:
        return rf_session.strip()
    return None


def get_current_user(token: Optional[str] = Depends(extract_token)) -> dict:
    """Strict authentication dependency. Always resolves identity server-side."""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required. Please sign in.")
    user = resolve_session_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or has expired. Please sign in again.")
    return user


def get_optional_user(token: Optional[str] = Depends(extract_token)) -> Optional[dict]:
    if not token:
        return None
    return resolve_session_token(token)


def require_manager(user: dict = Depends(get_current_user)) -> dict:
    """Authorization guard restricting approval/ops write actions to managers/admins."""
    if user.get("role") not in ("manager", "admin"):
        raise HTTPException(status_code=403, detail="Manager authorization is required for this action.")
    return user


def generate_customer_id(s) -> str:
    """Generate the next sequential customer ID (C006, C007, ...)."""
    existing = s.query(m.Customer.id).all()
    max_num = 5
    for (cid,) in existing:
        if cid and cid.startswith("C") and cid[1:].isdigit():
            max_num = max(max_num, int(cid[1:]))
    return f"C{max_num + 1:03d}"
