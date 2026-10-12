"""Dự báo hướng giá vàng nhẫn. Chỉ để tham khảo.

Đường chính: mô hình máy học (services/ml.py) + điều chỉnh nhỏ theo tin tức 24 giờ.
Dự phòng khi chưa đủ dữ liệu huấn luyện: công thức cộng điểm MA/RSI/MACD (`compute`).
"""
import threading
from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..collectors.base import utcnow
from ..models import NewsItem
from . import indicators, learning, ml, prices

DISCLAIMER = "Chỉ để tham khảo, không phải khuyến nghị đầu tư."
# Khung dự báo (ngày) → cặp MA ngắn/dài: khung dài hơn dùng MA chậm hơn để bớt nhiễu
HORIZONS = {1: (10, 30), 7: (20, 50)}
MACD_WARMUP = 35  # MACD(26, 9) cần chừng này phiên để ổn định tối thiểu
WEIGHTS = {"ma": 0.4, "macd": 0.35, "rsi": 0.25}
NEUTRAL_BAND = 0.15


def _vi(value: float, digits: int = 2) -> str:
    """Định dạng số kiểu Việt Nam: 4.180,84. Giá VND (hàng trăm nghìn trở lên) đổi sang triệu: 143,37 triệu."""
    if abs(value) >= 100_000:
        return _vi(value / 1_000_000, digits) + " triệu"
    return f"{value:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def min_points(horizon: int) -> int:
    return max(MACD_WARMUP, HORIZONS[horizon][1] + 5)


NEWS_WEIGHT = 0.2      # phần của tin tức trong điểm dự báo; biểu đồ giá vẫn là chính
NEWS_MIN_SIGNALS = 3   # ít hơn chừng này tin có chiều rõ thì chưa đủ căn cứ để tính
NEWS_HOURS = 24


def _technical_details(ma: tuple[int, int], ma_short: float, ma_long: float, rsi: float, hist: float) -> list[str]:
    """Số liệu chỉ báo cho người rành phân tích kỹ thuật (app để trong mục thu gọn)."""
    above = ma_short > ma_long
    details = [
        f"MA{ma[0]} ({_vi(ma_short)}) nằm {'trên' if above else 'dưới'} MA{ma[1]} ({_vi(ma_long)}) "
        f"→ xu hướng {'ngắn' if ma[1] <= 30 else 'trung'} hạn {'tăng' if above else 'giảm'}",
        f"MACD histogram {'dương' if hist > 0 else 'âm'} ({_vi(hist)}) → đà {'tăng' if hist > 0 else 'giảm'}",
    ]
    if rsi >= 70:
        details.append(f"RSI14 = {_vi(rsi, 1)} (quá mua) → dễ điều chỉnh giảm")
    elif rsi <= 30:
        details.append(f"RSI14 = {_vi(rsi, 1)} (quá bán) → dễ hồi phục")
    else:
        lean = "nghiêng tăng" if rsi > 50 else "nghiêng giảm" if rsi < 50 else "trung tính"
        details.append(f"RSI14 = {_vi(rsi, 1)} (vùng trung tính) → {lean}")
    return details


def _plain_reasons(ma: tuple[int, int], ma_short: float, ma_long: float, rsi: float, hist: float) -> list[str]:
    """Cùng nội dung với _technical_details nhưng bằng lời thường, không dùng tên chỉ báo."""
    if ma_short > ma_long:
        trend = (f"Giá trung bình {ma[0]} ngày gần đây ({_vi(ma_short)}) cao hơn mức trung bình {ma[1]} ngày "
                 f"({_vi(ma_long)}) → giá đang có đà đi lên.")
    else:
        trend = (f"Giá trung bình {ma[0]} ngày gần đây ({_vi(ma_short)}) thấp hơn mức trung bình {ma[1]} ngày "
                 f"({_vi(ma_long)}) → giá đang yếu đi.")
    momentum = (
        "Sức mua đang mạnh dần lên so với vài tuần trước → nghiêng về tăng." if hist > 0
        else "Sức mua đang yếu dần so với vài tuần trước → nghiêng về giảm."
    )
    if rsi >= 70:
        heat = "Giá đã tăng nóng trong hai tuần qua → dễ có nhịp chững lại hoặc giảm."
    elif rsi <= 30:
        heat = "Giá đã giảm sâu trong hai tuần qua → dễ có nhịp hồi phục."
    elif rsi > 50:
        heat = "Hai tuần qua lực tăng nhỉnh hơn lực giảm → hơi nghiêng về tăng."
    elif rsi < 50:
        heat = "Hai tuần qua lực giảm nhỉnh hơn lực tăng → hơi nghiêng về giảm."
    else:
        heat = "Hai tuần qua lực tăng và lực giảm cân bằng."
    return [trend, momentum, heat]


