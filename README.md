# Gold Tracker Service — backend theo dõi giá vàng nhẫn SJC 9999

Backend Python (FastAPI + SQLAlchemy/SQLite + APScheduler + scikit-learn) cho ứng dụng cá nhân, **phi thương mại**,
theo dõi giá **vàng nhẫn SJC 9999**: thu thập giá và tin tức, dự báo hướng giá bằng mô hình máy học, và đối chiếu
dự báo với thực tế. App Android dùng API này nằm ở repo [gold-tracker](https://github.com/hieu-kaoru/gold-tracker).

Dự báo **chỉ để tham khảo, không phải khuyến nghị đầu tư**.

## Chạy

```bash
python3 -m venv .venv            # cần gói python3-venv; xem ghi chú bên dưới nếu thiếu
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Khi khởi động, scheduler chạy nền ngay một lượt (nạp ~1 năm nến ngày XAU/USD, lấy giá, lấy tin),
sau đó lặp lại: giá mỗi 10 phút, tin mỗi 30 phút. Tài liệu API tự sinh ở `http://localhost:8000/docs`.

Nếu máy không có `python3-venv` và không muốn dùng `sudo apt install python3.12-venv`:

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
```

Lưu ý khi chạy:

- Chỉ chạy **một tiến trình** (không dùng `--workers` > 1): scheduler nằm trong tiến trình web, nhiều tiến trình
  sẽ thu thập trùng và ghi đè lẫn nhau.
- API không có xác thực và chỉ phục vụ đọc; `--host 0.0.0.0` mở nó cho cả mạng LAN. Dùng `--host 127.0.0.1`
  nếu chỉ cần cho emulator trên cùng máy.
- Ngày nào backend không chạy thì không có giá của ngày đó; mô hình coi các ngày thiếu là "giữ giá", nên
  khoảng trống dài sẽ làm lệch dữ liệu huấn luyện.

## Test

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pyflakes app tests   # kiểm tra import/biến thừa
```

Test chạy offline (parser dùng fixture, DB SQLite tạm, scheduler tắt).

## API

| Endpoint | Mô tả |
|---|---|
| `GET /prices/latest` | Giá mua/bán mới nhất của nhẫn SJC 9999 (`SJC_RING`), kèm thay đổi so với phiên trước |
| `GET /prices/history?days=30&interval=daily\|raw` | Lịch sử giá nhẫn |
| `GET /prices/day?date=YYYY-MM-DD` | Giá nhẫn chốt của một ngày (giờ VN), các lần cập nhật trong ngày, thay đổi so với ngày có giá liền trước; 404 kèm thông báo tiếng Việt nếu không có dữ liệu |
| `GET /spread` | Chênh lệch giá bán nhẫn SJC so với giá thế giới quy đổi (VND/lượng) |
| `GET /news?limit=30&kind=drivers\|gold\|all&region=domestic\|world&driver=fed&lang=vi\|en` | Mặc định (`kind=drivers`) chỉ trả tin là **tác nhân** làm giá vàng biến động, kèm nhóm yếu tố, chiều tác động (`impact`: up/down/unclear), độ rõ tín hiệu và lời giải thích |
| `GET /forecast?horizon=1\|7` | Dự báo của mô hình máy học: hướng, xác suất từng hướng, lời giải thích bằng tiếng Việt cho khung 1 hoặc 7 ngày |
| `GET /forecast/history?days=60` | Bảng đối chiếu dự báo với thực tế theo ngày (khung 1 và 7 ngày), tỉ lệ đúng, và thông tin mô hình máy học đang dùng |
| `GET /health` | Trạng thái lần chạy gần nhất của từng collector |

Quy ước: giá vàng trong nước là **VND/lượng**; `price` = giá bán ra (trong nước) hoặc giá khớp
(thế giới); thời gian là ISO 8601 UTC (hậu tố `Z`).

## Nguồn dữ liệu

| Dữ liệu | Nguồn | Ghi chú |
|---|---|---|
| Nhẫn SJC 9999 | 1) sjc.com.vn → 2) giavang.org → 3) baomoi.com (trang tiện ích giá vàng) | sjc.com.vn đứng sau Cloudflare challenge nên bị từ chối (HTTP 403); backend **không tìm cách vượt**, tự chuyển sang nguồn kế tiếp. Trường `source` cho biết nguồn thực tế. |
| Lịch sử nhẫn SJC theo ngày | (không có nguồn nạp bù) | 323 ngày đầu (23/11/2025–11/10/2026) đã nạp một lần từ API vang.today trước khi phát hiện `robots.txt` của họ ghi `Disallow: /api/`; việc nạp tự động đã tắt. Đã dò VnExpress, CafeF, 24h, Thanh Niên, Tuổi Trẻ, Báo Mới: các nơi này chỉ có lịch sử vàng miếng SJC hoặc nhẫn PNJ, hoặc không cho lấy theo ngày (Báo Mới chặn `?date=`). Ngày cũ hơn có thể tra lẻ ở giavang.org (chỉ vài giai đoạn có giá nhẫn). |
| XAU/USD (tham chiếu) | yfinance, ticker `GC=F`: nến ngày (1 năm) và nến giờ (~2 năm, cập nhật mỗi lượt lấy giá) | Yahoo không có `XAUUSD=X`; `GC=F` là hợp đồng tương lai COMEX, lệch nhẹ so với giá giao ngay. |
| USD/VND (tham chiếu) | XML công khai của Vietcombank | Nguồn yêu cầu tối đa 1 request / 5 phút. `buy` = mua chuyển khoản, `sell`/`price` = bán. |
| Tin tức | RSS tiếng Việt: VnExpress Kinh doanh, VnExpress Thế giới, VnEconomy, Dân Trí, Tuổi Trẻ, CafeF. RSS tiếng Anh: BBC Business, BBC World, CNBC (Top News, Economy), The Guardian Business | `robots.txt` của các nguồn này đều cho phép các đường dẫn RSS đang dùng. FXStreet (chặn kết nối) và Kitco (không còn RSS) đã bị bỏ. Feed nào lỗi sẽ hiện ở `/health`. |

