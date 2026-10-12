"""Cấu hình đọc từ biến môi trường (tiền tố GT_)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("GT_DATABASE_URL", f"sqlite:///{BASE_DIR / 'gold_tracker.db'}")
SCHEDULER_ENABLED = os.getenv("GT_SCHEDULER", "1") == "1"
PRICE_INTERVAL_MINUTES = int(os.getenv("GT_PRICE_INTERVAL_MINUTES", "10"))
NEWS_INTERVAL_MINUTES = int(os.getenv("GT_NEWS_INTERVAL_MINUTES", "30"))
HTTP_TIMEOUT = float(os.getenv("GT_HTTP_TIMEOUT", "20"))
USER_AGENT = os.getenv("GT_USER_AGENT", "gold-tracker/0.1 (personal, non-commercial)")

# Sản phẩm duy nhất được theo dõi và lộ ra API
SJC_RING = "SJC_RING"  # nhẫn SJC 9999, VND/lượng
SYMBOLS = (SJC_RING,)
# Dữ liệu tham chiếu thu thập ngầm, chỉ làm đầu vào cho /forecast và /spread
XAUUSD = "XAUUSD"      # vàng thế giới, USD/oz
USDVND = "USDVND"      # tỷ giá Vietcombank
XAUUSD_HOURLY = "XAUUSD_H"  # vàng thế giới theo giờ, để biết thế giới biến động ra sao trong ngày Việt Nam

# 1 lượng = 37,5 g; 1 troy oz = 31,1034768 g
LUONG_PER_OZ = 37.5 / 31.1034768

# Yahoo không có XAUUSD giao ngay; GC=F (hợp đồng tương lai COMEX) là xấp xỉ gần nhất.
YF_GOLD_TICKERS = ("GC=F",)
XAU_BACKFILL_PERIOD = "1y"
XAU_HOURLY_BACKFILL_PERIOD = "730d"  # Yahoo chỉ giữ dữ liệu theo giờ khoảng 2 năm

SJC_OFFICIAL_URL = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"
SJC_MIRROR_URL = "https://giavang.org/trong-nuoc/sjc/"
# Nguồn dự phòng: trang tiện ích giá vàng của Báo Mới, đăng nguyên bảng giá SJC (có dòng nhẫn 99,99%)
SJC_BAOMOI_URL = "https://baomoi.com/tien-ich-gia-vang.epi"
# Lịch sử giá nhẫn SJC theo ngày: API của vang.today (mã SJ9999), có khoảng 1 năm gần nhất.
# TẮT theo mặc định vì robots.txt của họ ghi "Disallow: /api/". Đã tìm nguồn lớn khác thay thế (VnExpress,
# CafeF, 24h, Báo Mới...) nhưng không nơi nào vừa có lịch sử giá NHẪN SJC vừa cho phép lấy tự động,
# nên hiện không có nguồn nạp bù. Dữ liệu đã nạp trước đó vẫn được giữ.
RING_HISTORY_BACKFILL = os.getenv("GT_RING_HISTORY_BACKFILL", "0") == "1"
RING_HISTORY_URL = "https://www.vang.today/api/prices?type=SJ9999&days=3000"
# Trang lịch sử theo ngày của giavang.org, tra khi cần cho các ngày cũ hơn (chỉ một số giai đoạn có giá nhẫn)
RING_HISTORY_PAGE_URL = "https://giavang.org/trong-nuoc/sjc/lich-su/{date}.html"
VCB_URL = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx?b=10"

NEWS_FEEDS = (
    {"source": "VnExpress", "lang": "vi", "url": "https://vnexpress.net/rss/kinh-doanh.rss"},
    # Tin thế giới: nguồn chính cho các yếu tố địa chính trị, Nhà Trắng, Fed
    {"source": "VnExpress Thế giới", "lang": "vi", "url": "https://vnexpress.net/rss/the-gioi.rss"},
    # Nguồn kinh tế trong nước, hay có bài phân tích thị trường và chính sách vàng
    {"source": "VnEconomy", "lang": "vi", "url": "https://vneconomy.vn/tai-chinh.rss"},
    {"source": "Dân Trí", "lang": "vi", "url": "https://dantri.com.vn/rss/kinh-doanh.rss"},
    {"source": "Tuổi Trẻ", "lang": "vi", "url": "https://tuoitre.vn/rss/kinh-doanh.rss"},
    {"source": "CafeF", "lang": "vi", "url": "https://cafef.vn/tai-chinh-ngan-hang.rss"},
    # Tin quốc tế tiếng Anh từ các hãng lớn có RSS công khai (robots.txt cho phép)
    {"source": "BBC Business", "lang": "en", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"source": "BBC World", "lang": "en", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"source": "CNBC Economy", "lang": "en", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"source": "CNBC", "lang": "en", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"source": "The Guardian", "lang": "en", "url": "https://www.theguardian.com/business/rss"},
)