def news_signal(session: Session, hours: int = NEWS_HOURS) -> dict:
    """Tổng hợp các tin tác động có chiều rõ trong `hours` giờ gần nhất (bỏ tin "chưa rõ chiều")."""
    rows = session.scalars(
        select(NewsItem)
        .where(
            NewsItem.drivers != "", NewsItem.impact.in_(("up", "down")),
            NewsItem.published_at >= utcnow() - timedelta(hours=hours),
        )
        .order_by(NewsItem.published_at.desc())
    ).all()
    up = [n for n in rows if n.impact == "up"]
    down = [n for n in rows if n.impact == "down"]
    used = len(rows) >= NEWS_MIN_SIGNALS
    vote = (len(up) - len(down)) / len(rows) if rows else 0.0
    # Nêu tên tin tiêu biểu của phía đang chiếm ưu thế, ưu tiên tin có tín hiệu rõ
    leading = up if vote > 0 else down if vote < 0 else []
    leading = sorted(leading, key=lambda n: n.impact_level != "high")
    return {
        "up": len(up), "down": len(down), "used": used, "vote": round(vote, 2) if used else 0.0,
        "highlights": [n.title for n in leading[:2]] if used else [],
    }


def _news_reason(signal: dict, hours: int = NEWS_HOURS) -> str:
    counts = f"{signal['up']} tin hỗ trợ giá tăng, {signal['down']} tin gây áp lực giảm"
    if not signal["used"]:
        return f"Tin tức {hours} giờ qua chưa đủ tín hiệu rõ ({counts}) nên chưa tính vào dự báo."
    lean = "nghiêng về tăng" if signal["vote"] > 0 else "nghiêng về giảm" if signal["vote"] < 0 else "hai phía cân bằng"
    text = f"Tin tức {hours} giờ qua: {counts} → {lean}."
    if signal["highlights"]:
        text += " Ví dụ: " + "; ".join(f"“{title}”" for title in signal["highlights"]) + "."
    return text + " (Máy tự chấm theo từ khoá nên có thể sai.)"


def _summary(direction: str, horizon: int, agree: int, total: int) -> str:
    when = "trong ngày tới" if horizon == 1 else f"trong {horizon} ngày tới"
    if direction == "sideways":
        return f"Giá vàng nhẫn nhiều khả năng đi ngang {when}, vì các dấu hiệu đang trái chiều nhau."
    verb = "tăng" if direction == "up" else "giảm"
    if agree == total:
        return f"Giá vàng nhẫn có khả năng {verb} {when}, các dấu hiệu đều cùng chiều."
    return f"Giá vàng nhẫn có thể {verb} nhẹ {when}, nhưng các dấu hiệu chưa thống nhất ({agree}/{total} cùng chiều)."


def confidence_label(confidence: float) -> str:
    return "thấp" if confidence < 0.58 else "trung bình" if confidence < 0.67 else "khá"


