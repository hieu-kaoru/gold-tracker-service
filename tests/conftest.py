import os
import tempfile

# Phải đặt trước khi import app: DB tạm riêng cho test và tắt scheduler (không gọi mạng)
_tmp = tempfile.mkdtemp(prefix="gold-tracker-test-")
os.environ["GT_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["GT_SCHEDULER"] = "0"

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def session():
    from app import models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
def fast_isolated_ml(monkeypatch):
    """Mỗi test tự huấn luyện lại (không dùng mô hình nhớ từ test khác) và chỉ với mô hình nhanh nhất."""
    from app.services import forecast, ml

    logistic = ml._candidates()["logistic"]
    monkeypatch.setattr(ml, "_candidates", lambda: {"logistic": logistic})
    forecast._ML_CACHE.clear()
    yield
    forecast._ML_CACHE.clear()
