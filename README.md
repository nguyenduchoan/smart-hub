# Smart Hub — “Maika ơi”

Microphone → nhận diện câu gọi local → đáp “em nghe” → log một câu nói sau wake.
**Chỉ nhận và log lời nói, chưa thực thi lệnh.** Ngày 2026-09-13, người dùng yêu
cầu thêm STT tiếng Việt để thử nhận nhiều người nói, chọn đủ cụm “Maika ơi”,
và đã tự thử, xác nhận gọi được và nghe “em nghe” ổn. Runtime phát WAV VieNeu local đã
chọn; không tổng hợp lời đáp động, không LLM, BroadLink, điều khiển thiết bị
hoặc service production.

Ngày **2026-09-15**, đã hoàn tất **Phase 1B**: chọn bộ điều phối lượt
**local với Silero ONNX** cho bước phát triển tiếp theo. Thử nghiệm offline
đối chiếu Pipecat đã có số đo CPU/RAM, kiểm tra lifecycle và quyết định tại
[conversation-orchestration.md](docs/decisions/conversation-orchestration.md).
Phần thu mẫu giọng thêm được để lại sau. Ngày **2026-09-17**, đã tích hợp
**Phase 2: `--capture-only`** và kiểm thử tự động; nghiệm thu giọng thật còn
chờ. Tiến độ toàn kế hoạch và điểm tạm dừng: [implementation-status.md](docs/implementation-status.md).

## Thu trọn lượt nói — Phase 2

```bash
.venv/bin/python scripts/smart_hub.py assistant --capture-only --diagnostic
```

Gọi “Maika ơi”, chờ tiếng đáp và `[LISTEN TURN]`, rồi nói một câu. Chương
trình giữ đầu câu, tự chốt sau khoảng 0,7 giây im lặng và in `[TURN] captured
... s`. Chờ nói tối đa 8 giây, mỗi lượt tối đa 30 giây; quá hạn/mất audio
thì bỏ lượt và yêu cầu nói lại. Mặc định chỉ giữ PCM trong RAM.

Chế độ này chưa STT/phát câu đáp động cho lượt đã thu. Chạy không có
`--capture-only` để dùng luồng log câu STT bên dưới. Cấu hình `turn`, cách
tự kiểm tra, WAV chẩn đoán opt-in và giới hạn: [turn-capture.md](docs/turn-capture.md).

```bash
python3 scripts/smart_hub.py assistant --capture-only --mock
```

## Chạy: wake → đáp → xem câu lệnh nhận được

Thêm lệnh `assistant`, dùng lại STT và giọng VieNeu đã chọn. Mic có một luồng
thu xuyên suốt; STT và playback xử lý riêng; buffer tối đa 500 ms. Chạy:

```bash
cd /home/mrhoan/source/wake-work/smart-hub
.venv/bin/python scripts/smart_hub.py assistant
```

Chờ `[READY ASSISTANT]`, nói **“Maika ơi”**, nghe **“em nghe”**. Khi hiện
**`[LISTEN COMMAND]`**, nói một câu ngắn, ví dụ **“bật đèn phòng khách”**,
rồi ngừng nói khoảng một giây. STT in kết quả một lần và trở lại chờ wake:

```text
[WAKE] detected at 2026-09-13 20:30:00
[LISTEN COMMAND] Nói một câu trong 8 giây.
[COMMAND] BẬT ĐÈN PHÒNG KHÁCH
[READY ASSISTANT] Đang nghe ‘Maika ơi’; Ctrl+C để dừng.
```

Đây là ví dụ định dạng, nội dung thực tế tùy STT. Cửa sổ mặc định **8 giây**,
tính sau khi phát xong và bỏ tiếng vọng 0,1 giây. Hết hạn chưa có câu hoàn
chỉnh sẽ hiện `[COMMAND TIMEOUT]` rồi chờ wake tiếp. Mỗi lần wake nhận một
đoạn nói; Ctrl+C để dừng. Chưa hỗ trợ nói liền “Maika ơi, bật đèn…” hoặc nói
chen trong tiếng đáp: hãy đợi `[LISTEN COMMAND]` để tránh mất đầu câu.

