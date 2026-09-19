# Hướng dẫn vận hành Smart Hub Local Dashboard
(Broadlink RM4 mini, Thu âm và Wake Word Model)

Ngày: **2026-09-19**  
Nhánh triển khai: `codex/local-dashboard-broadlink`  
Tài liệu kế hoạch gốc: [broadlink-wake-dashboard-plan.md](broadlink-wake-dashboard-plan.md)

---

## 1. Giới thiệu

Smart Hub Local Dashboard là giao diện web chạy hoàn toàn độc lập với runtime `assistant`, cung cấp 3 nhóm tính năng cốt lõi:

1. **Broadlink & Điều khiển IR:**
   - Quét LAN UDP hoặc kết nối trực tiếp IP tới RM4 mini.
   - Quản lý danh mục thiết bị gia dụng (TV, Quạt, Điều hòa).
   - Chọn bộ mã từ Catalog chuẩn (tương thích SmartIR và Broadlink IR) hoặc học trực tiếp từ remote vật lý gốc.
   - Thử từng nút, lưu bộ điều khiển và ghi nhận quan sát thực tế (*Đúng chức năng*, *Sai/Không phản hồi*, *Chưa rõ*).
   - Cơ chế Command Ledger chống gửi lặp (idempotent request ID) và tự động phục hồi sau restart.

2. **Thu âm mẫu câu gọi (Adult & Child Voice Study):**
   - Thiết lập phiên thu: Người lớn / Trẻ em, Split (`pilot`, `dev`, `test`), nhãn câu gọi (`positive` 'Maika ơi' hoặc 10 câu âm tính preset).
   - Chế độ chủ động (*Manual Advance*): Chờ người điều khiển bấm *"Bắt đầu lượt tiếp theo"* để người nói chuẩn bị thoải mái trước tiếng tít.
   - Đồng bộ tiếng tít và thu 5.0 giây chuẩn PCM mono 16kHz 16-bit.
   - Giám sát clipping và tự động ngắt phiên an toàn nếu clipping > 1% (không tự ý chỉnh gain).

3. **Wake Word Lab & Đánh giá Offline:**
   - Quản lý các candidate model: Baseline STT Tiếng Việt (*Standard* và *Sensitive*), DTW.
   - Tạo candidate mới từ các mẫu đã duyệt (*accepted* + *speaker_confirmed=True*). Tự động chặn mẫu thuộc tập `test` để tránh rò rỉ dữ liệu kiểm thử.
   - Chạy so sánh offline (benchmark) trên cùng snapshot dữ liệu, xuất bảng so sánh chi tiết và báo cáo Markdown.

---

## 2. Yêu cầu & Cài đặt môi trường

Môi trường web dashboard sử dụng `.venv-dashboard` độc lập để không gây xung đột với môi trường audio/STT runtime (`.venv`).

### Cài đặt dependencies

```bash
# Kiểm tra / tạo môi trường .venv-dashboard (Python 3.11)
.venv/bin/virtualenv .venv-dashboard

# Cài đặt các gói phụ thuộc được pin trong requirements-dashboard.txt
.venv-dashboard/bin/pip install -r requirements-dashboard.txt
```

Nội dung gói trong [requirements-dashboard.txt](../requirements-dashboard.txt):
- `fastapi==0.141.1`
- `uvicorn==0.53.0`
- `broadlink==0.19.0`
- `cryptography==50.0.1`
- `pydantic==2.13.5`
- `starlette==1.6.0`
- `httpx==0.28.1`

---

## 3. Khởi chạy Dashboard

Khởi chạy web server qua lệnh CLI `smart_hub.py`:

```bash
.venv-dashboard/bin/python scripts/smart_hub.py dashboard --host 127.0.0.1 --port 8765
```