## Cảm xúc tin tức và dự báo

- **Tin tác động** (`app/services/drivers.py`): mỗi bài được xếp vào các nhóm yếu tố bằng từ khoá Việt/Anh
  chạy cục bộ — Địa chính trị, Nhà Trắng & thương mại, Fed & lãi suất, Lạm phát & kinh tế Mỹ, USD & lợi suất,
  Các nước & quỹ mua bán vàng, Chính sách vàng trong nước, Thị trường vàng trong nước (bài phân tích cung cầu,
  nhận định). Hai nhóm cuối là tin "trong nước", còn lại là "thế giới". Bài không khớp nhóm nào, bài bảng giá
  trong ngày ("giá vàng hôm nay…") và bài bình luận tài sản khác (cặp tiền, dầu, bạc) bị loại. Chiều tác động suy
  từ cụm từ leo thang/hạ nhiệt, nới lỏng/thắt chặt... theo quy tắc kinh nghiệm; **không hiểu ngữ cảnh**
  (phủ định, hoài nghi, mỉa mai) nên có thể sai — chỉ để tham khảo. Tin đã lưu được chấm lại mỗi lần khởi động.
- **Cảm xúc**: từ điển cụm từ Việt/Anh chạy cục bộ, miễn phí (`app/services/sentiment.py`), chấm theo
  tác động ước đoán lên giá vàng. Đây là mức khởi đầu thô; có thể thay bằng underthesea/PhoBERT mà
  không đổi chữ ký hàm `analyze`. Không dùng LLM trả phí.
- **Dự báo bằng mô hình máy học** (`app/services/ml.py`, scikit-learn): phân loại 3 lớp — sau 1 hoặc 7 ngày giá
  nhẫn tăng / đi ngang / giảm (đi ngang = biến động dưới 0,15% với khung 1 ngày, 0,5% với khung 7 ngày).
  - **Dữ kiện đầu vào** (17 đặc trưng mỗi ngày): thay đổi giá nhẫn 1/3/7/14 ngày, khoảng cách tới trung bình 10
    và 30 ngày, RSI, MACD, độ dao động 10 ngày, số ngày đứng giá liên tiếp, thay đổi giá vàng thế giới 1/3/7 phiên,
    biến động vàng thế giới trong chính ngày đó theo giờ Việt Nam (24 giờ tính tới 23h, và riêng đoạn 17h–23h là
    lúc SJC đã ngừng đổi giá), chênh lệch trong nước/thế giới so với mức quen thuộc, và ngày đích có rơi vào
    cuối tuần không.
  - **Không nhìn trước tương lai**: đặc trưng của ngày t chỉ dùng dữ liệu tới hết ngày t (phiên vàng thế giới đề
    ngày t chỉ được dùng từ ngày t+1); khi kiểm tra lùi, mô hình đoán ngày t chỉ học từ các ngày đã lộ kết quả.
  - **Chọn mô hình**: so sánh hồi quy logistic, rừng ngẫu nhiên và tăng cường gradient bằng kiểm tra lùi
    (walk-forward, huấn luyện lại mỗi 5 ngày), chọn cái đúng nhiều nhất cho từng khung, rồi học lại trên toàn bộ
    dữ liệu. Tự huấn luyện lại khi có thêm ngày mới (mất vài chục giây, giữ trong bộ nhớ).
  - **Tin tức 24 giờ** không phải đặc trưng của mô hình (không có kho tin cũ để học); khi có từ 3 tin tác động có
    chiều rõ, nó dịch tối đa 10 điểm phần trăm xác suất giữa "tăng" và "giảm" (`news_used`).
  - **Kết quả trả về**: `direction`, `confidence` (xác suất của hướng được chọn), `probabilities`, `summary` và
    `reasons` bằng lời thường, `technical_details` (mô hình, kết quả kiểm tra, đặc trưng ảnh hưởng nhất),
    `model` (`ml:<tên>` hoặc `rules`).
  - **Dự phòng**: khi chưa đủ ~105 ngày có kết quả để huấn luyện, dùng công thức cộng điểm MA/RSI/MACD cũ
    (`model = rules`), và nếu giá nhẫn cũng thiếu thì suy từ XAU/USD.
  - **Chỉ để tham khảo, không phải khuyến nghị đầu tư.**

