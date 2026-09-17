# Kiểm tra lời nói sau “Maika ơi”

Ngày 2026-09-13, người dùng gửi log wake của `assistant` và yêu cầu in những
từ microphone nhận được sau wake để kiểm tra câu lệnh. Increment này thêm
đúng bước đó bằng model STT/VAD tiếng Việt đang có, chạy local trên CPU.
Không cần cài package, tải model hay đổi cấu hình mic/loa.

## Chạy và tự kiểm thử

```bash
cd /home/mrhoan/source/wake-work/smart-hub
.venv/bin/python scripts/smart_hub.py assistant --diagnostic
```

1. Chờ `[READY ASSISTANT]`, nói “Maika ơi” một lần.
2. Nghe “em nghe”, **đợi `[LISTEN COMMAND]`** rồi mới nói câu lệnh.
3. Nói “bật quạt”, “tắt đèn nhà bếp”, “mở điều hòa” hoặc một câu khác,
   rồi ngừng nói khoảng một giây.
4. Đối chiếu `[COMMAND] ...` với câu vừa nói; chữ hoa/thường do STT trả về.
5. Khi thấy READY lần nữa, gọi wake rồi thử câu khác; thử cả bật/tắt,
   tăng/giảm để kiểm tra nhận đúng động từ. Ctrl+C để dừng.

Ví dụ định dạng (không phải kết quả nghiệm thu giọng thật):

```text
[WAKE] detected at 2026-09-13 20:30:00
[LISTEN COMMAND] Nói một câu trong 8 giây.
[COMMAND] BẬT ĐÈN PHÒNG KHÁCH
[READY ASSISTANT] Đang nghe ‘Maika ơi’; Ctrl+C để dừng.
```

Cửa sổ **8 giây tính sau tiếng đáp và guard tiếng vọng 0,1 giây**, bao gồm
thời gian chờ bạn nói, nói, khoảng im lặng kết câu và xử lý STT. Chỉ log đoạn
nói đã chốt đầu tiên không rỗng, không in từng từ đang đoán. Nếu không có câu hoàn
chỉnh trước hạn, `[COMMAND TIMEOUT]` xuất hiện một lần rồi quay về chờ wake.
Nếu clipping hoặc mất audio, hiện `[COMMAND DROPPED]` và bỏ lượt đó.

Nên nói câu ngắn dưới khoảng 6 giây. VAD hiện tại có giới hạn tách đoạn 6
giây, nên một câu dài hoặc ngập ngừng trên 0,8 giây có thể bị tách; chỉ đoạn
đầu được log. Tăng cửa sổ dưới đây giúp chờ bạn nói lâu hơn, không thay giới
hạn tách đoạn. Chưa hỗ trợ nói liền wake với câu lệnh hoặc nói chen lúc loa
đang đáp. Trong cửa sổ nghe lệnh, nói lại “Maika ơi” cũng là nội dung được
log, không tạo một wake mới.

```bash
# Cửa sổ dài hơn, phải > 0 và <= 30 giây
.venv/bin/python scripts/smart_hub.py assistant --command-seconds 12

# Chỉ wake và tiếng đáp, dùng để so sánh với bản trước
.venv/bin/python scripts/smart_hub.py assistant --wake-only

# Ba vòng giả lập, không cần microphone, loa, model hoặc numpy
python3 scripts/smart_hub.py assistant --mock

# Test runtime có model/WAV tiếng Việt local, không mở mic/loa
.venv/bin/python scripts/run_tests.py --runtime
.venv/bin/python scripts/run_tests.py
python3 scripts/run_tests.py --mock
```

Chỉ chạy một listener tại một thời điểm. Máy hiện tại đã có dependency;
lệnh cài trên máy mới nằm trong [README](../README.md).

`--seconds` vẫn giới hạn toàn phiên, tính từ READY đầu tiên. Hết phiên thì
bỏ câu lệnh còn dở; không flush hoặc log kết quả STT muộn. Tiếng đáp đang
phát được hoàn tất khi hết giờ tự nhiên. Ctrl+C/SIGTERM dừng cả lượt và
đóng mic/loa. `--expect-events` kiểm đếm wake và chu kỳ hoàn tất (đã log hoặc
timeout), không xác minh nội dung câu lệnh đúng. Ngắt bằng signal khi có
`--expect-events` vẫn là INCOMPLETE/exit 130.

