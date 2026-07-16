"""
app/domains/users/models.py
==============================
Two tables, on `SharedBase` (this is a new domain, added after crypto,
so it follows the same "share one database" default — see
ARCHITECTURE.md):

- `User` — the account itself. `username` and `avatar_url` are nullable:
  a user exists (and can authenticate) the moment their phone number is
  verified once via OTP; picking a username/avatar is a separate
  "complete your profile" step, not a precondition for having an account.
  `username` is UNIQUE but nullable — SQLite allows multiple NULLs under
  a UNIQUE constraint, which is exactly the "not chosen yet" state we
  want, no sentinel value needed.
- `RefreshToken` — one row per issued refresh token, keyed by `jti` (the
  JWT's unique ID, a UUID4 string) rather than an autoincrement int. This
  is what makes logout and refresh-token rotation actually work: an
  access token is stateless and can't be revoked before it expires, but
  every refresh either succeeds against a live, unrevoked DB row or it
  doesn't — that's the enforcement point.

OTP codes are NOT a table here — they're ephemeral, short-TTL data and
live in Redis instead (`otp_store.py`), not this database. See that
file's docstring for why.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import SharedBase


class User(SharedBase):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(unique=True, index=True)  # normalized E.164-ish — see security.py
    username: Mapped[Optional[str]] = mapped_column(unique=True)
    full_name: Mapped[Optional[str]]
    bio: Mapped[Optional[str]]
    avatar_url: Mapped[Optional[str]]
    role: Mapped[str] = mapped_column(default="user")
    is_active: Mapped[int] = mapped_column(default=1)  # int, not bool — SQLite has no native bool, matches rest of app
    created_at: Mapped[str]
    updated_at: Mapped[str]


class RefreshToken(SharedBase):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("idx_refresh_user", "user_id"),
    )

    jti: Mapped[str] = mapped_column(primary_key=True)  # UUID4 string, matches the JWT's own jti claim
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str]
    device_label: Mapped[Optional[str]]  # optional, client-supplied (e.g. "iPhone 14") for a future sessions UI
    expires_at: Mapped[str]
    revoked_at: Mapped[Optional[str]]
    created_at: Mapped[str]
