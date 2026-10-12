"""Lưu dự báo mỗi ngày để sau này đối chiếu với thực tế (xem services/learning.py)."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import config
from ..models import ForecastRecord
from . import forecast, learning, ml, prices


def _upsert(session: Session, day, horizon: int, base_price: float, source: str, *, direction: str,
            confidence: float, score: float, model: str, votes: dict | None = None) -> None:
    record = session.scalar(
        select(ForecastRecord).where(ForecastRecord.day == day, ForecastRecord.horizon == horizon)
    )
    if record is None:
        record = ForecastRecord(day=day, horizon=horizon)
        session.add(record)
    votes = votes or {}
    record.direction, record.confidence, record.score, record.model = direction, confidence, score, model
    record.vote_ma, record.vote_macd, record.vote_rsi = (votes.get(name, 0.0) for name in ("ma", "macd", "rsi"))
    record.vote_news = votes.get("news")
    record.base_price = base_price
    record.source = source


def record_today(session: Session) -> int:
    """Ghi (hoặc cập nhật) dự báo của hôm nay; bản cuối ngày là bản được đem đối chiếu."""
    latest = prices.latest(session, config.SJC_RING)
    if latest is None:
        return 0
    saved = 0
    for horizon in learning.HORIZONS:
        result = forecast.forecast(session, config.SJC_RING, horizon)
        # Chỉ lưu khi dự báo dựa trên chính giá nhẫn, để đối chiếu cùng một loại giá
        if result["direction"] is None or result["basis"] != config.SJC_RING:
            continue
        _upsert(session, prices.today_vn(), horizon, latest.price, "live", direction=result["direction"],
                confidence=result["confidence"], score=result["score"], model=result["model"], votes=result["votes"])
        saved += 1
    session.commit()
    return saved


def backfill(session: Session) -> int:
    """Dựng lại dự báo cho các ngày quá khứ, mỗi ngày chỉ dùng dữ liệu có tới ngày đó.

    - Có mô hình máy học: lấy kết quả kiểm tra lùi của mô hình đang dùng (mỗi ngày do một bản mô hình
      chỉ học từ trước ngày đó đoán). Các dòng tính lại cũ được thay hết để bảng khớp với mô hình hiện tại.
    - Chưa đủ dữ liệu cho máy học: dùng công thức cộng điểm, chỉ bổ sung ngày còn thiếu.
    Không có tin tức trong các dòng này vì không có kho tin cũ. Dòng "live" không bao giờ bị đụng tới.
    """
    closes = learning.closes_by_day(session)
    days = sorted(closes)
    today = prices.today_vn()
    inserted = 0
    for horizon in learning.HORIZONS:
        trained = forecast.ml_state(session, horizon)
        if trained is not None:
            session.execute(delete(ForecastRecord).where(
                ForecastRecord.horizon == horizon, ForecastRecord.source == "backfill"))
            live = set(session.scalars(select(ForecastRecord.day).where(ForecastRecord.horizon == horizon)))
            directions = ml.direction_from(trained.replay)
            for stamp, row in trained.replay.iterrows():
                day = stamp.date()
                if day >= today or day in live or day not in closes:
                    continue
                direction = directions[stamp]
                _upsert(session, day, horizon, closes[day], "backfill", direction=direction,
                        confidence=round(float(row[direction]), 2), score=round(float(row["up"] - row["down"]), 3),
                        model=f"ml:{trained.name}")
                inserted += 1
            continue
        known = set(session.scalars(select(ForecastRecord.day).where(ForecastRecord.horizon == horizon)))
        needed = forecast.min_points(horizon)
        for index, day in enumerate(days):
            if day >= today or day in known or index + 1 < needed:
                continue
            result = forecast.compute([closes[d] for d in days[: index + 1]], horizon)
            _upsert(session, day, horizon, closes[day], "backfill", direction=result["direction"],
                    confidence=result["confidence"], score=result["score"], model="rules", votes=result["votes"])
            inserted += 1
    session.commit()
    return inserted
