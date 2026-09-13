# Smart Hub — Milestone 1: “Maika ơi”

Microphone → nhận diện wake word local → log/event và câu phản hồi cố định.
**Chỉ Milestone 1.** Người dùng bổ sung yêu cầu loa nói “em nghe”/“em đây”
để biết đã nhận wake word. Runtime phát WAV local, không tổng hợp lời đáp động.
Không có BroadLink, device control, STT, LLM, automation hay service production.

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

Mock tạo hai đợt score kích hoạt kéo dài và kiểm tra chỉ có 2 event. Ghi rõ
`[MOCK]` trên stderr, không mở microphone, không cần model hoặc numpy.

## Phạm vi và giới hạn

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
