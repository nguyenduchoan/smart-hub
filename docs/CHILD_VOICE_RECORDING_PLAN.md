# Kế hoạch thu âm giọng bé cho wake word “Maika ơi”

Ngày lập: **2026-09-17**. Dự án: **wake-work / smart-hub**.

Trạng thái: **kế hoạch chuẩn bị; chưa thu âm, chưa thay cấu hình hoặc model**.

## 1. Mục tiêu và hướng thực hiện

Giúp bé gọi **“Maika ơi” bằng giọng tự nhiên** để thiết bị nhận được một lần,
đáp “em nghe” một lần, đồng thời vẫn nhận người lớn và hạn chế kích hoạt nhầm.

Hướng ưu tiên là **thu WAV thật → kiểm tra dữ liệu → đo hệ thống hiện tại →
thử điều chỉnh → nghiệm thu bằng các lần nói mới**. Bắt đầu bằng 5 mẫu của bé
và 5 mẫu người lớn; chỉ mở rộng sau khi nghe lại và xác nhận cách thu phù hợp.

Các số lượng mẫu, khoảng cách và ngưỡng nghiệm thu dưới đây là **đề xuất cho
pilot tại nhà**, không phải mức bảo đảm độ chính xác của model hoặc tiêu chuẩn
áp dụng cho mọi trẻ. Chưa biết tuổi, cách phát âm và khoảng cách thường dùng
của bé; ưu tiên điều kiện thực tế, không yêu cầu bé đọc theo một giọng chuẩn.

### Hiểu đúng việc “đưa giọng bé vào bộ nhận”

| Thành phần hiện có | Dữ liệu của bé được dùng như thế nào? | Giới hạn cần biết |
| --- | --- | --- |
| `assistant` / `listen-stt`: Silero VAD + STT Zipformer tiếng Việt + khớp cụm gọi | WAV giúp xác định lỗi ở mic, tách giọng, nhận chữ hoặc khớp từ; dùng để so sánh cấu hình | Chép WAV vào thư mục không làm STT tự học; chưa có luồng fine-tune |
| `assistant --wake-profile sensitive` | Thử nhận giọng nhỏ/ngắn bằng thông số VAD và khuếch đại có giới hạn | Chưa có nghiệm thu giọng bé; cần kiểm tra cả báo nhầm |
| `listen`: EfficientWord-Net với mẫu cá nhân | Có thể tạo bộ đặc trưng tham chiếu từ một số mẫu của bé | Đây là engine khác; `assistant` hiện không dùng bộ mẫu này |
| Thu lượt nói sau wake (`--capture-only`) | Kiểm tra câu bé nói sau khi đã đánh thức | Không thay thế thu câu gọi; WAV lượt sau wake không phải mẫu wake |

Trong kế hoạch này, **wake word** là nhận câu gọi, không phải xác minh danh tính
người nói. Người lớn gọi đúng câu vẫn phải được nhận. Nhận lệnh sau wake là
bài kiểm tra bổ sung, không gộp vào độ chính xác câu gọi.

## 2. Những gì cần chuẩn bị

### Người tham gia và nhịp thu

- Một người lớn vận hành máy, ghi nhãn và xác nhận người nói; bé chỉ cần gọi.
- Giải thích đơn giản: “Nghe tiếng tít xong, con gọi Maika ơi một lần nhé”.
- Cho bé thử tự nhiên trước khi ghi; không nói đè hoặc đọc mẫu trong cửa sổ thu.
- Mỗi đợt 5 câu, nghỉ 1–2 phút. Một buổi nhỏ khoảng 3–5 phút, tối đa 10–15 câu
  nếu bé vẫn thoải mái; dừng sớm khi bé chán, mệt hoặc không muốn tiếp tục.
- Không yêu cầu hét, cố nói thật nhỏ, giả giọng người lớn hoặc lặp lại đến khi
  hệ thống nhận. Một lần không nhận vẫn là dữ liệu cần giữ để phân tích.

### Thiết bị và phòng

- Dùng **đúng microphone, máy và vị trí đặt dự kiến sử dụng**. Với mic laptop,
  giữ hướng và góc màn hình nhất quán; đặt mic phù hợp tầm bé.
- Chọn vị trí thường gọi làm mốc; gợi ý ban đầu 0,5–1 m. Mốc xa 1,5–2 m chỉ
  áp dụng nếu gia đình thực sự dùng ở khoảng đó. Ghi khoảng cách đã chọn.
- Buổi đầu tắt TV/nhạc, tránh quạt thổi trực tiếp vào mic và tiếng gõ bàn.
- Các buổi sau thêm quạt/tiếng sinh hoạt ở mức thường dùng, không tăng tiếng
  ồn chỉ để tạo thử thách. Ghi nguồn tiếng nền và vị trí tương đối.
- Giữ nguyên mic, routing và gain trong một nhóm đo. Nếu đổi, tạo phiên mới.
- Chỉ chạy một chương trình thu/nghe tại một thời điểm. Dừng listener đang
  chạy bằng Ctrl+C ở terminal của nó trước khi thu; không dừng dịch vụ khác.

### Kiểm tra trước buổi đầu

Các lệnh trong tài liệu chạy từ thư mục sau. **Những lệnh mở mic/loa chỉ chạy
khi bắt đầu buổi thu hoặc kiểm thử, không cần chạy để đọc kế hoạch.**

