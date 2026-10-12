import math

import pandas as pd
import pytest

from app.services import forecast, indicators, sentiment


def test_sentiment_longest_phrase_wins():
    # "tăng lãi suất" là tin xấu cho vàng, không được tính chữ "tăng" là tích cực
    assert sentiment.analyze("Fed tăng lãi suất", "vi") == (-1.0, "negative")
    assert sentiment.analyze("Fed giảm lãi suất", "vi") == (1.0, "positive")
    assert sentiment.analyze("Gold rallies on rate cut hopes", "en")[1] == "positive"
    assert sentiment.analyze("Thị trường đi ngang", "vi") == (0.0, "neutral")


def test_gold_keyword_needs_word_boundary():
    assert sentiment.is_gold_related("Giá vàng hôm nay")
    assert not sentiment.is_gold_related("Golden Gate khai trương")


def test_rsi_extremes_and_range():
    rising = pd.Series(range(1, 40), dtype="float64")
    assert indicators.rsi(rising).iloc[-1] == 100
    assert indicators.rsi(rising[::-1].reset_index(drop=True)).iloc[-1] == pytest.approx(0)
    wavy = pd.Series([100 + 5 * math.sin(i / 3) for i in range(60)])
    assert 0 < indicators.rsi(wavy).iloc[-1] < 100


def test_macd_sign_follows_trend():
    line, _, _ = indicators.macd(pd.Series([100 + i for i in range(60)], dtype="float64"))
    assert line.iloc[-1] > 0


def test_forecast_direction():
    # Xu hướng tăng có nhịp điều chỉnh để RSI không chạm vùng quá mua
    up = [100 + i * 0.5 + 3 * math.sin(i) for i in range(80)]
    down = [200 - i * 0.5 + 3 * math.sin(i) for i in range(80)]
    result_up, result_down = forecast.compute(up), forecast.compute(down)
    assert result_up["direction"] == "up" and result_down["direction"] == "down"
    assert 0.5 <= result_up["confidence"] <= 0.75
    assert forecast.compute([100.0] * 20 + [100 + (i % 2) * 0.01 for i in range(40)])["direction"] == "sideways"


def test_forecast_needs_enough_data():
    with pytest.raises(ValueError):
        forecast.compute([1.0] * 10)


def test_forecast_reasons_use_millions_for_vnd():
    closes = [140_000_000 + i * 100_000 + 300_000 * math.sin(i) for i in range(80)]
    reasons = forecast.compute(closes)["reasons"]
    assert "triệu" in reasons[0] and "000.000" not in reasons[0]


def test_driver_analysis():
    from app.services import drivers

    assert drivers.analyze("Giá vàng hôm nay tăng 500.000 đồng mỗi lượng") is None
    hike = drivers.analyze("Fed phát tín hiệu tăng lãi suất thêm một lần nữa")
    assert (hike.drivers, hike.direction) == (["fed"], "down")
    english = drivers.analyze("Gold jumps as Middle East tensions escalate and Fed rate cut bets grow")
    assert english.direction == "up" and set(english.drivers) == {"geopolitics", "fed"}
    # Từ khoá chung như "Ngân hàng Nhà nước" chỉ tính khi bài nói về vàng
    assert drivers.analyze("Ngân hàng Nhà nước điều chỉnh room tín dụng") is None
    policy = drivers.analyze("Ngân hàng Nhà nước cấp phép nhập khẩu vàng cho doanh nghiệp")
    assert (policy.drivers, policy.direction) == (["vn_policy"], "down")


def test_other_instrument_commentary_is_not_a_gold_driver():
    from app.services import drivers

    body = "The pair slides as hawkish Fed and Middle East conflict weigh."
    assert drivers.analyze(body, "GBP/USD Price Forecast: Edges higher near 1.3250") is None
    assert drivers.analyze(body, "WTI falls to near $90.00 on ceasefire hopes") is None
    assert drivers.analyze(body, "Gold Price Forecast: XAU/USD slides on hawkish Fed") is not None
    assert drivers.analyze(body, "Fed's Powell signals more hikes") is not None


def test_domestic_market_analysis_and_price_reports():
    from app.services import drivers

    # Bảng giá trong ngày bị loại dù có nêu nguyên nhân
    assert drivers.analyze("Vàng tăng do căng thẳng Trung Đông.", "Giá vàng hôm nay 12/10: nhẫn SJC tăng") is None
    title = "Vì sao vàng nhẫn SJC khan hiếm?"
    market = drivers.analyze(f"{title}. Chuyên gia cho rằng nguồn cung hạn chế khiến người dân xếp hàng mua.", title)
    assert market.drivers == ["vn_market"] and market.direction == "up"
    assert drivers.region(market.drivers) == "domestic" and drivers.region(["fed"]) == "world"
    # Nhận định về vàng thế giới (không nhắc thị trường trong nước) không tính là tin trong nước
    assert drivers.analyze("Chuyên gia dự báo giá vàng thế giới tăng", "Vàng thế giới sẽ ra sao?") is None


def test_generic_attack_words_do_not_make_geopolitics():
    from app.services import drivers

    assert drivers.analyze("Tin tặc tấn công hệ thống email của doanh nghiệp") is None
    assert drivers.analyze("Houthi tấn công sân bay, xung đột Trung Đông leo thang").direction == "up"
