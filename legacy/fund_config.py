from dataclasses import dataclass, field
from typing import Dict


@dataclass
class FundConfig:
    fund_id: str
    name_fa: str
    name_en: str
    search_term: str
    code_prefix: str
    strike_multiplier: int
    date_from: str = "1405/01/01"
    date_to: str = "1406/12/29"

    def matches_code(self, code: str) -> bool:
        return code.startswith(self.code_prefix)


FUNDS: Dict[str, FundConfig] = {
    "lotus": FundConfig(
        fund_id="lotus", name_fa="صندوق طلای لوتوس", name_en="Gold Lotus Fund",
        search_term="لوتوس", code_prefix="TL", strike_multiplier=10000,
    ),
    "kahroba": FundConfig(
        fund_id="kahroba", name_fa="صندوق طلای کهربا", name_en="Kahroba Gold Fund",
        search_term="کهربا", code_prefix="KA", strike_multiplier=1000,
    ),
    "Silver": FundConfig(
        fund_id="Silver", name_fa="نقره", name_en="SilverBar",
        search_term="نقره", code_prefix="SL", strike_multiplier=1000,
    ),
    "Gold": FundConfig(
        fund_id="Gold", name_fa="شمش", name_en="Gold",
        search_term="شمش", code_prefix="GB", strike_multiplier=1000,
    ),
}

MONTHS = {
    "ME": "مهر", "TR": "تیر", "DY": "دی", "AB": "آبان", "AZ": "آذر",
    "BA": "بهمن", "FA": "فروردین", "OR": "اردیبهشت", "KH": "خرداد",
    "SH": "شهریور", "MO": "مرداد", "ES": "اسفند", "BH": "بهمن",
}


def get_fund(fund_id: str) -> FundConfig:
    if fund_id not in FUNDS:
        raise ValueError(f"Unknown fund '{fund_id}'. Available: {list(FUNDS.keys())}")
    return FUNDS[fund_id]


def detect_fund(contract_code: str):
    for fund in FUNDS.values():
        if fund.matches_code(contract_code):
            return fund
    return None


def all_fund_ids():
    return list(FUNDS.keys())