```bash
cd /home/mrhoan/source/wake-work/smart-hub
wpctl status
wpctl inspect @DEFAULT_AUDIO_SOURCE@
.venv/bin/python scripts/smart_hub.py check-mic --seconds 10
```

Trong lượt kiểm tra, chờ khoảng 2 giây ổn định rồi gọi vài lần với mức giọng
thường dùng. Kiểm tra thiết bị đúng, có tín hiệu, không rè/vỡ và không clipping.
Lệnh này đo audio trong RAM, không lưu WAV; PASS chưa chứng minh nhận được bé.

Repo từng gặp mic quá gain. Mức PipeWire `0.10` trong tài liệu cũ là lịch sử
của đúng mic đó, **không phải mức cần ép cho mọi thiết bị**. Khi có clipping,
kiểm tra nguồn và gain trước, không dùng profile nhạy hơn để chữa âm bị vỡ.
Quy trình này không cần mở port hoặc triển khai dịch vụ.

## 3. Buổi pilot: làm được ngay bằng công cụ hiện có

### Bước 1 — Thu thử một câu của bé

```bash
.venv/bin/python scripts/record_wake_samples.py --speaker child --takes 1
```

1. Người lớn đứng ngoài hướng thu, bé ở vị trí đã chọn.
2. Chờ khoảng 7 giây chuẩn bị; chỉ bắt đầu sau tiếng tít. Chừa một nhịp ngắn
   khoảng 0,3–0,5 giây để tránh nói vào phần đuôi tiếng tít bị bỏ.
3. Bé nói **“Maika ơi” đúng một lần**, rồi im lặng tới hết cửa sổ 5 giây.
4. Ghi lại đường dẫn xuất hiện sau `[OUTPUT]` và kiểm tra `[SAVED]`.
5. Dừng công cụ xong mới nghe lại WAV, ở âm lượng vừa phải, xác nhận đủ đầu
   câu và âm “ơi”. Mẫu thử này dùng làm quen, không đưa vào điểm nghiệm thu.

Có thể nghe bằng trình phát local hoặc lệnh dưới; thay đường dẫn bằng file
thật vừa thu:

```bash
aplay -D pipewire 'recordings/THU_MUC_VUA_THU/take-01.wav'
```

### Bước 2 — Thu 5 câu của bé và 5 câu người lớn

Chạy từng lệnh khi đúng người đã sẵn sàng, không chạy đồng thời:

```bash
.venv/bin/python scripts/record_wake_samples.py --speaker child --takes 5
.venv/bin/python scripts/record_wake_samples.py --speaker adult --takes 5
```

Mỗi lệnh khoảng 40 giây, gồm chuẩn bị, 5 cửa sổ thu và khoảng nghỉ ngắn.
Mỗi tiếng tít ứng với một câu. Nếu bé muốn nghỉ giữa chừng, Ctrl+C; các file
đã ghi vẫn cần được nghe và đánh dấu riêng. Không tính phiên dở là đủ 5 mẫu.

Kết quả hiện có:

```text
recordings/
  <ngày-giờ>-child-<mã>/
    take-01.wav
    ...
    take-05.wav
    manifest.json
  <ngày-giờ>-adult-<mã>/
    ...
```

WAV là **PCM signed 16-bit, mono, 16.000 Hz, mỗi file 5 giây**. Script ghi
thời gian, nhãn `child/adult`, thiết bị ALSA, thống kê tín hiệu và SHA-256;
`speaker_confirmed` ban đầu là `false`, `status` khi thu đủ là
`captured_pending_review`. Các trường này chưa thay thế việc nghe và duyệt.

### Bước 3 — Chẩn đoán pilot rồi mới mở rộng

- Nghe lại cả 10 mẫu, xác nhận bé/người lớn và nội dung thật.
- Đánh dấu mẫu lỗi kỹ thuật, thu bù nếu cần; giữ mẫu hợp lệ mà hệ thống nhận sai.
- Chạy baseline WAV ở mục 8; đối chiếu trẻ/người lớn cùng điều kiện mic.
- Nếu cả hai đều hỏng tín hiệu, sửa cách thu trước. Nếu người lớn nhận được
  nhưng bé không nhận, chuyển sang phân loại lỗi VAD/STT và thu thêm có mục đích.
- Nếu pilot tốt, vẫn cần những lần gọi mới và câu âm tính trước khi kết luận.

## 4. Bộ dữ liệu mở rộng và cách chia

Thu theo nhiều buổi ngắn trong vài ngày; số buổi phụ thuộc sự thoải mái của bé.
Không cần hoàn tất toàn bộ trong lần đầu. **5 mẫu pilot dùng để khám phá lỗi,
không thay thế bộ kiểm thử cuối.**

| Nhóm | Dùng để điều chỉnh (`dev`) | Kiểm thử giữ riêng (`test`) | Cách thu |
| --- | ---: | ---: | --- |
| Bé gọi đúng, phòng yên, khoảng cách thường dùng | 10 | 10 | Giọng và hướng mặt tự nhiên |
| Bé gọi đúng, phòng yên, khoảng cách xa thực tế | 10 | 10 | Giữ nguyên thiết bị/gain; ghi khoảng cách |
| Bé gọi đúng, tiếng nền thường gặp, khoảng cách thường dùng | 10 | 10 | Quạt hoặc tiếng sinh hoạt; không nói chồng có chủ đích |
| Bé nói câu không kích hoạt/gần giống | 20 | 20 | Danh sách ở mục 5, mỗi câu 2 lượt độc lập |
| Người lớn gọi đúng để kiểm tra hồi quy | Pilot 5 mẫu | 10 | Các lượt mới ở vị trí thường dùng |
| Nền không có câu gọi, đo live | 15 phút | 30 phút | Không cần bé ngồi chờ; ghi đủ thời gian và điều kiện |

