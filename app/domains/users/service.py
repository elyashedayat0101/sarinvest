"""
app/domains/users/service.py
===============================
One service class covering both auth (OTP + JWT lifecycle) and profile
management — not split into `AuthService`/`ProfileService`, for the same
reason `portfolio`'s repository wasn't split from `strategy`'s (see
ARCHITECTURE.md): both operate on the same aggregate root (`User`), and
a login flow that creates a user is not meaningfully separable from
"manage that user's profile." Organized with section comments instead.

Takes both a `UserRepository` (SQLAlchemy — `users`/`refresh_tokens`)
and an `OtpStore` (Redis — OTP codes) as separate dependencies. They're
deliberately not merged behind one interface: they're backed by two
different kinds of storage for two different reasons (durable relational
data vs. ephemeral TTL data — see `otp_store.py`'s docstring), and a
caller constructing this service should have to notice that, not have it
hidden behind a single "the repository" parameter.
"""
from __future__ import annotations

import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.domains.users import security
from app.domains.users.exceptions import (
    AvatarUploadError, InvalidPhoneNumberError, InvalidTokenError, OtpInvalidError,
    OtpRequestRateLimitedError, OtpVerifyRateLimitedError, SelfRoleChangeForbiddenError,
    UserInactiveError, UserNotFoundError, UsernameTakenError,
)
from app.domains.users.otp_sender import OtpSender
from app.domains.users.otp_store import OtpStore
from app.domains.users.repository import UserRepository
from app.domains.users.schemas import MeOut, TokenPairOut

log = logging.getLogger("users.service")

_OTP_PURPOSE = "login"


