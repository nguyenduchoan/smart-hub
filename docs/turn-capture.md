# Phase 2 — Thu trọn một lượt nói sau wake

**Ngày 2026-09-17:** đã tích hợp thu lượt vào `assistant --capture-only`.
Kiểm thử tự động đã đạt; nghiệm thu người nói thật vẫn để lại sau theo yêu
cầu của người dùng. Đây là chế độ thu lượt riêng. Mặc định `assistant` vẫn
in một câu STT theo luồng đã có.

## Chạy

Từ thư mục project, chỉ mở một listener:

```bash
.venv/bin/python scripts/smart_hub.py assistant --capture-only --diagnostic
```

1. Chờ `[READY ASSISTANT]`, gọi **“Maika ơi”**.
2. Nghe “em nghe”, chờ **`[LISTEN TURN]`** rồi nói một câu.
3. Ngừng nói khoảng 0,7 giây. Chương trình tự chốt, in thời lượng rồi chờ
   wake tiếp. Không cần Enter. Ctrl+C để dừng.

Ví dụ định dạng, không phải số đo giọng thật:

```text
[WAKE] detected at 2026-09-17 10:00:00
[LISTEN TURN] Hãy nói một câu; Maika tự chốt sau khoảng im lặng.
[TURN] captured 2.40 s
[READY ASSISTANT] Đang nghe ‘Maika ơi’; Ctrl+C để dừng.
```

Thời lượng gồm pre-roll và khoảng im lặng để chốt. Chế độ này chỉ thu một
lượt trong RAM và log thời lượng; chưa chuyển lượt sang STT, LLM, TTS động
hoặc thực thi thiết bị. Không hỗ trợ nói liền wake với câu lệnh hay nói chen
trong tiếng đáp; audio lúc phát đáp/guard vẫn bị bỏ.

## Thông số cấu hình

Thêm object `turn` vào bản sao `config.local.json`, rồi chạy với
`--config config.local.json` đặt **trước** `assistant`. Nếu không có object
này, dùng toàn bộ mặc định sau:

```json
{
  "turn": {
    "vad_threshold": 0.5,
    "preroll_seconds": 0.32,
    "min_speech_seconds": 0.25,
    "silence_seconds": 0.7,
    "max_seconds": 30,
    "wait_seconds": 8
  }
}
```

Ví dụ trên là cấu hình tối thiểu hợp lệ, các trường khác dùng mặc định.
Nếu cần giữ thiết bị/ngưỡng wake đã chỉnh, thêm `turn` vào bản sao cấu hình
hiện tại thay vì thay toàn bộ bằng ví dụ.

| Trường | Khoảng hợp lệ | Ý nghĩa |
| --- | --- | --- |
| `vad_threshold` | 0,01–0,99 | Ngưỡng xác suất giọng từ model |
| `preroll_seconds` | 0–0,8 s | Giữ audio trước điểm bắt đầu giọng |
| `min_speech_seconds` | 0,05–1 s | Xác nhận giọng đủ dài; mặc định 0,25 s |
| `silence_seconds` | 0,1–2 s | Im lặng liên tục để chốt; mặc định 0,7 s |
| `max_seconds` | 1–30 s | Giới hạn từ đầu giọng, gồm khoảng im lặng kết lượt |
| `wait_seconds` | 0,1–30 s | Chờ giọng sau LISTEN TURN; mặc định 8 s |

`max_seconds` phải lớn hơn tổng `min_speech_seconds + silence_seconds`.
VAD xử lý block 32 ms nên ngưỡng thời gian được xác nhận ở biên block tiếp
theo: mặc định 0,256 s giọng và 0,704 s im lặng. Đã giữ phần dư khi ghép
frame capture 20 ms, không padding từng frame hoặc bỏ sample.

Thời gian chờ nói bắt đầu sau tiếng đáp/guard. Khi đã phát hiện giọng, giới
hạn chuyển sang `max_seconds`, không còn bị cửa sổ chờ 8 giây cắt câu.
Deadline vẫn hoạt động khi không có frame mới hoặc worker đang bận.
`--command-seconds` chỉ chỉnh luồng log câu STT cũ, không chỉnh capture-only.

## Lượt không hoàn chỉnh và câu ngắn

- Không đủ giọng trước hạn: `[TURN TIMEOUT]`, quay lại chờ wake.
- Quá thời lượng: `[TURN INCOMPLETE] max_duration`, bỏ PCM và yêu cầu nói lại.
- Mất frame/overflow hoặc vỡ tiếng: hủy lượt với `audio_gap`/`clipping`.
- Dừng giữa câu: bỏ phần đang thu, không flush thành lượt hoàn chỉnh.

