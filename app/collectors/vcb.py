"""Tỷ giá USD/VND từ XML công khai của Vietcombank (tối đa 1 request / 5 phút)."""
import xml.etree.ElementTree as ET

from .. import config
from .base import CollectorError, Quote, http_get


def _num(text: str | None) -> float | None:
    text = (text or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse(xml_text: str) -> Quote:
    root = ET.fromstring(xml_text.strip())
    for node in root.iter("Exrate"):
        if node.get("CurrencyCode") == "USD":
            buy = _num(node.get("Transfer")) or _num(node.get("Buy"))
            sell = _num(node.get("Sell"))
            if not buy or not sell:
                break
            return Quote(
                symbol=config.USDVND, buy=buy, sell=sell, price=sell,
                currency="VND", unit="VND/USD", source="vietcombank",
            )
    raise CollectorError("Không thấy tỷ giá USD trong XML Vietcombank")


def fetch() -> list[Quote]:
    return [parse(http_get(config.VCB_URL).text)]
