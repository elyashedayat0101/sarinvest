"""
app/domains/users/otp_sender.py
==================================
Mirrors the `ExchangeClient` pattern from `domains/crypto/clients/` —
one small ABC, swap implementations via `main.py`'s lifespan wiring. Kept
as one file (ABC + the one real implementation) rather than a `senders/`
subpackage the way crypto has `clients/`, because there's only one
concrete sender today; split into a subpackage the moment you add a
second real provider (Kavenegar, Twilio, etc.) — same "don't build the
subpackage until there's more than one thing in it" rule ARCHITECTURE.md
applies elsewhere.

`LogOtpSender` is the only implementation provided — it logs the code
instead of sending an SMS, which is what you want in development and
**must not** reach production. `main.py` should only wire this one up
when `settings.env != "production"`; a real provider (see the docstring
below) is a required addition before shipping OTP auth for real users.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger("users.otp")


class OtpSender(ABC):
    @abstractmethod
    async def send(self, phone_number: str, code: str) -> None:
        """Deliver `code` to `phone_number`. Raise on failure — callers
        (service.py) treat any exception here as "OTP request failed,"
        not as "silently succeeded.\""""
        raise NotImplementedError


class LogOtpSender(OtpSender):
    """
    Development-only. Logs the code instead of sending it.

    To add a real provider: create e.g. `KavenegarOtpSender` in this same
    file (or promote to a `senders/` subpackage once there's a second
    one), implementing `send()` via that provider's HTTP API — follow the
    same shape as `domains/crypto/clients/*.py`: one `httpx.AsyncClient`
    call, translate provider errors into a raised exception, no silent
    failures. Wire it in `main.py`'s lifespan in place of `LogOtpSender`.
    """
    async def send(self, phone_number: str, code: str) -> None:
        log.warning("DEV OTP — phone=%s code=%s (this must never log in production)", phone_number, code)
