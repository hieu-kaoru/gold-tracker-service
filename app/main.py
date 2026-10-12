import logging
from contextlib import asynccontextmanager
from datetime import date
from enum import Enum
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from . import config, scheduler, schemas
from .db import get_session, init_db
from .services import forecast as forecast_service
from .services import learning
from .services import news as news_service
from .services import prices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

Symbol = Enum("Symbol", {s: s for s in config.SYMBOLS}, type=str)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    running = scheduler.start() if config.SCHEDULER_ENABLED else None
    yield
    if running:
        running.shutdown(wait=False)


app = FastAPI(title="Gold Tracker API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "collectors": scheduler.status}


@app.get("/prices/latest", response_model=schemas.LatestPrices)
def prices_latest(session: Session = Depends(get_session)):
    return {"items": prices.latest_all(session)}


@app.get("/prices/history", response_model=schemas.History)
def prices_history(
    symbol: Symbol = Symbol(config.SJC_RING),
    days: int = Query(30, ge=1, le=730),
    interval: Literal["raw", "daily"] = "daily",
    session: Session = Depends(get_session),
):
    points = prices.history(session, symbol.value, days, interval)
    return {"symbol": symbol.value, "days": days, "interval": interval, "points": points}


@app.get("/prices/day", response_model=schemas.DayPrice)
def prices_day(
    day: date = Query(..., alias="date", description="Ngày cần xem (giờ Việt Nam), dạng YYYY-MM-DD"),
    session: Session = Depends(get_session),
):
    if day > prices.today_vn():
        raise HTTPException(422, "Không thể xem giá của ngày trong tương lai")
    result = prices.day(session, day)
    if result is None:
        earliest = prices.continuous_from(session, config.SJC_RING)
        hint = f" Dữ liệu liên tục có từ {earliest:%d/%m/%Y}." if earliest else ""
        raise HTTPException(404, f"Không có dữ liệu giá nhẫn SJC cho ngày {day:%d/%m/%Y}.{hint}")
    return result


@app.get("/spread", response_model=schemas.Spread)
def spread(session: Session = Depends(get_session)):
    result = prices.spread(session)
    if result is None:
        raise HTTPException(503, "Chưa đủ dữ liệu nhẫn SJC, XAU/USD và USD/VND để tính chênh lệch")
    return result


@app.get("/news", response_model=schemas.NewsList)
def news(
    limit: int = Query(30, ge=1, le=100),
    kind: Literal["drivers", "gold", "all"] = "drivers",
    lang: Literal["vi", "en"] | None = None,
    driver: str | None = Query(None, description="Lọc theo một nhóm yếu tố, ví dụ fed, geopolitics"),
    region: Literal["domestic", "world"] | None = None,
    session: Session = Depends(get_session),
):
    return {"items": news_service.list_news(session, limit, kind, lang, driver, region)}


@app.get("/forecast/history", response_model=schemas.ForecastHistory)
def forecast_history(days: int = Query(60, ge=1, le=400), session: Session = Depends(get_session)):
    """Bảng đối chiếu dự báo với thực tế, tỉ lệ đúng và mô hình máy học đang dùng."""
    result = learning.history(session, days)
    result["model"] = {f"h{h}": forecast_service.model_info(session, h) for h in learning.HORIZONS}
    return result


@app.get("/forecast", response_model=schemas.Forecast)
def forecast(
    symbol: Symbol = Symbol(config.SJC_RING),
    horizon: int = Query(1, description="Khung dự báo theo ngày: 1 hoặc 7"),
    session: Session = Depends(get_session),
):
    if horizon not in forecast_service.HORIZONS:
        raise HTTPException(422, "horizon chỉ nhận 1 hoặc 7")
    return forecast_service.forecast(session, symbol.value, horizon)
