from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .. import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class Quote:
    symbol: str
    price: float
    currency: str
    unit: str
    source: str
    buy: float | None = None
    sell: float | None = None
    ts: datetime = field(default_factory=utcnow)


class CollectorError(RuntimeError):
    pass


def http_get(url: str, **kwargs) -> httpx.Response:
    headers = {"User-Agent": config.USER_AGENT, **kwargs.pop("headers", {})}
    # retries=2: thử lại khi lỗi lúc thiết lập kết nối (rớt mạng, bắt tay TLS hỏng); không thử lại lỗi HTTP
    transport = httpx.HTTPTransport(retries=2)
    with httpx.Client(transport=transport, timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, headers=headers, **kwargs)
    resp.raise_for_status()
    return resp
