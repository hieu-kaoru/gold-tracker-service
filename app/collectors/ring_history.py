"""Lịch sử giá nhẫn SJC 9999 theo ngày (giá chốt cuối ngày)."""
from datetime import date, datetime, time, timedelta

from bs4 import BeautifulSoup

from .. import config
from .base import CollectorError, Quote, http_get
from .sjc import _classify, _digits, _quote

VN_OFFSET = timedelta(hours=7)
# Giá chốt ngày được gắn mốc 23:59 giờ Việt Nam để luôn là tick cuối cùng của ngày đó
CLOSE_TIME = time(23, 59)
# Nguồn của các ngày lẻ tra theo yêu cầu; tách riêng để không bị tính vào chuỗi dữ liệu liên tục
PAGE_SOURCE = "giavang.org/lich-su"


def close_ts(day: date) -> datetime:
    return datetime.combine(day, CLOSE_TIME) - VN_OFFSET


def parse_api(payload: dict, today: date) -> list[Quote]:
    """JSON vang.today: {"history": [{"date": "YYYY-MM-DD", "prices": {"SJ9999": {buy, sell}}}]}, VND/lượng.

    Bỏ ngày hôm nay (chưa chốt, đã có collector trực tiếp) và các dòng thiếu hoặc sai giá.
    """
    quotes = []
    for row in payload.get("history") or []:
        price = (row.get("prices") or {}).get("SJ9999") or {}
        try:
            day = date.fromisoformat(row["date"])
            quote = _quote(config.SJC_RING, float(price["buy"]), float(price["sell"]), "vang.today")
        except (KeyError, TypeError, ValueError, CollectorError):
            continue
        if day < today:
            quote.ts = close_ts(day)
            quotes.append(quote)
    return quotes


def parse_page(html: str, day: date) -> Quote | None:
    """Trang lịch sử giavang.org (nghìn đồng/lượng, có thêm cột Thời gian). None nếu ngày đó không có giá nhẫn."""
    for tr in BeautifulSoup(html, "html.parser").select("table tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 3 or _classify(cells[0]) != config.SJC_RING:
            continue
        buy, sell = _digits(cells[1]), _digits(cells[2])
        if buy and sell:
            quote = _quote(config.SJC_RING, buy * 1000, sell * 1000, PAGE_SOURCE)
            quote.ts = close_ts(day)
            return quote
    return None


def fetch_daily(today: date) -> list[Quote]:
    payload = http_get(config.RING_HISTORY_URL).json()
    if not payload.get("success"):
        raise CollectorError("vang.today không trả lịch sử giá nhẫn")
    return parse_api(payload, today)


def fetch_day(day: date) -> Quote | None:
    return parse_page(http_get(config.RING_HISTORY_PAGE_URL.format(date=day.isoformat())).text, day)
