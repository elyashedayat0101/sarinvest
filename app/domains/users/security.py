"""
app/domains/users/security.py
================================
Three unrelated-but-small concerns kept in one file rather than three,
since each is under 20 lines: JWT issuance/verification, OTP code
hashing, and phone number normalization. Split out the moment any one of
these grows (e.g. if you add a second signing algorithm, or asymmetric
keys for JWT — see the note in `create_access_token`).

Hashing choice for OTP codes (`hash_otp_code`) — deliberately plain
`hashlib.sha256` + a server-side pepper, NOT bcrypt/argon2/scrypt:
a 6-digit OTP has only 10^6 possible values, so a slow password-hashing
algorithm doesn't meaningfully raise the cost of guessing it — what
actually protects it is a short expiry (`settings.otp_expire_seconds`)
and a hard cap on verify attempts (`settings.otp_max_verify_attempts`,
enforced in `service.py`), not hash cost. If you ever add real
*passwords* to this app, use `passlib`/argon2 there instead — high-entropy
secrets and low-entropy OTP codes have different threat models and
shouldn't be hashed the same way.

Refresh tokens (`hash_refresh_token`) also use sha256, for a different
reason: the token itself is a signed JWT with high entropy (a random
`jti` plus our signature), so brute-forcing the hash is already
infeasible regardless of hash speed — sha256 here is just "don't store
the literal bearer token in the DB," not resistance to guessing.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, Tuple

import jwt

from app.core.config import Settings
from app.domains.users.exceptions import InvalidTokenError

TokenType = Literal["access", "refresh"]


# ---- JWT ----

def create_access_token(user_id: int, role: str, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    # HS256 (symmetric) is fine for a single-process/single-secret deployment.
    # If this app ever needs a separate service to *verify* tokens without
    # being trusted to *issue* them, switch to RS256 (asymmetric) so the
    # verifying service only needs the public key.
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, settings: Settings) -> Tuple[str, str, datetime]:
    """Returns (token, jti, expires_at) — `jti` and `expires_at` are what
    the caller persists to `RefreshToken` for later verification/revocation."""
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    payload = {"sub": str(user_id), "jti": jti, "type": "refresh", "iat": now, "exp": expires_at}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_token(token: str, settings: Settings, expected_type: TokenType) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("توکن منقضی شده است")
    except jwt.InvalidTokenError:
        raise InvalidTokenError("توکن نامعتبر است")

    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"نوع توکن نامعتبر است — انتظار می‌رفت {expected_type}")
    return payload


def hash_refresh_token(token: str, settings: Settings) -> str:
    return hashlib.sha256(f"{settings.jwt_secret_key}:{token}".encode()).hexdigest()


# ---- OTP ----

def generate_otp_code(length: int) -> str:
    import secrets
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp_code(phone_number: str, code: str, settings: Settings) -> str:
    return hashlib.sha256(f"{settings.otp_hash_secret}:{phone_number}:{code}".encode()).hexdigest()


# ---- Phone numbers ----

_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def normalize_phone_number(raw: str) -> Optional[str]:
    """
    Normalizes to a canonical `+<countrycode><number>` form so
    "09121234567" and "+989121234567" (same Iranian number, two common
    input formats) collide into one account instead of silently creating
    two. Returns None if the input doesn't look like a phone number at
    all, so callers can raise InvalidPhoneNumberError with context.

    This defaults to Iranian mobile numbers specifically (the "0" prefix
    -> "+98" rewrite) since that's this app's audience; if you serve other
    countries, replace this with a proper phone-number library (e.g.
    `phonenumbers`, Google's libphonenumber port) rather than extending
    this regex — international number formats are not something worth
    hand-rolling past one country.
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw.strip())

    if digits.startswith("0") and len(digits) == 11:  # Iranian local format: 09121234567
        digits = "+98" + digits[1:]
    elif digits.startswith("98") and not digits.startswith("+"):
        digits = "+" + digits
    elif not digits.startswith("+"):
        digits = "+" + digits

    return digits if _PHONE_RE.match(digits) else None
