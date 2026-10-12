from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..collectors.news import Article, impact_fields
from ..models import NewsItem
from . import drivers, sentiment


def save_articles(session: Session, articles: list[Article]) -> int:
    urls = [a.url for a in articles]
    known = set(session.scalars(select(NewsItem.url).where(NewsItem.url.in_(urls)))) if urls else set()
    inserted = 0
    for article in articles:
        if article.url in known:
            continue
        known.add(article.url)
        session.add(NewsItem(**article.__dict__))
        inserted += 1
    session.commit()
    return inserted


def reclassify_all(session: Session) -> int:
    """Chấm lại yếu tố tác động cho tin đã lưu (sau khi thêm cột hoặc sửa bộ từ khoá)."""
    changed = 0
    for item in session.scalars(select(NewsItem)):
        text = f"{item.title}. {item.summary}"
        fields = {"gold_related": sentiment.is_gold_related(text), **impact_fields(item.title, item.summary)}
        if any(getattr(item, name) != value for name, value in fields.items()):
            for name, value in fields.items():
                setattr(item, name, value)
            changed += 1
    session.commit()
    return changed


def list_news(
    session: Session, limit: int, kind: str, lang: str | None,
    driver: str | None = None, region: str | None = None,
) -> list[dict]:
    """kind: "drivers" = tin là tác nhân biến động giá; "gold" = mọi bài nhắc tới vàng; "all" = tất cả."""
    query = select(NewsItem).order_by(NewsItem.published_at.desc())
    if kind == "drivers":
        query = query.where(NewsItem.drivers != "")
    elif kind == "gold":
        query = query.where(NewsItem.gold_related.is_(True))
    if lang:
        query = query.where(NewsItem.lang == lang)
    if driver:
        # Khoá ngăn bằng dấu phẩy nên bọc dấu phẩy hai đầu để khớp nguyên khoá
        query = query.where(("," + NewsItem.drivers + ",").contains(f",{driver},"))
    if region:
        domestic = or_(*(("," + NewsItem.drivers + ",").contains(f",{key},") for key in drivers.DOMESTIC_KEYS))
        query = query.where(domestic) if region == "domestic" else query.where(NewsItem.drivers != "", ~domestic)
    return [to_dict(item) for item in session.scalars(query.limit(limit))]


def to_dict(item: NewsItem) -> dict:
    keys = [key for key in item.drivers.split(",") if key]
    return {
        "url": item.url, "source": item.source, "lang": item.lang, "title": item.title, "summary": item.summary,
        "published_at": item.published_at, "gold_related": item.gold_related,
        "sentiment_score": item.sentiment_score, "sentiment_label": item.sentiment_label,
        "drivers": keys, "driver_labels": drivers.labels(keys), "region": drivers.region(keys) if keys else None,
        "impact": item.impact or None, "impact_level": item.impact_level or None, "impact_reason": item.impact_reason or None,
    }