Chỉ lượt kết thúc bằng im lặng hợp lệ mới tạo `UserTurnReady`. Các lượt bị
hủy không mang PCM ra bước xử lý sau. Bộ đệm có giới hạn: trước khi xác
nhận giọng chỉ giữ pre-roll + giọng đang xác nhận + phần dư; khi thu lượt,
giới hạn khoảng 1 MiB với cấu hình tối đa. PCM không xuất hiện trong `repr`
của sự kiện và không tự lưu vào log/file.

Các tiếng ngắn như “có”, “không”, “dừng” dưới 0,25 s có thể bị bỏ qua với
mặc định. Test xác định rõ tiếng giả lập 0,096 s bị loại ở 0,25 s và được
nhận ở 0,05 s; đây không phải kết quả nhận đúng từ bằng giọng thật. Có thể
thử `min_speech_seconds: 0.1` khi nghiệm thu, đồng thời kiểm tra báo nhầm.
Ngập ngừng ngắn hơn ngưỡng im lặng vẫn thuộc cùng lượt; dài hơn có thể tách
câu. VAD không xác định ai đang nói hoặc lời TV có phải yêu cầu của bạn.
Giọng nền trước wake không tạo lượt; giọng nền sau wake vẫn có thể được
thu và không được coi là bằng chứng cho phép thực thi lệnh.

## Lưu WAV để chẩn đoán khi chủ động bật

```bash
.venv/bin/python scripts/smart_hub.py assistant --capture-only --debug-recordings
```

Lượt **hoàn chỉnh đã được chấp nhận** được ghi vào thư mục riêng
`recordings/turns-<mã>/`; thư mục mode 0700, WAV mode 0600, tên ngẫu nhiên
không ghi đè. File PCM mono 16-bit/16 kHz. Không ghi audio wake, tiếng đáp,
lượt timeout, lượt lỗi hoặc hội thoại nền trước wake. Trong lúc ghi, chương
trình tiếp tục đọc/bỏ audio và chỉ báo READY sau khi ghi xong. Lỗi đĩa được
báo FAIL, file dở được xóa. Thư mục `recordings/` đã được Git bỏ qua.

Không kết hợp tùy chọn này với `--wake-only`, luồng log STT mặc định hay
`--mock`. Bỏ `--debug-recordings` thì không tạo recorder/thư mục lưu lượt.

## Kiểm chứng và phần còn chờ

```bash
python3 scripts/smart_hub.py assistant --capture-only --mock
python3 scripts/run_tests.py --mock
.venv/bin/python scripts/run_tests.py --runtime
.venv/bin/python scripts/run_tests.py
```

Kết quả tại checkpoint:

| Hạng mục | Kết quả |
| --- | --- |
| Hồi quy trước thay đổi | PASS 108/108; 38,955 s |
| Toàn bộ hồi quy sau thay đổi | PASS 138/138; 47,249 s; không skip |
| Bộ mock chạy riêng bằng Python hệ thống | PASS 77/77; 11,963 s |
| Model VAD thật, 10 WAV tiếng Việt tổng hợp có checksum | PASS 10/10, tự chốt không ép flush |
| Wake STT thật → đáp giả → thu lượt VAD thật | PASS; một capture mở/đóng; không mất frame; không tạo recorder |
| Deadline, ngập ngừng, phần dư 320 → 512, clipping, gap, kết quả cũ và cleanup | PASS |
| WAV chẩn đoán đúng PCM, quyền file, tên riêng và lỗi ghi | PASS |
| Mic/loa thật và 10 câu mới 2–10 s | Chưa kiểm thử; người dùng đang hoãn thu thêm dữ liệu |

Mock/fixture không chứng minh không mất âm tiết trên mic thật hoặc nhận tốt
mọi người nói. Nghiệm thu thực tế còn cần 10 câu mới, câu ngắn, khoảng nghỉ
và tiếng TV/phòng ồn; đối chiếu đầu/cuối câu khi chủ động bật ghi chẩn đoán.
Các test async trong môi trường phát triển chạy ngoài sandbox do sandbox
chặn socket nội bộ asyncio; không mở cổng dịch vụ hoặc dùng thiết bị thật.

Người dùng yêu cầu hoàn tất phần đang làm rồi tạm dừng ngày 2026-09-17.
Đã dừng ở checkpoint phần mềm Phase 2; Phase 3 chưa triển khai.
