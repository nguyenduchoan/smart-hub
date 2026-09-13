# M1 — Wake word tiếng Việt và tiếng xác nhận cố định

## Story, tiêu chí và phạm vi

Microphone tích hợp nghe liên tục; mỗi lần “Maika ơi” có đúng một event và loa
phát một câu “em nghe”/“em đây”. Tiếng đáp được người dùng bổ sung để dễ kiểm
thử. Chạy local/headless, không BroadLink, điều khiển thiết bị, STT, LLM, web
server, automation hay service production. Không chuyển milestone tiếp theo.

Tiêu chí nghiệm thu: TEST 1 liệt kê mic thật; TEST 2 capture PCM đủ và không
clipping quá mức; TEST 3 mở engine; TEST 4 nhận những lần nói mới; TEST 5 mỗi
lần đúng một event. Cần thêm kiểm thử âm tính độc lập, mock, cleanup khi dừng
và command tái hiện trong README. Chỉ đóng M1 sau khi người dùng xác nhận ổn định.

## Quyết định đã thực hiện

- Debian 12, x86_64/i5-1135G7, Python 3.11.2, mic ALC256 Analog card 0/device 0.
  Capture qua `arecord -D pipewire`; tận dụng ALSA/PipeWire hiện có.
- MFCC/DTW với mẫu cá nhân đã thử nhưng không đạt lời nói mới. Baseline được giữ;
  mặc định hiện là EfficientWord-Net INT8, cửa sổ 1,5 giây mỗi 0,2 giây, một
  luồng CPU. Không coi đây là model “Maika ơi” được huấn luyện chuyên biệt.
- Đã thu 5 mẫu neural trong 5 cửa sổ cố định và 143 embedding âm tính từ 30 giây
  tiếng nền/câu khác. Chỉ lưu đặc trưng local, không giữ raw audio của microphone.
- Ngưỡng thử 0,72, cần cao hơn mẫu âm tính ít nhất 0,03; thêm yêu cầu 2 cửa sổ
  cao liên tiếp sau khi người dùng xác nhận có báo nhầm. Score không phải xác suất.
- WakeGate cần tín hiệu thấp thật và cooldown để tái kích hoạt. Audio bị bỏ qua
  trong lúc phát phản hồi và 0,7 giây tiếng vọng không được coi là im lặng thật.
- Phản hồi eSpeak ban đầu đã phát ra loa nhưng người dùng đánh giá rè/máy móc.
  Đã tạo hai WAV bằng VieNeu v3 Nano trên CPU: giọng preset Trúc Ly, PCM 24 kHz,
  biên độ đỉnh −6,02 dBFS, không clipping. Chỉ thêm `sea-g2p` vào `.venv`;
  không cài SDK TTS đầy đủ, PyTorch/CUDA/GUI. Listener chỉ dùng WAV.
- Runtime không gọi network. Công cụ tải model có pin revision/checksum; công cụ
  tạo WAV dùng model local, không mở mic, không tải audio hoặc văn bản lên cloud.

## Bằng chứng kiểm thử đến 2026-09-12

| Hạng mục | Kết quả | Bằng chứng / giới hạn |
| --- | --- | --- |
| Tự động | PASS 38/38 | Audio giả lập, model thật khởi động, tín hiệu tổng hợp, mock, log, chống lặp và cleanup player |
| Mock độc lập | PASS 9/9 | Chạy bằng Python hệ thống, không cần microphone/model/dependency; CLI phát đúng 2 event mô phỏng |
| TEST 1 mic | PASS | Host nhận ALC256 Analog / microphone tích hợp |
| TEST 2 capture | PASS | 5 s, 80.000 sample, peak 852, RMS 162,72, clipping 0% |
| TEST 3 engine | PASS | Backbone INT8 và 5 nhóm mẫu neural đã mở thành công |
| TEST 4 lời nói mới | PASS 3/3 | 3 lượt mới × 15 s lúc 21:01–21:02, đều nhận “Maika ơi” |
| TEST 5 một event/lần | PASS | Số event [1, 1, 1]; người dùng xác nhận mỗi lượt nói một lần và nghe một câu đáp |
| Âm tính trước đó | PASS trong 30 giây | 0 event; chỉ là lượt ngắn, chưa chứng minh chống nhiễu dài hạn |
| Âm tính sau giọng đáp mới | PASS trong 60 giây | 0 event, 0 cửa sổ clipping; 124 cửa sổ được chấm, positive tối đa 0,635 / negative cùng cửa sổ 0,644; RMS tối đa 594,5 |
| Tạo WAV trên CPU | PASS | Nạp model 0,81 s; tạo “em nghe” 0,55 s và “em đây” 0,59 s, mỗi WAV dài 1,20 / 1,39 s |
| Chất lượng WAV mới | PASS theo người dùng | Đã nghe qua loa, xác nhận rõ/tự nhiên hơn và chọn giọng này |
| Dependency | PASS | `pip check` không có dependency lỗi |

Ba wake event mới: 21:01:44, 21:02:07, 21:02:35 (Asia/Ho_Chi_Minh).
Điểm positive cao nhất từng lượt: 0,774 / 0,797 / 0,789; điểm âm tính cùng cửa
sổ: 0,578 / 0,651 / 0,617; RMS cao nhất: 400,2 / 459,9 / 376,8.

