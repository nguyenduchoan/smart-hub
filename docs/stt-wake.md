# Đánh thức bằng STT tiếng Việt local

## Phạm vi đã hoàn thiện

Lệnh `listen-stt` dùng model nhận tiếng Việt có sẵn, không dùng 5 mẫu giọng
cá nhân của `listen`. Chỉ phát `[WAKE]` và WAV “em nghe” đã chọn. Không có
nhận lệnh, hội thoại, LLM, điều khiển thiết bị hoặc dịch vụ production.

Người dùng chọn **chỉ “Maika ơi”**, đã tự thử và xác nhận gọi/đáp hoạt động ổn
ngày 2026-09-13. Chưa có kết quả đếm định lượng cho nhiều người hoặc tiếng TV.
[Runtime Phase 1](runtime-phase1.md) đã dùng lại backend này cho vòng chạy
`assistant`; các phần nhận lệnh/hội thoại chưa triển khai.

## Cài đặt và chạy

Từ thư mục repo, với Python 3.11 như máy Debian hiện tại:

```bash
python3 scripts/setup_env.py
.venv/bin/python -m pip install -r requirements-stt.txt
python3 scripts/download_stt_models.py
.venv/bin/python -m pip check
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
.venv/bin/python scripts/smart_hub.py listen-stt
```

Không cần `download_model.py`, `enroll` hoặc `enroll-negative` để chạy STT.
Runtime dùng đường dẫn model cố định `models/stt`, chỉ đọc file đã được kiểm
tra checksum, không tự tải. Downloader không ghi đè file khác checksum.

Giữ yên lặng trong 3 giây kiểm tra mic. Sau `[READY STT]`, nói một câu gọi,
đợi “em nghe”, cách lượt tiếp theo ít nhất 5 giây. Ctrl+C để dừng. Chưa hỗ trợ
nói liền câu gọi và command, nói chen trong lúc đáp hoặc hội thoại liên tục.

Kết quả mong đợi:

```text
[READY STT] Đang nghe ‘Maika ơi’. ...
[WAKE] detected at 2026-09-13 13:36:48
```

Nghe loa là kiểm tra riêng: có event chưa chứng minh loa phát tới tai người
dùng. `--no-feedback` tắt loa riêng phiên hiện tại. Device mic/loa và WAV lấy
từ `config.json`; threshold, engine và mẫu cá nhân của `listen` không dùng
trong STT. Không tự thay gain, routing, audio configuration hoặc volume loa.

## Tự kiểm thử giọng thật

### Kiểm tra một lượt trước

```bash
.venv/bin/python scripts/smart_hub.py listen-stt --seconds 20 --show-text --diagnostic --expect-events 1
```

Chờ READY, nói đúng một lần “Maika ơi” trong 10 giây đầu, rồi im lặng tới khi
chương trình tự kết thúc. Cần đúng một event và một câu đáp. Không nói lặp
trong cùng lượt dù chưa nghe đáp; nếu không nhận thì ghi một lần bỏ sót.

### Nhiều lượt, nhiều người

```bash
.venv/bin/python scripts/smart_hub.py listen-stt --seconds 300 --diagnostic --expect-events 10
```

Trong cùng phiên, nói đúng 10 lần, mỗi lần cách nhau ít nhất 5 giây và sau
câu đáp. Chừa một khoảng nghỉ 60 giây rồi gọi tiếp để kiểm tra nghe lại sau
khi nghỉ. Ghi kết quả từng lần; tổng 10 event vẫn có thể gồm cả bỏ sót và báo
thừa nên không dùng tổng số một mình để kết luận chính xác.

Chạy một phiên riêng cho mỗi người. Ghi khoảng cách, vị trí mic, người nói,
số lần thực sự gọi, event từng lần và tiếng đáp thực sự nghe được. Sau đó
thử riêng với quạt/tiếng sinh hoạt và với TV ở âm lượng sử dụng bình thường.

### Câu không kích hoạt và tiếng nền

```bash
.venv/bin/python scripts/smart_hub.py listen-stt --seconds 60 --show-text --expect-events 0
.venv/bin/python scripts/smart_hub.py listen-stt --seconds 1800 --diagnostic --expect-events 0
```

