"""Giá vàng thế giới (USD/oz) qua yfinance."""
import logging
from datetime import timedelta, timezone

import yfinance as yf

from .. import config
from .base import CollectorError, Quote, utcnow

log = logging.getLogger(__name__)


def _history(period: str, interval: str):
    for ticker in config.YF_GOLD_TICKERS:
        try:
            frame = yf.Ticker(ticker).history(period=period, interval=interval)
        except Exception as exc:
            log.warning("yfinance %s lỗi: %s", ticker, exc)
            continue
        frame = frame.dropna(subset=["Close"])
        if not frame.empty:
            return ticker, frame
    raise CollectorError("yfinance không trả dữ liệu vàng thế giới")


def _to_quotes(ticker: str, frame) -> list[Quote]:
    quotes = []
    for index, close in frame["Close"].items():
        ts = index.to_pydatetime()
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        quotes.append(Quote(
            symbol=config.XAUUSD, price=round(float(close), 2), currency="USD",
            unit="USD/oz", source=f"yfinance:{ticker}", ts=ts,
        ))
    return quotes


def fetch() -> list[Quote]:
    ticker, frame = _history("1d", "5m")
    return _to_quotes(ticker, frame.tail(1))


def fetch_hourly(period: str = "5d") -> list[Quote]:
    """Nến 1 giờ; mốc thời gian lưu là lúc ĐÓNG nến (UTC), tức thời điểm giá đó thực sự được biết."""
    ticker, frame = _history(period, "1h")
    quotes = _to_quotes(ticker, frame)
    for quote in quotes:
        quote.symbol = config.XAUUSD_HOURLY
        quote.ts = quote.ts + timedelta(hours=1)
    # Nến của giờ hiện tại chưa đóng: bỏ, lượt sau sẽ lấy bản hoàn chỉnh
    return [quote for quote in quotes if quote.ts <= utcnow()]


def fetch_daily_history(period: str = config.XAU_BACKFILL_PERIOD) -> list[Quote]:
    """Nến ngày để dự báo có dữ liệu ngay từ lần chạy đầu."""
    ticker, frame = _history(period, "1d")
    return _to_quotes(ticker, frame)
