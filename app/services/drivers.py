"""Nhận diện tin tức là TÁC NHÂN làm giá vàng biến động (không phải bài tường thuật giá).

Chạy cục bộ bằng từ khoá, miễn phí. Mỗi nhóm yếu tố có: từ khoá nhận diện, cụm từ cho thấy
tác động đẩy giá vàng lên / kéo xuống, và lời giải thích tiếng Việt. Đây là quy tắc kinh nghiệm
(trú ẩn an toàn, lãi suất thực, sức mạnh USD), chỉ để tham khảo.
"""
import re
from dataclasses import dataclass

from . import sentiment

# Bài bình luận về một tài sản khác (cặp tiền, dầu, bạc...) không phải tin tác nhân của vàng,
# dù có nhắc Fed hay xung đột. Nhận diện qua tiêu đề; tiêu đề có nhắc vàng thì vẫn giữ.
_OTHER_PAIR = re.compile(r"\b(?!XAU)[A-Z]{3}/[A-Z]{3}\b")
_OTHER_INSTRUMENTS = (
    "wti", "brent", "crude", "silver", "xag", "euro", "yen", "pound", "sterling", "australian dollar",
    "new zealand dollar", "canadian dollar", "rupee", "franc", "bitcoin", "crypto", "ethereum",
    "price forecast", "price analysis",
)


# Bài chỉ tường thuật bảng giá trong ngày, không nói nguyên nhân
_PRICE_REPORT = re.compile(
    r"(bảng giá vàng|giá vàng (hôm nay|sáng nay|trưa nay|chiều nay|tối nay|ngày|sáng|chiều|mới nhất)"
    r"|gold price today|price today)",
    re.IGNORECASE,
)
DOMESTIC_KEYS = ("vn_policy", "vn_market")


def is_price_report(title: str) -> bool:
    return bool(_PRICE_REPORT.search(title))


def region(keys: list[str]) -> str:
    """domestic nếu bài thuộc nhóm chính sách/thị trường vàng trong nước, còn lại là world."""
    return "domestic" if any(key in DOMESTIC_KEYS for key in keys) else "world"


def is_other_instrument(title: str) -> bool:
    if sentiment.is_gold_related(title):
        return False
    return bool(_OTHER_PAIR.search(title)) or sentiment.count_phrases(title.lower(), _OTHER_INSTRUMENTS) > 0


@dataclass(frozen=True)
class Driver:
    key: str
    label: str
    keywords: tuple[str, ...]
    up: tuple[str, ...]
    down: tuple[str, ...]
    up_reason: str
    down_reason: str
    neutral_reason: str
    # Nhóm dùng từ khoá quá chung (vd. "Ngân hàng Nhà nước") chỉ tính khi bài có nhắc tới vàng
    requires_gold: bool = False
    # Cụm từ chứa từ khoá của nhóm nhưng thuộc nghĩa khác, bị gạch khỏi văn bản trước khi xét nhóm này
    exclude: tuple[str, ...] = ()
    # Nếu có: ngoài `keywords`, bài còn phải chứa ít nhất một cụm trong đây
    also: tuple[str, ...] = ()


