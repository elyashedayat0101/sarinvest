"""
app/core/config.py
===================
Centralized, typed configuration. Replaces the old argparse-only setup
(host/port/interval were CLI-only; everything else was a bare module-level
constant like DEFAULT_FUND or the queue maxsize).

Values are read from environment variables / a .env file. See .env.example.
"""
from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LOTUS_",
        extra="ignore",
    )

    # -- App --
    app_name: str = "Lotus Options Monitor"
    env: str = Field(default="development")  # development | staging | production
    debug: bool = False

    # -- Server --
    host: str = "0.0.0.0"
    port: int = 5000

    # -- Background fetch loop --
    poll_interval: float = 15.0
    max_poll_interval: float = 60.0  # ceiling for auto-adjust
    first_fetch_timeout: float = 60.0

    # -- Persistence queue --
    persist_queue_maxsize: int = 10

    # -- Databases (SQLAlchemy async URLs) --
    # aiosqlite driver, matching the original two-file design (one SQLite
    # file for market data, one for portfolios). Use an absolute path in
    # any deployment where the process's working directory isn't
    # guaranteed, e.g. "sqlite+aiosqlite:////var/lib/lotus/lotus_options.db"
    # (note the 4 slashes for an absolute path vs 3 for relative).
    lotus_db_url: str = "sqlite+aiosqlite:///lotus_options.db"
    portfolio_db_url: str = "sqlite+aiosqlite:///lotus_portfolio.db"
    # Shared database for every domain added from here on (crypto first).
    # See app/db/models/base.py::SharedBase and ARCHITECTURE.md.
    shared_db_url: str = "sqlite+aiosqlite:///app_shared.db"
    db_echo: bool = False  # log every SQL statement — verbose, dev-only

    # -- Crypto domain --
    crypto_tracked_symbols: List[str] = Field(default_factory=lambda: ["USDT-USD"])
    crypto_poll_interval: float = 30.0
    crypto_cache_ttl: float = 10.0
    crypto_http_timeout: float = 10.0
    crypto_min_call_interval: float = 1.0  # crude per-exchange local rate limit; see clients/base.py
    crypto_binance_base_url: str = "https://api.binance.com"
    crypto_coinbase_base_url: str = "https://api.exchange.coinbase.com"
    crypto_kraken_base_url: str = "https://api.kraken.com"
    # Canonical symbol -> exchange-specific pair string. IMPORTANT: verify
    # these against each exchange's live docs before deploying — pair
    # naming varies and changes over time (see clients/*.py docstrings).
    # Binance's main international exchange does NOT list a direct
    # USDT/USD spot pair (USDT is usually the *quote* currency there, not
    # something priced directly in USD) — Binance.US does list "USDTUSD",
    # so either point crypto_binance_base_url at Binance.US, pick a proxy
    # pair deliberately (e.g. USDT/BUSD), or drop Binance from the client
    # list for this specific symbol. Left unmapped by default rather than
    # guessing wrong; UnsupportedSymbolError explains why if you query it
    # before configuring this.
    crypto_binance_symbol_map: dict[str, str] = Field(default_factory=dict)
    crypto_coinbase_symbol_map: dict[str, str] = Field(default_factory=lambda: {"USDT-USD": "USDT-USD"})
    crypto_kraken_symbol_map: dict[str, str] = Field(default_factory=lambda: {"USDT-USD": "USDTZUSD"})

    # -- Users / Auth domain --
    # OTP codes live in Redis (see app/domains/users/otp_store.py), not
    # this app's SQLite databases — they're ephemeral, short-TTL data.
    # Leave unset for local dev: falls back to an in-memory store
    # (single-process only, resets on restart — never use in production,
    # see otp_store.py's docstring).
    redis_url: Optional[str] = None
    # jwt_secret_key and otp_hash_secret MUST be overridden via env in any
    # non-dev environment — these defaults are intentionally obviously
    # insecure so a deployment that forgets to set them is easy to notice,
    # not one that silently ships with a guessable secret.
    jwt_secret_key: str = "CHANGE_ME_dev_only_insecure_secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    otp_code_length: int = 6
    otp_expire_seconds: int = 300
    otp_resend_interval_seconds: int = 60
    otp_max_verify_attempts: int = 5
    otp_hash_secret: str = "CHANGE_ME_dev_only_otp_pepper"
    # Phone numbers (normalized E.164 form, e.g. "+989121234567") promoted
    # to admin automatically on first login — see service.py::verify_otp.
    # This is the bootstrap mechanism for getting your first admin account
    # without direct DB access; leave empty and promote manually via a
    # trusted existing admin (or direct DB edit) if you'd rather not have
    # a standing config-driven promotion path in production.
    bootstrap_admin_phone_numbers: List[str] = Field(default_factory=list)
    avatar_upload_dir: str = "uploads/avatars"
    avatar_max_size_bytes: int = 2 * 1024 * 1024
    avatar_allowed_content_types: List[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )

    # -- Commodities domain (gold/silver ETFs via TSETMC) --
    commodity_groups: List[str] = Field(default_factory=lambda: ["gold"])  # add "silver" once registry.py is populated
    commodity_poll_interval: float = 60.0
    commodity_cache_ttl: float = 20.0
    commodity_http_timeout: float = 10.0
    commodity_max_concurrent_requests: int = 8  # per-instrument fetch fan-out cap — see clients/tsetmc.py

    # -- Gold retail-price platforms (commodities domain, platform_service.py) --
    # See clients/platform_base.py — these six response shapes are UNVERIFIED.
    gold_platform_poll_interval: float = 60.0  # "every 1 min", as requested
    gold_platform_cache_ttl: float = 90.0      # slightly longer than the poll interval so a slow cycle doesn't cause a cache miss
    gold_platform_http_timeout: float = 10.0
    melligold_symbol: str = "XAU18"

    # -- Defaults --
    default_fund: str = "lotus"

    # -- CORS --
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    cors_allow_credentials: bool = False

    # -- Logging --
    log_level: str = "INFO"

    # -- Static frontend --
    static_dir: str = "static"
    index_file: str = "index1.html"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import this, don't instantiate Settings() directly."""
    return Settings()