Mở trình duyệt truy cập:
👉 **[http://127.0.0.1:8765](http://127.0.0.1:8765)**

*(Giao diện tiếng Việt, tự động tương thích desktop và màn hình điện thoại ~390px).*

---

## 4. Hướng dẫn sử dụng từng phân hệ

### 4.1. Phân hệ Broadlink RM4 mini

1. **Chuẩn bị phần cứng:**
   - Cấp nguồn cho RM4 mini và đưa thiết bị vào cùng mạng Wi-Fi 2.4GHz với máy Smart Hub (thực hiện qua app BroadLink nếu là thiết bị mới).
   - **Quan trọng:** Trong app BroadLink, vào mục cài đặt thiết bị RM4 mini và tắt tùy chọn **"Lock device"** (Khóa thiết bị) để cho phép điều khiển cục bộ qua mạng LAN.
2. **Kết nối Gateway trên Dashboard:**
   - Vào tab **Thiết bị & IR** → Bấm **"➕ Kết nối RM4 mini"**.
   - Chọn **"Quét trên LAN"** hoặc chuyển sang tab **"Nhập IP trực tiếp"** nếu broadcast UDP bị chặn.
   - Khi tìm thấy RM4, hệ thống hiển thị model (`0x6508...`), MAC, IP và trạng thái khóa.
   - Đặt tên hiển thị (ví dụ *RM4 Phòng Khách*), chọn phòng và bấm **"💾 Lưu Gateway"**.
3. **Thêm thiết bị & Ghép bộ điều khiển:**
   - Bấm **"➕ Thêm thiết bị gia dụng"**.
   - Chọn Gateway đã kết nối, chọn loại thiết bị (*Điều hòa / Quạt / TV*).
   - Chọn Hãng (ví dụ *Daikin, Panasonic, Senko, Casper...*) và bộ mã tương ứng từ Catalog.
   - Đặt tên thiết bị và bấm lưu. Bảng điều khiển từ xa sẽ xuất hiện với đầy đủ nút chức năng / preset.
4. **Thử nút & Ghi nhận phản hồi thực tế:**
   - Bấm một nút bất kỳ trên bảng điều khiển. Dashboard gửi mã IR qua RM4 và hiển thị kết quả: *"Đã gửi tới RM4 (nhận ACK)"*.
   - Quan sát thiết bị gia dụng thực tế và bấm:
     - **✅ Đúng chức năng**: Đánh dấu mã IR là *Đã xác minh (Verified)*.
     - **❌ Sai / Không phản hồi**: Ghi nhận lỗi quan sát.
     - **❓ Chưa rõ**: Lưu trạng thái chờ kiểm chứng lại.
5. **Học mã từ remote gốc (IR Learning):**
   - Bấm **"🎯 Học thêm nút IR"**.
   - Nhập khóa nút (ví dụ `power_toggle`, `speed_up`, `cool_auto_26c`) và tên hiển thị.
   - Bấm **"🎯 Bắt đầu học mã"** (RM4 sẽ chuyển sang chế độ học trong 30 giây).
   - Hướng remote gốc vào RM4 mini (khoảng cách 5-10cm) và nhấn nút 1 lần.
   - Dashboard tự động nhận mã, kiểm tra định dạng và tạo revision mới cho nút đó.

### 4.2. Phân hệ Thu âm (Voice Recording)

1. Vào tab **Thu âm**.
2. Thiết lập thông số:
   - **Người nói:** Trẻ em (`child`) hoặc Người lớn (`adult`).
   - **Mã người nói:** ví dụ `child_01`, `adult_01`.
   - **Split:** `pilot` (khám phá), `dev` (phát triển), hoặc `test` (giữ riêng cho kiểm thử).
   - **Câu nói:** `positive` ('Maika ơi') hoặc chọn 1 trong 10 câu `negative` preset.
   - **Số lượt:** Mặc định 5 lượt.
   - **Chế độ:** Tích chọn *Chế độ chủ động* (chờ người điều khiển bấm để bé chuẩn bị).
3. Bấm **"🚀 Bắt đầu phiên thu"**.
4. Máy Smart Hub ổn định mic trong 2.5s rồi chuyển sang trạng thái `WAITING_USER`.
5. Khi bé sẵn sàng, bấm **"▶️ Bắt đầu lượt tiếp theo"**:
   - Host phát tiếng tít ngắn qua loa.
   - Cắt đuôi tiếng tít và thu đúng 5.0 giây.
   - Hiển thị mức Peak, RMS, và Clipping %.
   - Nếu clipping > 1%, phiên tự động ngắt để nhắc kiểm tra mic gain.
6. Sau khi hoàn tất đủ các lượt, dữ liệu tự động lưu vào `recordings/<session_id>/` và cập nhật vào `recordings/child-study/`.

### 4.3. Phân hệ Dữ liệu & Duyệt thủ công (Manual Review)

> [!IMPORTANT]
> Quy tắc bất biến: Mẫu âm thanh chỉ được đưa vào tập đánh giá chính thức khi đã đạt chuẩn kiểm tra kỹ thuật (Technical Pass) **VÀ** được con người nghe lại xác nhận đúng người nói (`speaker_confirmed=True`) kèm nội dung câu nói thực tế.

1. Vào tab **Dữ liệu & Duyệt**.
2. Lọc danh sách theo trạng thái duyệt (*Chờ duyệt / Đã chấp nhận / Cần xem lại / Từ chối*).
3. Bấm **"Duyệt mẫu"** trên một dòng:
   - Nghe lại file WAV gốc qua trình phát HTML5.
   - Kiểm tra thông số QC (WAV 16kHz 16-bit mono, SHA-256, clipping).
   - Tích chọn **"Tôi xác nhận đúng người nói"**.
   - Xác nhận câu nói nghe được thực tế.
   - Bấm **"✅ Chấp nhận (Accepted)"**, **"⚠️ Cần xem lại"**, hoặc **"❌ Từ chối"**.

### 4.4. Phân hệ Wake Word Lab & Đánh giá Offline

1. Vào tab **Wake Word**:
   - Xem các candidate có sẵn: `cand_stt_standard` (STT chuẩn), `cand_stt_sensitive` (STT nhạy cho bé), `cand_dtw_baseline`.
   - Bấm **"➕ Tạo ứng viên từ mẫu đã duyệt"** để tạo reference candidate từ các mẫu accepted (hệ thống tự động khóa không cho chọn mẫu thuộc tập test).
2. Vào tab **Kết quả & So sánh**:
   - Bấm **"🔬 Chạy bài đánh giá mới"**.
   - Chọn tập dữ liệu (`dev`, `pilot`, hoặc `test`), chọn chế độ (`official` cho benchmark chuẩn mực).
   - Tích chọn các candidate cần so sánh.
   - Bấm **"Bắt đầu chạy đánh giá"**.
   - Xem bảng so sánh tổng hợp (Tỷ lệ nhận diện chung, riêng bé, riêng người lớn, tỷ lệ bỏ sót FRR, tỷ lệ báo nhầm FAR trên câu âm, RTF decode, lỗi xử lý).
   - Bấm **"📥 Tải báo cáo Markdown"** để xuất báo cáo chi tiết.

---

## 5. Danh sách kiểm chứng & Độ tin cậy (Test Parity)

Tất cả các bộ kiểm thử tự động của repo đều vượt qua:

```bash
# 1. 111 baseline mock tests (runtime, turn, keyword, feedback, assistant)
python3 scripts/run_tests.py --mock
-> OK (111 tests passed)

# 2. 34 child-study tests (schema, data integrity, audit review, evaluation)
python3 -m unittest tests/test_mock_child_study.py
-> OK (34 tests passed)

# 3. 10 device, catalog, and command ledger tests
python3 -m unittest tests/test_devices.py
-> OK (10 tests passed)

# 4. 2 cooperative lock tests
python3 -m unittest tests/test_locks.py
-> OK (2 tests passed)

# 5. 2 recording service state machine tests
python3 -m unittest tests/test_recording_service.py
-> OK (2 tests passed)

# 6. 4 wake lab and candidate tests
python3 -m unittest tests/test_wake_lab.py
-> OK (4 tests passed)

# 7. 8 dashboard API & security integration tests
.venv-dashboard/bin/python -m unittest tests/test_dashboard_api.py
-> OK (8 tests passed)
```

**Tổng cộng:** **171/171 tests PASS 100%**.