## Chỉnh thông số cho lệnh ngắn

Sau phản hồi timeout/thiếu từ đầu, người dùng yêu cầu nhận câu tiếng Việt
nói chung và xác nhận giữ hoàn toàn local trên Debian. Runtime không có
danh sách lệnh hay bước sửa chữ theo ý đoán; các câu trong test chỉ là mẫu.

Thông số nằm ở đầu [src/smart_hub/local_stt.py](../src/smart_hub/local_stt.py):

| Thông số | Mặc định | Tác dụng |
| --- | --- | --- |
| `COMMAND_VAD_THRESHOLD` | `0.35` | Ngưỡng giọng nói; thấp hơn dễ nhận giọng yếu nhưng cũng dễ nhận tiếng nền |
| `COMMAND_MIN_SPEECH_SECONDS` | `0.10` | Nhận giọng ngắn, thay vì tối thiểu 0,25 giây như wake |
| `COMMAND_SILENCE_SECONDS` | `0.80` | Chờ im lặng để chốt câu |
| `COMMAND_PREROLL_SECONDS` | `0.64` | Giữ audio trước điểm VAD nhận giọng, giảm mất từ đầu nói nhỏ |
| `COMMAND_MAX_GAIN` | `8.0` | Giới hạn khuếch đại bản sao audio lệnh; `1.0` tắt khuếch đại |

Trước tiên giữ gain mic `0.10` đã tự thử ổn và chạy mặc định mới. Nếu vẫn
timeout sau LISTEN COMMAND mà không có clipping, thử riêng threshold
`0.30`. Nếu thiếu đầu câu, thử riêng pre-roll `0.80`. Nếu nền ồn làm nhận
nhầm, thử max gain `4.0` hoặc `1.0`. **Đổi một thông số mỗi lần**, Ctrl+C
rồi chạy lại. Thử nhiều câu và một lượt im lặng sau wake. Kết quả chỉ có
“quạt” không tự thêm “bật”, vì có thể nhầm với “tắt quạt”.

Khoảng hợp lệ: threshold lớn hơn 0 và nhỏ hơn 1; min speech/silence lớn
hơn 0 và không quá 1 giây; pre-roll từ 0 đến 0,8 giây; max gain từ 1 đến 8.
Các thông số này chỉ áp dụng sau wake. `threshold`/`min_rms` của engine mẫu
giọng cá nhân không chỉnh STT. `--command-seconds` chỉ tăng thời gian chờ,
không sửa được chữ model nhận sai.

Khoảng bỏ frame sát tiếng đáp 20 ms hoàn tất trước LISTEN COMMAND để
không bỏ đầu câu sau thông báo sẵn sàng. Guard tiếng vọng mặc định 0,1 giây
và chỉnh được bằng `--reply-guard`. VAD lệnh được nạp trước khi mở capture,
không chờ frame lệnh đầu tiên mới tạo. STT vẫn dùng chung model/worker.
Đổi chế độ sẽ xóa lịch sử audio và khôi phục VAD wake đã chọn. Gain áp dụng
trên bản sao float cho VAD/STT lệnh và wake `sensitive`; PCM gốc vẫn kiểm tra clipping.
Không đổi gain PipeWire/ALSA. Khuếch đại không phải khử nhiễu hay tăng SNR.

## Wake giọng nhỏ và khoảng chờ sau tiếng đáp

Người dùng xác nhận câu lệnh tạm ổn với giọng mình, nhưng bé gọi wake chưa
ổn và dễ mất từ đầu khi nói sau “em nghe”. Thay đổi ngày 2026-09-14:

- `assistant` giảm guard sau playback từ 0,7 xuống 0,1 giây. Dùng
  `--reply-guard 0.3` nếu phòng có vọng và STT nhận tiếng đáp; giới hạn 0–2 giây.