Lịch sử FAIL được giữ để đánh giá giới hạn: neural ban đầu có số event [0, 1, 1].
Sau thêm mẫu âm tính, có lượt phát 2 event cách 5 giây trong khi người dùng chỉ
nói một lần. Đã thêm 2 cửa sổ cao liên tiếp; phiên tiếp theo còn bỏ sót trước
khi phát hiện gain hệ thống thay đổi. Không coi các lỗi cũ đã được chứng minh
hết trong mọi môi trường chỉ vì ba lượt mới PASS.

Các test mock/tổng hợp không xác nhận khả năng nhận tiếng Việt trên mic thật.
Mẫu enrollment không được dùng làm bằng chứng kiểm thử độc lập. Đã thêm chẩn
đoán độ giống trước/sau lọc và RMS để phân biệt audio yếu với mẫu âm tính chặn.
Lượt âm tính 60 giây hướng dẫn người dùng nói câu khác/để tiếng nền bình thường;
không đo mức dB hay xác nhận TV/nhạc cụ thể. Không suy ra mức chống nhiễu chuẩn hóa.

## Audio hệ thống và rollback

Ban đầu mic clipping 79,354% ở lượt 5 giây. Sau thông báo và được phép:
`Internal Mic Boost,0` giảm 3 (+30 dB) → 0 (0 dB); `Capture` giảm 63 (+30 dB)
→ 23 (0 dB). Chẩn đoán còn thấy xung DC lớn lúc mở mic, nên code bỏ 2 giây đầu
capture; tín hiệu sau đó ổn định, clipping 0% trong các cửa sổ đã đo.

Trong phiên tiếp tục, gain trở lại +30/+30 dB và ID PipeWire đổi. Chưa xác định
nguồn thay đổi. Đã thông báo rồi đặt lại cả hai về 0 dB; không tự sửa file cấu
hình để cố định gain. Mức phát loa đọc được là 35%; chưa đổi volume hệ thống.
Không restart dịch vụ audio, không mở port, không sửa ALSA/PipeWire config file.

### Sửa lỗi clipping tái diễn ngày 2026-09-13

Người dùng chạy `listen` và lỗi ngay khi calibrate. Mixer cho thấy Capture và
Internal Mic Boost,0 đều +30 dB; đo 10 giây thấy clipping 99,719% ở giây đầu,
vẫn 26,175% ở giây 10. Đây là quá gain kéo dài, không chỉ xung khởi động.

PipeWire source ALC256 báo volume 1,00; route input có `volumeBase=0.001`,
`save=false`. State WirePlumber chưa có volume của input. Vì vậy cách chỉ
chỉnh ALSA trước đó chưa lưu mức mong muốn vào trình quản lý tuyến audio;
việc tái tạo/khôi phục tuyến với volume mặc định là nguyên nhân phù hợp bằng chứng.
Chưa quan sát trực tiếp tác nhân đã tái tạo tuyến giữa hai phiên.

Sau thông báo, chạy `wpctl set-volume 47 0.10` trên đúng nguồn tích hợp đang
được inspect. PipeWire báo 0,10; ALSA xác nhận Capture 23/0 dB và Boost 0/0 dB.
WirePlumber tự lưu mục `input:analog-input-internal-mic:channelVolumes`
≈0,001 cho hai kênh vào `default-routes`. Không chỉnh volume loa, restart
service, đổi routing, cài package hoặc sửa file cấu hình hệ thống thủ công.
App vẫn từ chối audio clipping và hiện thêm số đo để người dùng chẩn đoán.

Kiểm chứng sau sửa: TEST 1–2 PASS; capture 5 giây/80.000 sample, RMS 81,32,
peak 941, clipping 0%. Mở lại `listen --seconds 15 --diagnostic` qua `[READY]`,
exit 0, không cửa sổ clipping; mức mic sau khi đóng/mở capture vẫn 0,10.
38/38 test tự động và 9/9 mock PASS. Lượt listen này không có cửa sổ vượt
min_rms, nên chỉ xác nhận khởi động/capture, không tính là kiểm thử wake word
mới. Đã xác nhận state lưu trên đĩa; chưa reboot/restart audio để thử khôi phục.

Đọc trạng thái: `amixer -c 0 scontents`, `wpctl status`.
Khôi phục mức gain PipeWire ban đầu khi cần, trên đúng mic tích hợp (có thể
clipping trở lại):

```bash
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0
```

Tắt tiếng đáp bằng `feedback_enabled: false` trong config project; chọn lại WAV
cũ bằng `feedback_path: "assets/em_nghe.wav"`. Các model/mẫu cũ được giữ nguyên.

## Việc còn lại trong M1

Kiểm thử câu khác, quạt/TV/nhạc ở khoảng cách sử dụng và thời gian dài hơn.
Chưa có bằng chứng nhận diện ổn định đa giọng, khoảng cách xa hoặc nhiều giờ.
Chỉ sang milestone sau khi người dùng xác nhận wake word hoạt động ổn định.
