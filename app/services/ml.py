"""Mô hình máy học dự báo hướng giá vàng nhẫn (scikit-learn).

Bài toán: phân loại 3 lớp — sau `horizon` ngày giá nhẫn tăng / đi ngang / giảm so với hôm nay.
Module này thuần tính toán (không đụng DB): nhận chuỗi giá theo ngày, trả đặc trưng, kết quả
kiểm tra lùi (walk-forward) và mô hình đã huấn luyện.

Nguyên tắc chống "nhìn trước tương lai":
- Đặc trưng của ngày t chỉ dùng dữ liệu biết được tới hết ngày t (giờ Việt Nam). Phiên vàng thế giới
  đề ngày t đóng cửa vào rạng sáng t+1 giờ VN, nên với ngày t chỉ dùng các phiên đề ngày ≤ t−1.
- Giá thế giới theo giờ chỉ dùng tới 16:00 UTC (23h Việt Nam) của ngày t — sau giờ SJC ngừng đổi giá
  nhưng trước khi hết ngày, nên là thông tin đã biết lúc dự báo mà giá nhẫn chưa kịp phản ánh.
- Khi kiểm tra lùi, mô hình đoán ngày t chỉ được học từ các ngày mà kết quả đã lộ ra trước t.
"""
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import indicators

CLASSES = ("down", "sideways", "up")
MIN_TRAIN = 100   # ít hơn chừng này ngày đã có kết quả thì chưa huấn luyện
RETRAIN_EVERY = 5  # khi kiểm tra lùi, huấn luyện lại sau mỗi 5 ngày (đủ sát thực tế, chạy nhanh)
DAY_END_UTC = 16   # 23h Việt Nam: mốc "cuối ngày" để chốt dữ kiện thế giới của ngày t
AFTERNOON_UTC = 10  # 17h Việt Nam: quanh lúc SJC ngừng cập nhật giá trong ngày
HOLDOUT_DAYS = 60  # giai đoạn gần nhất để riêng: không tham gia chọn mô hình, chỉ dùng để chấm điểm
MIN_SELECTION_ROWS = 40  # phần còn lại phải đủ chừng này ngày mới tách được giai đoạn để riêng

# Tên đặc trưng → mô tả ngắn (dùng trong "Chi tiết kỹ thuật")
FEATURES = {
    "r1": "thay đổi giá nhẫn 1 ngày (%)",
    "r3": "thay đổi giá nhẫn 3 ngày (%)",
    "r7": "thay đổi giá nhẫn 7 ngày (%)",
    "r14": "thay đổi giá nhẫn 14 ngày (%)",
    "gap_ma10": "giá so với trung bình 10 ngày (%)",
    "gap_ma30": "giá so với trung bình 30 ngày (%)",
    "rsi": "độ nóng RSI 14 ngày",
    "macd_hist": "đà MACD so với giá (%)",
    "vol10": "độ dao động 10 ngày",
    "flat_days": "số ngày giá đứng yên liên tiếp",
    "x1": "vàng thế giới: phiên gần nhất (%)",
    "x3": "vàng thế giới: 3 phiên (%)",
    "x7": "vàng thế giới: 7 phiên (%)",
    "x_day": "vàng thế giới: 24 giờ tính tới 23h hôm nay (%)",
    "x_late": "vàng thế giới: từ 17h tới 23h hôm nay (%)",
    "prem_z": "chênh lệch trong nước/thế giới so với mức quen thuộc",
    "target_weekend": "ngày đích rơi vào cuối tuần",
}


def _candidates() -> dict:
    """Các mô hình đem so sánh; đều nhỏ và được ghìm mạnh vì dữ liệu chỉ vài trăm ngày."""
    return {
        "logistic": lambda: make_pipeline(
            StandardScaler(), LogisticRegression(C=0.3, class_weight="balanced", max_iter=2000)),
        "random_forest": lambda: RandomForestClassifier(
            n_estimators=150, max_depth=3, min_samples_leaf=8, class_weight="balanced", random_state=0),
        "gradient_boosting": lambda: HistGradientBoostingClassifier(
            max_depth=2, max_iter=60, learning_rate=0.05, l2_regularization=1.0,
            class_weight="balanced", random_state=0),
    }


