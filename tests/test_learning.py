from datetime import date, timedelta

import pytest

from app import config
from app.collectors.base import Quote
from app.collectors.ring_history import close_ts
from app.models import ForecastRecord
from app.services import learning, prices, tracking

START = date(2026, 1, 1)


def seed_closes(session, closes):
    prices.save_quotes(session, [
        Quote(config.SJC_RING, price, "VND", "VND/luong", "test", buy=price - 3e6, sell=price,
              ts=close_ts(START + timedelta(days=i)))
        for i, price in enumerate(closes)
    ])


def add_record(session, day, horizon=1, direction="up", base=100e6, ma=1.0, macd=1.0, rsi=1.0):
    session.add(ForecastRecord(
        day=day, horizon=horizon, direction=direction, confidence=0.6, score=0.5,
        vote_ma=ma, vote_macd=macd, vote_rsi=rsi, vote_news=None, base_price=base, source="backfill",
    ))


def test_outcome_compares_with_price_after_horizon(session):
    seed_closes(session, [100e6, 101e6, 101.05e6, 99e6])
    closes = learning.closes_by_day(session)
    record = ForecastRecord(day=START, horizon=1, direction="up", base_price=100e6)
    assert learning.outcome(record, closes) == {"change": 1e6, "pct": 1.0, "direction": "up", "correct": True}
    # +0,05% nằm trong ngưỡng đi ngang nên dự báo "tăng" bị tính là sai
    flat = ForecastRecord(day=START + timedelta(days=1), horizon=1, direction="up", base_price=101e6)
    assert learning.outcome(flat, closes)["direction"] == "sideways" and not learning.outcome(flat, closes)["correct"]
    # Chưa tới ngày đích thì chưa có kết quả
    pending = ForecastRecord(day=START + timedelta(days=3), horizon=7, direction="down", base_price=99e6)
    assert learning.outcome(pending, closes) is None


def test_backfill_uses_only_past_prices_and_is_idempotent(session):
    seed_closes(session, [100e6 + i * 2e5 for i in range(70)])
    inserted = tracking.backfill(session)
    assert inserted > 0 and tracking.backfill(session) == 0
    first = session.query(ForecastRecord).filter_by(horizon=1).order_by(ForecastRecord.day).first()
    # Ngày đầu tiên dự báo được là ngày thứ 35 (đủ dữ liệu), giá gốc là giá của chính ngày đó
    assert first.day == START + timedelta(days=34) and first.base_price == 100e6 + 34 * 2e5
    assert first.source == "backfill" and first.direction == "up"


def test_history_rows_and_accuracy(client_free_session):
    session = client_free_session
    seed_closes(session, [100e6, 101e6, 100e6, 100e6])
    add_record(session, START, direction="up")                           # đúng: +1%
    add_record(session, START + timedelta(days=1), direction="up", base=101e6)   # sai: giảm
    add_record(session, START + timedelta(days=3), direction="up")       # chưa có kết quả
    session.commit()
    result = learning.history(session, days=400)
    assert result["accuracy"]["h1"]["evaluated"] == 2 and result["accuracy"]["h1"]["rate"] == 0.5
    newest = result["rows"][0]
    assert newest["date"] == START + timedelta(days=3) and newest["h1"]["actual"] is None


@pytest.fixture
def client_free_session(session, monkeypatch):
    # Cố định "hôm nay" để cửa sổ học và lọc ngày không phụ thuộc đồng hồ
    monkeypatch.setattr(prices, "today_vn", lambda: START + timedelta(days=200))
    return session