Trong lượt 60 giây, thử “Maika” đơn lẻ, “mai đi chơi”, trò chuyện bình thường;
không nói đủ “Maika ơi”. Lượt 30 phút để tiếng nền thường gặp và không gọi.
Nếu có event, ghi báo nhầm cùng điều kiện thực tế. VAD vẫn nhận tiếng người
trên TV; STT không tự phân biệt ai đang cố gọi trợ lý.

| Lượt | Người/tiếng nền/khoảng cách | Số lần thực sự gọi | Event từng lần | Câu đáp nghe được | Bỏ sót/báo thừa |
| --- | --- | --- | --- | --- | --- |
| Điền khi tự thử | | | | | |

Mục tiêu pilot trong plan: 10/10 lượt riêng có đúng một event/câu đáp; ít nhất
9/10 ở mỗi điều kiện nhiễu; không event thừa và 30 phút âm tính không event.
Chưa có bằng chứng đạt các mục tiêu này trên người thật với STT mới.

## Đọc kết quả và xử lý lỗi

- `--show-text` hiện transcript trên stderr; mặc định không hiện nội dung.
  Không chuyển output sang file nếu không muốn lưu cuộc trò chuyện.
- Có thể thấy `MAI CA ƠI` hoặc `MAI KA ƠI`: đây là hai cách viết mặc định
  được chấp nhận cho đủ câu gọi. `Maika`, `mai ca`, `maika oi`, `maika ôi`,
  `mai cá ơi`, `Mekka ơi` không nằm trong các mẫu khớp mặc định.
- Model có thể viết sai tên. Kiểm tra transcript trước khi cân nhắc thêm
  `--alias 'cách viết đầy đủ đã kiểm chứng'`; đừng thêm một từ ngắn hoặc bỏ
  `ơi` vì sẽ thay đổi yêu cầu kích hoạt. Mọi alias mới cần thử câu âm tính.
- Clipping: chạy `check-mic` và xem phần xử lý gain trong README. Chương trình
  không tự tăng/giảm gain. Frame clipping bị bỏ và xóa đoạn VAD đang giữ.
- Buffer đầy hoặc audio cũ quá 500 ms: chương trình báo FAIL và đóng capture,
  không âm thầm tiếp tục từ audio thiếu đoạn. Kiểm tra tải CPU trước khi thử lại.
- Với `--expect-events`, kết thúc đủ bài trả `0` khi số event khớp và không
  clipping, `1` khi FAIL. Ctrl+C/SIGTERM dừng sớm trả `130`, in INCOMPLETE dù
  số event tạm thời đã khớp. Không có `--expect-events`, Ctrl+C dừng bình thường
  trả `0` và vẫn hiện thống kê. Startup/capture/model/player lỗi trả `1`.
- `listen-stt --wav` đọc hết WAV PCM mono 16-bit/16 kHz và không mở mic/loa.
  Cuối WAV sẽ flush đoạn còn lại; dừng live không giải mã câu đang nói dở.

## Test tự động, không cần người nói

```bash
# Riêng bản STT: bắt buộc có package và model thật, không dùng enrollment.
.venv/bin/python scripts/run_tests.py --stt
# Toàn bộ repo; các test engine cá nhân cần backbone từ download_model.py.
.venv/bin/python scripts/run_tests.py
# Không cần numpy/model/mic/loa:
python3 scripts/run_tests.py --mock
python3 scripts/smart_hub.py listen-stt --mock
# Model STT thật với audio tổng hợp có provenance trong repo:
.venv/bin/python scripts/smart_hub.py listen-stt --wav tests/fixtures/stt/0-1.wav --expect-events 1
.venv/bin/python scripts/smart_hub.py listen-stt --wav tests/fixtures/stt/0-3.wav --expect-events 0
```

Mock dùng transcript giả, không giả vờ đã chạy nhận giọng. Bộ test model thật
dùng WAV tổng hợp VieNeu, không phải bản ghi microphone hay giọng người mới.
Provenance tại `tests/fixtures/stt/provenance.json`; fixture giúp hồi quy việc
cắt mất âm đầu, không chứng minh chất lượng với người thật.

## Bằng chứng kỹ thuật ngày 2026-09-13

