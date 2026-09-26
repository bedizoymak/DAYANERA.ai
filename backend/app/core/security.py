"""Password hashing and opaque session tokens (standard library only).

Beta mechanism: PBKDF2-HMAC-SHA256 password hashes and random 256-bit
session tokens delivered in an HttpOnly, SameSite=Strict cookie. Only the
SHA-256 hash of a session token is stored in PostgreSQL. Hardening items
(SSO, MFA, rotation, lockout policy) are documented as future work.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 390_000

SESSION_COOKIE = "dayanera_session"
CSRF_HEADER = "X-DAYANERA-CSRF"


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = encoded.split("$", 3)
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:  # malformed hash -> never authenticate
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