Tổng ngoài pilot: **60 câu gọi của bé + 40 câu âm tính của bé + 10 câu gọi
người lớn**, cùng 45 phút quan sát nền. Chia thành các đợt 5 câu; gộp 2–3 đợt
thành một buổi nhỏ, nghỉ theo nhu cầu. Nếu chưa thu đủ, báo số thực tế và
trạng thái “chưa đủ dữ liệu”, không nội suy thành kết quả của toàn bộ kế hoạch.

Quy tắc chia dữ liệu:

1. Gán `dev/test` theo **buổi thu độc lập**, không chia ngẫu nhiên các file
   liên tiếp của cùng buổi vào hai tập. Mỗi buổi có `session_id` riêng.
2. Có thể nghe bộ test để xác nhận chất lượng/nhãn, nhưng chưa dùng kết quả
   nhận dạng của nó để chọn profile, alias, ngưỡng hoặc mẫu tham chiếu.
3. Chốt ứng viên và phiên bản trước lần chấm test đầu tiên. Nếu đã dùng lỗi
   của test để chỉnh tiếp, bộ đó trở thành dữ liệu phát triển; thu test mới.
4. File cắt lại, tăng âm lượng, thêm nhiễu hoặc đổi tốc độ phải theo cùng tập
   với file gốc. Không coi bản biến đổi là một lần bé nói độc lập.
5. Nghe nhầm/bỏ sót trên WAV rõ, đúng câu vẫn phải tính là lỗi nhận dạng;
   không loại khỏi test để làm tỷ lệ cao hơn.

## 5. Kịch bản lời nói

### Mẫu dương: phải có một wake

Mỗi file chỉ có **một lần “Maika ơi”**. Cho bé nói như lúc gọi thiết bị thật:
thanh điệu, nhịp và âm lượng tự nhiên. Khi đã có mẫu thường dùng, có thể thêm
lượt quay đầu nhẹ hoặc nói nhỏ tự nhiên nếu đó là tình huống trong nhà.
Ghi thành điều kiện riêng, không trộn với nhóm phòng yên tiêu chuẩn.

Không bắt bé rút câu vào 1,5 giây chỉ để hợp một model. STT hiện tại và engine
mẫu cá nhân có giới hạn khác nhau; nếu câu của bé chậm hơn, giữ nguyên bản thu.
Nếu không chắc bé đã nói đủ câu, gắn `needs_review`, không gán nhãn bằng chữ
STT đoán được. Mục tiêu là hệ thống thích nghi với lời gọi thực tế của bé.

### Mẫu âm: không được wake

Thu 10 câu dưới, mỗi câu 2 lần ở tập dev và 2 lần **mới** ở tập test:

| STT | Câu | Mục đích |
| --- | --- | --- |
| 1 | Maika | Thiếu “ơi” |
| 2 | Mai ca | Gần tên nhưng thiếu “ơi” |
| 3 | Mai ơi | Gần âm nhưng khác cụm gọi |
| 4 | Mẹ ơi | Lời gọi thường gặp |
| 5 | Ba ơi | Lời gọi thường gặp |
| 6 | Mai đi chơi | Gần một phần âm của tên |
| 7 | Con chơi nữa | Lời nói sinh hoạt |
| 8 | Bật đèn phòng khách | Câu lệnh chưa có wake |
| 9 | Tắt quạt | Câu lệnh ngắn chưa có wake |
| 10 | Em nghe | Câu giống tiếng đáp của thiết bị |

Có thể thay câu bé khó nhớ bằng câu đời thường tương đương; lưu chính xác
nội dung thay thế. Tiếng cười, ho, đồ chơi, bàn phím và quạt là nhóm nền bổ
sung, không yêu cầu bé cố ho hay tạo âm không thoải mái.

**TV/người khác nói đủ “Maika ơi” là trường hợp riêng.** Bộ khớp từ hiện tại
có thể kích hoạt vì thực sự nghe đủ cụm; nó chưa biết ý định gọi và chưa phân
biệt người nói. Gắn nhãn `keyword_in_background`, báo riêng giới hạn này;
không gộp vào tập “không chứa câu gọi”. Nếu cần bỏ qua TV nói đúng câu,
đó là yêu cầu phát triển riêng về nguồn âm/ý định gọi.

### Thu câu âm tính bằng script hiện có

`record_wake_samples.py` chưa có tùy chọn câu đọc, nhãn âm tính hoặc thu nền
dài. Có thể tận dụng cửa sổ WAV để thu từng câu ở trên:

```bash
.venv/bin/python scripts/record_wake_samples.py --speaker child --takes 1
```

Người lớn nói rõ câu cần đọc **trước khi bắt đầu**; bé đọc câu âm tính đã
chuẩn bị sau tiếng tít. Lời nhắc terminal và `manifest.expected_phrase` vẫn
là “Maika ơi”: đó là giá trị mặc định của công cụ, **không phải nhãn đúng của
file âm tính**. Ghi `label=negative`, `expected_events=0` và transcript thật
vào bản duyệt ở mục 6. Không để benchmark suy ra nhãn từ manifest thô này.
Khi thu nhiều, ưu tiên bổ sung công cụ theo đầu việc CV-04 để tránh nhầm nhãn.