| Kiểm tra | Kết quả |
| --- | --- |
| Toàn bộ test sau sửa cleanup capture | PASS 61/61, không skip trên máy này |
| Các test STT trong bộ trên | PASS 23/23, có model thật và không cần enrollment; lệnh riêng `--stt` đã chạy PASS 22/22 trước khi thêm test cleanup |
| Mock Python hệ thống | PASS 17/17; CLI STT mock 2/2 event |
| Model STT và VAD thật | PASS khởi động CPU; khoảng 0,66–0,83 s trong các lần kiểm tra ngắn |
| 5 fixture tổng hợp | PASS: 2 câu gọi bằng 2 preset TTS; 3 câu âm tính không event |
| Gọi lặp với model thật | PASS 10/10 event trong một session bằng audio tổng hợp, có 65 s audio im lặng mô phỏng; không phải 65 s chờ ngoài đời |
| Capture thật 5 giây | PASS READY → kết thúc, queue peak 12/25, clipping 0; không có đoạn giọng, không dùng làm test độ chính xác |
| Audio sau khi đóng capture | PipeWire không còn stream kiểm thử, mic tích hợp vẫn volume 0,10; loa mặc định lúc đọc lại là Bluetooth Tronsmart T8 Mini volume 0,39 |
| Dependency | PASS `pip check` |
| Người thật | Người dùng xác nhận tự gọi “Maika ơi” và nghe “em nghe” ổn ngày 2026-09-13 |
| Nhiều người/tiếng TV/đếm định lượng | CHƯA NGHIỆM THU — chưa có số liệu từ lần tự thử |

Model từng giải mã audio TTS tạo từ chữ “Maika ơi” thành “Mekka ơi”. Không
thêm “Mekka” vào alias để làm kết quả trông tốt hơn. Fixture dương dùng đầu
vào TTS “Mai ka ơi”; test giọng thật vẫn cần thiết để biết cách model viết tên
khi người trong nhà gọi. Không báo các fixture này là độ chính xác tổng quát.

Rà soát sau bàn giao còn phát hiện: lỗi capture/đầy queue chỉ được công bố
sau khi đóng `arecord`. Đã sửa để báo lỗi ngay trước cleanup, tránh cho STT
phát event khi luồng capture đã lỗi mà tiến trình con còn đang đóng. Test hồi
quy tái hiện FAIL trước sửa và PASS sau sửa; bộ 61 test bao gồm test này.

Đo kỹ thuật ngắn với 5 fixture tổng hợp, một lượt làm nóng và 5 lượt đo:
decode P50 25,615 ms, P90 29,666 ms, RTF riêng decode 0,0225, RSS đỉnh tiến
trình khoảng 142,12 MiB. 25 kết quả đều khớp nhãn của các fixture này. Bộ
regression chạy đồng thời, không cô lập tải máy. Các số đo không gồm capture,
chờ VAD kết thúc câu hay loa, không phải độ trễ phản hồi ngoài đời hoặc CPU
idle 24/7. [Dữ liệu và điều kiện đo](benchmarks/stt-wake-smoke.json).

## Chi phí và giới hạn

VAD chạy cửa sổ 512 sample/32 ms, nối từ capture 320 sample/20 ms; giữ 320 ms
trước đoạn giọng và tối đa 8 giây PCM. STT chạy theo đoạn, kết thúc sau khoảng
600 ms im lặng, giới hạn đoạn giọng 6 giây. Đây không phải streaming STT từng
từ. Một kết quả cuối chỉ phát tối đa một event; cooldown mặc định 2 giây.

Một capture thread liên tục đọc mic; hàng đợi tối đa 25 frame/500 ms. STT
chạy đồng bộ ở luồng xử lý riêng với capture, mỗi model 1 luồng inference.
Khi quá tải bản thử dừng rõ lỗi; chưa có auto-restart hay phục hồi mất frame.
Mic được đọc/bỏ qua trong lúc phát “em nghe” và 0,7 giây sau playback; không
đưa audio loa vào STT. Không hỗ trợ nói chen trong giai đoạn này.

Quay lại bản mẫu giọng cá nhân khi cần:

```bash
.venv/bin/python scripts/smart_hub.py listen
```

[Model upstream](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/zipformer-transducer-models.html)
và [giấy phép](licensing.md). Không cài service hoặc sửa audio hệ thống trong thay đổi này.