class UserService:
    def __init__(self, repo: UserRepository, otp_sender: OtpSender, otp_store: OtpStore, settings: Settings):
        self._repo = repo
        self._otp_sender = otp_sender
        self._otp_store = otp_store
        self._settings = settings

    # =================================================================
    # Auth — OTP request/verify, token issue/refresh/revoke
    # =================================================================

    async def request_otp(self, raw_phone_number: str) -> dict:
        phone = security.normalize_phone_number(raw_phone_number)
        if not phone:
            raise InvalidPhoneNumberError("شماره موبایل نامعتبر است")

        existing = await self._otp_store.get(phone, _OTP_PURPOSE)
        if existing:
            created = datetime.fromisoformat(existing.created_at)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            if elapsed < self._settings.otp_resend_interval_seconds:
                raise OtpRequestRateLimitedError(
                    f"لطفاً {int(self._settings.otp_resend_interval_seconds - elapsed)} ثانیه دیگر تلاش کنید"
                )

        code = security.generate_otp_code(self._settings.otp_code_length)
        code_hash = security.hash_otp_code(phone, code, self._settings)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._settings.otp_expire_seconds)
        await self._otp_store.save(
            phone, _OTP_PURPOSE, code_hash, now.isoformat(), expires_at.isoformat(),
            ttl_seconds=self._settings.otp_expire_seconds,
        )

        # Any failure here (network down, provider rejects the number) is
        # a real failure — never silently swallow it and pretend the code
        # was sent.
        await self._otp_sender.send(phone, code)

        return {
            "message": "کد تایید ارسال شد",
            "expires_in_seconds": self._settings.otp_expire_seconds,
            "resend_after_seconds": self._settings.otp_resend_interval_seconds,
        }

    async def verify_otp(self, raw_phone_number: str, code: str, device_label: Optional[str] = None) -> Tuple[TokenPairOut, dict]:
        phone = security.normalize_phone_number(raw_phone_number)
        if not phone:
            raise InvalidPhoneNumberError("شماره موبایل نامعتبر است")

        otp = await self._otp_store.get(phone, _OTP_PURPOSE)
        if not otp:
            raise OtpInvalidError("کد نامعتبر است یا منقضی شده")

        if otp.attempt_count >= self._settings.otp_max_verify_attempts:
            raise OtpVerifyRateLimitedError("تعداد تلاش‌های مجاز به پایان رسیده — یک کد جدید درخواست کنید")

        expires_at = datetime.fromisoformat(otp.expires_at)
        if datetime.now(timezone.utc) >= expires_at:
            raise OtpInvalidError("کد نامعتبر است یا منقضی شده")

        supplied_hash = security.hash_otp_code(phone, code, self._settings)
        # constant-time comparison — this is a security-sensitive
        # equality check, not just data plumbing; == would leak timing
        # information about how many leading characters matched.
        if not hmac.compare_digest(supplied_hash, otp.code_hash):
            await self._otp_store.increment_attempts(phone, _OTP_PURPOSE)
            raise OtpInvalidError("کد نامعتبر است یا منقضی شده")

        await self._otp_store.consume(phone, _OTP_PURPOSE)

        user = await self._repo.get_by_phone(phone)
        is_new_user = user is None
        if is_new_user:
            role = "admin" if phone in self._settings.bootstrap_admin_phone_numbers else "user"
            user = await self._repo.create(phone, role=role)
        elif phone in self._settings.bootstrap_admin_phone_numbers and user["role"] != "admin":
            # Ongoing sync, promote-only: if a phone is added to the
            # bootstrap-admin list after the account already existed,
            # promote it on next login. Deliberately never auto-*demotes*
            # on removal from the list — that should be an explicit admin
            # action (see set_role below), not a side effect of an env
            # var change, which would be a surprising way to lock someone
            # out.
            user = await self._repo.update_profile(user["id"], role="admin")

        if not user["is_active"]:
            raise UserInactiveError("حساب کاربری غیرفعال است")

        tokens = await self._issue_token_pair(user["id"], user["role"], device_label)
        return tokens.model_copy(update={"is_new_user": is_new_user}), user

    async def refresh(self, refresh_token: str) -> TokenPairOut:
        payload = security.decode_token(refresh_token, self._settings, expected_type="refresh")
        jti = payload["jti"]
        record = await self._repo.get_refresh_token(jti)
        if not record or record["revoked_at"] is not None:
            # A revoked-but-otherwise-valid-looking refresh token being
            # presented again is exactly the "stolen token reuse" signal
            # refresh-token rotation exists to catch — revoke every
            # session for this user as a precaution, not just this token.
            if record:
                await self._repo.revoke_all_refresh_tokens(record["user_id"])
            raise InvalidTokenError("توکن نامعتبر است — لطفاً دوباره وارد شوید")

        expires_at = datetime.fromisoformat(record["expires_at"])
        if datetime.now(timezone.utc) >= expires_at:
            raise InvalidTokenError("توکن منقضی شده — لطفاً دوباره وارد شوید")

        user = await self._repo.get_by_id(record["user_id"])
        if not user or not user["is_active"]:
            raise UserInactiveError("حساب کاربری غیرفعال است")

        # Rotation: revoke the presented token, issue a brand new pair.
        await self._repo.revoke_refresh_token(jti)
        return await self._issue_token_pair(user["id"], user["role"])

    async def logout(self, refresh_token: str, everywhere: bool = False) -> None:
        payload = security.decode_token(refresh_token, self._settings, expected_type="refresh")
        jti = payload["jti"]
        record = await self._repo.get_refresh_token(jti)
        if not record:
            return  # already gone — logout is idempotent, not an error
        if everywhere:
            await self._repo.revoke_all_refresh_tokens(record["user_id"])
        else:
            await self._repo.revoke_refresh_token(jti)

    async def _issue_token_pair(self, user_id: int, role: str, device_label: Optional[str] = None) -> TokenPairOut:
        access_token = security.create_access_token(user_id, role, self._settings)
        refresh_token, jti, expires_at = security.create_refresh_token(user_id, self._settings)
        token_hash = security.hash_refresh_token(refresh_token, self._settings)
        await self._repo.save_refresh_token(jti, user_id, token_hash, expires_at.isoformat(), device_label)
        return TokenPairOut(access_token=access_token, refresh_token=refresh_token)

    # =================================================================
    # Current-user resolution (used by deps.py::get_current_user)
    # =================================================================

    async def get_user_from_access_token(self, access_token: str) -> dict:
        payload = security.decode_token(access_token, self._settings, expected_type="access")
        user = await self._repo.get_by_id(int(payload["sub"]))
        if not user:
            raise UserNotFoundError("کاربر یافت نشد")
        if not user["is_active"]:
            raise UserInactiveError("حساب کاربری غیرفعال است")
        return user

    # =================================================================
    # Profile
    # =================================================================

    async def get_profile(self, user_id: int) -> dict:
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError("کاربر یافت نشد")
        return user

    async def update_profile(self, user_id: int, **fields) -> dict:
        fields = {k: v for k, v in fields.items() if v is not None}
        if "username" in fields:
            existing = await self._repo.get_by_username(fields["username"])
            if existing and existing["id"] != user_id:
                raise UsernameTakenError("این نام کاربری قبلاً استفاده شده است")
        return await self._repo.update_profile(user_id, **fields)

    async def is_username_available(self, username: str) -> bool:
        return (await self._repo.get_by_username(username.lower())) is None

    async def set_avatar_url(self, user_id: int, avatar_url: str) -> dict:
        return await self._repo.update_profile(user_id, avatar_url=avatar_url)

    async def save_avatar(self, user_id: int, filename: str, content: bytes, content_type: Optional[str]) -> dict:
        """
        Validates and saves an uploaded avatar image to disk, then updates
        the user's `avatar_url`. Runs the actual file write via
        `run_in_threadpool` — it's blocking disk I/O, and while a single
        small avatar write is cheap, "never block the event loop" is worth
        holding as a rule uniformly rather than making per-call judgment
        calls about what counts as small enough to skip it — see
        `market_repo.py`'s original rationale for the same pattern with
        SQLite before this app moved to SQLAlchemy's async engine.
        """
        if content_type not in self._settings.avatar_allowed_content_types:
            raise AvatarUploadError(
                f"نوع فایل مجاز نیست — انواع مجاز: {', '.join(self._settings.avatar_allowed_content_types)}"
            )
        if len(content) > self._settings.avatar_max_size_bytes:
            max_mb = self._settings.avatar_max_size_bytes / (1024 * 1024)
            raise AvatarUploadError(f"حجم فایل بیش از حد مجاز است (حداکثر {max_mb:.1f} مگابایت)")

        ext = Path(filename).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        new_filename = f"{uuid.uuid4()}{ext}"

        upload_dir = Path(self._settings.avatar_upload_dir)

        def _write() -> None:
            upload_dir.mkdir(parents=True, exist_ok=True)
            (upload_dir / new_filename).write_bytes(content)

        await run_in_threadpool(_write)

        avatar_url = f"/avatars/{new_filename}"
        return await self.set_avatar_url(user_id, avatar_url)

    # =================================================================
    # Admin
    # =================================================================

    async def list_users(self, limit: int, offset: int):
        return await self._repo.list_users(limit, offset)

    async def set_role(self, acting_admin_id: int, target_user_id: int, role: str) -> dict:
        if acting_admin_id == target_user_id and role != "admin":
            # Prevent an admin from locking themselves out by demoting
            # their own only-admin account via this endpoint — a real
            # role change for yourself should go through a second admin,
            # or direct DB access, not a single self-service API call.
            raise SelfRoleChangeForbiddenError("نمی‌توانید نقش خودتان را از طریق این مسیر تغییر دهید")
        target = await self._repo.get_by_id(target_user_id)
        if not target:
            raise UserNotFoundError("کاربر یافت نشد")
        return await self._repo.update_profile(target_user_id, role=role)

    async def set_active(self, target_user_id: int, is_active: bool) -> dict:
        target = await self._repo.get_by_id(target_user_id)
        if not target:
            raise UserNotFoundError("کاربر یافت نشد")
        if not is_active:
            await self._repo.revoke_all_refresh_tokens(target_user_id)  # deactivation should end active sessions too
        return await self._repo.update_profile(target_user_id, is_active=1 if is_active else 0)