## 6. Lưu trữ, nhãn và quyền riêng tư

Giữ WAV gốc và manifest do recorder tạo ở nguyên thư mục. Lập thư mục phụ
`recordings/child-study/` chứa nhãn, kết quả và các bản xử lý; đây là cấu trúc
**cần tạo khi triển khai**, chưa được script tự sinh:

```text
recordings/child-study/
  sessions.json       # Điều kiện và thông tin tối thiểu từng buổi
  labels.jsonl        # Một dòng JSON cho mỗi WAV đã duyệt
  derived/            # Bản cắt/chuyển đổi, truy ngược được WAV gốc
  results/            # Kết quả baseline và ứng viên theo phiên bản
  decisions.md        # Kết luận giữ/đổi cấu hình, lý do và cách quay lại
```

Đường dẫn `source` trong nhãn tính từ gốc repo. Mẫu minh họa sau phải thay
đường dẫn/checksum và chỉ xác nhận người nói sau khi người lớn duyệt:

```json
{
  "sample_id": "c01-s01-p01",
  "source": "recordings/THU_MUC_VUA_THU/take-01.wav",
  "source_sha256": "THAY_BANG_SHA256_THAT",
  "speaker_id": "child_01",
  "speaker_confirmed": true,
  "session_id": "S01",
  "split": "dev",
  "label": "positive",
  "transcript_human": "Maika ơi",
  "expected_events": 1,
  "distance_m": 1.0,
  "condition": "quiet_normal_voice",
  "review_status": "accepted",
  "review_note": "Đủ đầu câu và âm ơi; chỉ có một người nói"
}
```

Mỗi `session_id` cần ghi ngày giờ, mic/nguồn PipeWire thực, gain, khoảng cách,
phòng, nguồn nền, người duyệt và các thay đổi giữa buổi. `device=pipewire`
trong manifest thô chưa đủ xác định mic phần cứng. Dùng mã `child_01`,
`adult_01`; không cần tên thật, ngày sinh, địa chỉ hoặc thông tin trường học.

Giữ `manifest.json` gốc làm thông tin thu; bản nhãn đã duyệt là nguồn quyết
định đưa mẫu vào phép đo. Nếu manifest vẫn `speaker_confirmed=false`, chỉ
dùng mẫu khi có bản duyệt liên kết đúng SHA-256 và xác nhận người nói; không
đọc `status=captured_pending_review` thành dữ liệu đã đạt.

Người giám hộ thống nhất mục đích lưu giọng, thời gian giữ và người được truy
cập; bé có thể dừng bất cứ lúc nào. Lưu và xử lý local; không đưa WAV, đặc
trưng giọng hoặc transcript riêng tư lên Git/cloud. Thư mục `recordings/`
và `models/*.npz` đã được `.gitignore` loại trừ, nhưng Git ignore không phải
kiểm soát truy cập. Recorder hiện tạo dữ liệu với `umask 077`; thư mục phụ
cũng cần quyền riêng tư tương đương. Khi chia sẻ kết quả, ưu tiên số đếm
tổng hợp. Có thể chọn mốc xem lại/xóa sau 30 ngày nghiệm thu, theo thỏa thuận
của gia đình; việc xóa phải bao gồm bản cắt, embedding và bản sao liên quan.

## 7. Kiểm tra chất lượng từng file

| Nội dung kiểm tra | Chấp nhận | Xử lý khi không đạt |
| --- | --- | --- |
| Định dạng | WAV PCM không nén, mono, 16-bit, 16 kHz; đọc đủ dữ liệu | Chuyển đổi trên bản sao hoặc thu lại; đổi đuôi MP3 thành WAV không đủ |
| Độ dài | WAV gốc do recorder tạo đủ 5 giây | Kiểm tra phiên bị ngắt; chỉ chấp nhận riêng nếu vẫn đủ câu và ngữ cảnh |
| Nội dung | Một câu theo nhãn, đúng người, không bị người lớn nói đè | `needs_review` hoặc thu lại; ghi lý do |
| Đầu/cuối câu | Không mất “Mai/Mai-ka” hoặc “ơi”; có khoảng lặng sau câu | Thu lại, tránh nói sát tiếng tít/cuối cửa sổ |
| Tín hiệu | Nghe rõ, không rè/vỡ; mục tiêu không có clipping đáng kể | Xem waveform/thống kê nếu cần; kiểm tra mic/gain và thu lại |
| Phân loại | Mẫu khó nhưng đúng, đủ câu vẫn được giữ | Ghi điều kiện/lỗi nhận dạng, không loại vì STT sai |
| Trùng lặp | SHA-256 và nguồn gốc xác định được | Không tính file sao chép như một mẫu mới |

Script dừng khi tỷ lệ clipping toàn clip **lớn hơn 1%**, còn runtime có thể
loại frame/đoạn bị clipping. Vì vậy, “script không báo lỗi” chưa đủ: một xung
vỡ ngắn vẫn có thể làm hỏng câu. Thống kê `clipped_percent` làm tròn cũng
không thay thế nghe lại và kiểm tra chi tiết khi nghi ngờ. Không đặt một
ngưỡng RMS cứng để loại toàn bộ giọng nhỏ của bé.