- WAV đáp bỏ đúng 0,238625 giây im lặng số ở cuối, giữ lại 50 ms và toàn
  bộ sample khác 0. Không tổng hợp lại giọng. Tổng phần chờ cố định giảm
  0,838625 giây; chưa bao gồm và chưa đo độ trễ thiết bị/driver.
- VAD nghe lệnh được tạo trước READY, tránh nạp model khi từ đầu đi vào.
- Mặc định wake `standard` giữ VAD/gain đã dùng. `sensitive` thử ngưỡng
  VAD 0,35, giọng tối thiểu 0,10 giây, pre-roll 0,64 giây, gain tối đa 4.
  Model STT và quy tắc đủ “Maika ơi” giữ nguyên; không thêm alias đoán.

```bash
# Thử mức wake nhạy hơn và xem chữ STT nghe được, không lưu audio
.venv/bin/python scripts/smart_hub.py assistant --wake-profile sensitive --show-wake-text --diagnostic

# Mức wake cũ; vẫn dùng tiếng đáp và guard ngắn mới
.venv/bin/python scripts/smart_hub.py assistant --wake-profile standard --diagnostic
```

`[WAKE TEXT]` chỉ hiện khi bật `--show-wake-text`, có thể gồm hội thoại nền
trước wake. Nếu có chữ nhưng không có wake thì cần đối chiếu chữ model
nhận; nếu không có dòng này, chưa có đoạn giọng được VAD chốt. `sensitive`
có thể nhận thêm tiếng nền, nên chỉ thử khi cần và so với `standard`.

Kiểm tra WAV tổng hợp hai giọng, gồm bản nhỏ hơn và tăng tốc/cao độ, vẫn
giữ đúng số wake và loại câu không có “ơi”. Cả hai profile đều qua bộ mẫu
này; đây **không phải bằng chứng cải thiện giọng bé**. Không có dữ liệu
thật của bé, nên chưa nghiệm thu hoặc khẳng định nguyên nhân cụ thể.

Người dùng hẹn thu mẫu vào lần làm việc tiếp theo. Khi người dùng báo sẵn
sàng, dự kiến thu riêng khoảng 5 lượt “Maika ơi” mỗi người, kèm câu gần
giống/tiếng nền và các lượt mới để kiểm thử. Với STT hiện tại, mẫu giúp
chẩn đoán, tinh chỉnh và so sánh, không tự huấn luyện model từ 5 mẫu.
Engine `listen` cũ có cơ chế so mẫu cá nhân riêng; chưa chuyển về engine
đó hoặc ghi đè enrollment. Chưa thu mic trong lần sửa này.

## Đối chiếu chất lượng STT câu lệnh

Bộ mẫu gồm 10 câu tổng hợp bằng VieNeu Nano, hai giọng Trúc Ly/Đức Trí:
quạt, đèn, âm lượng, điều hòa, nhiệt độ, rèm và hẹn giờ. WAV và provenance
ở `tests/fixtures/commands/`. Mỗi câu thử ở ba điều kiện: gốc; biên độ
nhân 0,1; nhiễu trắng SNR 15 dB. Feed từng frame qua VAD rồi STT, không ép
flush, không mở mic/loa.

| Phương án | Gốc | Nhỏ tiếng | Nhiễu trắng | Tổng khớp toàn câu |
| --- | --- | --- | --- | --- |
| VAD/STT như trước | 7/10 | 5/10 | 7/10 | 19/30 |
| Chỉ tách VAD cho lệnh | 8/10 | 5/10 | 8/10 | 21/30 |
| **VAD lệnh + gain có giới hạn, đã chọn** | **8/10** | **9/10** | **8/10** | **25/30** |
| Thêm modified beam search, 4 paths | 8/10 | 9/10 | 8/10 | 25/30 |
| PhoWhisper-small INT8, 4 CPU threads | 4/10 | 5/10 | 2/10 | 11/30 |

