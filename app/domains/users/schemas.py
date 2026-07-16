"""
app/domains/users/schemas.py
===============================
Same three-tier discipline as crypto's schemas.py: internal shapes never
leak past their boundary. Here that mostly means "the OTP code and token
hashes never appear in any response model" — `UserOut` is deliberately
missing `phone_number` by default (see its docstring) and none of these
models ever include `code_hash`/`token_hash`.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---- Auth flow ----

class OtpRequestIn(BaseModel):
    phone_number: str = Field(description="Iranian mobile format (09...) or E.164 (+98...)")


class OtpRequestOut(BaseModel):
    message: str
    expires_in_seconds: int
    resend_after_seconds: int


class OtpVerifyIn(BaseModel):
    phone_number: str
    code: str = Field(min_length=4, max_length=8)
    device_label: Optional[str] = None  # e.g. "iPhone 14" — optional, for a future sessions UI


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    is_new_user: bool = False  # lets the frontend route straight to "complete your profile"


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str
    everywhere: bool = Field(default=False, description="Revoke every session for this user, not just this one")


# ---- Profile ----

class UserOut(BaseModel):
    """
    Deliberately does NOT include `phone_number` — this is the shape
    returned for "another user's public profile" (not yet used by any
    endpoint here, but the distinction matters the moment one is added:
    e.g. a future public leaderboard/social feature). `MeOut` below is
    the "this is you" shape and does include it.
    """
    id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


class MeOut(UserOut):
    phone_number: str
    role: str
    is_active: bool
    profile_complete: bool  # True once username is set
    created_at: str


class ProfileUpdateIn(BaseModel):
    """All optional — PATCH semantics, only supplied fields are changed."""
    username: Optional[str] = Field(default=None, min_length=3, max_length=20)
    full_name: Optional[str] = Field(default=None, max_length=100)
    bio: Optional[str] = Field(default=None, max_length=500)

    @field_validator("username")
    @classmethod
    def _username_charset(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.lower()
        if not v.replace("_", "").isalnum() or not v[0].isalpha():
            raise ValueError("username must start with a letter and contain only letters, numbers, and underscores")
        return v


class UsernameAvailableOut(BaseModel):
    username: str
    available: bool


class AvatarUploadOut(BaseModel):
    avatar_url: str


# ---- Admin ----

class AdminUserOut(MeOut):
    """Same shape as MeOut — admins see the full record for any user,
    including phone_number. Kept as a distinct model (not a reused alias)
    because these two are answering different questions ("this is my own
    account" vs "an admin is looking at someone else's") and are likely
    to diverge — e.g. AdminUserOut is a natural place to add moderation
    fields (ban reason, flagged count) that MeOut should never expose."""
    pass


class AdminUserListOut(BaseModel):
    users: List[AdminUserOut]
    total: int
    limit: int
    offset: int


class AdminUserUpdateIn(BaseModel):
    role: Optional[Literal["user", "admin"]] = None
    is_active: Optional[bool] = None
