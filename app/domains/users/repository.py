"""
app/domains/users/repository.py
==================================
Same discipline as every other domain's repository: translate between
SQLAlchemy and plain dicts/ORM objects, no business logic (OTP
generation, token issuance, rate-limit decisions all live in
`service.py`), one short-lived `AsyncSession` per method.

OTP storage isn't here — it's in `otp_store.py` (Redis-backed), not
SQLAlchemy. This class only ever touches `users` and `refresh_tokens`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.utils import model_to_dict
from app.domains.users.models import RefreshToken, User


class UserRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # ---- User ----

    async def get_by_phone(self, phone_number: str) -> Optional[dict]:
        async with self._session_factory() as session:
            row = (await session.execute(
                select(User).where(User.phone_number == phone_number)
            )).scalar_one_or_none()
            return model_to_dict(row) if row else None

    async def get_by_id(self, user_id: int) -> Optional[dict]:
        async with self._session_factory() as session:
            row = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            return model_to_dict(row) if row else None

    async def get_by_username(self, username: str) -> Optional[dict]:
        async with self._session_factory() as session:
            row = (await session.execute(
                select(User).where(User.username == username)
            )).scalar_one_or_none()
            return model_to_dict(row) if row else None

    async def create(self, phone_number: str, role: str = "user") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                obj = User(phone_number=phone_number, role=role, is_active=1, created_at=now, updated_at=now)
                session.add(obj)
                await session.flush()
                return model_to_dict(obj)

    async def update_profile(self, user_id: int, **fields) -> dict:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(update(User).where(User.id == user_id).values(**fields))
                row = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
                return model_to_dict(row)

    async def set_role(self, user_id: int, role: str) -> None:
        await self.update_profile(user_id, role=role)

    async def set_active(self, user_id: int, is_active: bool) -> None:
        await self.update_profile(user_id, is_active=1 if is_active else 0)

    async def list_users(self, limit: int = 50, offset: int = 0) -> Tuple[List[dict], int]:
        async with self._session_factory() as session:
            total = (await session.execute(select(func.count(User.id)))).scalar_one()
            rows = (await session.execute(
                select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
            )).scalars().all()
            return [model_to_dict(r) for r in rows], total

    # ---- Refresh tokens ----

    async def save_refresh_token(self, jti: str, user_id: int, token_hash: str, expires_at: str, device_label: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                session.add(RefreshToken(
                    jti=jti, user_id=user_id, token_hash=token_hash,
                    device_label=device_label, expires_at=expires_at, created_at=now,
                ))

    async def get_refresh_token(self, jti: str) -> Optional[dict]:
        async with self._session_factory() as session:
            row = (await session.execute(select(RefreshToken).where(RefreshToken.jti == jti))).scalar_one_or_none()
            return model_to_dict(row) if row else None

    async def revoke_refresh_token(self, jti: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(update(RefreshToken).where(RefreshToken.jti == jti).values(revoked_at=now))

    async def revoke_all_refresh_tokens(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