Giữ bản gốc. Chưa lọc nhiễu, chuẩn hóa âm lượng hoặc cắt sát lời nói trước
khi chạy baseline vì sẽ làm khác tín hiệu runtime thật. Nếu tạo bản xử lý,
ghi tham số và liên kết nguồn; chỉ so sánh như một phương án thử nghiệm.

## 8. Đưa dữ liệu vào quá trình cải thiện bộ nhận

### 8.1. Baseline offline có thể chạy ngay

Với một WAV dương đã duyệt:

```bash
.venv/bin/python scripts/smart_hub.py listen-stt \
  --wav 'recordings/THU_MUC_MAU_DUONG/take-01.wav' \
  --show-text --diagnostic --expect-events 1
```

Với một WAV âm đã duyệt:

```bash
.venv/bin/python scripts/smart_hub.py listen-stt \
  --wav 'recordings/THU_MUC_MAU_AM/take-01.wav' \
  --show-text --diagnostic --expect-events 0
```

Thay đường dẫn bằng file thật. Chế độ `--wav` không mở mic/loa; PASS nghĩa là
số event khớp và không có lỗi clipping được báo, không chứng minh đã nghe
tiếng đáp ngoài đời. Ghi cả transcript, số event, mã thoát và chẩn đoán lỗi.

**Giới hạn CLI đã kiểm tra:** `listen-stt --wav` dùng profile mặc định
`standard`; chưa có `--wake-profile`. `assistant` có chọn profile nhưng
chưa nhận `--wav`. Không dùng một lệnh không tồn tại để tuyên bố A/B trên
cùng WAV đã chạy.

### 8.2. Xác định lỗi trước khi sửa

| Quan sát | Khả năng cần kiểm tra | Hành động tiếp theo |
| --- | --- | --- |
| WAV rè, quá nhỏ do đặt mic hoặc mất âm đầu | Thu âm/thiết bị | Chỉnh cách đặt và thời điểm nói; kiểm tra gain khi cần |
| WAV rõ nhưng không có đoạn chữ được chốt | VAD, cắt câu hoặc lỗi audio | So sánh `standard/sensitive`, xem thống kê và đầu/cuối đoạn |
| Có chữ, nhưng sai tên hoặc mất “ơi” | STT/cách phát âm/đoạn đưa vào STT | So sánh mẫu thật, tránh kết luận chỉ từ một lần |
| Có đủ cụm đúng mà không wake | Quy tắc khớp, cooldown hoặc trạng thái runtime | Kiểm tra event theo thời điểm và trạng thái |
| Có `[WAKE]` nhưng bé không nghe “em nghe” | Playback/loa/routing | Kiểm tra riêng tiếng đáp; không tăng độ nhạy wake để sửa loa |
| Người lớn tốt, bé kém ở cùng điều kiện | Khác biệt giọng, mức âm hoặc cách phát âm | Dùng dữ liệu bé để thử ứng viên rồi kiểm tra hồi quy người lớn |

Với STT, chỉnh `threshold` của engine neural trong `config.json` không thay
đổi VAD/STT. `sensitive` thay nhiều thông số cùng lúc; so sánh hai profile
chỉ cho biết khác biệt giữa hai cấu hình, chưa chứng minh thông số nào gây ra.

### 8.3. Thử profile bằng mic thật trên các lượt phát triển

Dừng phiên trước rồi mới chạy phiên sau. Mỗi phiên nói **đúng 10 lần mới**,
cách nhau ít nhất 5 giây và sau tiếng đáp; giữ cùng điều kiện. Đây là thử
live bổ sung, không phải cùng một WAV và không thay thế test giữ riêng.

```bash
.venv/bin/python scripts/smart_hub.py assistant --wake-only \
  --wake-profile standard --show-wake-text --diagnostic \
  --seconds 180 --expect-events 10

.venv/bin/python scripts/smart_hub.py assistant --wake-only \
  --wake-profile sensitive --show-wake-text --diagnostic \
  --seconds 180 --expect-events 10
```

Có thể chia 10 câu thành hai đợt 5 trong cùng phiên, hoặc hai phiên với
`--expect-events 5` nếu bé cần nghỉ lâu. Đổi thứ tự thử ở buổi khác nếu cần
giảm ảnh hưởng bé quen bài/mệt. Ghi kết quả từng lượt; **10 event tổng không
đảm bảo 10 lần đều đúng** nếu có lượt bỏ sót và lượt nhận hai lần.

### 8.4. So sánh chính xác trên cùng WAV — cần bổ sung công cụ

Đầu việc CV-05 cần tạo runner đọc nhãn đã duyệt, phát từng frame WAV qua
`LocalSTT(wake_profile=...)` và bộ khớp từ, chạy cả hai profile trên cùng tập
dev. Runner phải dùng quy tắc kiểm tra audio/clipping và reset tương ứng
runtime, ghi rõ việc flush cuối WAV; không gọi thẳng STT trên câu đã cắt để
thay cho đánh giá cả VAD + STT + wake. Mỗi WAV độc lập có trạng thái mới;
thêm bài audio liên tục để kiểm tra cooldown và nghe lại sau nghỉ.

