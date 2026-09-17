# Runtime Phase 1 — nghe và phản hồi liên tục

**Cập nhật 2026-09-13:** theo yêu cầu kiểm tra lời nói sau wake, `assistant`
mặc định đã thêm cửa sổ nghe/log một câu. Xem [command-transcript.md](command-transcript.md)
cho hành vi mới. Dùng **`--wake-only`** để chạy đúng vòng Phase 1 mô tả bên dưới.

**Cập nhật 2026-09-14:** `assistant`/`--wake-only` dùng guard đáp 0,1 giây
mặc định, chỉnh qua `--reply-guard`; WAV đáp bỏ 0,239 giây im lặng cuối.
Các số đo Phase 1 ngày 2026-09-13 bên dưới là số liệu của bản trước.

Ngày 2026-09-13, người dùng xác nhận đã tự thử `listen-stt`: gọi “Maika ơi”
được và nghe “em nghe” ổn. Phase 1 dùng lại backend đó, thêm vòng chạy có
trạng thái và một luồng thu mic xuyên suốt. Chức năng của vòng `--wake-only` là
đánh thức và đáp WAV cố định. Không xử lý command, hội thoại hoặc thiết bị.

## Chạy

Máy hiện tại đã có môi trường và model. Từ thư mục project:

```bash
cd /home/mrhoan/source/wake-work/smart-hub
.venv/bin/python scripts/smart_hub.py assistant --wake-only
```

Đợi `[READY ASSISTANT]`, nói “Maika ơi” một lần, nghe “em nghe”, rồi chờ
khoảng 0,1 giây để chương trình trở lại nghe. Khi tự thử, nên cách mỗi lượt
ít nhất 5 giây. Ctrl+C hoặc SIGTERM sẽ dừng và đóng capture/playback.

```text
[READY ASSISTANT] Đang nghe ‘Maika ơi’; Ctrl+C để dừng.
[WAKE] detected at 2026-09-13 18:00:00
[WAKE] detected at 2026-09-13 18:00:06
```

Ví dụ trên minh họa định dạng log. Mỗi lần nhận đủ cụm tạo một event; “Maika”
đứng riêng chưa đủ. Quy tắc dấu tiếng Việt và alias `mai ca ơi`, `mai ka ơi`
giống `listen-stt`. Mặc định transcript wake không được in hay lưu;
`--show-wake-text` bật log để chẩn đoán. Chưa hỗ trợ nói liền
câu gọi với câu lệnh hoặc nói chen lúc loa đang đáp.

## Cài trên máy mới

Không có package mới riêng cho Phase 1. `asyncio`, thread và bộ đệm dùng thư
viện chuẩn Python; model và audio dùng lại các dependency của `listen-stt`.

```bash
python3 scripts/setup_env.py
.venv/bin/python -m pip install -r requirements-stt.txt
python3 scripts/download_stt_models.py
.venv/bin/python -m pip check
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
```

Hai package STT/VAD CPU là `sherpa-onnx` và `sherpa-onnx-core`; lý do, dung
lượng và phương án mẫu cá nhân nhẹ hơn ở [stt-wake.md](stt-wake.md). Chỉ cài
package và tải model cần mạng. Thiết bị mic/loa, WAV, cụm gọi và cooldown
lấy từ `config.json`; không đổi gain, routing hoặc volume.

## Tự kiểm thử

```bash
# Không dùng model, microphone, loa hoặc numpy: 3 chu kỳ giả lập
python3 scripts/smart_hub.py assistant --mock --wake-only
python3 scripts/run_tests.py --mock

# Runtime và STT thật trên các WAV fixture local, không mở mic/loa
.venv/bin/python scripts/run_tests.py --runtime

# Toàn bộ hồi quy, gồm cả hai listener trước đó
.venv/bin/python scripts/run_tests.py

# Kiểm tra capture/model 5 giây, tắt tiếng đáp riêng phiên này
.venv/bin/python scripts/smart_hub.py assistant --wake-only --seconds 5 --no-feedback --diagnostic

# Tự gọi đúng 10 lần trong 5 phút, có một khoảng nghỉ trên 60 giây
.venv/bin/python scripts/smart_hub.py assistant --wake-only --seconds 300 --expect-events 10 --diagnostic

# Không gọi cụm wake trong 30 phút, kiểm tra báo nhầm do tiếng nền
.venv/bin/python scripts/smart_hub.py assistant --wake-only --seconds 1800 --expect-events 0 --diagnostic
```

`--seconds` tính từ READY. Khi hết thời gian, chương trình hoàn tất câu đáp
đang phát. Khi bị ngắt sớm với `--expect-events`, kết quả là INCOMPLETE,
exit 130, kể cả số event đã khớp. Sai số lượng, clipping, mất audio do
quá tải/quá cũ hoặc lỗi engine/mic/loa trả exit 1. PASS kiểm đếm yêu cầu số
chu kỳ hoàn tất khớp số wake. `--no-feedback` không kiểm chứng âm thanh loa.

Chỉ chạy một listener tại một thời điểm. Có thể quay lại chế độ đã tự thử:

