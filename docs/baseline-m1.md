# Baseline M1 — kiểm thử ngày 2026-09-13

## Phạm vi và trạng thái

Baseline mã nguồn: `f93d83aa5655d9b9daf31b1f3571b6b2439a9745` trên `main`.
Khi bắt đầu kiểm tra, chỉ `docs/SMART_HUB_PLAN.md` chưa được Git theo dõi.
Phần kiểm thử baseline ban đầu chưa đổi mã nguồn, dependency, model, threshold
hoặc audio hệ thống. Bản mở rộng STT làm sau đó được ghi riêng ở cuối tài liệu.

M1 đang nhận mẫu giọng cá nhân “Maika ơi” và phát WAV local “em nghe”. Người
dùng xác nhận nghe được câu đáp và thấy kết quả hiện tại đủ dùng cho mình,
nhưng báo rằng người khác gọi chưa được. Đây chưa phải bằng chứng đạt các
tiêu chí nhiều người nói hoặc toàn bộ pilot trong plan.

## Baseline tự động và microphone

| Kiểm tra | Kết quả quan sát |
| --- | --- |
| Toàn bộ test tự động | PASS 38/38; không thay thế lời nói thật |
| Mock độc lập | PASS 9/9 bằng Python hệ thống |
| CLI mock | PASS 2/2 event giả lập |
| Dependency | PASS: `pip check` không phát hiện dependency lỗi |
| TEST 1 — microphone | PASS: card 0 PCH/HDA Intel PCH, device 0 ALC256 Analog, mic tích hợp |
| TEST 2 — capture thật | PASS: 5 giây, 80.000 sample, peak 1568, RMS 425,41, clipping 0% |
| TEST 3 — engine | PASS: cả hai phiên `listen` mở được engine và đến `[READY]` |
| TEST 4 — 10 lần gọi mới | INCOMPLETE: chưa hoàn thành 10 lượt có số lần nói xác định |
| TEST 5 — một event mỗi lần gọi | INCOMPLETE: chưa đối chiếu được từng lần nói với từng event |

Lúc kiểm tra trước phiên nghe: PipeWire source mặc định là Built-in Audio
Analog Stereo, volume 0,10; ALSA Capture 23/0 dB, Internal Mic Boost 0/0 dB.
Loa mặc định là HDMI, volume 0,38. Các phiên mở lại capture đều qua calibrate;
chưa kiểm chứng khôi phục gain sau reboot hoặc restart audio trong phiên này.

Các lệnh đã chạy từ thư mục project:

```bash
.venv/bin/python scripts/run_tests.py
python3 scripts/run_tests.py --mock
python3 scripts/smart_hub.py listen --mock
.venv/bin/python -m pip check
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
.venv/bin/python scripts/smart_hub.py listen --seconds 300 --diagnostic --expect-events 10
```

## Hai phiên nghe thật

Mỗi phiên dùng một tiến trình `listen` và một capture liên tục sau calibrate.
Cả hai được dừng sớm để đối chiếu số lần nói, không chạy đủ 300 giây. Không
lưu audio thô. Timestamp bên dưới theo Asia/Ho_Chi_Minh.

| Phiên | Event quan sát | Phản hồi người dùng | Kết luận |
| --- | --- | --- | --- |
| 1, khoảng 12:36–12:38 | 6 event: 12:36:42, 12:36:50, 12:36:59, 12:37:17, 12:37:27, 12:37:33 | Đã nói 4 lần trong cửa sổ đầu và còn gọi thêm sau đó; tổng số lần chưa xác định | INCOMPLETE; không suy ra 2 event thừa từ số 4 của cửa sổ đầu |
| 2, khoảng 12:40–12:41 | 4 event: 12:40:48, 12:40:55, 12:41:02, 12:41:10 | Nhớ khoảng 3–4 lần gọi, có nghe “em nghe”; thấy như vậy đủ dùng cho mình | INCOMPLETE đối với tiêu chí đếm chính xác; có xác nhận tiếng đáp hoạt động |

RMS nền calibrate lần lượt 51,4 và 116,3. Score các event phiên 1:
0,785 / 0,794 / 0,758 / 0,771 / 0,761 / 0,759; phiên 2:
0,808 / 0,822 / 0,768 / 0,772. Score là độ tương đồng, không phải xác suất.

Quan sát chứng minh detector có thể phát nhiều event trong cùng một phiên,
nhưng không xác nhận mỗi lần gọi đều có đúng một event. Chưa thử lượt sau
60 giây nghỉ, 20 lượt trong hai điều kiện nhiễu hoặc 30 phút âm tính liên tục.
Hai tiến trình đã dừng và đóng capture.