## Đối chiếu dự báo với thực tế

- **Lưu dự báo** (`app/services/tracking.py`): mỗi lượt lấy giá, dự báo 1 và 7 ngày của hôm nay được ghi vào bảng
  `forecast_records` (bản cuối ngày là bản được đối chiếu, `source = live`). Các ngày trước đó là kết quả kiểm tra
  lùi của mô hình đang dùng (`source = backfill`), dựng lại mỗi lần khởi động.
- **Đối chiếu** (`app/services/learning.py`): thực tế = giá bán nhẫn sau 1/7 ngày so với giá ngày dự báo.
- **Cách chấm điểm**: 60 ngày gần nhất được **để riêng** — mô hình được chọn bằng kết quả kiểm tra lùi của giai
  đoạn trước đó, rồi mới chấm trên 60 ngày này. Mọi con số đều đặt cạnh "cách đoán đơn giản" (lúc nào cũng đoán
  hướng hay gặp nhất trong dữ liệu đã biết lúc đó).
- **Kết quả tại thời điểm viết (12/10/2026, 324 ngày lịch sử)**:

  | Khung | Mô hình được chọn | Mô hình (60 ngày để riêng) | Đoán đơn giản (60 ngày) | Mô hình (toàn bộ thử lại) |
  |---|---|---|---|---|
  | 1 ngày | rừng ngẫu nhiên | 53,3% | 48,3% (luôn "đi ngang") | 57,2% / 194 ngày |
  | 7 ngày | rừng ngẫu nhiên | 40,0% | 50,0% (luôn "giảm") | 46,6% / 178 ngày |

  Khung 1 ngày hơn cách đoán đơn giản khoảng 5 điểm; khung 7 ngày **chưa** vượt được. Trước khi thêm hai dữ kiện
  "vàng thế giới trong ngày" (`x_day`, `x_late`), cùng cách chấm cho 43,3% và 31,7%. Dữ liệu chỉ ~1 năm và 60
  ngày là mẫu nhỏ (chênh 5 điểm ≈ 3 ngày) nên kết luận còn dao động. App hiển thị cả hai con số để người dùng
  tự so. Dự báo trong ngày chỉ đầy đủ dữ kiện sau khoảng 23h (giờ VN); bản lưu cuối ngày là bản được đối chiếu.

## Cấu hình (biến môi trường)

`GT_DATABASE_URL`, `GT_SCHEDULER` (0 để tắt), `GT_RING_HISTORY_BACKFILL` (1 để bật nạp bù lịch sử từ vang.today), `GT_PRICE_INTERVAL_MINUTES`,
`GT_NEWS_INTERVAL_MINUTES`, `GT_HTTP_TIMEOUT`, `GT_USER_AGENT` — xem `app/config.py`.

## Dữ liệu không nằm trong repo

File SQLite (`gold_tracker.db`) bị loại khỏi git: nó chứa dữ liệu thu thập từ các nguồn bên thứ ba. Chạy lần đầu,
backend tự tạo DB trống và bắt đầu thu thập; mô hình máy học cần khoảng 105 ngày giá nhẫn mới huấn luyện được,
trước đó API dùng công thức dự phòng.

## Chưa làm

Lưu offline phía app cho biểu đồ, dự báo, tin tức; mô hình chấm chiều tin tức tốt hơn; cảnh báo giá.