DRIVERS = (
    Driver(
        key="geopolitics", label="Chiến sự, xung đột",
        keywords=(
            "chiến tranh", "xung đột", "chiến sự", "không kích", "tập kích", "chiến dịch quân sự",
            "tên lửa", "ngừng bắn", "hòa đàm", "đình chiến", "địa chính trị", "trung đông",
            "ukraine", "israel", "iran", "gaza", "đài loan", "triều tiên",
            "war", "conflict", "airstrike", "airstrikes", "missile", "missiles", "ceasefire",
            "truce", "geopolitical", "invasion", "middle east", "taiwan", "north korea",
        ),
        up=(
            "chiến tranh", "không kích", "tập kích", "tấn công", "leo thang", "tên lửa", "căng thẳng",
            "chiến dịch quân sự", "war", "airstrike", "airstrikes", "missile", "missiles", "escalation",
            "escalates", "escalating", "invasion", "attack", "attacks", "tensions",
        ),
        down=(
            "ngừng bắn", "ngừng tập kích", "ngừng tấn công", "hòa đàm", "hạ nhiệt", "thỏa thuận hòa bình",
            "đình chiến", "ceasefire", "truce", "peace deal", "peace talks", "de-escalation",
        ),
        up_reason="Căng thẳng địa chính trị leo thang làm tăng nhu cầu trú ẩn vào vàng.",
        down_reason="Căng thẳng hạ nhiệt làm giảm nhu cầu trú ẩn, gây áp lực lên giá vàng.",
        neutral_reason="Diễn biến địa chính trị chi phối nhu cầu trú ẩn vào vàng.",
        exclude=("chiến tranh thương mại", "chiến tranh giá", "trade war", "price war"),
    ),
    Driver(
        key="us_policy", label="Nhà Trắng & thương mại",
        keywords=(
            "nhà trắng", "tổng thống mỹ", "trump", "thuế quan", "áp thuế", "sắc lệnh", "đóng cửa chính phủ",
            "trần nợ", "chiến tranh thương mại", "trừng phạt", "white house", "tariff", "tariffs",
            "executive order", "government shutdown", "shutdown", "debt ceiling", "trade war", "sanctions",
        ),
        up=(
            "áp thuế", "thuế quan", "đóng cửa chính phủ", "chiến tranh thương mại", "trừng phạt", "đe dọa",
            "trần nợ", "tariff", "tariffs", "shutdown", "trade war", "sanctions", "threatens", "debt ceiling",
        ),
        down=(
            "thỏa thuận thương mại", "đạt thỏa thuận", "hoãn áp thuế", "giảm thuế quan", "dỡ bỏ trừng phạt",
            "trade deal", "tariff pause", "tariff relief", "reopens",
        ),
        up_reason="Chính sách Mỹ gây bất ổn (thuế quan, trừng phạt, bế tắc ngân sách) thường đẩy dòng tiền sang vàng.",
        down_reason="Bất ổn chính sách và thương mại dịu đi làm giảm sức hấp dẫn trú ẩn của vàng.",
        neutral_reason="Quyết định từ Nhà Trắng ảnh hưởng tới USD và tâm lý rủi ro, qua đó tới giá vàng.",
    ),
    Driver(
        key="fed", label="Fed & lãi suất",
        keywords=(
            "fed", "fomc", "powell", "cục dự trữ liên bang", "federal reserve", "lãi suất mỹ",
            "chính sách tiền tệ mỹ", "ecb", "boj", "rate cut", "rate cuts", "rate hike", "rate hikes",
        ),
        up=(
            "hạ lãi suất", "giảm lãi suất", "cắt giảm lãi suất", "nới lỏng", "rate cut", "rate cuts",
            "cut rates", "cuts rates", "dovish", "easing",
        ),
        down=(
            "tăng lãi suất", "nâng lãi suất", "thắt chặt", "giữ lãi suất cao", "rate hike", "rate hikes",
            "hawkish", "tightening", "higher for longer",
        ),
        up_reason="Kỳ vọng lãi suất giảm làm giảm chi phí cơ hội nắm giữ vàng, hỗ trợ giá.",
        down_reason="Lãi suất cao hơn làm tăng chi phí cơ hội nắm giữ vàng, gây áp lực lên giá.",
        neutral_reason="Định hướng lãi suất của Fed là biến số chính với giá vàng.",
    ),
    Driver(
        key="us_data", label="Kinh tế Mỹ",
        keywords=(
            "lạm phát", "cpi", "pce", "bảng lương phi nông nghiệp", "việc làm mỹ", "thất nghiệp", "suy thoái",
            "inflation", "nonfarm", "non-farm", "payrolls", "jobless", "unemployment", "recession",
        ),
        up=("suy thoái", "thất nghiệp tăng", "recession", "weak jobs", "jobless claims rise"),
        down=("việc làm tăng mạnh", "strong jobs", "strong payrolls"),
        up_reason="Kinh tế Mỹ yếu đi làm tăng kỳ vọng nới lỏng tiền tệ và nhu cầu trú ẩn.",
        down_reason="Kinh tế Mỹ mạnh làm giảm kỳ vọng hạ lãi suất, bất lợi cho vàng.",
        neutral_reason="Số liệu lạm phát và việc làm Mỹ định hướng kỳ vọng lãi suất của Fed.",
    ),
    Driver(
        key="usd", label="Đồng USD",
        keywords=(
            "dxy", "chỉ số usd", "chỉ số đô la", "đồng usd", "đồng đô la", "giá usd", "dollar index",
            "lợi suất trái phiếu", "treasury yields", "bond yields", "softer usd", "weaker dollar",
            "stronger dollar",
        ),
        up=(
            "usd suy yếu", "usd giảm", "đô la suy yếu", "lợi suất giảm", "weaker dollar", "softer usd",
            "dollar weakens", "dollar falls", "yields fall",
        ),
        down=(
            "usd mạnh lên", "usd tăng", "đô la mạnh", "lợi suất tăng", "stronger dollar", "firmer usd",
            "dollar strengthens", "dollar rises", "yields rise",
        ),
        up_reason="USD và lợi suất yếu đi khiến vàng (định giá bằng USD) hấp dẫn hơn.",
        down_reason="USD và lợi suất mạnh lên khiến vàng đắt hơn với người mua ngoài Mỹ.",
        neutral_reason="Vàng thường đi ngược chiều USD và lợi suất trái phiếu Mỹ.",
    ),
    Driver(
        key="gold_demand", label="Các nước & quỹ mua bán vàng",
        keywords=(
            "dự trữ vàng", "mua vàng", "bán vàng", "quỹ etf", "etf vàng", "spdr", "ngân hàng trung ương",
            "gold reserves", "central bank", "central banks", "gold etf", "etf inflows", "etf outflows", "pboc",
        ),
        up=("mua vàng", "mua ròng", "gom vàng", "tăng dự trữ", "inflows", "etf inflows", "buying"),
        down=("bán vàng", "bán ròng", "giảm dự trữ", "outflows", "etf outflows", "selling"),
        up_reason="Lực mua từ ngân hàng trung ương và quỹ ETF nâng đỡ giá vàng.",
        down_reason="Ngân hàng trung ương hoặc quỹ ETF bán ra gây áp lực lên giá vàng.",
        neutral_reason="Dòng tiền của ngân hàng trung ương và quỹ ETF ảnh hưởng cung cầu vàng.",
        requires_gold=True,
    ),
    Driver(
        key="vn_policy", label="Chính sách vàng trong nước",
        keywords=(
            "ngân hàng nhà nước", "nhnn", "nghị định 24", "nhập khẩu vàng", "đấu thầu vàng",
            "độc quyền vàng miếng", "sàn giao dịch vàng", "thuế vàng", "quản lý thị trường vàng", "hạn ngạch",
        ),
        up=("siết", "hạn chế", "khan hiếm", "thiếu nguồn cung", "đánh thuế"),
        down=("nhập khẩu vàng", "tăng cung", "đấu thầu vàng", "bỏ độc quyền", "cấp phép", "bình ổn"),
        up_reason="Nguồn cung trong nước bị siết làm giá vàng nội và chênh lệch với thế giới tăng.",
        down_reason="Tăng nguồn cung trong nước giúp thu hẹp chênh lệch, kéo giá vàng nội xuống.",
        neutral_reason="Chính sách quản lý vàng trong nước ảnh hưởng nguồn cung và chênh lệch với giá thế giới.",
        requires_gold=True,
    ),
    Driver(
        key="vn_market", label="Thị trường vàng trong nước",
        # Bài phân tích cung cầu, nhận định — không phải bảng giá
        keywords=(
            "chuyên gia", "dự báo", "nhận định", "nguyên nhân", "vì sao", "nguồn cung", "khan hiếm", "xếp hàng",
            "mua gom", "chênh lệch", "nhu cầu", "bán tháo", "chốt lời", "đầu cơ", "thanh tra", "buôn lậu",
        ),
        also=(
            "trong nước", "sjc", "vàng nhẫn", "vàng miếng", "nhẫn trơn", "việt nam", "doji", "pnj", "bảo tín",
            "tiệm vàng", "ngân hàng nhà nước", "nhnn",
        ),
        up=(
            "khan hiếm", "xếp hàng", "mua gom", "dự báo tăng", "nguồn cung hạn chế", "thiếu nguồn cung",
            "nhu cầu tăng", "đầu cơ", "tăng mạnh", "lập đỉnh", "kỷ lục",
        ),
        down=(
            "bán tháo", "chốt lời", "dự báo giảm", "nguồn cung tăng", "hạ nhiệt", "nhu cầu giảm",
            "thu hẹp chênh lệch", "giảm mạnh", "lao dốc",
        ),
        up_reason="Sức mua trong nước mạnh hoặc nguồn cung hạn chế đẩy giá vàng nội lên.",
        down_reason="Lực bán hoặc nguồn cung tăng làm giá vàng trong nước hạ nhiệt.",
        neutral_reason="Nhận định về cung cầu vàng trong nước, ảnh hưởng trực tiếp tới giá vàng nhẫn.",
        requires_gold=True,
    ),
)
BY_KEY = {driver.key: driver for driver in DRIVERS}


