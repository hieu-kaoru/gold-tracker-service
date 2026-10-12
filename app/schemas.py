from datetime import date, datetime, timezone
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

# DB lưu UTC naive → luôn trả ISO 8601 có hậu tố Z để client khỏi đoán múi giờ
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(lambda v: v.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"), return_type=str),
]


class LatestPrice(BaseModel):
    symbol: str
    buy: float | None
    sell: float | None
    price: float
    currency: str
    unit: str
    source: str
    ts: UtcDateTime
    change: float | None
    change_pct: float | None


class LatestPrices(BaseModel):
    items: list[LatestPrice]


class HistoryPoint(BaseModel):
    ts: UtcDateTime
    buy: float | None
    sell: float | None
    price: float


class History(BaseModel):
    symbol: str
    days: int
    interval: str
    points: list[HistoryPoint]


class DayPrice(BaseModel):
    date: date
    buy: float | None
    sell: float | None
    unit: str
    source: str
    change: float | None
    change_pct: float | None
    previous_date: date | None
    updates: list[HistoryPoint]


class Spread(BaseModel):
    ring_sell: float
    xau_usd: float
    usd_vnd: float
    world_vnd_per_luong: float
    spread: float
    spread_pct: float
    unit: str
    ts: UtcDateTime


class News(BaseModel):
    url: str
    source: str
    lang: str
    title: str
    summary: str
    published_at: UtcDateTime
    gold_related: bool
    sentiment_score: float
    sentiment_label: str
    drivers: list[str]
    driver_labels: list[str]
    region: str | None
    impact: str | None
    impact_level: str | None
    impact_reason: str | None


class NewsList(BaseModel):
    items: list[News]


class ForecastActual(BaseModel):
    change: float
    pct: float
    direction: str
    correct: bool


class ForecastCell(BaseModel):
    direction: str
    confidence: float
    source: str
    model: str
    actual: ForecastActual | None


class ForecastHistoryRow(BaseModel):
    date: date
    base_price: float
    h1: ForecastCell | None = None
    h7: ForecastCell | None = None


class ForecastAccuracy(BaseModel):
    evaluated: int
    correct: int
    rate: float | None
    recent_rate: float | None
    recent_rows: int
    baseline_direction: str | None
    baseline_rate: float | None


class ModelScore(BaseModel):
    rows: int
    rate: float | None
    macro_f1: float | None


class ModelFeature(BaseModel):
    name: str
    label: str
    share: float


class ModelInfo(BaseModel):
    name: str
    label: str
    train_rows: int
    last_day: date
    test: ModelScore            # điểm trên giai đoạn để riêng (hoặc toàn bộ thử lại nếu chưa đủ dữ liệu để tách)
    baseline: ModelScore        # "cách đoán đơn giản" trên cùng giai đoạn
    baseline_direction: str
    holdout_days: int | None
    replay: ModelScore          # mô hình được chọn trên toàn bộ kiểm tra lùi
    candidates: dict[str, ModelScore]
    top_features: list[ModelFeature]


class ForecastHistory(BaseModel):
    rows: list[ForecastHistoryRow]
    accuracy: dict[str, ForecastAccuracy]
    # Mô hình máy học đang dùng cho từng khung ("h1", "h7"); None = đang dùng công thức dự phòng
    model: dict[str, ModelInfo | None]
    flat_threshold_pct: dict[str, float]


class Forecast(BaseModel):
    symbol: str
    basis: str
    direction: str | None
    confidence: float | None
    confidence_label: str | None = None
    summary: str
    score: float | None = None
    horizon_days: int
    data_points: int
    indicators: dict[str, float]
    votes: dict[str, float]
    reasons: list[str] = []
    technical_details: list[str] = []
    model: str = "rules"  # "ml:<tên mô hình>" hoặc "rules" (công thức dự phòng)
    probabilities: dict[str, float] = {}
    # Mô hình so với "cách đoán đơn giản" trên giai đoạn kiểm tra (chỉ có khi dùng mô hình máy học)
    comparison: str | None = None
    baseline_direction: str | None = None
    news_used: bool = False
    note: str | None
    disclaimer: str
