"""
app/domains/users/exceptions.py
==================================
Same pattern as every other domain — subclass `LotusError` so the
existing global handler covers these without extra registration.
"""
from __future__ import annotations

from app.core.exceptions import LotusError


class InvalidPhoneNumberError(LotusError):
    status_code = 400


class OtpInvalidError(LotusError):
    """Wrong code, expired code, or no active code for this phone number —
    deliberately one generic error/message for all three (see
    service.py): telling a caller *which* of these is true is a minor
    enumeration-attack surface for no real UX benefit."""
    status_code = 400


class OtpRequestRateLimitedError(LotusError):
    status_code = 429


class OtpVerifyRateLimitedError(LotusError):
    status_code = 429


class UsernameTakenError(LotusError):
    status_code = 409


class UserNotFoundError(LotusError):
    status_code = 404


class UserInactiveError(LotusError):
    status_code = 403


class InvalidTokenError(LotusError):
    status_code = 401


class AdminRequiredError(LotusError):
    status_code = 403


class SelfRoleChangeForbiddenError(LotusError):
    status_code = 400


class AvatarUploadError(LotusError):
    status_code = 400
