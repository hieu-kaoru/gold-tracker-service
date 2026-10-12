"""Chấm cảm xúc tin tức bằng từ điển, chạy cục bộ và miễn phí.

Điểm thể hiện tác động ước đoán lên GIÁ VÀNG (dương = hỗ trợ giá tăng), không phải
cảm xúc chung của bài viết. Đây là mức khởi đầu thô; có thể thay bằng underthesea
hoặc PhoBERT sau mà không đổi chữ ký hàm `analyze`.
"""
import re

GOLD_KEYWORDS = (
    "vàng", "sjc", "kim loại quý", "gold", "xau", "bullion", "precious metal",
)

LEXICON = {
    "vi": {
        "pos": (
            "tăng", "tăng vọt", "tăng mạnh", "lập đỉnh", "kỷ lục", "bứt phá", "đi lên", "leo thang",
            "phục hồi", "khởi sắc", "cao nhất", "trú ẩn", "mua ròng", "gom mua", "hạ lãi suất",
            "giảm lãi suất", "nới lỏng", "bất ổn", "căng thẳng", "usd suy yếu", "đô la suy yếu",
        ),
        "neg": (
            "giảm", "giảm mạnh", "lao dốc", "bán tháo", "chốt lời", "đi xuống", "mất mốc", "rơi",
            "thấp nhất", "hạ nhiệt", "điều chỉnh", "bán ròng", "tăng lãi suất", "nâng lãi suất",
            "thắt chặt", "usd mạnh lên", "đô la mạnh lên", "sụt",
        ),
    },
    "en": {
        "pos": (
            "rise", "rises", "rally", "rallies", "surge", "surges", "gain", "gains", "climb", "climbs",
            "jump", "jumps", "record high", "all-time high", "bullish", "safe haven", "safe-haven",
            "rate cut", "rate cuts", "dovish", "weaker dollar", "uncertainty", "tensions", "rebound",
        ),
        "neg": (
            "fall", "falls", "drop", "drops", "slump", "slumps", "decline", "declines", "plunge",
            "plunges", "tumble", "tumbles", "sell-off", "selloff", "bearish", "profit-taking",
            "rate hike", "rate hikes", "hawkish", "stronger dollar", "retreat", "retreats", "slide", "slides",
        ),
    },
}
THRESHOLD = 0.2


def count_phrases(text: str, phrases: tuple[str, ...]) -> int:
    return sum(len(re.findall(rf"(?<!\w){re.escape(p)}(?!\w)", text)) for p in phrases)


def score_counts(text: str, lexicon: dict[str, tuple[str, ...]]) -> tuple[int, int]:
    """Khớp cụm dài trước rồi xoá khỏi văn bản, để "tăng lãi suất" không bị tính thêm là "tăng"."""
    tagged = [(p, "pos") for p in lexicon["pos"]] + [(p, "neg") for p in lexicon["neg"]]
    counts = {"pos": 0, "neg": 0}
    for phrase, polarity in sorted(tagged, key=lambda item: -len(item[0])):
        text, hits = re.subn(rf"(?<!\w){re.escape(phrase)}(?!\w)", " ", text)
        counts[polarity] += hits
    return counts["pos"], counts["neg"]


def is_gold_related(text: str) -> bool:
    return count_phrases(text.lower(), GOLD_KEYWORDS) > 0


def analyze(text: str, lang: str = "vi") -> tuple[float, str]:
    """Trả về (điểm trong [-1, 1], nhãn positive/negative/neutral)."""
    lexicon = LEXICON.get(lang, LEXICON["en"])
    pos, neg = score_counts(text.lower(), lexicon)
    if pos + neg == 0:
        return 0.0, "neutral"
    score = round((pos - neg) / (pos + neg), 3)
    label = "positive" if score >= THRESHOLD else "negative" if score <= -THRESHOLD else "neutral"
    return score, label
