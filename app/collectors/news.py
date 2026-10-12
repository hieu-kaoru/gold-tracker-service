"""Tin tức qua RSS. Mỗi feed lỗi độc lập, không chặn các feed khác."""
import calendar
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape

import feedparser

from .. import config
from ..services import drivers, sentiment
from .base import http_get, utcnow

log = logging.getLogger(__name__)


@dataclass
class Article:
    url: str
    source: str
    lang: str
    title: str
    summary: str
    published_at: datetime
    gold_related: bool
    sentiment_score: float
    sentiment_label: str
    drivers: str = ""
    impact: str = ""
    impact_level: str = ""
    impact_reason: str = ""


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def impact_fields(title: str, summary: str) -> dict:
    impact = drivers.analyze(f"{title}. {summary}", title)
    if impact is None:
        return {"drivers": "", "impact": "", "impact_level": "", "impact_reason": ""}
    return {
        "drivers": ",".join(impact.drivers), "impact": impact.direction,
        "impact_level": impact.level, "impact_reason": impact.reason,
    }


def parse_feed(content: bytes | str, source: str, lang: str) -> list[Article]:
    articles = []
    for entry in feedparser.parse(content).entries:
        url, title = entry.get("link"), _strip_html(entry.get("title", ""))
        if not url or not title:
            continue
        summary = _strip_html(entry.get("summary", ""))[:1000]
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        published = (
            datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc).replace(tzinfo=None)
            if parsed else utcnow()
        )
        text = f"{title}. {summary}"
        score, label = sentiment.analyze(text, lang)
        articles.append(Article(
            url=url, source=source, lang=lang, title=title[:512], summary=summary,
            published_at=published, gold_related=sentiment.is_gold_related(text),
            sentiment_score=score, sentiment_label=label, **impact_fields(title, summary),
        ))
    return articles


def fetch() -> tuple[list[Article], dict[str, str]]:
    """Trả về (bài viết, lỗi theo nguồn)."""
    articles: list[Article] = []
    errors: dict[str, str] = {}
    for feed in config.NEWS_FEEDS:
        try:
            parsed = parse_feed(http_get(feed["url"]).content, feed["source"], feed["lang"])
            if not parsed:
                raise ValueError("feed không có bài nào (có thể không còn là RSS)")
            articles.extend(parsed)
        except Exception as exc:
            errors[feed["source"]] = str(exc)
            log.warning("Feed %s lỗi: %s", feed["source"], exc)
    return articles, errors