Kết quả tối thiểu: `sample_id`, SHA-256, split, phiên bản code/model/cấu hình,
profile, transcript, số event mong đợi/thực tế, clipping, lỗi xử lý và thời
gian xử lý. WAV hỏng/runner lỗi là `ERROR`, không được tính là âm tính đúng.
Không coi thời gian giải mã offline là độ trễ phản hồi mic/loa.

### 8.5. Quy tắc chọn ứng viên

1. Giữ `standard` nếu đã đáp ứng mục tiêu; thử `sensitive` nếu dữ liệu chỉ
   ra giọng nhỏ/ngắn bị bỏ sót. Chỉ chọn khi không làm tăng lỗi âm tính.
2. Nếu cần chỉnh tiếp, thay một thông số mỗi lần trong cùng pipeline, lưu
   cấu hình và kết quả dev trước/sau. Không chỉnh theo test cuối.
3. Alias mới chỉ được cân nhắc khi chữ sai lặp lại trên các mẫu dev khác nhau
   của **đủ câu gọi**, và đã thử các câu âm tính dễ lẫn. Không bỏ “ơi”, không
   bỏ dấu hoặc thêm từ ngắn để làm tỷ lệ nhận cao hơn.
4. `listen-stt` có `--alias`, nhưng `assistant` hiện chưa có tùy chọn này.
   Nếu quyết định dùng alias trong assistant, cần đầu việc tích hợp và test
   riêng; thử alias bằng `listen-stt` chưa có nghĩa runtime đã áp dụng.

### 8.6. Nếu STT vẫn không nhận ổn: thử bộ mẫu cá nhân riêng

Đây là nhánh tùy chọn sau khi có bằng chứng STT chưa đạt, không phải bước
tự động của thu âm:

- Chọn khoảng 5 mẫu dev rõ, đại diện cho giọng bé; engine hiện nhận **3–10
  nhóm mẫu**, không nạp toàn bộ 60 WAV vào một bộ tham chiếu.
- Bộ tham chiếu neural dùng cửa sổ tối đa **1,5 giây**. Chỉ tạo bản cắt khi
  vẫn giữ trọn câu; nếu bé thường nói dài hơn, ghi giới hạn của phương án.
- `enroll` hiện thu trực tiếp từ mic, không có `enroll --wav`. Để tái sử dụng
  WAV đã duyệt cần bổ sung công cụ import/cắt/kiểm tra nguồn, hoặc tổ chức
  buổi enrollment mới và ghi rõ đó là dữ liệu mới.
- Tạo đường dẫn model, negative model và cấu hình thử riêng; bảo toàn bộ mẫu
  người lớn. Đường dẫn tương đối trong config được tính từ thư mục config,
  nên phải kiểm tra trước khi chạy. Không ghi đè/copy đè model mặc định.
- Dùng mẫu âm tính dev cho bộ lọc, giữ test riêng; không đưa lời gọi đúng
  của bé hoặc người lớn vào negative chỉ để giảm báo nhầm.
- Kiểm thử bằng `listen` với bộ riêng. Muốn assistant dùng kết quả này cần
  tích hợp backend và nghiệm thu toàn vòng; chưa có cơ chế tự ghép hai engine.
  Không bật kiểu “engine nào nhận cũng wake” nếu chưa đo lại báo nhầm.

Huấn luyện lại toàn bộ STT hoặc một model wake word mới là phạm vi khác,
cần thiết kế dữ liệu/huấn luyện riêng. Bộ thu nhỏ này chưa phải cơ sở để
hứa hẹn độ chính xác đa trẻ hoặc đa thiết bị.

## 9. Nghiệm thu và cách tính kết quả

Chốt ứng viên trước khi mở kết quả test; dùng cùng code/model/cấu hình khi
chấm offline và kiểm tra live. Các ngưỡng sau là **mục tiêu cần đạt**, chưa
có số liệu xác nhận.

| Bài kiểm tra | Mục tiêu pilot |
| --- | --- |
| 10 WAV test bé gọi, yên tĩnh/khoảng cách thường dùng | 10/10 file có đúng một event |
| 10 WAV test bé gọi, khoảng cách xa đã chọn | Ít nhất 9/10 có đúng một event |
| 10 WAV test bé gọi, nền thường dùng | Ít nhất 9/10 có đúng một event |
| 20 WAV test câu âm tính của bé | 0 event |
| 10 WAV test mới của người lớn | 10/10 có đúng một event |
| 30 phút nền live không chứa câu gọi | 0 event; không clipping/dropout/phiên dừng lỗi |
| 10 lần gọi live mới của bé ở vị trí thường dùng | 10/10 nhận đúng một lần và nghe đúng một tiếng đáp |
| 5 lần gọi live mới của người lớn | 5/5 nhận đúng một lần và nghe đúng một tiếng đáp |
| Độ trễ live | Mục tiêu tạm: ít nhất 9/10 lượt bé nhận thành công bắt đầu đáp trong 2 giây sau khi nói xong |

Khoảng xa và nền là hai điều kiện đã chọn cụ thể, không khái quát thành
“nhận tốt mọi khoảng cách/tiếng ồn”. Báo từng nhóm thay vì chỉ báo tỷ lệ gộp.
Nếu một lượt có 2 event, ghi lỗi trùng; không tính nó là lượt thành công
đúng một lần.

Các chỉ số:

