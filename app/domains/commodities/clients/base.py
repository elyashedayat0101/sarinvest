"""
app/domains/commodities/clients/base.py
==========================================
Mirrors `domains/crypto/clients/base.py::ExchangeClient` — this is the
same "multiple external data sources behind one interface" shape, for
the same reason: you mentioned another online platform as a possible
second gold data source eventually. Add it as a second file in this
`clients/` package implementing this same ABC; `service.py` already
takes a list of clients and merges/tolerates-partial-failure across all
of them, exactly like the crypto service does across exchanges.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from app.domains.commodities.registry import InstrumentRef
from app.domains.commodities.schemas import RawCommodityPrice


class CommodityDataClient(ABC):
    name: str

    @abstractmethod
    async def fetch_one(self, instrument: InstrumentRef) -> RawCommodityPrice:
        """Fetch full current data for one instrument. Raises
        TsetmcUnavailableError on failure — never lets a raw httpx/parse
        exception escape uncaught."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_many(self, instruments: List[InstrumentRef]) -> Tuple[List[RawCommodityPrice], List[Tuple[str, str]]]:
        """Fetch many instruments, tolerating partial failure. Returns
        (successes, [(ins_code, error_message), ...])."""
        raise NotImplementedError