def _series(by_day: dict[date, float]) -> pd.Series:
    series = pd.Series({pd.Timestamp(day): value for day, value in by_day.items()}, dtype="float64").sort_index()
    return series.asfreq("D").ffill()  # ngày thiếu giá coi như giữ giá hôm trước


def intraday_moves(hourly: pd.Series | None, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Biến động vàng thế giới trong từng ngày Việt Nam, chỉ từ các mức giá đã biết trước 23h hôm đó.

    `hourly`: giá theo giờ, chỉ số là thời điểm giá được biết (UTC). Trả về hai cột (%):
    x_day = mức lúc 23h hôm nay so với 23h hôm qua; x_late = mức lúc 23h so với 17h cùng ngày.
    Thiếu dữ liệu thì bằng 0.
    """
    moves = pd.DataFrame(0.0, index=days, columns=["x_day", "x_late"])
    if hourly is None or hourly.empty:
        return moves
    day = hourly.index.normalize()
    elapsed = hourly.index - day
    end = hourly[elapsed <= pd.to_timedelta(DAY_END_UTC, unit="h")]
    afternoon = hourly[elapsed <= pd.to_timedelta(AFTERNOON_UTC, unit="h")]
    end = end.groupby(end.index.normalize()).last()
    afternoon = afternoon.groupby(afternoon.index.normalize()).last()
    calendar = pd.date_range(min(end.index[0], days[0]), days[-1], freq="D")
    end = end.reindex(calendar).ffill()            # cuối tuần/ngày nghỉ: giữ mức gần nhất
    afternoon = afternoon.reindex(calendar)        # không có phiên chiều thì không có biến động chiều → tối
    moves["x_day"] = (end.pct_change(fill_method=None) * 100).reindex(days)
    moves["x_late"] = ((end / afternoon - 1) * 100).reindex(days)
    return moves.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_features(
    ring: dict[date, float], xau: dict[date, float], horizon: int, hourly: pd.Series | None = None,
) -> pd.DataFrame:
    """Bảng đặc trưng theo ngày (chỉ các ngày đã đủ dữ liệu quá khứ để tính)."""
    price = _series(ring)
    returns = price.pct_change() * 100
    frame = pd.DataFrame(index=price.index)
    frame["r1"] = returns
    for days in (3, 7, 14):
        frame[f"r{days}"] = price.pct_change(days) * 100
    frame["gap_ma10"] = (price / indicators.sma(price, 10) - 1) * 100
    frame["gap_ma30"] = (price / indicators.sma(price, 30) - 1) * 100
    frame["rsi"] = indicators.rsi(price)
    frame["macd_hist"] = indicators.macd(price)[2] / price * 100
    frame["vol10"] = returns.rolling(10).std()
    unchanged = returns.fillna(0).eq(0)
    frame["flat_days"] = unchanged.groupby((~unchanged).cumsum()).cumsum().astype(float)

    if xau:
        world = _series(xau).reindex(price.index.union(_series(xau).index)).ffill()
        known = world.shift(1).reindex(price.index)  # tới hết ngày t mới biết phiên thế giới đề ngày t−1
        for days in (1, 3, 7):
            frame[f"x{days}"] = known.pct_change(days) * 100
        premium = np.log(price / known)
        frame["prem_z"] = (premium - premium.rolling(30).mean()) / premium.rolling(30).std()
    else:
        for name in ("x1", "x3", "x7", "prem_z"):
            frame[name] = 0.0
    frame[["x_day", "x_late"]] = intraday_moves(hourly, frame.index)
    frame["target_weekend"] = ((frame.index + pd.to_timedelta(horizon, unit="D")).dayofweek >= 5).astype(float)

    frame = frame.dropna(subset=["gap_ma30", "rsi", "vol10"])  # bỏ giai đoạn đầu chưa đủ 30 ngày
    return frame[list(FEATURES)].replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_labels(ring: dict[date, float], horizon: int, flat_pct: float) -> pd.Series:
    """Hướng thực tế sau `horizon` ngày; NaN ở những ngày chưa tới hạn."""
    price = _series(ring)
    pct = (price.shift(-horizon) / price - 1) * 100
    labels = pd.Series(np.where(pct.abs() < flat_pct, "sideways", np.where(pct > 0, "up", "down")), index=price.index)
    return labels.where(pct.notna())


def _fit(factory, features: pd.DataFrame, labels: pd.Series):
    model = factory()
    model.fit(features.to_numpy(), labels.to_numpy())
    return model


def _probabilities(model, features: pd.DataFrame) -> pd.DataFrame:
    """Xác suất theo đủ 3 lớp (lớp mô hình chưa từng gặp khi học thì = 0)."""
    raw = pd.DataFrame(model.predict_proba(features.to_numpy()), index=features.index, columns=model.classes_)
    return raw.reindex(columns=list(CLASSES), fill_value=0.0)


def walk_forward(features: pd.DataFrame, labels: pd.Series, factory, horizon: int) -> pd.DataFrame:
    """Đoán lần lượt từng ngày, mỗi lần chỉ học từ các ngày đã có kết quả trước ngày đó.

    Trả về xác suất 3 lớp + cột `baseline` (hướng hay gặp nhất trong dữ liệu học lúc đó).
    """
    labels = labels.reindex(features.index)
    blocks = []
    for start in range(0, len(features), RETRAIN_EVERY):
        cutoff = features.index[start]
        # Kết quả của ngày d lộ ra cuối ngày d + horizon, nên chỉ học các ngày có d + horizon ≤ cutoff
        usable = (features.index + pd.to_timedelta(horizon, unit="D") <= cutoff) & labels.notna().to_numpy()
        if usable.sum() < MIN_TRAIN or labels[usable].nunique() < 2:
            continue
        model = _fit(factory, features[usable], labels[usable])
        block = _probabilities(model, features.iloc[start:start + RETRAIN_EVERY])
        block["baseline"] = labels[usable].mode().iloc[0]
        blocks.append(block)
    if not blocks:
        return pd.DataFrame(columns=[*CLASSES, "baseline"])
    return pd.concat(blocks)


def score(predicted: pd.Series, actual: pd.Series) -> dict:
    """Tỉ lệ đúng và macro-F1 trên các ngày đã có kết quả."""
    actual = actual.reindex(predicted.index)
    known = actual.notna()
    predicted, actual = predicted[known], actual[known]
    if actual.empty:
        return {"rows": 0, "rate": None, "macro_f1": None}
    return {
        "rows": int(len(actual)), "rate": round(float((predicted == actual).mean()), 3),
        "macro_f1": round(float(f1_score(actual, predicted, labels=list(CLASSES), average="macro", zero_division=0)), 3),
    }


def direction_from(probabilities: pd.DataFrame) -> pd.Series:
    return probabilities[list(CLASSES)].idxmax(axis=1)


@dataclass
class Trained:
    name: str                       # mô hình được chọn
    horizon: int
    model: object                   # đã học trên toàn bộ ngày có kết quả, dùng để dự báo hôm nay
    train_rows: int
    last_day: date                  # ngày mới nhất trong dữ liệu
    today: pd.DataFrame             # một dòng đặc trưng của ngày mới nhất
    replay: pd.DataFrame            # kết quả kiểm tra lùi của mô hình được chọn (xác suất theo ngày)
    # stats: "model"/"baseline" = điểm trên giai đoạn để riêng (con số công bố); "candidates" = điểm của từng
    # mô hình trên giai đoạn dùng để chọn; "replay" = điểm của mô hình được chọn trên toàn bộ kiểm tra lùi
    stats: dict
    baseline_direction: str         # "cách đoán đơn giản": hướng hay gặp nhất trong dữ liệu đã có kết quả
    importance: list[tuple[str, float]]

    def predict(self, features_row: pd.DataFrame | None = None) -> dict[str, float]:
        """Xác suất 3 hướng cho một dòng đặc trưng (mặc định: ngày mới nhất lúc huấn luyện)."""
        row = _probabilities(self.model, self.today if features_row is None else features_row).iloc[0]
        return {name: float(row[name]) for name in CLASSES}


def _importance(model, features: pd.DataFrame, labels: pd.Series) -> list[tuple[str, float]]:
    """Mức ảnh hưởng tương đối của từng đặc trưng (tổng = 1), để giải thích mô hình dựa vào đâu nhiều."""
    estimator = model[-1] if hasattr(model, "steps") else model
    if hasattr(estimator, "coef_"):
        raw = np.abs(estimator.coef_).mean(axis=0)
    elif hasattr(estimator, "feature_importances_"):
        raw = estimator.feature_importances_
    else:
        from sklearn.inspection import permutation_importance

        raw = permutation_importance(
            model, features.to_numpy(), labels.to_numpy(), n_repeats=5, random_state=0).importances_mean.clip(min=0)
    total = float(np.sum(raw)) or 1.0
    return sorted(((name, round(float(value) / total, 3)) for name, value in zip(features.columns, raw)),
                  key=lambda item: -item[1])


def train(
    ring: dict[date, float], xau: dict[date, float], horizon: int, flat_pct: float,
    hourly: pd.Series | None = None,
) -> Trained | None:
    """So sánh các mô hình bằng kiểm tra lùi, chọn cái đúng nhiều nhất, rồi học lại trên toàn bộ dữ liệu.

    None nếu chưa đủ dữ liệu để huấn luyện và kiểm tra.
    """
    if len(ring) < 40:
        return None
    features = build_features(ring, xau, horizon, hourly)
    labels = build_labels(ring, horizon, flat_pct).reindex(features.index)
    labeled = labels.notna()
    if labeled.sum() < MIN_TRAIN + RETRAIN_EVERY or labels[labeled].nunique() < 2:
        return None

    replays = {}
    for name, factory in _candidates().items():
        replay = walk_forward(features, labels, factory, horizon)
        if replay.empty:
            return None
        replays[name] = replay

    # Tách giai đoạn gần nhất ra: mô hình được CHỌN bằng phần trước đó, rồi mới chấm trên phần để riêng.
    # Nhờ vậy con số công bố không bị "đẹp" lên vì đã chọn cái tốt nhất trên chính giai đoạn đó.
    judged = next(iter(replays.values())).index.intersection(labels[labeled].index)
    holdout_start = judged[-1] - pd.to_timedelta(HOLDOUT_DAYS - 1, unit="D") if len(judged) else None
    has_holdout = holdout_start is not None and (judged < holdout_start).sum() >= MIN_SELECTION_ROWS
    selection_labels = labels[labels.index < holdout_start] if has_holdout else labels
    holdout_labels = labels[labels.index >= holdout_start] if has_holdout else labels

    results = {name: score(direction_from(replay), selection_labels) for name, replay in replays.items()}
    # Chọn theo tỉ lệ đúng; hoà thì ưu tiên macro-F1 (không thiên về chỉ đoán một hướng)
    best = max(results, key=lambda name: (results[name]["rate"] or 0, results[name]["macro_f1"] or 0))
    replay = replays[best]
    model = _fit(_candidates()[best], features[labeled], labels[labeled])
    stats = {
        "model": score(direction_from(replay), holdout_labels), "baseline": score(replay["baseline"], holdout_labels),
        "candidates": results, "replay": score(direction_from(replay), labels),
        "holdout_days": HOLDOUT_DAYS if has_holdout else None,
    }
    return Trained(
        name=best, horizon=horizon, model=model, train_rows=int(labeled.sum()),
        last_day=features.index[-1].date(), today=features.iloc[[-1]], replay=replay,
        stats=stats, baseline_direction=str(labels[labeled].mode().iloc[0]),
        importance=_importance(model, features[labeled], labels[labeled]),
    )
