"""
app/domains/commodities/clients/platform_base.py
=====================================================
Same shape as `crypto`'s `ExchangeClient` — one current-price quote per
platform, not per-instrument like `tsetmc.py`. That's the right ABC to
copy here, not `CommodityDataClient` (which is "one registry, many
instruments, one data source" — this is "one thing (gold), many
sources").

READ THIS BEFORE TRUSTING ANY VALUE THESE CLIENTS RETURN.

Every URL in the six concrete client files was supplied directly and is
used verbatim — that part is certain. The JSON *parsing* is not: all six
platforms (hamrahgold.com, digikala.com, technogold.gold, talasea.ir,
milli.gold, melligold.com) either block automated fetching via
`robots.txt` or have no public API documentation, and none could be
independently reached to confirm actual response shapes while building
this. This is a materially different situation from `clients/tsetmc.py`,
where the field names came from a real, checked-in, typed open-source
library — there is no equivalent ground truth here.

What each client does instead:
1. Calls the exact URL/params supplied, with a browser-like `User-Agent`
   (several of these looked like they might reject non-browser clients,
   same situation as TSETMC).
2. Tries several plausible field-name variants for buy/sell price,
   informed by common conventions in Iranian gold-platform JSON APIs
   (`price`, `sell`, `buy`, `sellPrice`, `buyPrice`, Persian-transliterated
   keys, etc.) — a heuristic, not a confirmed schema.
3. If nothing plausible is found, raises `GoldPlatformUnavailableError`
   with the **actual top-level keys of the response** in the message —
   so fixing a wrong guess is "look at the error, see the real key name,
   change one line," not "re-reverse-engineer from scratch."

**Before relying on this domain for real prices**: hit each URL directly
(`curl`, browser devtools, Postman — anywhere that isn't this sandboxed
environment) with real response in hand, and update the corresponding
client's `_parse` method to match exactly. That's expected, necessary
follow-up work, not a sign something was built wrong.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence

from app.domains.commodities.exceptions import GoldPlatformUnavailableError
from app.domains.commodities.schemas import RawGoldPlatformPrice


class GoldPricePlatformClient(ABC):
    name: str

    @abstractmethod
    async def fetch_price(self) -> RawGoldPlatformPrice:
        """Raises GoldPlatformUnavailableError on any failure — network,
        non-200, or unparseable response. Never lets a raw httpx/KeyError
        escape uncaught."""
        raise NotImplementedError


def find_number(data: Any, *candidate_paths: Sequence[str]) -> Optional[float]:
    """
    Tries each dotted-path-as-tuple in `candidate_paths` against `data`
    in order, returns the first one that resolves to something
    number-like. E.g. `find_number(j, ("data", "sell"), ("sellPrice",))`
    tries `j["data"]["sell"]` then `j["sellPrice"]`.

    Deliberately permissive about type (accepts int/float/numeric string)
    since these platforms may return prices as JSON numbers or as
    strings — both show up in the wild across Iranian financial APIs.
    """
    for path in candidate_paths:
        node = data
        try:
            for key in path:
                node = node[key]
        except (KeyError, TypeError, IndexError):
            continue
        if isinstance(node, (int, float)):
            return float(node)
        if isinstance(node, str):
            try:
                return float(node.replace(",", "").strip())
            except ValueError:
                continue
    return None


def require_parsed(platform: str, raw_response: Any, **parsed_fields) -> dict:
    """Raises a diagnostic error if every parsed field came back None —
    that means none of our field-name guesses matched this platform's
    real response shape. The error message includes the actual top-level
    keys so fixing it is fast, not another blind guess."""
    if all(v is None for v in parsed_fields.values()):
        keys = list(raw_response.keys()) if isinstance(raw_response, dict) else type(raw_response).__name__
        raise GoldPlatformUnavailableError(
            f"{platform}: none of the expected price fields were found in the response. "
            f"Top-level keys/shape seen: {keys}. Update this client's field-name guesses "
            f"to match — see clients/platform_base.py's module docstring."
        )
    return parsed_fields
