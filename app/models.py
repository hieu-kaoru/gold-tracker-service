from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PriceTick(Base):
    """Một lần ghi nhận giá. Mọi mốc thời gian lưu UTC (naive)."""

    __tablename__ = "price_ticks"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", name="uq_price_symbol_ts"),
        Index("ix_price_symbol_ts", "symbol", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16))
    buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Giá đại diện để vẽ biểu đồ/tính chỉ báo: giá bán ra (trong nước) hoặc giá khớp (thế giới)
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    unit: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    source: Mapped[str] = mapped_column(String(32))
    lang: Mapped[str] = mapped_column(String(4))
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    gold_related: Mapped[bool] = mapped_column(default=False)
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    sentiment_label: Mapped[str] = mapped_column(String(10), default="neutral")
    # Yếu tố tác động tới giá vàng (khoá trong services/drivers.py, ngăn bằng dấu phẩy; rỗng = không có)
    drivers: Mapped[str] = mapped_column(String(128), default="", server_default="")
    impact: Mapped[str] = mapped_column(String(10), default="", server_default="")  # up / down / unclear
    impact_level: Mapped[str] = mapped_column(String(10), default="", server_default="")  # high/medium/low
    impact_reason: Mapped[str] = mapped_column(String(256), default="", server_default="")


class ForecastRecord(Base):
    """Dự báo đã đưa ra vào một ngày (giờ VN) cho `horizon` ngày sau, giữ lại để đối chiếu với thực tế."""

    __tablename__ = "forecast_records"
    __table_args__ = (UniqueConstraint("day", "horizon", name="uq_forecast_day_horizon"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    horizon: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(10))
    confidence: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    # Phiếu của từng dấu hiệu trong [-1, 1] (chỉ có nghĩa với dự báo theo công thức; mô hình máy học để 0)
    vote_ma: Mapped[float] = mapped_column(Float)
    vote_macd: Mapped[float] = mapped_column(Float)
    vote_rsi: Mapped[float] = mapped_column(Float)
    vote_news: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_price: Mapped[float] = mapped_column(Float)  # giá bán nhẫn tại ngày dự báo
    source: Mapped[str] = mapped_column(String(10))   # live = lưu thật trong ngày; backfill = tính lại
    model: Mapped[str] = mapped_column(String(32), default="rules", server_default="rules")  # vd. ml:logistic