def compute(closes: list[float], horizon: int = 1, news: dict | None = None) -> dict:
    """Trả về hướng (up/down/sideways), độ tin cậy và lời giải thích từ chuỗi đóng cửa theo ngày.

    `news` là kết quả `news_signal`; nếu đủ tín hiệu thì chiếm NEWS_WEIGHT trong điểm.
    Đây là công thức cộng điểm cũ, chỉ còn dùng khi chưa đủ dữ liệu để huấn luyện mô hình máy học.
    """
    needed = min_points(horizon)
    if len(closes) < needed:
        raise ValueError(f"Cần ít nhất {needed} phiên, hiện có {len(closes)}")
    short_window, long_window = HORIZONS[horizon]
    series = pd.Series(closes, dtype="float64")
    ma_short = indicators.sma(series, short_window).iloc[-1]
    ma_long = indicators.sma(series, long_window).iloc[-1]
    rsi = indicators.rsi(series).iloc[-1]
    macd_line, signal_line, hist = (s.iloc[-1] for s in indicators.macd(series))

    votes = {
        "ma": 1.0 if ma_short > ma_long else -1.0,
        "macd": 1.0 if hist > 0 else -1.0,
        # Quá mua/quá bán báo đảo chiều; ở giữa thì theo đà hiện tại nhưng yếu hơn
        "rsi": -1.0 if rsi >= 70 else 1.0 if rsi <= 30 else (rsi - 50) / 20,
    }
    score = sum(WEIGHTS[name] * vote for name, vote in votes.items())
    reasons = _plain_reasons((short_window, long_window), ma_short, ma_long, rsi, hist)
    news_used = bool(news and news["used"])
    if news is not None:
        reasons.append(_news_reason(news))
    if news_used:
        votes["news"] = news["vote"]
        score = (1 - NEWS_WEIGHT) * score + NEWS_WEIGHT * news["vote"]
    direction = "up" if score > NEUTRAL_BAND else "down" if score < -NEUTRAL_BAND else "sideways"
    sign = 1 if score > 0 else -1
    agree = sum(1 for vote in votes.values() if vote * sign > 0)
    # Chỉ báo kỹ thuật không đáng tin hơn ~75% kể cả khi đồng thuận tuyệt đối
    confidence = round(0.5 + 0.25 * abs(score), 2)
    return {
        "model": "rules", "probabilities": {},
        "direction": direction, "confidence": confidence, "confidence_label": confidence_label(confidence),
        "score": round(score, 3), "summary": _summary(direction, horizon, agree, len(votes)),
        "reasons": reasons, "news_used": news_used,
        "technical_details": _technical_details((short_window, long_window), ma_short, ma_long, rsi, hist),
        "indicators": {
            f"ma{short_window}": round(ma_short, 2), f"ma{long_window}": round(ma_long, 2), "rsi14": round(rsi, 2),
            "macd": round(macd_line, 3), "macd_signal": round(signal_line, 3), "macd_hist": round(hist, 3),
        },
        "votes": {name: round(vote, 2) for name, vote in votes.items()},
    }


# ---- Mô hình máy học ----

MODEL_NAMES = {
    "logistic": "hồi quy logistic", "random_forest": "rừng ngẫu nhiên", "gradient_boosting": "tăng cường gradient",
}
DIRECTION_VI = {"up": "tăng", "down": "giảm", "sideways": "đi ngang"}
_ML_CACHE: dict[int, tuple[tuple, ml.Trained | None]] = {}
_ML_LOCK = threading.Lock()


def ml_state(session: Session, horizon: int) -> ml.Trained | None:
    """Mô hình đã huấn luyện cho khung `horizon`; huấn luyện lại khi có thêm ngày dữ liệu mới.

    None nếu chưa đủ dữ liệu. Huấn luyện mất chừng chục giây nên kết quả được giữ trong bộ nhớ.
    """
    ring = learning.closes_by_day(session)
    hourly = learning.world_hourly(session)
    # Có/không có dữ liệu theo giờ cũng là một phần của khoá: nạp xong lịch sử theo giờ thì phải học lại
    key = (max(ring) if ring else None, len(ring), hourly.empty)
    with _ML_LOCK:
        cached = _ML_CACHE.get(horizon)
        if cached and cached[0] == key:
            return cached[1]
        xau = learning.closes_by_day(session, config.XAUUSD)
        trained = ml.train(ring, xau, horizon, learning.FLAT_PCT[horizon], hourly)
        _ML_CACHE[horizon] = (key, trained)
        return trained


def model_info(session: Session, horizon: int) -> dict | None:
    """Tóm tắt mô hình đang dùng và kết quả kiểm tra lùi, cho màn hình Tổng quan."""
    trained = ml_state(session, horizon)
    if trained is None:
        return None
    return {
        "name": trained.name, "label": MODEL_NAMES[trained.name], "train_rows": trained.train_rows,
        "last_day": trained.last_day, "test": trained.stats["model"], "baseline": trained.stats["baseline"],
        "baseline_direction": trained.baseline_direction, "holdout_days": trained.stats["holdout_days"],
        "replay": trained.stats["replay"], "candidates": trained.stats["candidates"],
        "top_features": [{"name": name, "label": ml.FEATURES[name], "share": share}
                         for name, share in trained.importance[:5]],
    }


def ml_confidence_label(probability: float) -> str:
    # Với 3 hướng, đoán mò đã là ~33%, nên thang đánh giá thấp hơn thang của công thức cũ
    return "thấp" if probability < 0.45 else "trung bình" if probability < 0.6 else "khá"


def _percent(value: float) -> str:
    return f"{abs(value):.2f}".replace(".", ",") + "%"