Phát hiện tại thời điểm baseline: Ctrl+C/SIGINT đi qua handler chung,
trả exit code 0 cho `listen` và bỏ qua thống kê cuối cùng cùng kiểm tra
`--expect-events`. Vì vậy exit code 0 của hai phiên dừng sớm không phải PASS.
Trong bản thay đổi tiếp theo đã sửa: giữ thống kê và đánh dấu INCOMPLETE,
exit 130 khi ngắt bài thử có số event kỳ vọng; có regression test cho cả hai
chế độ listen. Các phiên trước sửa vẫn giữ nguyên kết luận INCOMPLETE.

## Yêu cầu mới: nhiều người có thể gọi

Người dùng báo người khác gọi “Maika ơi” không được và hỏi chuyển sang STT
nhận từ “Maika” để đánh thức. Bộ mẫu hiện tại được thu từ một người; chưa có
bộ kiểm thử nhiều người. Chưa xác định tách biệt tác động của giọng nói,
khoảng cách và tiếng nền trong tình huống vừa được báo.

Hướng đề xuất: phát hiện có giọng nói → STT tiếng Việt local trên CPU → so
khớp cụm cấu hình → một wake event và WAV “em nghe”. Đây là thay đổi cách
kích hoạt so với M1 chỉ so khớp mẫu âm thanh. Tại thời điểm đề xuất chưa được
triển khai; trạng thái bản triển khai tiếp theo được ghi bên dưới. Có thể
chọn riêng “Maika”, nhưng ưu tiên thử đủ “Maika ơi” để giảm các trường hợp
chỉ nhắc đến tên. STT cũng xử lý lời nói từ TV, nên vẫn cần test âm tính,
chống lặp và bỏ audio loa phản hồi như hiện tại.

Ứng viên để thử nhỏ: Zipformer tiếng Việt 30M INT8 qua sherpa-onnx. Tài liệu
upstream có ví dụ microphone với VAD, chạy CPU; bản này là nhận dạng từng
đoạn, không phải model streaming từng frame. Tại thời điểm đề xuất chưa cài package hoặc tải model,
chưa đo CPU/RAM/độ trễ trên máy này và chưa kiểm chứng cách model viết “Maika”.
Model mang license CC BY-NC-ND 4.0 theo model card; cần giữ thông tin giấy
phép khi dùng hoặc phân phối model. Không gán license này cho mã nguồn repo.

- [Tài liệu model và microphone của sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/zipformer-transducer-models.html#sherpa-onnx-zipformer-vi-30m-int8-2026-02-09-vietnamese)
- [Model card của tác giả](https://huggingface.co/hynt/Zipformer-30M-RNNT-6000h)

Đánh giá tiếp cần lời nói mới từ ít nhất hai người, số lần gọi được đếm rõ,
tiếng nền đại diện và đo độ trễ/CPU/RAM. Kết quả của engine cá nhân không
được dùng để kết luận engine STT mới đã đạt. Các tính năng hội thoại, LLM,
điều khiển thiết bị và service production chưa được triển khai.

## Cập nhật sau khi người dùng chốt yêu cầu

Người dùng chọn chỉ đủ “Maika ơi”, yêu cầu hoàn thiện code và hướng dẫn,
để họ chủ động thử độ chính xác sau. Đã thêm `listen-stt`, hai package sherpa
CPU và model public có checksum; giữ nguyên enrollment/threshold engine cá
nhân và audio hệ thống. Đã sửa công cụ kiểm thử dừng sớm như mô tả trên.

Kết quả kỹ thuật và lệnh tự thử: [stt-wake.md](stt-wake.md). Các hàng live
INCOMPLETE của baseline không được đổi thành PASS vì người dùng hoãn việc
nghiệm thu hoặc vì test tự động của engine mới chạy thành công.

## Xác nhận sử dụng bản STT — 2026-09-13

Người dùng tự thử và báo: “tôi đã test thử stt thành công wake-work hoạt động
ổn, gọi maika ơi, và phản hồi em nghe được rồi”. Ghi nhận nghiệm thu chức năng
đánh thức bằng STT và âm thanh phản hồi cho nhu cầu hiện tại; có thể tiếp tục
Phase 1 theo yêu cầu triển khai plan đã có.

Chưa có số lượt, danh sách người nói hoặc điều kiện nhiễu từ lần tự thử này.
Giữ nguyên các kết quả INCOMPLETE ở trên; không suy ra tỉ lệ nhận đúng hoặc
đánh giá chạy 24/7 từ xác nhận này.
