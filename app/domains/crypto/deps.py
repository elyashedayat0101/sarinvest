"""
app/domains/crypto/deps.py
=============================
Domain-local DI providers. `app/api/deps.py` stays for truly cross-cutting
dependencies (settings, fund config) that predate the domain split;
anything domain-specific lives with its domain instead of growing that
file into a junk drawer as more domains arrive. See ARCHITECTURE.md.
"""
from __future__ import annotations

from fastapi import Request

from app.domains.crypto.service import CryptoPriceService


def get_crypto_service(request: Request) -> CryptoPriceService:
    return request.app.state.crypto_service