- **Nhận đúng một lần** = số lượt dương có đúng 1 event / tổng lượt dương hợp lệ.
- **Bỏ sót (FRR)** = số lượt dương có 0 event / tổng lượt dương hợp lệ.
- **Lượt trùng** = số lượt dương có trên 1 event; báo cả số event thừa.
- **Báo nhầm trên câu âm** = số câu âm có ít nhất 1 event / tổng câu âm.
- **Báo nhầm theo giờ** = tổng event sai / số giờ nền thực sự quan sát.
- **Độ trễ live** = lúc bắt đầu nghe tiếng đáp trừ lúc kết thúc câu gọi;
  báo cách đo, số lượt, trung vị và lượt chậm nhất. Đo tay chỉ là ước lượng;
  benchmark decode không đo được chỉ số này. Nếu cần đo chính xác hơn bằng
  bản ghi có cả mic/loa, đó là dữ liệu bổ sung cần thống nhất trước khi thu.

Không ghi “0 báo nhầm/giờ đã được chứng minh” chỉ từ 30 phút: báo đúng
**“0 event trong 0,5 giờ quan sát”**. Khi thiếu thời gian hoặc có lỗi capture,
đánh dấu bài chưa hoàn tất và ghi thời gian hợp lệ thực tế.

### Đo nền và gọi live bằng ứng viên đã chọn

Ví dụ dưới dùng `sensitive`; thay bằng `standard` nếu đó là ứng viên đã chốt.
Chạy ba phiên nền 600 giây ở ba bối cảnh đã ghi để đủ 30 phút. Trong phiên
nền không có ai chủ động nói đủ cụm gọi; không cần bé ở cạnh máy:

```bash
.venv/bin/python scripts/smart_hub.py assistant --wake-only \
  --wake-profile sensitive --seconds 600 --diagnostic --expect-events 0
```

Sau đó chạy riêng bài gọi live, mỗi lần chỉ gọi một câu và chờ đủ lượt:

```bash
.venv/bin/python scripts/smart_hub.py assistant --wake-only \
  --wake-profile sensitive --seconds 180 --diagnostic --expect-events 10
```

Chờ READY rồi nói; không gọi bù khi máy bỏ sót. Ghi thiếu là thiếu. Ngắt
Ctrl+C khi đang dùng `--expect-events` là bài chưa hoàn tất, không phải PASS.
Sau khoảng nghỉ ít nhất 60 giây, cần thử một lượt để kiểm tra nghe lại;
có thể bố trí khoảng nghỉ đó trong phiên gọi.

### Kiểm tra thêm câu nói sau wake

Sau khi wake đạt, bỏ `--wake-only`, giữ profile đã chọn; chờ `[LISTEN COMMAND]`
rồi cho bé nói 5 câu quen thuộc, ví dụ “bật đèn”, “tắt quạt”, “mở nhạc”.
Chấm riêng có mất từ đầu, sai chữ hoặc timeout không. Chưa yêu cầu bé nói
liền “Maika ơi, bật đèn” vì luồng hiện tại chưa hỗ trợ cách dùng đó. Nếu
kiểm tra `--capture-only`, chờ `[LISTEN TURN]`; chế độ đó chưa xuất transcript.

## 10. Backlog thực hiện và điều kiện hoàn tất

Một người có thể kiêm các vai trò. Các đầu việc dưới đây là kế hoạch;
không hàm ý công cụ mới đã được viết hoặc mẫu thật đã được thu.

| Mã | Ưu tiên / phụ thuộc | Phụ trách | Đầu việc và tiêu chí hoàn tất |
| --- | --- | --- | --- |
| CV-01 | P0 | Người lớn + BA | Chốt câu gọi, điều kiện thường dùng, mic và cách lưu; có phiếu phiên và lịch ngắn phù hợp bé |
| CV-02 | P0, sau CV-01 | Người lớn + QC | Thu thử rồi pilot 5 bé + 5 người lớn; nghe, xác nhận nhãn/người và kiểm tra clipping từng file |
| CV-03 | P0, sau CV-02 | Backend + QC | Chạy baseline standard trên pilot; lưu từng kết quả và nhóm nguyên nhân, không đoán thiếu dữ liệu |
| CV-04 | P1, trước thu mở rộng nếu cần | Backend | Bổ sung recorder chọn câu/nhãn dương-âm/session/split và chờ sẵn sàng; giữ WAV gốc, không ghi đè, ghi phiên bị ngắt rõ ràng |
| CV-05 | P1, trước A/B offline | Backend + QC | Runner hai profile trên cùng WAV, dựa nhãn đã duyệt; xuất lỗi và số liệu từng nhóm; không tự gửi dữ liệu hoặc thay cấu hình |
| CV-06 | P1, sau pilot đạt chất lượng | Người lớn + QC | Thu/duyệt đủ dev và test theo mục 4, không trộn buổi giữa hai tập; mẫu thiếu/lỗi có lý do và lượt bù |
| CV-07 | Có điều kiện, sau CV-03/05 | Backend | Nếu STT chưa đạt: thử alias có căn cứ hoặc bộ mẫu neural riêng; mỗi phương án có tích hợp cần thiết và rollback cụ thể |
| CV-08 | P1, sau CV-05/06 và ứng viên chốt | QC + người lớn | Chạy test giữ riêng, nền và live; báo đạt/chưa đạt theo từng tiêu chí, không che lượt bỏ sót/trùng |
| CV-09 | P1, sau CV-08 đạt | Backend + người dùng | Ghi cách khởi chạy ứng viên, bảo toàn baseline, kiểm tra hồi quy người lớn và lưu kết luận sử dụng |

