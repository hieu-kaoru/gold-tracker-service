from datetime import datetime

import pytest

from app import config
from app.collectors import news, sjc, vcb
from app.collectors.base import CollectorError


def test_sjc_mirror_parses_only_ring(fixture_text):
    (ring,) = sjc.parse_mirror(fixture_text("giavang_sjc.html"))
    assert (ring.symbol, ring.buy, ring.sell) == (config.SJC_RING, 141_300_000, 144_300_000)
    assert ring.price == ring.sell and ring.unit == "VND/luong"


def test_sjc_official_payload():
    payload = {"success": True, "data": [
        {"TypeName": "Vàng SJC 1L, 10L, 1KG", "BranchName": "Hồ Chí Minh", "BuyValue": 141800000, "SellValue": 144800000},
        {"TypeName": "Vàng nhẫn SJC 99,99% 1 chỉ, 2 chỉ, 5 chỉ", "BuyValue": 141300000, "SellValue": 144300000},
        {"TypeName": "Nữ trang 99%", "BuyValue": 134881000, "SellValue": 141881000},
    ]}
    assert [(q.symbol, q.sell) for q in sjc.parse_official(payload)] == [(config.SJC_RING, 144300000)]


def test_baomoi_table_parses_ring_in_vnd_per_luong(fixture_text):
    (ring,) = sjc.parse_baomoi(fixture_text("baomoi_gold.html"))
    assert (ring.symbol, ring.source, ring.unit) == (config.SJC_RING, "baomoi.com", "VND/luong")
    assert ring.sell - ring.buy == 3_000_000 and 100e6 < ring.buy < 200e6
    assert sjc.parse_baomoi("<table><tr><td>SJC 1L, 10L, 1KG</td><td>1</td><td>2</td></tr></table>") == []


def test_sjc_uses_next_source_when_earlier_ones_have_no_ring(monkeypatch, fixture_text):
    monkeypatch.setattr(sjc, "SOURCES", (
        ("sjc.com.vn", lambda: (_ for _ in ()).throw(CollectorError("Cloudflare"))),
        ("giavang.org", lambda: []),  # trang đổi giao diện, không còn dòng nhẫn
        ("baomoi.com", lambda: sjc.parse_baomoi(fixture_text("baomoi_gold.html"))),
    ))
    assert [q.source for q in sjc.fetch()] == ["baomoi.com"]


def test_sjc_rejects_price_in_wrong_unit():
    html = "<table><tr><td>Vàng nhẫn SJC 99,99% 1 chỉ, 2 chỉ, 5 chỉ</td><td>14,13</td><td>14,43</td></tr></table>"
    with pytest.raises(CollectorError):
        sjc.parse_mirror(html)


def test_sjc_fetch_falls_back_when_first_source_blocked(monkeypatch, fixture_text):
    def blocked():
        raise CollectorError("Cloudflare")

    monkeypatch.setattr(sjc, "SOURCES", (
        ("sjc.com.vn", blocked),
        ("giavang.org", lambda: sjc.parse_mirror(fixture_text("giavang_sjc.html"))),
    ))
    assert {q.source for q in sjc.fetch()} == {"giavang.org"}

    monkeypatch.setattr(sjc, "SOURCES", (("sjc.com.vn", blocked),))
    with pytest.raises(CollectorError):
        sjc.fetch()


def test_vcb_parses_usd(fixture_text):
    quote = vcb.parse(fixture_text("vcb.xml"))
    assert (quote.symbol, quote.buy, quote.sell, quote.price) == (config.USDVND, 26100.0, 26430.0, 26430.0)


def test_vcb_without_usd_raises():
    with pytest.raises(CollectorError):
        vcb.parse("<ExrateList><Exrate CurrencyCode='EUR' Buy='1' Transfer='1' Sell='1'/></ExrateList>")


def test_news_feed_parsing_sentiment_and_relevance(fixture_text):
    report, tariff, truce, fed, other = news.parse_feed(fixture_text("feed.xml"), "VnExpress", "vi")
    assert report.published_at == datetime(2026, 10, 12, 2, 15, 56)  # 09:15 +07 → UTC
    assert "<" not in report.summary
    assert (report.gold_related, report.sentiment_label) == (True, "positive")
    # Bài chỉ tường thuật giá: nhắc tới vàng nhưng không phải tác nhân
    assert (report.drivers, report.impact) == ("", "")
    # Tác nhân không cần nhắc tới vàng
    assert (tariff.gold_related, tariff.drivers, tariff.impact) == (False, "us_policy", "up")
    assert (tariff.impact_level, fed.impact_level) == ("high", "low")
    assert (truce.drivers, truce.impact) == ("geopolitics", "down") and "hạ nhiệt" in truce.impact_reason
    assert (fed.drivers, fed.impact) == ("fed", "unclear")
    assert (other.drivers, other.sentiment_label) == ("", "neutral")


def test_ring_history_api_skips_today_and_bad_rows(fixture_text):
    import json
    from datetime import date

    from app.collectors import ring_history

    payload = json.loads(fixture_text("vangtoday_ring.json"))
    payload["history"].append({"date": "2026-10-01", "prices": {"SJ9999": {"buy": 0, "sell": 0}}})
    payload["history"].append({"date": "2026-09-30", "prices": {}})
    quotes = ring_history.parse_api(payload, today=date(2026, 10, 12))
    assert [q.ts for q in quotes] == [datetime(2026, 10, d, 16, 59) for d in (11, 10, 9)]  # 23:59 giờ VN
    assert (quotes[0].symbol, quotes[0].buy, quotes[0].sell) == (config.SJC_RING, 141_300_000, 144_300_000)


def test_ring_history_page(fixture_text):
    from datetime import date

    from app.collectors import ring_history

    quote = ring_history.parse_page(fixture_text("giavang_history_2024-06-03.html"), date(2024, 6, 3))
    assert (quote.buy, quote.sell, quote.ts) == (73_400_000, 75_000_000, datetime(2024, 6, 3, 16, 59))
    assert ring_history.parse_page(fixture_text("giavang_history_empty.html"), date(2026, 10, 11)) is None
