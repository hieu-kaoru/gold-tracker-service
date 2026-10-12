"""Đối chiếu dự báo đã lưu với giá thực tế, tính tỉ lệ đúng.

Việc học (huấn luyện mô hình) nằm ở services/ml.py; module này chỉ chấm điểm kết quả.
"""
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import ForecastRecord, PriceTick
from . import prices

HORIZONS = (1, 7)
# Biến động nhỏ hơn ngưỡng này (%) coi là "đi ngang"; khung 7 ngày dao động rộng hơn nên ngưỡng lớn hơn
FLAT_PCT = {1: 0.15, 7: 0.5}
MAX_GAP_DAYS = 3          # ngày đích thiếu giá thì lấy ngày có giá kế tiếp trong phạm vi này


def closes_by_day(session: Session, symbol: str = config.SJC_RING) -> dict[date, float]:
    points = prices.history(session, symbol, 800, "daily")
    return {(p["ts"] + prices.VN_OFFSET).date(): p["price"] for p in points}


def world_hourly(session: Session) -> pd.Series:
    """Giá vàng thế giới theo giờ (chỉ số = thời điểm giá được biết, UTC)."""
    rows = session.execute(
        select(PriceTick.ts, PriceTick.price).where(PriceTick.symbol == config.XAUUSD_HOURLY).order_by(PriceTick.ts)
    ).all()
    return pd.Series([price for _, price in rows], index=pd.DatetimeIndex([ts for ts, _ in rows]), dtype="float64")


def actual_direction(pct: float, horizon: int) -> str:
    if abs(pct) < FLAT_PCT[horizon]:
        return "sideways"
    return "up" if pct > 0 else "down"


def outcome(record: ForecastRecord, closes: dict[date, float]) -> dict | None:
    """Thực tế sau `horizon` ngày kể từ ngày dự báo; None nếu chưa tới ngày hoặc thiếu giá."""
    target = record.day + timedelta(days=record.horizon)
    for offset in range(MAX_GAP_DAYS + 1):
        price = closes.get(target + timedelta(days=offset))
        if price is not None:
            break
    else:
        return None
    change = price - record.base_price
    pct = change / record.base_price * 100 if record.base_price else 0.0
    direction = actual_direction(pct, record.horizon)
    return {"change": change, "pct": round(pct, 2), "direction": direction, "correct": direction == record.direction}


def _records(session: Session, horizon: int, since: date | None = None) -> list[ForecastRecord]:
    query = select(ForecastRecord).where(ForecastRecord.horizon == horizon).order_by(ForecastRecord.day)
    if since:
        query = query.where(ForecastRecord.day >= since)
    return list(session.scalars(query))


def history(session: Session, days: int = 60) -> dict:
    """Bảng đối chiếu theo ngày (mới nhất trước) và tỉ lệ đúng của từng khung dự báo."""
    closes = closes_by_day(session)
    since = prices.today_vn() - timedelta(days=days - 1)
    rows: dict[date, dict] = {}
    accuracy = {}
    for horizon in HORIZONS:
        evaluated = correct = 0
        recent: list[bool] = []
        actual_counts: dict[str, int] = {}
        for record in _records(session, horizon):
            result = outcome(record, closes)
            if result is not None:
                evaluated += 1
                correct += result["correct"]
                recent.append(result["correct"])
                actual_counts[result["direction"]] = actual_counts.get(result["direction"], 0) + 1
            if record.day < since:
                continue
            rows.setdefault(record.day, {"date": record.day, "base_price": record.base_price})[f"h{horizon}"] = {
                "direction": record.direction, "confidence": record.confidence, "source": record.source,
                "model": record.model,
                "actual": result,
            }
        recent = recent[-30:]
        # Mốc so sánh: nếu lúc nào cũng đoán hướng hay xảy ra nhất thì đúng bao nhiêu
        common = max(actual_counts, key=actual_counts.get) if actual_counts else None
        accuracy[f"h{horizon}"] = {
            "evaluated": evaluated, "correct": correct,
            "rate": round(correct / evaluated, 3) if evaluated else None,
            "recent_rate": round(sum(recent) / len(recent), 3) if recent else None, "recent_rows": len(recent),
            "baseline_direction": common,
            "baseline_rate": round(actual_counts[common] / evaluated, 3) if common else None,
        }
    return {
        "rows": [rows[day] for day in sorted(rows, reverse=True)],
        "accuracy": accuracy, "flat_threshold_pct": {f"h{h}": FLAT_PCT[h] for h in HORIZONS},
    }