STT nhận câu tiếng Việt nói chung: quạt, đèn, điều hòa, âm lượng, hẹn giờ…
Phần nghe lệnh dùng thông số tách giọng riêng, giữ thêm 0,64 giây đầu câu
và nâng mức audio nhỏ có giới hạn. Wake mặc định giữ mức `standard`. Cách tự thử
nhiều câu khác nhau và chỉnh `COMMAND_*` nằm trong
[hướng dẫn nghe lệnh](docs/command-transcript.md#chỉnh-thông-số-cho-lệnh-ngắn).
Benchmark tổng hợp đạt 25/30 câu khớp hoàn toàn, còn lỗi được ghi rõ trong
hướng dẫn; chưa thay thế kiểm thử giọng thật trên mic của bạn.

```bash
# Chờ câu nói lâu hơn (tối đa 30 giây)
.venv/bin/python scripts/smart_hub.py assistant --command-seconds 12

# Quay về vòng chỉ wake và đáp, không log câu lệnh
.venv/bin/python scripts/smart_hub.py assistant --wake-only

# Thử giọng nhỏ/ngắn; hiện chữ STT trước wake để biết model nghe thành gì
.venv/bin/python scripts/smart_hub.py assistant --wake-profile sensitive --show-wake-text --diagnostic

# Nếu nhận tiếng vọng của “em nghe”, tăng khoảng chờ từ 0.1 lên 0.3 giây
.venv/bin/python scripts/smart_hub.py assistant --reply-guard 0.3
```

`--wake-profile sensitive` là tùy chọn thử giọng nhỏ, chưa được nghiệm thu
với giọng trẻ em. `--show-wake-text` có thể hiện cả hội thoại nền; bỏ tùy chọn
này khi dùng bình thường. Luôn cần đủ cụm “Maika ơi”. Xem
[cách thử và kế hoạch thu mẫu từng người](docs/command-transcript.md#wake-giọng-nhỏ-và-khoảng-chờ-sau-tiếng-đáp).

File “em nghe” đã bỏ 0,239 giây im lặng cuối, giữ nguyên âm giọng; cùng
guard ngắn hơn, giảm khoảng 0,839 giây chờ cố định. VAD lệnh được nạp trước
READY. Chưa đo tổng độ trễ thực tế của mic/loa sau thay đổi này.

Không cần cài thêm dependency hay tải model so với `listen-stt`. Mặc định
chỉ nội dung sau wake được in ra terminal; chương trình không tự lưu audio/transcript
vào file, không gửi cloud và không điều khiển thiết bị.

```bash
python3 scripts/smart_hub.py assistant --mock
.venv/bin/python scripts/run_tests.py --runtime
.venv/bin/python scripts/run_tests.py
python3 scripts/run_tests.py --mock
```

Mock chạy 3 chu kỳ wake → đáp giả → log lệnh → chờ wake, không cần
microphone/model/loa hay numpy. Xem [cách kiểm tra câu lệnh](docs/command-transcript.md)
và [runtime Phase 1](docs/runtime-phase1.md). `listen-stt` và `listen` vẫn chỉ
wake/đáp, có thể chạy độc lập. Chỉ mở một listener tại một thời điểm.

## Chạy bản STT mới — không cần thu mẫu giọng cá nhân

Máy hiện tại đã cài dependency và model. Chạy:

```bash
cd /home/mrhoan/source/wake-work/smart-hub
.venv/bin/python scripts/smart_hub.py listen-stt
```

Chờ `[READY STT]`, nói **“Maika ơi”**, nghe **“em nghe”**, rồi chờ ít nhất
5 giây trước lượt gọi tiếp theo. Chương trình tiếp tục nghe đến khi Ctrl+C.
Chỉ “Maika” không đủ kích hoạt. STT có thể viết tên thành `mai ca ơi` hoặc
`mai ka ơi`; hai cách viết này được chấp nhận nhưng vẫn phải có `ơi`.
Không bỏ dấu tiếng Việt hoặc so khớp gần đúng tùy ý.

Luồng mới: mic → Silero VAD → Zipformer tiếng Việt 30M INT8 trên CPU → khớp
cụm → event + WAV. Cài trên máy mới, từ thư mục project:

```bash
python3 scripts/setup_env.py
.venv/bin/python -m pip install -r requirements-stt.txt
python3 scripts/download_stt_models.py
.venv/bin/python -m pip check
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
.venv/bin/python scripts/run_tests.py --stt
```

Hai package bổ sung là `sherpa-onnx` (API STT/VAD) và `sherpa-onnx-core`
(runtime native CPU), cùng phiên bản 1.13.8; wheel trên máy này khoảng 15 MB.
Tận dụng numpy/arecord hiện có; không cần GPU, PyTorch hay SDK cloud. Các file
STT/VAD/token khoảng 33 MiB sau tải, có kiểm tra SHA-256; được bỏ qua khi commit.
Chỉ lệnh cài/tải model cần mạng; listener không gọi mạng, lưu audio hay lưu
transcript. `--show-text` chỉ hiện transcript local khi bạn chủ động yêu cầu.

Test mock bằng Python hệ thống:

```bash
python3 scripts/smart_hub.py listen-stt --mock
python3 scripts/run_tests.py --mock
```

Để tự xem STT đang nghe thành chữ gì và kiểm tra mỗi lần gọi:

```bash
.venv/bin/python scripts/smart_hub.py listen-stt --show-text --diagnostic
```

Hướng dẫn đếm lượt, thử người khác, tiếng nền và đọc PASS/FAIL:
**[docs/stt-wake.md](docs/stt-wake.md)**. Các test tự động và kiểm tra capture
đã chạy; người dùng đã xác nhận gọi/đáp với giọng thật. Kiểm thử định lượng
nhiều người và tiếng nền vẫn cần thực hiện riêng.

Lệnh `listen` bên dưới vẫn là engine mẫu giọng cá nhân để đối chiếu hoặc quay
lại khi cần. `validate-live` cũng kiểm tra engine cá nhân, không phải STT.

## Thu mẫu để kiểm tra STT theo từng người

Dừng listener đang chạy, đứng ở khoảng cách thường dùng với mic. Chạy từng
lệnh riêng khi người nói tương ứng đã sẵn sàng:

```bash
.venv/bin/python scripts/record_wake_samples.py --speaker adult
.venv/bin/python scripts/record_wake_samples.py --speaker child
```

Sau khoảng 3 giây chuẩn bị (ổn định mic ~2.5s và tiếng tít), mỗi tiếng tít mở
một lượt thu 5 giây: nói **“Maika ơi” đúng một lần**, rồi im lặng. Tổng cộng 5
lượt, khoảng 35–40 giây; có thể dùng `--takes 1` để thử một lượt hoặc Ctrl+C để
dừng (trả về exit code 130 và đánh dấu manifest `interrupted` để caller phân biệt
với hoàn tất bình thường).

WAV mono 16-bit/16 kHz và thông tin từng lượt được lưu riêng trong
`recordings/<thời-gian>-<adult|child>-<mã>/`, được Git bỏ qua. Công cụ đóng mic
sau khi thu, không đổi gain hay model. Cần kiểm tra nội dung và xác nhận người
nói trước khi dùng mẫu đối chiếu. Thu 5 mẫu **không tự huấn luyện lại STT** và
không thay mẫu của engine `listen`.

## Duyệt và đánh giá dữ liệu giọng bé (Child Study)

Quy trình bảo đảm toàn vẹn dữ liệu: **thu âm → kiểm tra kỹ thuật → duyệt thủ công → đánh giá offline**.

### 1. Kiểm tra kỹ thuật tự động (`--auto-qc`)
Kiểm tra tính toàn vẹn file WAV, định dạng PCM 16kHz mono 16-bit và khớp SHA-256 với nhãn:
```bash
.venv/bin/python scripts/review_child_study.py --auto-qc
```
Lệnh này chỉ đánh dấu `technical_pass` và không tự động xác nhận người nói (`speaker_confirmed`).

### 2. Duyệt nhãn thủ công (Interactive Review)
Người vận hành nghe lại từng file, kiểm tra tạp âm/clipping, xác nhận đúng người nói và phê duyệt:
```bash
.venv/bin/python scripts/review_child_study.py
```
Sau khi duyệt, mẫu được cập nhật `speaker_confirmed: true`, `review_status: "accepted"`, lưu tên người duyệt và thời điểm duyệt.

### 3. Đánh giá offline chính thức (Official Benchmark)
Mặc định runner chỉ đánh giá tập `--split dev` và nghiêm ngặt yêu cầu các mẫu đã được duyệt chấp nhận (`review_status == "accepted"`, `speaker_confirmed == true`, đúng SHA-256). Tập `test` được bảo vệ độc lập:
```bash
.venv/bin/python scripts/evaluate_child_study.py
```
Muốn chạy tập test sau khi đã chốt candidate:
```bash
.venv/bin/python scripts/evaluate_child_study.py --split test
```

### 4. Đánh giá ad-hoc / chưa duyệt
Để chạy nhanh thử nghiệm trên file đơn hoặc thư mục chưa qua quy trình duyệt nhãn chính thức, bắt buộc truyền cờ `--allow-unreviewed`:
```bash
.venv/bin/python scripts/evaluate_child_study.py --allow-unreviewed --dir recordings/TIEU_DE_THU_MUC
.venv/bin/python scripts/evaluate_child_study.py --allow-unreviewed --wav recordings/path/to/take-01.wav --label positive
```
Báo cáo ad-hoc sẽ được gắn watermark cảnh báo rõ ràng và không được dùng làm căn cứ nghiệm thu chính thức.

## Chế độ mẫu giọng cá nhân (`listen`)

Bản thử nghiệm nhận **“Maika ơi” bằng mẫu giọng nói của bạn**, không cần
model wake word tiếng Anh hoặc tài khoản cloud. Engine mặc định dùng
**EfficientWord-Net INT8**, backbone đã học sẵn để tạo embedding âm thanh,
rồi so khớp với 5 mẫu tiếng Việt cá nhân; có mẫu âm tính để chặn câu gần giống.
Inference chạy trên cửa sổ trượt 1,5 giây, mỗi 0,2 giây, không đợi VAD tách câu.
Đây là nhận diện mẫu âm thanh cá nhân, chưa bảo đảm nhận mọi giọng tiếng Việt.
Phải kiểm thử với lời nói mới, tiếng nền và câu khác trước khi kết luận ổn định.

Phiên 2026-09-12: TEST 1–5 PASS; ba lần gọi mới có số event `[1, 1, 1]`, người
dùng xác nhận mỗi lần nghe đúng một câu “em nghe”. Lượt âm tính 60 giây có 0
event; toàn bộ 38 test tự động và 9 test mock đều PASS. Chưa xác nhận độ ổn định
trong nhiều giờ hoặc mọi mức tiếng nền.

## Môi trường đã kiểm tra

- Debian GNU/Linux 12 (bookworm), `x86_64`.
- Intel Core i5-1135G7, 4 nhân/8 luồng; RAM khoảng 23 GiB.
- Python 3.11.2 (`/usr/bin/python3`).
- ALSA: HDA Intel PCH, ALC256 Analog, card 0/device 0, Internal Mic.
- PipeWire 0.3.65 + WirePlumber 0.4.13; `pipewire-pulse` đang hoạt động.
- Có sẵn `alsa-utils`/`arecord` 1.2.8, `pipewire-alsa`, `pw-record`, `wpctl`.
- Ban đầu chưa có pip, numpy, sounddevice, PyAudio, openWakeWord, pytest.

Capture dùng `arecord -D pipewire`: ALSA plugin đi qua PipeWire đang có.
Không mở port. CLI chạy headless trong phiên người dùng có quyền truy cập
PipeWire. SSH cần cùng user và runtime audio hợp lệ; không chạy detector bằng
`sudo`, không tự bật lingering/systemd trong milestone này.

## Cài dependency

Clone trên máy khác:

```bash
git clone https://github.com/nguyenduchoan/smart-hub.git
cd smart-hub
```

Trên máy Debian hiện tại, thư mục project là
`/home/mrhoan/source/wake-work/smart-hub`. Chạy các lệnh dưới từ thư mục project:

```bash
python3 scripts/setup_env.py
python3 scripts/download_model.py
.venv/bin/python -m pip check
```

Repo chứa mã nguồn, test, tài liệu và WAV tiếng đáp tổng hợp. `.venv`, model
tải xuống, công cụ TTS tạm và mẫu giọng cá nhân `.npz` không được đưa lên Git.
**Clone mới cần thu mẫu wake word trước khi chạy `listen`** theo mục “Thu mẫu
tiếng Việt”; bản clone không có sẵn mẫu “Maika ơi” của người dùng. WAV tiếng đáp
đã có sẵn, không cần tải model TTS để chạy detector.

Script chỉ tạo `.venv` trong project, tải pip wheel có kiểm tra SHA-256 và cài
dependency đã khóa phiên bản trong `requirements.txt`. Không cần `sudo`,
`apt`, `python3-pip` hay `python3-venv` bổ sung
trên máy này: bootstrap dùng `venv --without-pip` tương đương qua Python API.
Wheel numpy tải khoảng 16,8 MB, pip khoảng 1,8 MB, ONNX Runtime khoảng 17,4 MB;
model INT8 22,1 MB. Chỉ bước cài/tải model cần Internet;
capture, thu mẫu, inference và mọi test chạy offline.

| Package | Mục đích |
| --- | --- |
| numpy | FFT/log filterbank và so sánh embedding |
| onnxruntime | Chạy model INT8 local, một luồng CPU |
| flatbuffers, protobuf | Dữ liệu model, dependency của runtime |
| packaging | Kiểm tra/so sánh phiên bản |
| sympy, mpmath | Xử lý biểu thức số, dependency của runtime |
| coloredlogs, humanfriendly | Tiện ích log của runtime |
| pip | Công cụ cài đặt trong .venv |

Dùng thư viện chuẩn cho CLI, JSON, test, subprocess; `arecord` có sẵn cho audio.
Không cài TensorFlow, PyTorch, librosa, scipy, PyAudio hoặc package EfficientWord-Net
đầy đủ. Phương án nhẹ hơn MFCC/DTW đã được thử nhưng không đạt kiểm thử giọng thật;
mã baseline vẫn có để so sánh, engine mặc định là neural.
Model được khóa theo commit và checksum; runtime không tự tải model, đã tắt telemetry.

## Kiểm tra microphone

```bash
arecord -l
arecord -L
wpctl status
wpctl inspect @DEFAULT_AUDIO_SOURCE@
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
```

Lệnh cuối in TEST 1 và TEST 2 PASS/FAIL, số sample, RMS, peak và tỷ lệ clipping.
Chỉ giữ audio trong RAM, không phát audio ra loa, không ghi WAV hoặc tải lên mạng.
FAIL nếu không có thiết bị, capture ngắt/timeout, audio gần bằng 0 hoặc clipping >1%.
Capture thành công chưa chứng minh đã nghe được lời nói của bạn.

**Thay đổi mixer đã thực hiện sau khi thông báo:** `Internal Mic Boost,0`
từ giá trị `3` (+30 dB) xuống `0` (0 dB); sau đó `Capture` giảm từ `63`
(+30 dB) xuống `23` (0 dB) khi clipping tái xuất hiện lúc mở mic.
Đo liên tục cho thấy xung nhiễu/DC offset lớn lúc khởi động, rồi tín hiệu ổn định.
Chương trình bỏ 2 giây đầu mỗi lần mở capture trước khi đo hoặc nhận giọng nói.
Không sửa file cấu hình ALSA/PipeWire, không restart service.
Một phiên tiếp tục đã thấy gain trở lại +30/+30 dB; sau thông báo, đã đặt lại
0/0 dB. Ngày 2026-09-13 đã xác định mức mic PipeWire vẫn ở 100%, tuyến input
chưa lưu volume (`save=false`, chưa có mục input trong state WirePlumber).
Chỉ chỉnh `amixer` trước đó chưa lưu mức mong muốn vào trình quản lý audio.

### Nếu assistant im lặng và log có `clipping > 0`

Clipping là tín hiệu chạm giới hạn biên độ. STT bỏ frame/đoạn bị lỗi và xóa
audio đang giữ; vì vậy mic có thể thu được tiếng nhưng không tạo `[WAKE]`.
`assistant` hiện cảnh báo `[AUDIO WARNING]` ngay lần clipping đầu tiên trong
phiên, không đợi tới Ctrl+C mới thấy số đếm.

Trong sự cố ngày 2026-09-13, log người dùng có `0 wake; clipping=205`.
Đo ALSA cho thấy Capture **+24,75 dB**, trong khi mức đã hoạt động ổn là
**0 dB**. Đã thông báo rồi đưa đúng mic tích hợp từ PipeWire 27% về 10%,
kiểm tra lại Capture 0 dB. Chưa xác định tác nhân đã tăng gain.

Kiểm tra khi **đang nói**, vì một lượt đo yên lặng có thể không clipping dù
giọng nói vẫn bị vỡ. Sau khi dừng listener, chạy:

```bash
.venv/bin/python scripts/smart_hub.py check-mic --seconds 10
```

Đợi khoảng 2 giây ổn định mic rồi nói “Maika ơi” vài lần trong lượt đo.
TEST 2 chỉ kiểm tra tín hiệu, chưa phải xác nhận wake. Cách kiểm tra/chỉnh
gain cho microphone tích hợp của máy này ở phần tiếp theo.

### Nếu listen báo Audio clipping

Trên **microphone tích hợp ALC256 của máy này**, mức PipeWire 10% đưa Capture
và Internal Mic Boost về đúng 0 dB; đã kiểm tra lại bằng ALSA. Mức 100% đưa
cả hai lên +30 dB và làm audio bão hòa. Không áp dụng số 10% này cho mọi mic.

```bash
wpctl inspect @DEFAULT_AUDIO_SOURCE@
wpctl get-volume @DEFAULT_AUDIO_SOURCE@
# Khi đã xác nhận default source là mic tích hợp ALC256:
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.10
amixer -c 0 scontents
.venv/bin/python scripts/smart_hub.py check-mic --seconds 5
.venv/bin/python scripts/smart_hub.py listen
```

Lệnh `set-volume` thay đổi gain đầu vào, có thể ảnh hưởng ứng dụng khác dùng
cùng mic. WirePlumber đã tự lưu volume mới của `analog-input-internal-mic`
vào `~/.local/state/wireplumber/default-routes`. Không cần tự sửa file state,
restart audio hoặc tạo service. App không tự thay gain khi chạy.
Đây là mức đã dùng để thu mẫu/kiểm thử “Maika ơi”. Sau khi chỉnh, nếu vẫn FAIL,
chạy `.venv/bin/python scripts/diagnose_audio.py` để đọc mixer và số đo từng
giây; không chỉ tăng thời gian warmup để bỏ qua clipping kéo dài.

Để đọc lại mixer:

```bash
amixer -c 0 scontents
```

Nếu muốn khôi phục mức mic PipeWire ban đầu (có thể làm clipping trở lại):

```bash
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0
```

Trong sandbox, `arecord -l` có thể báo `no soundcards found` dù host nhận mic.
Chạy kiểm thử phần cứng từ terminal host với user `mrhoan`. Nếu capture lỗi,
kiểm tra quyền/phiên PipeWire và nguồn input trước, không tự dừng dịch vụ audio.

## Thu mẫu tiếng Việt

```bash
.venv/bin/python scripts/smart_hub.py enroll --takes 5 --seconds 10 --fixed-windows
```

1. Giữ yên lặng khi chương trình ổn định microphone 2 giây và đo tiếng nền 1 giây.
2. Khi thấy `MẪU 1/5` đến `MẪU 5/5`, nói **“Maika ơi” một lần** trong mỗi cửa
   sổ 10 giây rồi im lặng; đứng ở khoảng cách sẽ sử dụng thực tế. Các lượt tự nối tiếp.
3. Chương trình lấy cửa sổ 1,5 giây quanh đoạn âm thanh nổi bật, từ chối clipping
   hoặc giọng không nổi trên tiếng nền. Khi đủ mẫu, lưu `models/maika_oi_neural.npz`.

Cửa sổ nổi bật không chứng minh nội dung là wake word: chỉ nói đúng câu gọi trong
lượt thu, tránh va chạm vào mic. Bản `--continuous` vẫn có, nhưng cần ngưỡng VAD
phù hợp để tách chính xác từng lượt. Khuyến nghị dùng `--fixed-windows` trên máy này.

Engine không hiểu nội dung lời nói; bạn chịu trách nhiệm nói đúng cụm ở bước
thu mẫu. Lưu đặc trưng, không lưu audio thô. Không ghi đè bộ mẫu đã có.
Nếu lỗi giữa chừng, model chưa được tạo và có thể chạy lại.

Để thu lại hoặc đổi wake word, sao chép cấu hình và dùng đường dẫn model mới:

```bash
cp config.json config.local.json
# Sửa wake_word và model_path trong config.local.json bằng editor.
.venv/bin/python scripts/smart_hub.py --config config.local.json enroll --takes 5
.venv/bin/python scripts/smart_hub.py --config config.local.json listen
```

## Chạy detector

```bash
.venv/bin/python scripts/smart_hub.py listen
```

Giữ yên lặng lúc ổn định microphone và đo tiếng nền; khi thấy `[READY]`, nói “Maika ơi”.
Giữa hai lần gọi, chờ ít nhất 5 giây để tiếng đáp kết thúc và detector tái kích hoạt.
Dừng bằng Ctrl+C.

Khi phát hiện, loa phát WAV cấu hình trong `feedback_path`. Microphone vẫn được
đọc liên tục; audio trong lúc phát và 0,7 giây sau đó bị loại khỏi detector để
tránh nhận chính tiếng phản hồi. Một lần gọi quá sát tiếng đáp có thể bị bỏ qua.

```text
[WAKE] detected at 2026-09-12 15:30:00
```

Chỉ wake event đi ra stdout, flush ngay. Trạng thái bắt đầu/kết thúc hoặc lỗi
đi ra stderr; không in score mỗi frame. Event nội bộ còn có nhãn wake word,
score, thời gian audio và thời gian có timezone. Không có callback điều khiển thiết bị.
SIGINT/SIGTERM đóng tiến trình capture; chưa cung cấp systemd unit.

Thử giới hạn 20 giây:

```bash
.venv/bin/python scripts/smart_hub.py listen --seconds 20
```

`config.json` cấu hình `wake_word`, `engine` (`neural` mặc định), `model_path`,
`backbone_path`, `negative_path` (tương đối với file config), `device`,
`threshold` (0–1, **không phải xác suất**), `cooldown_seconds`,
`min_rms`, `silence_seconds`, `min_speech_seconds`, `max_speech_seconds`.
Giảm `threshold` dễ nhận hơn và dễ báo nhầm hơn; phải chạy lại test âm tính.
`min_rms` là biên độ RMS PCM 16-bit để bỏ qua cửa sổ gần im lặng;
`silence_seconds`, `min_speech_seconds`, `max_speech_seconds` dùng cho thu mẫu bằng
VAD và engine DTW baseline. Neural không cần đợi một khoảng im lặng để phân đoạn.
Model thiếu hoặc khác nhãn sẽ gây lỗi rõ ràng, không tự chuyển model.

## Tiếng phản hồi “em nghe” / “em đây”

Nghe thử file hiện được chọn, không mở microphone:

```bash
.venv/bin/python scripts/smart_hub.py test-feedback
```

Trong `config.json`: `feedback_enabled` bật/tắt tiếng đáp, `feedback_path` chọn
WAV PCM dài tối đa 5 giây, `playback_device` mặc định `pipewire`. Đổi
`feedback_path` thành `assets/em_day_vieneu.wav` để chọn “em đây”. `listen --mock`
và `listen --wav` không phát ra loa. Lệnh phát thành công chỉ xác nhận phần mềm,
người nghe vẫn cần xác nhận tiếng thực tế rõ và dễ chịu.

Tạo hai WAV bằng **VieNeu-TTS v3 Nano**, chạy CPU, không cần GPU. Đây là công cụ
tùy chọn chạy một lần; listener không import TTS và không giữ model TTS trong RAM:

```bash
.venv/bin/python -m pip install --only-binary=:all: --no-cache-dir -r requirements-voice.txt
python3 scripts/download_voice.py
.venv/bin/python scripts/make_feedback_vieneu.py --voice 'Trúc Ly'
```

Chỉ thêm `sea-g2p==0.9.1` (~28 MB) để chuyển chữ tiếng Việt thành âm vị. Tận dụng
NumPy/ONNX Runtime hiện có. Không cài cả SDK VieNeu, Gradio, PyTorch, CUDA,
huggingface_hub hoặc audio package khác. Bốn graph và cấu hình model khoảng
282 MB nằm trong `.voice-tools/vieneu-nano`, được kiểm tra checksum và khóa
revision; không cần clone toàn bộ repo để chạy các command trên.

Hai file `assets/em_nghe_vieneu.wav` và `assets/em_day_vieneu.wav` là PCM mono
16-bit/24 kHz, có khoảng dự phòng biên độ 6 dB trước khi ghi PCM. Chương trình
không ghi đè file đã có; dùng `--output-dir assets/voice-trial` để tạo lượt mới.
Model và bước tạo WAV đều local sau khi tải; không gửi microphone hoặc văn bản
lên dịch vụ TTS. Code tạo audio tham khảo thuật toán ONNX của tác giả ở revision
ghi trong `assets/vieneu-provenance.json`; license đi kèm `assets/VIENEU-LICENSE.txt`.

Repo [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS) có bản Nano tối ưu CPU;
chất lượng cần nghe thử trên loa thực tế. File cũ `em_nghe.wav`/`em_day.wav` tạo
bằng eSpeak được giữ lại để so sánh; người dùng đã đánh giá giọng đó máy móc và rè.
Phương án nhẹ hơn về tải model là WAV tự thu hoặc Piper. Google Cloud TTS có
giọng tiếng Việt và hạn mức miễn phí nhưng yêu cầu billing cho API; nếu dùng,
chỉ cần tạo WAV một lần rồi phát local. Chưa tích hợp hoặc gọi Google TTS.
Tại ngày kiểm tra 2026-09-12: Neural2/Chirp 3 HD 1 triệu ký tự/tháng,
Standard/WaveNet 4 triệu ký tự/tháng; vượt hạn mức sẽ tính phí.
[Bảng giá chính thức](https://cloud.google.com/text-to-speech/pricing).

## Giảm báo nhầm trong tiếng nền

```bash
.venv/bin/python scripts/smart_hub.py enroll-negative --seconds 30
```

Khi thấy `ÂM TÍNH`, nói câu khác như “mai đi chơi”, “máy đang chạy”, trò chuyện
bình thường, để tiếng quạt/TV thường gặp. **Không nói “Maika ơi”** trong bước này.
Lưu embedding vào `models/maika_oi_negative.npz`, không lưu audio thô.
Detector yêu cầu độ giống mẫu đúng lớn hơn mẫu âm tính ít nhất 0,03.
Thu sai nhãn có thể làm bỏ sót; phải chạy lại validation bằng các lần nói mới.
Mẫu âm tính dùng để hiệu chỉnh không được tính là bộ test âm tính độc lập.

## Chạy toàn bộ test tự động

```bash
.venv/bin/python scripts/run_tests.py
```

Các test gồm capture giả lập bị chia nhỏ/timeout và cleanup, lỗi input, WAV
sai định dạng, clipping, model local, cụm sai, DTW với mẫu âm thanh tổng hợp
khác tốc độ, âm tính, giới hạn buffer, định dạng log và chống lặp event.
Mẫu âm thanh tổng hợp **không phải giọng tiếng Việt**; PASS ở đây không xác nhận
“Maika ơi” đã hoạt động trên microphone thật.

## TEST 1–5 với microphone và lời nói mới

Sau khi thu mẫu:

```bash
.venv/bin/python scripts/smart_hub.py validate-live --attempts 3 --seconds 15
```

| Test | Điều kiện PASS |
| --- | --- |
| TEST 1 | Hệ thống liệt kê thiết bị capture ALSA thật |
| TEST 2 | Capture đủ PCM, có tín hiệu, không clipping >1% |
| TEST 3 | Mở được model “Maika ơi” đã thu mẫu |
| TEST 4 | Cả 3 lần nói mới đều có wake event |
| TEST 5 | Mỗi lần nói mới có đúng 1 event |

Nhấn Enter từng lượt và nói đúng một lần mỗi cửa sổ. Thiếu model, không có mic
hoặc test thất bại sẽ in FAIL và trả exit code 1. Bộ mẫu thu ban đầu không được
dùng làm bằng chứng nhận diện độc lập. Engine neural yêu cầu 2 cửa sổ liên tiếp
vượt ngưỡng, cooldown và tái kích hoạt sau tín hiệu thấp; không bảo đảm mọi cách
nói liên tục. TEST 4–5 PASS ở ba lượt mới gần nhất; xem hồ sơ nghiệm thu để biết
lịch sử lỗi và giới hạn kiểm thử, chưa coi đây là kết quả chạy ổn định nhiều giờ.

Thử âm tính: trong 20 giây không nói “Maika ơi”, có thể nói câu khác/để tiếng nền:

```bash
.venv/bin/python scripts/smart_hub.py listen --seconds 20 --expect-events 0
```

Nếu bỏ sót wake word, thêm `--diagnostic` để xem cuối lượt: số cửa sổ đã chấm,
RMS cao nhất, độ giống mẫu đúng và mẫu âm tính trong cùng cửa sổ. Khi score sau
lọc bằng 0, các số này giúp phân biệt audio quá nhỏ với mẫu âm tính đang chặn.
Không tự hạ threshold trước khi kiểm tra tín hiệu và kiểm thử câu khác.

Nếu có WAV kiểm thử riêng, đúng PCM mono 16-bit/16 kHz và có ít nhất 0,5 giây
im lặng sau câu gọi:

```bash
.venv/bin/python scripts/smart_hub.py listen --wav /duong/dan/test.wav --expect-events 1
```

## Mock không cần microphone hoặc dependency

```bash
python3 scripts/smart_hub.py listen --mock
python3 scripts/run_tests.py --mock
```

Mock `listen` tạo hai đợt score kích hoạt kéo dài và kiểm tra chỉ có 2 event. Ghi rõ
`[MOCK]` trên stderr, không mở microphone, không cần model hoặc numpy.

## Phạm vi và giới hạn của engine mẫu giọng (`listen`)

- Neural dùng cửa sổ trượt để xử lý tiếng nền liên tục; chưa xác nhận câu gọi
  nằm giữa mọi dạng hội thoại dài. Câu gọi nên nằm gọn trong khoảng 1,3 giây.
- Model cá nhân phụ thuộc người nói, microphone, tiếng nền; chưa xác nhận đa giọng,
  khoảng cách xa, TV/nhạc lớn hoặc tỷ lệ báo nhầm trong nhiều giờ.
- Buffer neural chỉ 1,5 giây, inference mỗi 0,2 giây khi có tín hiệu; ONNX/BLAS
  giới hạn 1 luồng. Đo sơ bộ: 15,75 ms/inference, RSS đỉnh 120,7 MiB; đây là
  benchmark ngắn 20 lượt, không chứng minh mức CPU/RAM của toàn phiên 24/7.
- Không thu mẫu giọng từ cloud, không dùng speech-to-text để tìm từ khóa.
- Chỉ sang milestone sau khi bạn xác nhận wake word ổn định.

Model và hợp đồng tiền xử lý: [EfficientWord-Net](https://github.com/Ant-Brain/EfficientWord-Net/tree/adfd4119aabe793b435e522ed7e0e70a768e9edb),
[license Apache-2.0](models/EFFICIENTWORDNET-LICENSE.md).
Tham khảo baseline: [MFCC](https://librosa.org/doc/0.11.0/generated/librosa.feature.mfcc.html),
[DTW](https://librosa.org/doc/0.11.0/generated/librosa.sequence.dtw.html).

Kết quả nghiệm thu và quyết định kỹ thuật: [docs/milestone-1.md](docs/milestone-1.md).

Kết quả phiên mới: [baseline M1](docs/baseline-m1.md),
[bản STT và cách tự kiểm thử](docs/stt-wake.md), [kiến trúc](docs/architecture.md).
Tình trạng giấy phép mã nguồn và model: [docs/licensing.md](docs/licensing.md).
