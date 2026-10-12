import math
from datetime import date, timedelta

import pytest

from app import config
from app.collectors.base import Quote
from app.collectors.ring_history import close_ts
from app.models import ForecastRecord
from app.services import ml, prices, tracking

START = date(2026, 1, 1)
DAYS = 260


def world_and_ring(days=DAYS):
    """Vàng thế giới dao động theo chu kỳ; giá nhẫn lặp lại biến động của phiên thế giới vừa lộ ra.

    Tức là hướng giá nhẫn ngày mai đoán được từ dữ kiện "vàng thế giới phiên gần nhất" của hôm nay.
    """
    world, ring = {}, {}
    level = 4000.0
    for i in range(days):
        level *= 1 + 0.012 * math.sin(i / 2.5)
        world[START + timedelta(days=i)] = level
    price = 100e6
    for i in range(days):
        day = START + timedelta(days=i)
        if i >= 2:  # phiên thế giới đề ngày i-1 lộ ra trong ngày i và giá nhẫn ngày i+... đi theo
            price *= world[START + timedelta(days=i - 2)] / world[START + timedelta(days=i - 3)] if i >= 3 else 1
        ring[day] = price
    return ring, world


def test_features_do_not_look_ahead():
    ring, world = world_and_ring()
    cut = START + timedelta(days=150)
    full = ml.build_features(ring, world, 1)
    truncated = ml.build_features(
        {d: v for d, v in ring.items() if d <= cut}, {d: v for d, v in world.items() if d <= cut}, 1)
    # Đặc trưng của một ngày không đổi khi thêm dữ liệu của những ngày sau nó
    assert full.loc[truncated.index].round(6).equals(truncated.round(6))
    # Phiên thế giới đề ngày t chưa lộ ra trong ngày t: đổi giá phiên đó không làm đổi đặc trưng ngày t
    bumped = dict(world)
    bumped[cut] *= 1.05
    assert ml.build_features(ring, bumped, 1).loc[str(cut)].equals(full.loc[str(cut)])


def test_labels_use_flat_band_and_leave_recent_days_unknown():
    ring = {START + timedelta(days=i): price for i, price in enumerate([100e6, 101e6, 101.05e6, 99e6])}
    labels = ml.build_labels(ring, 1, flat_pct=0.15)
    assert list(labels[:3]) == ["up", "sideways", "down"] and labels.isna().iloc[-1]


def test_model_learns_a_real_pattern_and_beats_baseline():
    ring, world = world_and_ring()
    trained = ml.train(ring, world, 1, flat_pct=0.15)
    test, baseline = trained.stats["model"], trained.stats["baseline"]
    assert test["rows"] == ml.HOLDOUT_DAYS and test["rate"] > 0.7 > baseline["rate"]
    # Mô hình được chọn bằng giai đoạn trước đó, không dùng 60 ngày để riêng
    assert trained.stats["candidates"]["logistic"]["rows"] == trained.stats["replay"]["rows"] - ml.HOLDOUT_DAYS
    probabilities = trained.predict()
    assert set(probabilities) == set(ml.CLASSES) and sum(probabilities.values()) == pytest.approx(1)
    assert sum(share for _, share in trained.importance) == pytest.approx(1, abs=0.02)


def test_walk_forward_never_trains_on_unknown_outcomes(monkeypatch):
    ring, world = world_and_ring()
    features = ml.build_features(ring, world, 7)
    labels = ml.build_labels(ring, 7, 0.5).reindex(features.index)
    seen = []

    def spy(factory, train_features, train_labels):
        seen.append(train_features.index.max())
        return original(factory, train_features, train_labels)

    original = ml._fit
    monkeypatch.setattr(ml, "_fit", spy)
    replay = ml.walk_forward(features, labels, ml._candidates()["logistic"], 7)
    # Mỗi lần huấn luyện, ngày học mới nhất phải cách ngày được đoán ít nhất 7 ngày
    blocks = replay.index[:: ml.RETRAIN_EVERY]
    assert len(seen) == len(blocks)
    assert all((first - latest).days >= 7 for first, latest in zip(blocks, seen))