def _rate(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + "%"


def _ml_reasons(features: dict, probabilities: dict, trained: ml.Trained, horizon: int) -> list[str]:
    """Các dữ kiện chính mô hình đang nhìn vào, viết bằng lời thường."""
    order = sorted(probabilities, key=probabilities.get, reverse=True)
    reasons = ["Máy ước tính: " + ", ".join(f"{DIRECTION_VI[name]} {probabilities[name]:.0%}" for name in order) + "."]
    world = features["x1"]
    if abs(world) >= 0.3:
        reasons.append(
            f"Vàng thế giới phiên gần nhất {'tăng' if world > 0 else 'giảm'} {_percent(world)} — "
            "giá trong nước thường đi theo sau.")
    elif abs(features["x3"]) >= 0.5:
        reasons.append(f"Vàng thế giới 3 phiên gần đây {'tăng' if features['x3'] > 0 else 'giảm'} {_percent(features['x3'])}.")
    else:
        reasons.append("Vàng thế giới mấy phiên gần đây gần như đứng yên.")
    late = features["x_late"]
    if abs(late) >= 0.2:
        reasons.append(
            f"Từ chiều tới tối nay vàng thế giới {'tăng' if late > 0 else 'giảm'} thêm {_percent(late)} — "
            "giá nhẫn chưa kịp phản ánh phần này.")
    if features["flat_days"] >= 2:
        reasons.append(f"Giá nhẫn đã đứng yên {int(features['flat_days'])} ngày liên tiếp.")
    elif features["r1"] != 0:
        reasons.append(f"Hôm nay giá nhẫn {'tăng' if features['r1'] > 0 else 'giảm'} {_percent(features['r1'])}.")
    gap = features["gap_ma30"]
    if abs(gap) >= 0.2:
        reasons.append(f"Giá đang {'cao' if gap > 0 else 'thấp'} hơn mức trung bình 30 ngày {_percent(gap)}.")
    if abs(features["prem_z"]) >= 1:
        reasons.append(
            f"Chênh lệch giữa giá trong nước và thế giới đang {'cao' if features['prem_z'] > 0 else 'thấp'} "
            "hơn mức quen thuộc.")
    if horizon == 1 and features["target_weekend"]:
        reasons.append("Ngày mai là cuối tuần, giá thường ít thay đổi.")
    return reasons


def _comparison(trained: ml.Trained) -> str:
    """Đặt mô hình cạnh "cách đoán đơn giản" (lúc nào cũng chọn hướng hay gặp nhất) để người dùng tự so."""
    test, baseline = trained.stats["model"], trained.stats["baseline"]
    period = (f"{test['rows']} ngày gần nhất (để riêng, không dùng khi chọn mô hình)"
              if trained.stats["holdout_days"] else f"{test['rows']} ngày thử lại")
    verdict = ("mô hình đang tốt hơn" if test["rate"] > baseline["rate"]
               else "mô hình đang ngang bằng" if test["rate"] == baseline["rate"]
               else "mô hình CHƯA tốt hơn cách đoán đơn giản")
    return (
        f"Thử trên {period}: mô hình đúng {test['rate']:.0%}; cách đoán đơn giản (lúc nào cũng đoán "
        f"“{DIRECTION_VI[trained.baseline_direction]}”) đúng {baseline['rate']:.0%} → {verdict}.")


def _ml_details(trained: ml.Trained) -> list[str]:
    test, baseline = trained.stats["model"], trained.stats["baseline"]
    compared = ", ".join(
        f"{MODEL_NAMES[name]} {_rate(result['rate'])}" for name, result in trained.stats["candidates"].items())
    top = ", ".join(f"{ml.FEATURES[name]} ({share:.0%})" for name, share in trained.importance[:5])
    return [
        f"Mô hình: {MODEL_NAMES[trained.name]} (scikit-learn), học từ {trained.train_rows} ngày đã có kết quả, "
        f"dữ liệu tới {trained.last_day:%d/%m/%Y}; huấn luyện lại mỗi khi có thêm ngày mới.",
        f"Điểm trên {test['rows']} ngày {'để riêng' if trained.stats['holdout_days'] else 'thử lại'}: đúng "
        f"{_rate(test['rate'])}, macro-F1 {_vi(test['macro_f1'])}. Cách đoán đơn giản: {_rate(baseline['rate'])}, "
        f"macro-F1 {_vi(baseline['macro_f1'])}.",
        f"Chọn mô hình bằng giai đoạn trước đó ({next(iter(trained.stats['candidates'].values()))['rows']} ngày): "
        f"{compared}. Toàn bộ kiểm tra lùi ({trained.stats['replay']['rows']} ngày): {_rate(trained.stats['replay']['rate'])}.",
        f"Dữ kiện ảnh hưởng nhiều nhất: {top}.",
    ]


def _ml_forecast(session: Session, trained: ml.Trained, horizon: int) -> dict:
    ring = learning.closes_by_day(session)
    xau = learning.closes_by_day(session, config.XAUUSD)
    # Tính lại để theo kịp giá nhẫn và giá thế giới mới nhất trong ngày
    row = ml.build_features(ring, xau, horizon, learning.world_hourly(session)).iloc[[-1]]
    features = {name: float(value) for name, value in row.iloc[0].items()}
    probabilities = trained.predict(row)

    news = news_signal(session)
    if news["used"]:
        # Tin tức dịch một phần xác suất giữa "tăng" và "giảm" (tối đa NEWS_WEIGHT / 2 mỗi phía)
        shift = NEWS_WEIGHT * news["vote"] / 2
        probabilities["up"] = min(max(probabilities["up"] + shift, 0.0), 1.0)
        probabilities["down"] = min(max(probabilities["down"] - shift, 0.0), 1.0)
        total = sum(probabilities.values())
        probabilities = {name: value / total for name, value in probabilities.items()}
    direction = max(probabilities, key=probabilities.get)
    confidence = round(probabilities[direction], 2)
    runner_up = sorted(probabilities.values(), reverse=True)[1]

    when = "trong ngày tới" if horizon == 1 else f"trong {horizon} ngày tới"
    summary = f"Giá vàng nhẫn nhiều khả năng {DIRECTION_VI[direction]} {when} (máy ước tính khoảng {confidence:.0%})"
    summary += ", nhưng chưa nghiêng rõ về hướng nào." if confidence - runner_up < 0.08 else "."
    reasons = _ml_reasons(features, probabilities, trained, horizon)
    reasons.append(_news_reason(news))
    return {
        "model": f"ml:{trained.name}", "probabilities": {name: round(value, 3) for name, value in probabilities.items()},
        "direction": direction, "confidence": confidence, "confidence_label": ml_confidence_label(confidence),
        "score": round(probabilities["up"] - probabilities["down"], 3), "summary": summary,
        "reasons": reasons, "news_used": news["used"], "technical_details": _ml_details(trained),
        "comparison": _comparison(trained), "baseline_direction": trained.baseline_direction,
        "indicators": {name: round(value, 3) for name, value in features.items()},
        "votes": {"news": news["vote"]} if news["used"] else {},
    }


def forecast(session: Session, symbol: str, horizon: int = 1) -> dict:
    """Dự báo bằng mô hình máy học; chỉ khi chưa đủ dữ liệu huấn luyện mới dùng công thức cũ."""
    if symbol == config.SJC_RING and horizon in learning.HORIZONS:
        trained = ml_state(session, horizon)
        if trained is not None:
            return {
                "symbol": symbol, "basis": symbol, "horizon_days": horizon,
                "data_points": len(learning.closes_by_day(session)), "note": None, "disclaimer": DISCLAIMER,
                **_ml_forecast(session, trained, horizon),
            }
    return _rules_forecast(session, symbol, horizon)


def _rules_forecast(session: Session, symbol: str, horizon: int = 1) -> dict:
    needed = min_points(horizon)
    closes = prices.daily_closes(session, symbol)
    basis, note = symbol, None
    if len(closes) < needed and symbol != config.XAUUSD:
        # Chưa tích luỹ đủ lịch sử trong nước → tạm dùng xu hướng vàng thế giới
        closes = prices.daily_closes(session, config.XAUUSD)
        basis = config.XAUUSD
        note = f"Chưa đủ {needed} phiên dữ liệu giá trong nước; tạm dựa trên xu hướng vàng thế giới (XAU/USD)."
    base = {
        "symbol": symbol, "basis": basis, "horizon_days": horizon, "data_points": len(closes),
        "note": note, "disclaimer": DISCLAIMER,
    }
    if len(closes) < needed:
        return {
            **base, "direction": None, "confidence": None, "confidence_label": None, "indicators": {},
            "votes": {}, "reasons": [], "technical_details": [], "news_used": False,
            "model": "rules", "probabilities": {},
            "summary": "Chưa đủ dữ liệu giá để dự báo.", "note": f"Chưa đủ dữ liệu (cần {needed} phiên).",
        }
    result = compute(closes, horizon, news_signal(session))
    result["technical_details"].append("Đang dùng công thức cộng điểm vì chưa đủ dữ liệu để huấn luyện mô hình máy học.")
    if basis != symbol:
        result["confidence"] = round(max(0.5, result["confidence"] - 0.05), 2)
        result["confidence_label"] = confidence_label(result["confidence"])
    return {**base, **result}
