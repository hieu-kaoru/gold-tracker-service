import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import config
from app.collectors.base import Quote, utcnow
from app.collectors.news import parse_feed
from app.main import app
from app.services import news as news_service
from app.services import prices


@pytest.fixture
def client(session):
    with TestClient(app) as test_client:
        yield test_client


def seed_prices(session):
    now = utcnow()
    quotes = [
        Quote(config.SJC_RING, 142_500_000, "VND", "VND/luong", "test", buy=139_500_000, sell=142_500_000, ts=now - timedelta(days=1)),
        Quote(config.SJC_RING, 144_300_000, "VND", "VND/luong", "test", buy=141_300_000, sell=144_300_000, ts=now),
        Quote(config.USDVND, 26_430, "VND", "VND/USD", "test", buy=26_100, sell=26_430, ts=now),
    ]
    quotes += [
        Quote(config.XAUUSD, 4000 + i * 3, "USD", "USD/oz", "test", ts=now - timedelta(days=60 - i))
        for i in range(61)
    ]
    prices.save_quotes(session, quotes)


def test_empty_database(client):
    assert client.get("/prices/latest").json() == {"items": []}
    assert client.get("/spread").status_code == 503
    body = client.get("/forecast").json()
    assert body["direction"] is None and "tham khảo" in body["disclaimer"]


def test_latest_prices(client, session):
    seed_prices(session)
    (ring,) = client.get("/prices/latest").json()["items"]  # dữ liệu tham chiếu không lộ ra
    assert (ring["symbol"], ring["buy"], ring["sell"]) == ("SJC_RING", 141_300_000, 144_300_000)
    assert ring["change"] == 1_800_000 and ring["ts"].endswith("Z")


def test_save_quotes_is_idempotent(session):
    quote = Quote(config.XAUUSD, 4200, "USD", "USD/oz", "test")
    assert prices.save_quotes(session, [quote]) == 1
    assert prices.save_quotes(session, [quote]) == 0


def test_history(client, session):
    seed_prices(session)
    body = client.get("/prices/history", params={"days": 10}).json()
    assert body["symbol"] == "SJC_RING"
    assert [p["price"] for p in body["points"]] == [142_500_000, 144_300_000]
    for other in ("XAUUSD", "SJC_BAR"):
        assert client.get("/prices/history", params={"symbol": other}).status_code == 422


def test_spread(client, session):
    seed_prices(session)
    body = client.get("/spread").json()
    world = 4180 * 26_430 * 37.5 / 31.1034768
    assert body["world_vnd_per_luong"] == pytest.approx(world, abs=1)
    assert body["ring_sell"] == 144_300_000
    assert body["spread"] == pytest.approx(144_300_000 - world, abs=1)


def test_forecast_falls_back_to_world_trend(client, session):
    seed_prices(session)
    body = client.get("/forecast").json()
    assert (body["symbol"], body["basis"]) == ("SJC_RING", "XAUUSD") and body["note"]
    assert body["direction"] == "up"


def test_forecast_explains_its_basis(client, session, fixture_text):
    seed_prices(session)
    body = client.get("/forecast").json()
    # Lời giải thích cho người không chuyên: không có tên chỉ báo; số liệu kỹ thuật nằm riêng
    assert not any(term in " ".join(body["reasons"]) for term in ("MA10", "MACD", "RSI"))
    assert "cao hơn mức trung bình 30 ngày" in body["reasons"][0] and "4.1" in body["reasons"][0]
    assert [d.split(" ")[0] for d in body["technical_details"][:3]] == ["MA10", "MACD", "RSI14"]
    assert body["model"] == "rules"  # chưa đủ dữ liệu nhẫn để huấn luyện máy học
    assert body["summary"].startswith("Giá vàng nhẫn") and body["confidence_label"] in {"thấp", "trung bình", "khá"}
    assert body["news_used"] is False and "chưa tính vào dự báo" in body["reasons"][-1]


def recent_news(session, impacts):
    """Lưu các tin tác động vừa đăng với chiều cho trước, ví dụ ["down", "down", "up"]."""
    from app.models import NewsItem

    for index, impact in enumerate(impacts):
        session.add(NewsItem(
            url=f"https://example.com/{uuid.uuid4()}", source="test", lang="vi", title=f"Tin {impact} {index}",
            summary="", published_at=utcnow(), drivers="geopolitics", impact=impact,
            impact_level="high" if index == 1 else "medium", impact_reason="x",
        ))
    session.commit()


def test_forecast_uses_recent_news(client, session):
    seed_prices(session)
    before = client.get("/forecast").json()

    recent_news(session, ["unclear", "down"])  # tin "chưa rõ chiều" không tính; 1 tin rõ chiều là chưa đủ
    assert client.get("/forecast").json()["news_used"] is False

    recent_news(session, ["down"] * 4)
    after = client.get("/forecast").json()
    assert after["news_used"] is True and after["votes"]["news"] == -1
    # Tin tức chiếm 20% điểm: kéo điểm xuống nhưng biểu đồ giá vẫn quyết định hướng
    assert after["score"] == pytest.approx(0.8 * before["score"] - 0.2, abs=0.002)
    assert after["direction"] == "up" and "chưa thống nhất" in after["summary"]
    assert "5 tin gây áp lực giảm" in after["reasons"][-1] and "“Tin down" in after["reasons"][-1]