def test_too_little_data_means_no_model():
    ring, world = world_and_ring(days=80)
    assert ml.train(ring, world, 1, 0.15) is None


def seed(session, ring, world):
    today = prices.today_vn()
    shift = today - max(ring)  # dời chuỗi để ngày cuối là hôm nay
    quotes = [Quote(config.SJC_RING, value, "VND", "VND/luong", "test", buy=value - 3e6, sell=value,
                    ts=close_ts(day + shift)) for day, value in ring.items()]
    quotes += [Quote(config.XAUUSD, value, "USD", "USD/oz", "test", ts=close_ts(day + shift) - timedelta(hours=12))
               for day, value in world.items()]
    prices.save_quotes(session, quotes)


def test_forecast_endpoint_uses_the_ml_model(client, session):
    seed(session, *world_and_ring())
    body = client.get("/forecast").json()
    assert body["model"] == "ml:logistic" and body["basis"] == "SJC_RING"
    assert sum(body["probabilities"].values()) == pytest.approx(1, abs=0.01)
    assert body["direction"] == max(body["probabilities"], key=body["probabilities"].get)
    assert body["confidence"] == pytest.approx(body["probabilities"][body["direction"]], abs=0.01)
    assert body["summary"].startswith("Giá vàng nhẫn nhiều khả năng") and body["reasons"][0].startswith("Máy ước tính")
    assert "cách đoán đơn giản" in body["comparison"] and "để riêng" in body["comparison"]
    assert body["baseline_direction"] in {"up", "down", "sideways"}
    assert "scikit-learn" in body["technical_details"][0]
    assert client.get("/forecast", params={"horizon": 7}).json()["model"].startswith("ml:")


def test_history_is_rebuilt_from_the_ml_replay(client, session):
    seed(session, *world_and_ring())
    session.add(ForecastRecord(  # dòng tính lại kiểu cũ phải bị thay
        day=prices.today_vn() - timedelta(days=3), horizon=1, direction="sideways", confidence=0.5, score=0,
        vote_ma=0, vote_macd=0, vote_rsi=0, base_price=1, source="backfill", model="rules"))
    session.commit()
    assert tracking.backfill(session) > 50 and tracking.record_today(session) == 2

    body = client.get("/forecast/history", params={"days": 30}).json()
    assert {row["h1"]["model"] for row in body["rows"]} == {"ml:logistic"}
    assert body["rows"][0]["h1"]["source"] == "live" and body["rows"][1]["h1"]["source"] == "backfill"
    info = body["model"]["h1"]
    assert info["name"] == "logistic" and info["test"]["rate"] > info["baseline"]["rate"]
    # Tỉ lệ đúng trong bảng chính là kết quả kiểm tra lùi của mô hình trên toàn bộ các ngày
    assert body["accuracy"]["h1"]["rate"] == pytest.approx(info["replay"]["rate"], abs=0.03)
    assert info["holdout_days"] == 60 and info["test"]["rows"] == 60
    # Chạy lại không nhân đôi dòng và không đụng dòng lưu thật
    before = session.query(ForecastRecord).count()
    tracking.backfill(session)
    assert session.query(ForecastRecord).count() == before