```bash
.venv/bin/python scripts/smart_hub.py listen-stt
```

## Bằng chứng và giới hạn

Kết quả Phase 1 ngày 2026-09-13, **trước khi thêm bước log câu lệnh**, trên
máy Debian hiện tại. Kết quả hồi quy mới nằm trong [command-transcript.md](command-transcript.md):

| Kiểm tra | Kết quả |
| --- | --- |
| Toàn bộ hồi quy, gồm model thật | **PASS 83/83**, không skip, 23,954 giây |
| Mock bằng Python hệ thống | **PASS 38/38**, không skip, 4,183 giây |
| `assistant --mock` | **PASS 3/3** wake → đáp giả lập → sleeping |
| Model STT thật trong runtime | **PASS** 3 câu gọi, 3 chu kỳ, câu âm tính không wake; chỉ một capture giả lập |
| Capture thật: `assistant --seconds 5 --no-feedback --diagnostic` | **PASS**, tới READY, model startup 0,80 giây; clipping 0, mất frame 0, queue đỉnh 13/25, tuổi frame lớn nhất quan sát 0,008 giây; exit 0 |
| Shutdown/lỗi | **PASS** SIGINT/SIGTERM lúc khởi động/nghe, kết quả STT muộn, worker timeout, lỗi capture/playback/cleanup |
| Kiểm tra định dạng mã | **PASS** compileall và `git diff --check` |

Test ngắt tác vụ tìm được trường hợp Ctrl+C trùng lúc worker hoàn thành làm
vòng nghe tiếp tục. Đã chuyển deadline sang `asyncio.timeout` trong cùng task
và kiểm tra cờ dừng; cả test tái hiện trực tiếp và test signal subprocess đều
PASS sau sửa. Lỗi cleanup capture cũng phải làm phiên thất bại.

Test giọng thật của người dùng áp dụng cho backend STT đã có; không suy ra
nhiều người/nhiễu đều đạt từ xác nhận đó. Lượt capture thật của `assistant`
không yêu cầu người dùng đọc mẫu và không lưu audio. Test fixture phát hiện
3 câu gọi và bỏ các câu âm tính trong cùng một capture; loa được giả lập.
Chưa có xác nhận nghe loa từ một phiên gọi thật qua lệnh `assistant` mới.

Các test async được chạy ngoài sandbox của công cụ phát triển vì sandbox
chặn `send()` trên socket nội bộ của asyncio. Không mở cổng mạng hay dùng
Internet trong các test này. Chạy từ terminal Debian thông thường như các
lệnh trên; không cần `sudo`.

## Quyết định triển khai

- `AudioFrame` chứa sequence, offset sample tuyệt đối, timestamp monotonic,
  metadata PCM và mã continuity. PCM được loại khỏi biểu diễn log mặc định.
- `AudioPump` giữ tối đa 25 frame/500 ms. `--buffer-ms` chọn bội số 20 trong
  20–500 ms. Khi đầy hoặc frame quá cũ, bỏ phần cũ, gộp `AudioGap` và reset
  bằng chứng VAD. Runtime vẫn đọc mic khi STT bận.
- Một worker xử lý VAD/STT, một worker xử lý playback. Mỗi worker chỉ nhận
  một tác vụ; không có hàng đợi tác vụ ẩn hoặc gọi `to_thread(read_frame)`
  cho từng frame. Callback từ capture sang loop được gộp, tối đa một chờ chạy.
- Kết quả từ session/generation/continuity cũ, frame đã dùng hoặc sau stop
  không phát wake. Lỗi capture truyền qua kênh riêng trước cleanup của mic.
- Trong playback và guard cấu hình bằng `--reply-guard` (mặc định 0,1 giây),
  audio chỉ được đọc rồi bỏ. Hết guard,
  bỏ thêm frame sát biên 20 ms rồi xóa hàng đợi, trước khi báo sẵn sàng
  và nạp VAD trở lại. Bước chờ này không cắt đầu câu nói sau LISTEN COMMAND.
- Startup model có deadline 15 giây; mỗi thao tác inference/playback 5 giây.
  Khi dừng, yêu cầu arecord kết thúc để mở khóa read, join capture tối đa
  8 giây và từng worker tối đa 5 giây. Cleanup thất bại là lỗi, không PASS.
  Thread native đang chạy không thể bị Python cưỡng bức hủy; khi quá hạn,
  loại kết quả muộn, báo FAIL và kết thúc CLI. Chưa có tự restart/backoff.

Chưa đo idle CPU/RAM dài hạn, độ trễ từ giọng người đến loa, tiếng TV/nhiễu
kéo dài hoặc khả năng phục hồi sau reboot. Phase 1B đã chọn bộ điều phối
lượt local ngày 2026-09-15, xem [quyết định và số đo](decisions/conversation-orchestration.md).
Phase 2 đã tích hợp chế độ [thu lượt hội thoại](turn-capture.md) ngày
2026-09-17; nghiệm thu giọng thật còn chờ. Luồng wake-only ở tài liệu này
vẫn giữ nguyên.