Thứ tự thực tế: **CV-01 → CV-02 → CV-03 → hoàn thiện công cụ cần thiết →
thu mở rộng → chọn trên dev → chốt → chấm test/live → quyết định dùng**.
Nếu mục tiêu chỉ là khám phá lỗi, hoàn tất pilot và báo nguyên nhân trước,
chưa đánh dấu hệ thống đã nghiệm thu.

Không cần giao diện mới trong vòng đầu; CLI và file nhãn đủ để thực hiện.
Khi CV-04/05/07 thay đổi mã nguồn, bổ sung kiểm tra đúng phần thay đổi và
chạy hồi quy repo. Việc lập tài liệu này không cần mở mic hoặc chạy test model.

### Bàn giao và quay lại cấu hình trước

- Lưu phiên bản code (kèm diff nếu chưa commit), checksum model, cấu hình,
  hash dữ liệu đã duyệt và báo cáo trước/sau; code hiện có thay đổi chưa commit
  nên chỉ ghi commit hash sẽ chưa mô tả đủ bản chạy.
- Nếu chỉ đổi profile bằng CLI: dừng phiên đang chạy rồi dùng lại
  `assistant --wake-profile standard` để về baseline.
- Nếu thay alias/code/model: lưu ứng viên ở bản riêng, cùng cấu hình và lệnh
  chạy baseline trước khi chuyển; không dùng reset/ghi đè cả worktree để rollback.
- Sau vài ngày sử dụng, ghi lỗi thực tế có điều kiện đi kèm; chỉ thu bổ sung
  khi cần. Không bật thu âm liên tục mặc định hoặc cam kết chạy 24/7 từ pilot.

## 11. Mẫu ghi nhận khi thực hiện

### Phiếu phiên

```text
Session ID:
Ngày giờ:
Speaker ID: child_01 / adult_01
Mục đích: pilot / dev / test / live / background
Mic thực tế và gain:
Khoảng cách, hướng mặt, vị trí mic:
Tiếng nền:
Code/model/config/profile:
Thư mục dữ liệu hoặc mã bài live:
Số lượt dự kiến / đã thu / đã duyệt / cần thu bù:
Người duyệt:
Ghi chú và thời điểm nghỉ:
```

### Kết quả từng lượt

| Mã mẫu/lượt | Nhãn đúng | Điều kiện | Profile | Chữ STT nghe được | Số event | Nghe tiếng đáp | Độ trễ live | Kết luận/lý do |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Điền khi thực hiện | | | | | | | | |

Với WAV offline, ghi `không áp dụng` ở tiếng đáp/độ trễ live. Với phiên nền,
ghi thời gian bắt đầu/kết thúc thực tế, số event và các gián đoạn.

### Checklist chốt

- [ ] Bé thoải mái, người lớn đã xác nhận cách lưu và sử dụng dữ liệu.
- [ ] WAV và nhãn đã duyệt khớp nhau; không có dữ liệu riêng tư trong Git.
- [ ] Các buổi dev/test tách biệt; chưa dùng test để chọn ứng viên.
- [ ] Có kết quả baseline và ứng viên, bao gồm các lượt lỗi.
- [ ] Đạt từng nhóm câu gọi, câu âm, nền và hồi quy người lớn.
- [ ] Bé gọi trực tiếp nhận một lần, nghe một tiếng đáp; đã thử lại sau nghỉ.
- [ ] Ghi rõ giới hạn TV/nhiễu/khoảng cách và bài chưa thực hiện.
- [ ] Có lệnh chạy cấu hình đã chọn và cách quay lại baseline.

## 12. Nguồn đối chiếu

Hướng dẫn thao tác và giới hạn CLI được đối chiếu với mã trong repo tại ngày
lập kế hoạch:

- [Script thu mẫu](../scripts/record_wake_samples.py): cửa sổ 5 giây, tối đa
  5 lượt/lệnh, manifest và cơ chế lưu local.
- [CLI](../src/smart_hub/cli.py), [STT wake](../src/smart_hub/stt_wake.py),
  [profile VAD/STT](../src/smart_hub/local_stt.py),
  [khớp cụm gọi](../src/smart_hub/stt_keyword.py): tham số và luồng đang có.
- [Engine mẫu cá nhân](../src/smart_hub/neural.py): 3–10 nhóm mẫu, cửa sổ
  1,5 giây và bộ tham chiếu dương/âm.
- [Hướng dẫn wake STT](stt-wake.md), [ghi nhận giọng nhỏ](command-transcript.md),
  [thu lượt nói](turn-capture.md): cách kiểm thử và giới hạn đã biết của dự án.

Tài liệu chính thức bên ngoài, kiểm tra ngày 2026-09-17:

- Sherpa-ONNX có model Zipformer tiếng Việt 30M và hướng dẫn nhận từ WAV/mic
  với VAD. Hợp đồng WAV 16 kHz của kế hoạch này lấy từ **code của repo**,
  không suy ra mọi API upstream đều bắt buộc cùng tần số.
  [Tài liệu model tiếng Việt](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/zipformer-transducer-models.html).
- EfficientWord-Net dùng phương pháp nhận hotword từ ít mẫu tham chiếu;
  điều đó không bảo đảm nhận tốt giọng bé trong môi trường này.
  [Repository chính thức](https://github.com/Ant-Brain/EfficientWord-Net).