def test_forecast_horizon(client, session):
    seed_prices(session)
    week = client.get("/forecast", params={"horizon": 7}).json()
    assert week["horizon_days"] == 7 and week["direction"] == "up"
    assert "trung bình 50 ngày" in week["reasons"][0] and "7 ngày tới" in week["summary"]
    assert week["technical_details"][0].startswith("MA20") and "ma50" in week["indicators"]
    assert client.get("/forecast", params={"horizon": 3}).status_code == 422


def test_news(client, session, fixture_text):
    articles = parse_feed(fixture_text("feed.xml"), "VnExpress", "vi")
    assert news_service.save_articles(session, articles) == 5
    assert news_service.save_articles(session, articles) == 0
    # Mặc định chỉ trả tin là tác nhân biến động giá, bỏ bài tường thuật giá và tin không liên quan
    items = client.get("/news").json()["items"]
    assert [(n["drivers"], n["impact"]) for n in items] == [
        (["us_policy"], "up"), (["geopolitics"], "down"), (["fed"], "unclear")]
    assert items[0]["driver_labels"] == ["Nhà Trắng & thương mại"] and items[0]["impact_reason"]
    assert [n["title"] for n in client.get("/news", params={"driver": "fed"}).json()["items"]] == [
        "Fed họp chính sách tuần này"]
    assert {n["region"] for n in items} == {"world"}
    assert client.get("/news", params={"region": "domestic"}).json()["items"] == []
    assert len(client.get("/news", params={"region": "world"}).json()["items"]) == 3
    assert len(client.get("/news", params={"kind": "gold"}).json()["items"]) == 1
    assert len(client.get("/news", params={"kind": "all"}).json()["items"]) == 5


def test_reclassify_fills_impact_for_stored_news(session, fixture_text):
    from sqlalchemy import select, update

    from app.models import NewsItem

    news_service.save_articles(session, parse_feed(fixture_text("feed.xml"), "VnExpress", "vi"))
    session.execute(update(NewsItem).values(drivers="", impact="", impact_level="", impact_reason=""))  # như tin lưu từ bản cũ
    session.commit()
    assert news_service.reclassify_all(session) == 3
    assert session.scalar(select(NewsItem.impact).where(NewsItem.url.like("%nha-trang"))) == "up"
    assert news_service.reclassify_all(session) == 0


def seed_ring_days(session):
    from datetime import date

    from app.collectors.ring_history import close_ts

    def ring(day, buy, sell, ts=None):
        return Quote(config.SJC_RING, sell, "VND", "VND/luong", "test", buy=buy, sell=sell, ts=ts or close_ts(day))

    prices.save_quotes(session, [
        ring(date(2026, 3, 2), 100_000_000, 102_000_000),
        # 03/03 không có dữ liệu; 04/03 có hai lần cập nhật trong ngày
        ring(date(2026, 3, 4), 100_500_000, 102_500_000, ts=close_ts(date(2026, 3, 4)) - timedelta(hours=10)),
        ring(date(2026, 3, 4), 101_000_000, 103_000_000),
    ])


def test_price_of_a_day(client, session):
    seed_ring_days(session)
    body = client.get("/prices/day", params={"date": "2026-03-04"}).json()
    assert (body["buy"], body["sell"]) == (101_000_000, 103_000_000)  # giá chốt = lần cập nhật cuối
    assert len(body["updates"]) == 2
    # So với ngày có giá liền trước (02/03), không phải ngày lịch liền trước
    assert (body["change"], body["previous_date"]) == (1_000_000, "2026-03-02")
    first = client.get("/prices/day", params={"date": "2026-03-02"}).json()
    assert first["change"] is None and first["previous_date"] is None


def test_price_of_a_day_without_data(client, session, monkeypatch):
    from app.collectors import ring_history

    seed_ring_days(session)
    gap = client.get("/prices/day", params={"date": "2026-03-03"})
    assert gap.status_code == 404 and "03/03/2026" in gap.json()["detail"] and "02/03/2026" in gap.json()["detail"]
    assert client.get("/prices/day", params={"date": "2999-01-01"}).status_code == 422
    assert client.get("/prices/day", params={"date": "hôm qua"}).status_code == 422

    # Ngày cũ hơn mọi dữ liệu: tra nguồn lịch sử, có thì lưu lại, không có thì 404
    monkeypatch.setattr(ring_history, "fetch_day", lambda day: None)
    assert client.get("/prices/day", params={"date": "2024-06-03"}).status_code == 404
    found = Quote(config.SJC_RING, 75_000_000, "VND", "VND/luong", "giavang.org",
                  buy=73_400_000, sell=75_000_000, ts=ring_history.close_ts(date_of("2024-06-03")))
    found.source = ring_history.PAGE_SOURCE
    monkeypatch.setattr(ring_history, "fetch_day", lambda day: found)
    assert client.get("/prices/day", params={"date": "2024-06-03"}).json()["sell"] == 75_000_000
    monkeypatch.setattr(ring_history, "fetch_day", lambda day: pytest.fail("đã lưu thì không tra lại"))
    assert client.get("/prices/day", params={"date": "2024-06-03"}).json()["source"] == "giavang.org/lich-su"
    # Ngày lẻ vừa lưu không được kéo mốc "dữ liệu liên tục" về 2024
    monkeypatch.setattr(ring_history, "fetch_day", lambda day: None)
    assert "02/03/2026" in client.get("/prices/day", params={"date": "2025-01-01"}).json()["detail"]