@dataclass
class Impact:
    drivers: list[str]   # khoá các nhóm yếu tố khớp, theo thứ tự ưu tiên
    direction: str       # up / down / unclear — tác động ước đoán lên giá vàng
    level: str           # high / medium / low — độ rõ của tín hiệu, không phải mức biến động giá
    reason: str


def labels(keys: list[str]) -> list[str]:
    return [BY_KEY[key].label for key in keys if key in BY_KEY]


def analyze(text: str, title: str = "") -> Impact | None:
    """None nếu bài không chứa yếu tố nào (vd. bài chỉ tường thuật giá vàng hôm nay)."""
    if title and (is_other_instrument(title) or is_price_report(title)):
        return None
    lowered = text.lower()
    gold = sentiment.is_gold_related(text)
    matched: list[tuple[Driver, int]] = []
    for driver in DRIVERS:
        if driver.requires_gold and not gold:
            continue
        scoped = lowered
        for phrase in driver.exclude:
            scoped = scoped.replace(phrase, " ")
        if sentiment.count_phrases(scoped, driver.keywords) == 0:
            continue
        if driver.also and sentiment.count_phrases(scoped, driver.also) == 0:
            continue
        up, down = sentiment.score_counts(scoped, {"pos": driver.up, "neg": driver.down})
        matched.append((driver, up - down))
    if not matched:
        return None

    total = sum(vote for _, vote in matched)
    keys = [driver.key for driver, _ in matched]
    if total == 0:
        return Impact(keys, "unclear", "low", matched[0][0].neutral_reason)
    # Nhiều tín hiệu cùng chiều hoặc nhiều nhóm yếu tố cùng xuất hiện thì coi là tín hiệu rõ
    level = "high" if abs(total) >= 2 or len(matched) >= 2 else "medium"
    # Lời giải thích lấy từ yếu tố có tín hiệu mạnh nhất cùng chiều với kết luận
    lead = max(matched, key=lambda item: item[1] if total > 0 else -item[1])[0]
    if total > 0:
        return Impact(keys, "up", level, lead.up_reason)
    return Impact(keys, "down", level, lead.down_reason)
