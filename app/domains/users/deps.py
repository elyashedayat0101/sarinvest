"""
app/domains/users/deps.py
============================
`HTTPBearer` rather than FastAPI's `OAuth2PasswordBearer` — the latter's
name and Swagger-UI behavior (a login form asking for a "password")
implies a password grant flow, which this app doesn't have (OTP-only).
`HTTPBearer` just means "read the `Authorization: Bearer <token>` header
and hand it to me," which is what's actually happening here.
"""
from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domains.users.exceptions import AdminRequiredError
from app.domains.users.service import UserService

_bearer_scheme = HTTPBearer(description="Access token from /auth/otp/verify or /auth/refresh")


def get_user_service(request: Request) -> UserService:
    return request.app.state.user_service


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    service: UserService = Depends(get_user_service),
) -> dict:
    return await service.get_user_from_access_token(credentials.credentials)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise AdminRequiredError("این عملیات نیازمند دسترسی مدیر است")
    return user
