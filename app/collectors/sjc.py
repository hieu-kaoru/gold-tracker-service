"""Giá nhẫn SJC 9999.

Thứ tự nguồn:
1. sjc.com.vn (chính thức) — hiện đứng sau Cloudflare challenge nên thường bị chặn;
   không tìm cách vượt, chỉ thử và bỏ qua nếu bị từ chối.
2. giavang.org — trang tổng hợp đăng lại bảng giá SJC.
3. baomoi.com — trang tiện ích giá vàng của Báo Mới, cũng đăng nguyên bảng giá SJC (dự phòng).
"""
import logging
import re

import httpx
from bs4 import BeautifulSoup

from .. import config
from .base import CollectorError, Quote, http_get

log = logging.getLogger(__name__)

UNIT = "VND/luong"
# Giá SJC hợp lý nằm trong khoảng này (VND/lượng) — chặn lỗi sai đơn vị khi trang nguồn đổi định dạng
MIN_PRICE, MAX_PRICE = 20_000_000, 1_000_000_000


def _digits(text: str) -> float | None:
    cleaned = re.sub(r"[^\d]", "", text or "")
    return float(cleaned) if cleaned else None


def _quote(symbol: str, buy: float, sell: float, source: str) -> Quote:
    for value in (buy, sell):
        if not MIN_PRICE <= value <= MAX_PRICE:
            raise CollectorError(f"{source}: giá {symbol} ngoài khoảng hợp lý: {value}")
    return Quote(symbol=symbol, buy=buy, sell=sell, price=sell, currency="VND", unit=UNIT, source=source)


def _classify(name: str) -> str | None:
    n = name.lower()
    # Dòng "1 chỉ, 2 chỉ, 5 chỉ" là giá chuẩn; dòng "0.5 chỉ, 0.3 chỉ" có giá bán cao hơn nên bỏ qua
    if "nhẫn" in n and "sjc" in n and "99,99" in n and "1 chỉ" in n:
        return config.SJC_RING
    return None


def parse_official(payload: dict) -> list[Quote]:
    """JSON của PriceService.ashx: {"success": true, "data": [{TypeName, BuyValue, SellValue}, ...]}."""
    quotes: dict[str, Quote] = {}
    for row in payload.get("data") or []:
        symbol = _classify(str(row.get("TypeName", "")))
        if symbol and symbol not in quotes and row.get("BuyValue") and row.get("SellValue"):
            quotes[symbol] = _quote(symbol, float(row["BuyValue"]), float(row["SellValue"]), "sjc.com.vn")
    return list(quotes.values())


def parse_mirror(html: str) -> list[Quote]:
    """Bảng giavang.org, đơn vị nghìn đồng/lượng. Lấy dòng đầu tiên (khu vực Hồ Chí Minh)."""
    soup = BeautifulSoup(html, "html.parser")
    quotes: dict[str, Quote] = {}
    for tr in soup.select("table tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        symbol = _classify(cells[0])
        buy, sell = _digits(cells[-2]), _digits(cells[-1])
        if symbol and symbol not in quotes and buy and sell:
            quotes[symbol] = _quote(symbol, buy * 1000, sell * 1000, "giavang.org")
    return list(quotes.values())


def parse_baomoi(html: str) -> list[Quote]:
    """Bảng giá SJC trên Báo Mới: ô tên loại vàng rồi tới hai ô mua/bán, đơn vị đồng/lượng."""
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.select("table tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        for index, cell in enumerate(cells[:-2]):
            if _classify(cell) == config.SJC_RING:
                buy, sell = _digits(cells[index + 1]), _digits(cells[index + 2])
                if buy and sell:
                    return [_quote(config.SJC_RING, buy, sell, "baomoi.com")]
    return []


def _fetch_official() -> list[Quote]:
    resp = httpx.post(
        config.SJC_OFFICIAL_URL,
        data={"method": "GetCurrentGoldPricesByBranch", "BranchId": "1"},
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.HTTP_TIMEOUT,
    )
    if resp.status_code in (403, 503) or "json" not in resp.headers.get("content-type", ""):
        raise CollectorError(f"sjc.com.vn từ chối truy cập tự động (HTTP {resp.status_code}, Cloudflare)")
    resp.raise_for_status()
    return parse_official(resp.json())


def _fetch_mirror() -> list[Quote]:
    return parse_mirror(http_get(config.SJC_MIRROR_URL).text)


def _fetch_baomoi() -> list[Quote]:
    return parse_baomoi(http_get(config.SJC_BAOMOI_URL).text)


SOURCES = (("sjc.com.vn", _fetch_official), ("giavang.org", _fetch_mirror), ("baomoi.com", _fetch_baomoi))


def fetch() -> list[Quote]:
    """Thử lần lượt từng nguồn cho tới khi có giá nhẫn."""
    found: dict[str, Quote] = {}
    errors: list[str] = []
    for name, fetcher in SOURCES:
        try:
            for quote in fetcher():
                found.setdefault(quote.symbol, quote)
        except Exception as exc:  # một nguồn hỏng không được làm hỏng cả lượt thu thập
            errors.append(f"{name}: {exc}")
            log.warning("Nguồn SJC %s lỗi: %s", name, exc)
        if config.SJC_RING in found:
            break
    if not found:
        raise CollectorError("Không lấy được giá nhẫn SJC từ nguồn nào: " + " | ".join(errors))
    return list(found.values())