def date_of(text):
    from datetime import date

    return date.fromisoformat(text)


def test_ring_history_backfill_is_off_by_default(monkeypatch):
    from app import scheduler
    from app.collectors import ring_history

    assert config.RING_HISTORY_BACKFILL is False
    monkeypatch.setattr(ring_history, "fetch_daily", lambda today: pytest.fail("không được gọi vang.today"))
    scheduler.backfill_ring_history()
    assert scheduler.status["ring_history"]["skipped"]


def test_forecast_history_endpoint(client, session):
    from app.collectors.ring_history import close_ts
    from app.services import tracking

    today = prices.today_vn()
    prices.save_quotes(session, [
        Quote(config.SJC_RING, 100e6 + i * 2e5, "VND", "VND/luong", "test", buy=97e6, sell=100e6 + i * 2e5,
              ts=close_ts(today - timedelta(days=60 - i)))
        for i in range(60)
    ] + [Quote(config.SJC_RING, 112.2e6, "VND", "VND/luong", "test", buy=109e6, sell=112.2e6)])
    assert tracking.backfill(session) > 0
    assert tracking.record_today(session) == 2

    body = client.get("/forecast/history", params={"days": 10}).json()
    assert len(body["rows"]) == 10
    today_row, yesterday = body["rows"][0], body["rows"][1]
    assert today_row["date"] == today.isoformat() and today_row["h1"]["source"] == "live"
    assert today_row["h1"]["actual"] is None  # hôm nay chưa có kết quả
    assert yesterday["h1"]["source"] == "backfill" and yesterday["h1"]["actual"]["correct"] is True
    assert body["accuracy"]["h1"]["rate"] == 1.0 and body["accuracy"]["h1"]["baseline_direction"] == "up"
    # 60 ngày chưa đủ để huấn luyện máy học → đang dùng công thức dự phòng và nói rõ điều đó
    assert body["model"] == {"h1": None, "h7": None} and today_row["h1"]["model"] == "rules"


def test_isolated_lookup_days_stay_out_of_history_and_training(client, session):
    from app.collectors import ring_history
    from app.services import learning

    today = prices.today_vn()
    recent = [Quote(config.SJC_RING, 100e6 + i, "VND", "VND/luong", "test", buy=97e6, sell=100e6 + i,
                    ts=ring_history.close_ts(today - timedelta(days=5 - i))) for i in range(5)]
    lookup = Quote(config.SJC_RING, 75e6, "VND", "VND/luong", ring_history.PAGE_SOURCE, buy=73e6, sell=75e6,
                   ts=ring_history.close_ts(today - timedelta(days=300)))
    prices.save_quotes(session, recent + [lookup])

    points = client.get("/prices/history", params={"days": 730}).json()["points"]
    assert len(points) == 5 and all(p["price"] >= 100e6 for p in points)
    assert min(learning.closes_by_day(session)) == today - timedelta(days=5)
    # Ngày lẻ vẫn tra được riêng, và ngày kế sau nó nhiều tháng không lấy nó làm "ngày trước"
    old = client.get("/prices/day", params={"date": (today - timedelta(days=300)).isoformat()}).json()
    assert old["sell"] == 75e6
    first = client.get("/prices/day", params={"date": (today - timedelta(days=5)).isoformat()}).json()
    assert first["change"] is None and first["previous_date"] is None


def test_new_columns_are_added_to_an_existing_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app import db

    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as connection:  # bảng tin tức kiểu cũ, chưa có các cột tác động
        connection.execute(text("CREATE TABLE news_items (id INTEGER PRIMARY KEY, url VARCHAR(1024))"))
        connection.execute(text("INSERT INTO news_items (id, url) VALUES (1, 'https://example.com/a')"))
    original = db.engine
    db.engine = engine
    try:
        db.init_db()
    finally:
        db.engine = original
    columns = {column["name"] for column in inspect(engine).get_columns("news_items")}
    assert {"drivers", "impact", "impact_level", "impact_reason"} <= columns
    with engine.connect() as connection:  # dòng cũ nhận giá trị mặc định, không mất dữ liệu
        assert connection.execute(text("SELECT url, drivers FROM news_items")).one() == ("https://example.com/a", "")