Phương án chọn xử lý VAD/STT tối đa 0,078 giây/câu trong lượt đo offline,
chưa gồm thời gian bạn nói và im lặng chốt câu. Bản PhoWhisper thử mất
0,415–1,261 giây decode/đoạn và không cải thiện bộ mẫu này nên không đưa
vào runtime. Model ONNX khoảng 280 MiB đã tải, kiểm tra checksum trong
`/tmp` để so sánh; không cài package mới. Nguồn:
[VinAI PhoWhisper](https://github.com/VinAIResearch/PhoWhisper),
[bản chuyển ONNX](https://huggingface.co/quinguyen1502/phowhisper-salony-onnx).

**25/30 không phải độ chính xác trên mic thật hay mức đã đạt như điện thoại.**
Vẫn sai “tăng âm lượng” ở mẫu gốc, “giảm âm lượng” trong nhiễu, và lặp từ
“rèm” ở ba điều kiện. PhoWhisper được bỏ khác biệt dấu câu/hoa thường và
chấp nhận 25/5 viết bằng số hoặc chữ khi so sánh. Test hồi quy chỉ bảo vệ
mức đã đo, không coi những lỗi còn lại là transcript đúng. Chi tiết từng
câu: [command-stt-2026-09-13.json](benchmarks/command-stt-2026-09-13.json).

Chưa có audio thật của các lượt lỗi để xác nhận nguyên nhân cụ thể.
Theo lựa chọn của người dùng, nghiệm thu mic thật do người dùng chủ động
thử. Chưa gửi audio lên cloud hoặc triển khai STT điện thoại.

## Phạm vi và tiêu chí chấp nhận

- Chỉ mở cửa sổ nghe lệnh sau một wake và sau guard tiếng vọng; mặc định
  không log hội thoại nền trước wake. `--show-wake-text` là chẩn đoán tự chọn.
- Một lượt in tối đa một `[COMMAND]`, kèm context/turn ID nội bộ; không có
  callback thực thi thiết bị. Log chỉ là chữ STT nhận được.
- Nhận câu xong hoặc hết hạn thì quay về chờ wake; timer không phụ thuộc
  việc microphone có trả frame mới hay không.
- Audio bị mất/clipping không được ghép thành câu lệnh thiếu; kết quả từ
  session, generation, continuity cũ, frame lặp hoặc sau stop bị loại.
- Dùng chung một capture và một model STT với wake. Hai đối tượng VAD dùng
  cùng file Silero, chỉ một VAD nhận audio tại mỗi thời điểm. Buffer và
  worker giữ giới hạn cũ, không thêm thư viện điều phối lượt.
- `listen`, `listen-stt` và `assistant --wake-only` giữ vòng wake/đáp cũ.

`[WAKE]` và `[COMMAND]` đi ra stdout, flush ngay. Trạng thái đi ra stderr.
Chương trình không tự ghi file audio/transcript, không gửi audio lên cloud
và không mở port. Không có STT streaming, LLM, TTS động hoặc điều khiển thiết bị.

## Sự cố mic clipping sau khi thêm log lệnh

Người dùng sau đó báo gọi nhiều lần không có phản hồi, kèm log `0 wake;
clipping=205; bỏ=0 frame; kết quả cũ=0`. Đây là lỗi ngay ở audio đầu vào,
trước khi chương trình vào bước nghe lệnh. Frame clipping làm backend bỏ
audio và reset VAD. Lần kiểm tra yên lặng trước đó không kiểm chứng biên độ
của giọng nói thật.

Đọc phần cứng thấy nguồn mic tích hợp ALC256 ở PipeWire 0,27, ALSA Capture
56/**+24,75 dB**, Internal Mic Boost 0 dB. Hồ sơ lúc hoạt động ổn ghi
PipeWire 0,10, Capture 23/0 dB. Sau khi thông báo, đã chạy
`wpctl set-volume 68 0.10` trên đúng nguồn mic vừa inspect và xác nhận
Capture trở về 23/0 dB. ID 68 chỉ áp dụng tại thời điểm sửa; chưa xác định
tác nhân đã tăng gain. Không có tự động ép gain trong listener.

Code bổ sung cảnh báo clipping ngay lần đầu trong phiên; số lần lỗi vẫn
được đếm tới cuối phiên. Kiểm thử mới xác nhận cảnh báo không spam, không
phát wake từ frame lỗi, và giọng hợp lệ tiếp theo vẫn có thể wake.

Sau thay đổi gain, trước tối ưu STT lệnh, lượt
`assistant --seconds 8 --no-feedback --diagnostic` tới
READY, startup 0,88 giây, clipping/mất frame 0, queue đỉnh 12/25, tuổi frame
lớn nhất 0,007 giây, exit 0. Không yêu cầu nói trong lượt này, nên chưa tính
là xác nhận wake bằng giọng người dùng. Cách tự kiểm tra tín hiệu khi đang
nói và gain: [README](../README.md).

## Kiểm chứng

Kết quả trên máy Debian hiện tại; toàn bộ test được chạy lại ngày 2026-09-14
sau thay đổi wake profile và khoảng chờ đáp. Các lượt capture thật là số
liệu ngày 2026-09-13, chưa lặp lại sau lần sửa mới.

| Kiểm tra | Kết quả |
| --- | --- |
| Toàn bộ `scripts/run_tests.py` | **PASS 108/108**, không skip, 39,038 giây ngày 2026-09-14 |
| STT sau tối ưu lệnh, trước bổ sung wake profile | **PASS 29/29**, không skip, 4,958 giây ngày 2026-09-13 |
| Wake sensitive, VAD nạp sẵn và log tự chọn | **PASS**, WAV wake/âm tính nhỏ và nhanh hơn; VAD không tạo sau READY; chữ không khớp không phát wake |
| Khoảng chờ đáp ngắn | **PASS** bằng process/clock mock; bỏ audio trong playback/guard, nhận frame sau guard 0,1 giây |
| WAV đáp sau bỏ im lặng | **PASS**, PCM còn lại trùng bản gốc, phần bị cắt chỉ có sample 0; SHA-256 đúng |
| CLI mock mặc định | **PASS**, 3 wake → 3 đáp giả → 3 log lệnh, quay về chờ wake |
| CLI `--wake-only` | **PASS**, 3 wake/đáp, không có COMMAND hoặc LISTEN COMMAND |
| Model thật + WAV local | **PASS**, câu trước wake không log; sau wake/đáp có đúng một `[COMMAND] MAI ĐI CHƠI`; một capture, không clipping/mất frame |
| Lỗi và kết quả muộn | **PASS**, timeout khi không có PCM, deadline lúc STT xong, kết quả cũ/lặp, clipping/gap, stop trong khi nhận lệnh và cleanup |
| Cảnh báo clipping khi đang nghe | **PASS**, báo ngay lần đầu, không lặp mỗi frame; wake hợp lệ tiếp theo vẫn hoạt động |
| Capture thật 8 giây sau khôi phục gain, trước tối ưu STT lệnh | **PASS**, startup 0,88 giây, tới READY, clipping/mất frame 0, queue đỉnh 12/25, tuổi frame lớn nhất 0,007 giây, exit 0; Capture vẫn 0 dB sau khi đóng mic |
| Định dạng mã | **PASS**, compileall và `git diff --check` |

Lệnh capture thật đã chạy trước lần tối ưu STT lệnh:

```bash
.venv/bin/python scripts/smart_hub.py assistant --seconds 8 --no-feedback --diagnostic
```

Lượt này không yêu cầu nói mẫu, không có wake/command và không lưu audio;
chỉ xác nhận model, mic và vòng chạy đóng bình thường. Theo lựa chọn tự
kiểm thử của người dùng, chưa tổ chức lượt nói câu lệnh mới và nghe loa thật.
Độ chính xác với câu lệnh thật, người khác hoặc tiếng nền do người dùng tự
thử theo các bước trên; các WAV tổng hợp không thay thế kiểm thử đó.

Các test async chạy ngoài sandbox của công cụ vì sandbox chặn socket nội
bộ dùng để đánh thức asyncio. Chạy từ terminal Debian bình thường, không
cần `sudo`; các test này không mở cổng mạng hoặc gọi Internet.