@pytest.fixture
def client(session):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_model_is_chosen_without_looking_at_the_holdout(monkeypatch):
    """Mô hình tệ ở giai đoạn chọn nhưng "may" ở giai đoạn để riêng không được chọn."""
    import pandas as pd

    ring, world = world_and_ring()
    real = ml.walk_forward

    def fake_walk_forward(features, labels, factory, horizon):
        replay = real(features, labels, ml._candidates()["good"], horizon)
        if factory is ml._candidates()["lucky"]:
            # Đoán sai hết trước giai đoạn để riêng, đúng hết trong giai đoạn để riêng
            truth = labels.reindex(replay.index)
            cutoff = truth.dropna().index[-1] - pd.to_timedelta(ml.HOLDOUT_DAYS - 1, unit="D")
            for stamp in replay.index:
                actual = truth[stamp] if isinstance(truth[stamp], str) else "up"
                wrong = "down" if actual != "down" else "up"
                pick = actual if stamp >= cutoff else wrong
                replay.loc[stamp, list(ml.CLASSES)] = [1.0 if name == pick else 0.0 for name in ml.CLASSES]
        return replay

    logistic = ml._candidates()["logistic"]
    lucky = lambda: logistic()  # noqa: E731  (một factory riêng để nhận diện)
    monkeypatch.setattr(ml, "_candidates", lambda: {"good": logistic, "lucky": lucky})
    monkeypatch.setattr(ml, "walk_forward", fake_walk_forward)
    monkeypatch.setitem(__import__("app.services.forecast", fromlist=["x"]).MODEL_NAMES, "good", "good")
    trained = ml.train(ring, world, 1, flat_pct=0.15)
    assert trained.name == "good" and trained.stats["candidates"]["lucky"]["rate"] == 0.0


def hourly_series(days=DAYS, start_price=4000.0):
    """Giá theo giờ: mỗi ngày trôi đều, riêng buổi chiều → tối có thêm một nhịp biết trước."""
    import pandas as pd

    stamps, values, price = [], [], start_price
    for i in range(days):
        day = pd.Timestamp(START + timedelta(days=i))
        for hour in range(1, 25):
            price *= 1 + (0.004 * math.sin(i / 2.0) if 10 < hour <= 16 else 0.0001)
            stamps.append(day + pd.to_timedelta(hour, unit="h"))
            values.append(price)
    return pd.Series(values, index=pd.DatetimeIndex(stamps))


def test_intraday_features_only_use_prices_known_by_23h_vietnam_time():
    import pandas as pd

    ring, world = world_and_ring()
    hourly = hourly_series()
    cut = pd.Timestamp(START + timedelta(days=150))
    full = ml.build_features(ring, world, 1, hourly)
    assert full["x_late"].abs().max() > 1 and full["x_day"].abs().max() > 1  # đặc trưng thực sự có tín hiệu

    # Mọi mức giá sau 16:00 UTC (23h VN) của ngày `cut` — kể cả phần còn lại của chính ngày đó — không
    # được ảnh hưởng tới đặc trưng của ngày `cut` hay các ngày trước.
    tampered = hourly.copy()
    tampered[tampered.index > cut + pd.to_timedelta(ml.DAY_END_UTC, unit="h")] *= 1.5
    changed = ml.build_features(ring, world, 1, tampered)
    assert changed.loc[:cut].equals(full.loc[:cut])
    assert not changed.loc[cut + pd.to_timedelta(1, unit="D"):].equals(full.loc[cut + pd.to_timedelta(1, unit="D"):])
    # Không có dữ liệu theo giờ thì hai đặc trưng bằng 0, phần còn lại không đổi
    without = ml.build_features(ring, world, 1)
    assert (without[["x_day", "x_late"]] == 0).all().all()
    assert without.drop(columns=["x_day", "x_late"]).equals(full.drop(columns=["x_day", "x_late"]))


def test_hourly_collector_stamps_bars_at_close_and_drops_the_open_bar(monkeypatch):
    import pandas as pd

    from app.collectors import world
    from app.collectors.base import utcnow

    now = pd.Timestamp(utcnow()).floor("h")
    frame = pd.DataFrame(
        {"Close": [4000.0, 4010.0, 4020.0]},
        index=pd.DatetimeIndex([now - pd.to_timedelta(2, unit="h"), now - pd.to_timedelta(1, unit="h"), now], tz="UTC"))
    monkeypatch.setattr(world, "_history", lambda period, interval: ("GC=F", frame))
    quotes = world.fetch_hourly()
    assert [q.symbol for q in quotes] == [config.XAUUSD_HOURLY] * 2  # nến đang chạy bị bỏ
    assert [q.ts for q in quotes] == [(now - pd.to_timedelta(1, unit="h")).to_pydatetime(), now.to_pydatetime()]
    assert [q.price for q in quotes] == [4000.0, 4010.0]
