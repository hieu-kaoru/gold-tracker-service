import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config
from ..collectors import ring_history
from ..collectors.base import Quote, utcnow
from ..models import PriceTick

log = logging.getLogger(__name__)

VN_OFFSET = timedelta(hours=7)


def today_vn() -> date:
    return (utcnow() + VN_OFFSET).date()


def save_quotes(session: Session, quotes: list[Quote]) -> int:
    """Ghi các quote chưa có (trùng symbol + ts thì bỏ qua). Trả về số dòng mới."""
    if not quotes:
        return 0
    # Một truy vấn lấy các mốc đã có (thay vì hỏi từng dòng) để nạp hàng nghìn nến theo giờ vẫn nhanh
    symbols = {quote.symbol for quote in quotes}
    oldest = min(quote.ts for quote in quotes)
    known = set(session.execute(
        select(PriceTick.symbol, PriceTick.ts).where(PriceTick.symbol.in_(symbols), PriceTick.ts >= oldest)
    ).all())
    inserted = 0
    for quote in quotes:
        if (quote.symbol, quote.ts) in known:
            continue
        known.add((quote.symbol, quote.ts))
        session.add(PriceTick(
            symbol=quote.symbol, buy=quote.buy, sell=quote.sell, price=quote.price,
            currency=quote.currency, unit=quote.unit, source=quote.source, ts=quote.ts,
        ))
        inserted += 1
    session.commit()
    return inserted


def latest(session: Session, symbol: str) -> PriceTick | None:
    return session.scalar(
        select(PriceTick).where(PriceTick.symbol == symbol).order_by(PriceTick.ts.desc()).limit(1)
    )


def _previous_day_close(session: Session, tick: PriceTick) -> PriceTick | None:
    """Tick cuối cùng trước 0h (giờ VN) của ngày chứa `tick` — mốc so sánh tăng/giảm trong ngày.

    Chỉ nhìn lại tối đa 7 ngày: xa hơn thì "ngày trước" không còn là mốc so sánh có nghĩa
    (ví dụ một ngày lẻ tra từ nhiều tháng trước).
    """
    local_midnight = (tick.ts + VN_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    boundary = local_midnight - VN_OFFSET
    return session.scalar(
        select(PriceTick)
        .where(PriceTick.symbol == tick.symbol, PriceTick.ts < boundary,
               PriceTick.ts >= boundary - timedelta(days=7))
        .order_by(PriceTick.ts.desc()).limit(1)
    )


def latest_all(session: Session) -> list[dict]:
    items = []
    for symbol in config.SYMBOLS:
        tick = latest(session, symbol)
        if tick is None:
            continue
        previous = _previous_day_close(session, tick)
        change = tick.price - previous.price if previous else None
        items.append({
            "symbol": tick.symbol, "buy": tick.buy, "sell": tick.sell, "price": tick.price,
            "currency": tick.currency, "unit": tick.unit, "source": tick.source, "ts": tick.ts,
            "change": change,
            "change_pct": round(change / previous.price * 100, 3) if previous and previous.price else None,
        })
    return items


def history(session: Session, symbol: str, days: int, interval: str = "raw") -> list[dict]:
    """Chuỗi giá liên tục. Không gồm các ngày lẻ tra theo yêu cầu (xem `day`): chúng nằm cách xa chuỗi
    chính, nếu lẫn vào sẽ tạo đoạn thẳng giả trên biểu đồ và trong dữ liệu huấn luyện mô hình."""
    since = utcnow() - timedelta(days=days)
    ticks = session.scalars(
        select(PriceTick)
        .where(PriceTick.symbol == symbol, PriceTick.ts >= since, PriceTick.source != ring_history.PAGE_SOURCE)
        .order_by(PriceTick.ts)
    ).all()
    if interval == "daily":
        by_day: dict = {}
        for tick in ticks:  # đã sắp theo thời gian nên tick sau ghi đè = giá đóng cửa ngày
            by_day[(tick.ts + VN_OFFSET).date()] = tick
        ticks = list(by_day.values())
    return [{"ts": t.ts, "buy": t.buy, "sell": t.sell, "price": t.price} for t in ticks]


def daily_closes(session: Session, symbol: str, days: int = 400) -> list[float]:
    return [point["price"] for point in history(session, symbol, days, "daily")]


def spread(session: Session) -> dict | None:
    """Chênh lệch giá bán nhẫn SJC 9999 so với giá thế giới quy đổi (chưa gồm thuế, phí)."""
    ring, xau, fx = (latest(session, s) for s in (config.SJC_RING, config.XAUUSD, config.USDVND))
    if not (ring and xau and fx):
        return None
    world_vnd = xau.price * fx.price * config.LUONG_PER_OZ
    diff = ring.price - world_vnd
    return {
        "ring_sell": ring.price, "xau_usd": xau.price, "usd_vnd": fx.price,
        "world_vnd_per_luong": round(world_vnd), "spread": round(diff),
        "spread_pct": round(diff / world_vnd * 100, 2), "unit": "VND/luong",
        "ts": min(ring.ts, xau.ts, fx.ts),
    }


def _day_ticks(session: Session, symbol: str, day: date) -> list[PriceTick]:
    start = datetime.combine(day, datetime.min.time()) - VN_OFFSET
    return list(session.scalars(
        select(PriceTick)
        .where(PriceTick.symbol == symbol, PriceTick.ts >= start, PriceTick.ts < start + timedelta(days=1))
        .order_by(PriceTick.ts)
    ))


def continuous_from(session: Session, symbol: str) -> date | None:
    """Ngày bắt đầu chuỗi dữ liệu liên tục (không tính các ngày lẻ tra theo yêu cầu)."""
    first = session.scalar(
        select(func.min(PriceTick.ts))
        .where(PriceTick.symbol == symbol, PriceTick.source != ring_history.PAGE_SOURCE)
    )
    return (first + VN_OFFSET).date() if first else None


def day(session: Session, day_: date, symbol: str = config.SJC_RING) -> dict | None:
    """Giá của một ngày (giờ Việt Nam): giá chốt = lần cập nhật cuối, kèm thay đổi so với ngày có giá liền trước."""
    ticks = _day_ticks(session, symbol, day_)
    earliest = continuous_from(session, symbol)
    if not ticks and (earliest is None or day_ < earliest):
        # Ngày nằm trước chuỗi dữ liệu liên tục → tra trang lịch sử, có thì lưu lại để lần sau khỏi tra
        try:
            quote = ring_history.fetch_day(day_)
        except Exception as exc:
            log.warning("Tra lịch sử ngày %s lỗi: %s", day_, exc)
            quote = None
        if quote:
            save_quotes(session, [quote])
            ticks = _day_ticks(session, symbol, day_)
    if not ticks:
        return None
    close = ticks[-1]
    previous = _previous_day_close(session, close)
    change = close.price - previous.price if previous else None
    return {
        "date": day_, "buy": close.buy, "sell": close.sell, "unit": close.unit, "source": close.source,
        "change": change,
        "change_pct": round(change / previous.price * 100, 3) if previous and previous.price else None,
        "previous_date": (previous.ts + VN_OFFSET).date() if previous else None,
        "updates": [{"ts": t.ts, "buy": t.buy, "sell": t.sell, "price": t.price} for t in ticks],
    }


def count(session: Session, symbol: str) -> int:
    return session.scalar(select(func.count()).select_from(PriceTick).where(PriceTick.symbol == symbol)) or 0
