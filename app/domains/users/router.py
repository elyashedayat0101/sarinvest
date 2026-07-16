"""
app/domains/users/router.py
==============================
Three internal sub-routers (auth, profile, admin) combined into one
exported `router`, same shape as `domains/portfolio/router.py`. Mounted
at `/api/v1/users` — versioned in the URL like `crypto`, since this is a
new domain (see ARCHITECTURE.md's versioning section).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, UploadFile

from app.domains.users.deps import get_current_user, get_user_service, require_admin
from app.domains.users.schemas import (
    AdminUserListOut, AdminUserOut, AdminUserUpdateIn, AvatarUploadOut, MeOut, OtpRequestIn,
    OtpRequestOut, OtpVerifyIn, ProfileUpdateIn, RefreshIn, TokenPairOut, UsernameAvailableOut,
)
from app.domains.users.service import UserService

router = APIRouter(prefix="/api/v1/users", tags=["users"])


# =====================================================================
# Auth
# =====================================================================
_auth_router = APIRouter(prefix="/auth", tags=["auth"])


@_auth_router.post("/otp/request", response_model=OtpRequestOut)
async def request_otp(body: OtpRequestIn, service: UserService = Depends(get_user_service)):
    return await service.request_otp(body.phone_number)


@_auth_router.post("/otp/verify", response_model=TokenPairOut)
async def verify_otp(body: OtpVerifyIn, service: UserService = Depends(get_user_service)):
    tokens, _user = await service.verify_otp(body.phone_number, body.code, body.device_label)
    return tokens


@_auth_router.post("/refresh", response_model=TokenPairOut)
async def refresh(body: RefreshIn, service: UserService = Depends(get_user_service)):
    return await service.refresh(body.refresh_token)


@_auth_router.post("/logout")
async def logout(body: RefreshIn, service: UserService = Depends(get_user_service)):
    await service.logout(body.refresh_token)
    return {"ok": True}


# =====================================================================
# Profile ("me")
# =====================================================================
_profile_router = APIRouter(prefix="/me", tags=["profile"])


def _to_me_out(user: dict) -> MeOut:
    return MeOut(
        id=user["id"], username=user["username"], full_name=user["full_name"],
        bio=user["bio"], avatar_url=user["avatar_url"], phone_number=user["phone_number"],
        role=user["role"], is_active=bool(user["is_active"]),
        profile_complete=user["username"] is not None, created_at=user["created_at"],
    )


@_profile_router.get("", response_model=MeOut)
async def get_me(user: dict = Depends(get_current_user)):
    return _to_me_out(user)


@_profile_router.patch("", response_model=MeOut)
async def update_me(
    body: ProfileUpdateIn,
    user: dict = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    updated = await service.update_profile(user["id"], **body.model_dump(exclude_unset=True))
    return _to_me_out(updated)


@_profile_router.post("/avatar", response_model=AvatarUploadOut)
async def upload_avatar(
    file: UploadFile,
    user: dict = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    content = await file.read()
    updated = await service.save_avatar(user["id"], file.filename or "avatar.jpg", content, file.content_type)
    return AvatarUploadOut(avatar_url=updated["avatar_url"])


@router.get("/username-available", response_model=UsernameAvailableOut, tags=["profile"])
async def username_available(
    username: str = Query(min_length=3, max_length=20),
    service: UserService = Depends(get_user_service),
):
    return UsernameAvailableOut(username=username.lower(), available=await service.is_username_available(username.lower()))


# =====================================================================
# Admin
# =====================================================================
_admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@_admin_router.get("/users", response_model=AdminUserListOut)
async def admin_list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: UserService = Depends(get_user_service),
):
    users, total = await service.list_users(limit, offset)
    return AdminUserListOut(
        users=[AdminUserOut(**_to_me_out(u).model_dump()) for u in users],
        total=total, limit=limit, offset=offset,
    )


@_admin_router.get("/users/{user_id}", response_model=AdminUserOut)
async def admin_get_user(user_id: int, service: UserService = Depends(get_user_service)):
    user = await service.get_profile(user_id)
    return AdminUserOut(**_to_me_out(user).model_dump())


@_admin_router.patch("/users/{user_id}", response_model=AdminUserOut)
async def admin_update_user(
    user_id: int,
    body: AdminUserUpdateIn,
    admin: dict = Depends(require_admin),
    service: UserService = Depends(get_user_service),
):
    if body.role is not None:
        await service.set_role(admin["id"], user_id, body.role)
    if body.is_active is not None:
        await service.set_active(user_id, body.is_active)
    updated = await service.get_profile(user_id)
    return AdminUserOut(**_to_me_out(updated).model_dump())


router.include_router(_auth_router)
router.include_router(_profile_router)
router.include_router(_admin_router)
