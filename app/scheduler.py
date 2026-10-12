"""Các job thu thập định kỳ. Mỗi collector lỗi độc lập và được ghi vào `status`."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import config
from .collectors import news as news_collector
from .collectors import ring_history, sjc, vcb, world
from .collectors.base import utcnow
from .db import SessionLocal
from .services import news as news_service
from .services import prices, tracking

log = logging.getLogger(__name__)

# Trạng thái lần chạy gần nhất của từng collector, lộ ra qua GET /health
status: dict[str, dict] = {}


def _run(name: str, fetch) -> None:
    try:
        with SessionLocal() as session:
            inserted = fetch(session)
        status[name] = {"ok": True, "at": utcnow().isoformat() + "Z", "inserted": inserted, "error": None}
    except Exception as exc:
        log.exception("Collector %s lỗi", name)
        status[name] = {"ok": False, "at": utcnow().isoformat() + "Z", "inserted": 0, "error": str(exc)}


def collect_prices() -> None:
    _run("sjc", lambda s: prices.save_quotes(s, sjc.fetch()))
    _run("xauusd", lambda s: prices.save_quotes(s, world.fetch()))
    _run("xauusd_hourly", lambda s: prices.save_quotes(s, world.fetch_hourly()))
    _run("usdvnd", lambda s: prices.save_quotes(s, vcb.fetch()))
    _run("forecast_record", tracking.record_today)


def collect_news() -> None:
    def fetch(session):
        articles, errors = news_collector.fetch()
        inserted = news_service.save_articles(session, articles)
        if errors:
            status["news_feeds"] = {"ok": False, "at": utcnow().isoformat() + "Z", "errors": errors}
        else:
            status.pop("news_feeds", None)
        return inserted

    _run("news", fetch)


def backfill_world_history() -> None:
    """Nạp nến ngày XAU/USD một lần khi DB còn trống để /forecast dùng được ngay."""
    with SessionLocal() as session:
        if prices.count(session, config.XAUUSD) >= 30:
            return
    _run("xauusd_backfill", lambda s: prices.save_quotes(s, world.fetch_daily_history()))


def backfill_world_hourly() -> None:
    """Nạp ~2 năm nến giờ XAU/USD một lần: mô hình cần biết thế giới biến động ra sao trong từng ngày."""
    with SessionLocal() as session:
        if prices.count(session, config.XAUUSD_HOURLY) >= 2000:
            return
    _run("xauusd_hourly_backfill",
         lambda s: prices.save_quotes(s, world.fetch_hourly(config.XAU_HOURLY_BACKFILL_PERIOD)))


def backfill_ring_history() -> None:
    """Bù các ngày còn thiếu giá nhẫn (một request mỗi lần khởi động; ngày đã có thì bỏ qua)."""
    if not config.RING_HISTORY_BACKFILL:
        status["ring_history"] = {"ok": True, "skipped": "GT_RING_HISTORY_BACKFILL=0", "inserted": 0, "error": None}
        return
    _run("ring_history", lambda s: prices.save_quotes(s, ring_history.fetch_daily(prices.today_vn())))


def reclassify_news() -> None:
    with SessionLocal() as session:
        news_service.reclassify_all(session)


def initial_run() -> None:
    reclassify_news()
    backfill_ring_history()
    backfill_world_history()
    backfill_world_hourly()
    collect_news()   # trước giá, để dự báo lưu trong ngày đã tính tin mới nhất
    collect_prices()
    # Sau khi đã có giá mới nhất: huấn luyện mô hình (mất vài chục giây) rồi dựng lại bảng đối chiếu
    _run("forecast_backfill", tracking.backfill)


def start() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(initial_run, id="initial")  # chạy ngay một lần, không chặn startup
    scheduler.add_job(collect_prices, "interval", minutes=config.PRICE_INTERVAL_MINUTES, id="prices")
    scheduler.add_job(collect_news, "interval", minutes=config.NEWS_INTERVAL_MINUTES, id="news")
    scheduler.start()
    return scheduler
