# Kế hoạch dashboard local: Broadlink RM4 mini, thu âm và model wake word

Ngày: **2026-09-19**

Trạng thái: **DỰ THẢO ĐỂ DUYỆT — chưa triển khai**

Repo khảo sát: `smart-hub`, commit `abf0daa`.

Tài liệu này thay thế phạm vi và thứ tự triển khai của
[kế hoạch Broadlink trước](broadlink-provider-plan.md). Phase hiện tại làm dashboard
và kết nối thiết bị độc lập; phần nối câu lệnh giọng nói vào Broadlink chuyển sang phase sau.
Đây là kế hoạch, không phải mô tả các tính năng đã có.

## 1. Kết quả mong muốn

Mở một dashboard local để thực hiện ba nhóm việc:

1. **Broadlink:** kết nối RM4 mini → chọn loại thiết bị, hãng, model/bộ mã →
   thử các nút → lưu bộ điều khiển; có luồng học từ remote gốc.
2. **Thu âm:** tạo phiên người lớn/bé → chủ động bắt đầu từng lượt →
   nghe lại, duyệt mẫu và quản lý bộ dữ liệu đang có.
3. **Wake word:** quản lý các model/bộ mẫu tham chiếu, thêm ứng viên,
   chạy đánh giá offline trên cùng dữ liệu và so sánh riêng người lớn/bé.

Dashboard có giao diện tiếng Việt, nút dễ bấm và bố cục dùng được trên màn hình nhỏ.
Broadlink vẫn là một provider; phần lõi có chỗ nối TuyaSmart sau này.

**Thứ tự đề xuất:** nền dashboard → kết nối/học remote Broadlink → chọn hãng/model
và bộ điều khiển → thu âm/review → model wake word/benchmark → nghiệm thu chung.

### 1.1. Quyết định đề xuất để duyệt nhanh

| Nội dung | Đề xuất |
| --- | --- |
| Cách chạy | Một web server Python local, chạy độc lập với `assistant` |
| Thiết bị đã xác nhận | Một Broadlink **RM4 mini** |
| Điều khiển | Người dùng bấm nút trên dashboard; chưa tự thực thi từ STT |
| Danh mục hãng/model | Catalog local có nguồn rõ ràng, khởi đầu bằng dữ liệu cộng đồng tương thích Broadlink và dữ liệu tự học |
| Trải nghiệm giống app | Chọn loại → hãng → model/bộ mã → thử → lưu; không cam kết sao chép toàn bộ kho mã của app Broadlink |
| Thu âm | Mic/loa trên máy Smart Hub, giữ pipeline PCM hiện tại |
| Model wake word | Lưu ứng viên riêng, đánh giá offline; runtime hiện tại giữ cấu hình đã chọn |
| Dữ liệu cũ | Đọc vào dashboard; WAV/model gốc được giữ nguyên |
| TuyaSmart | Chuẩn bị contract provider, chưa kết nối thật |
| Truy cập | Loopback mặc định; truy cập điện thoại qua LAN là hạng mục triển khai riêng |

Người dùng đã xác nhận: **thu thêm mẫu giọng, quản lý và so sánh model wake word,
có bước duyệt thủ công như quy trình trước**. Phân biệt rõ trên giao diện:

- **Model thiết bị:** mã model của TV/quạt/điều hòa hoặc bộ mã remote.
- **Model wake word:** engine, trọng số, bộ mẫu tham chiếu và cấu hình phát hiện câu gọi.

Thu thêm WAV không tự huấn luyện hay tự cải thiện model đang chạy. Chất lượng
STT của cả câu lệnh cũng là bài toán riêng, chưa được giải quyết chỉ bằng tăng tỷ lệ wake.

## 2. Phạm vi phase này

### Có trong kế hoạch

- Dashboard tổng quan và các màn hình Broadlink, thu âm, duyệt mẫu, wake model, kết quả.
- Discovery hoặc nhập IP RM4 mini, xác thực local, kiểm tra kết nối, lưu gateway.
- Danh mục thiết bị theo loại/hãng/model hoặc bộ mã ứng viên; tìm kiếm và lọc.
- Import catalog được hỗ trợ; thử từng nút có mã; lưu remote đã xác minh.
- Học IR từ remote gốc, học lại thành phiên bản mới, thử và gán nút.
- Bộ điều khiển theo capability thực có: quạt/TV dạng nút, điều hòa theo preset/trạng thái có mã.
- Thu nhiều lượt có nút “Bắt đầu lượt tiếp theo”, hiển thị clipping/RMS/peak, nghe và duyệt.
- Đọc các phiên thu cũ, nhận diện metadata thiếu/lỗi, lưu lịch sử review.
- Registry model/ứng viên, import hoặc tải gói từ nguồn đã khai báo, quản lý phiên bản.
- Tạo bộ mẫu tham chiếu mới cho engine hiện có khi phù hợp; không ghi đè bộ đang dùng.
- Chạy so sánh offline, báo cáo JSON/Markdown và bảng kết quả trên dashboard.
- CLI được giữ làm công cụ chẩn đoán và dùng chung service với dashboard.

### Để phase sau

- Nối wake → STT → router → Broadlink/Tuya; LLM điều khiển thiết bị.
- Nhận dạng danh tính người nói, fine-tune STT tiếng Việt cho trẻ em.
- Huấn luyện mạng wake word mới từ đầu, tạo dữ liệu giọng tổng hợp hàng loạt.
- Đồng bộ toàn bộ tài khoản/kho remote trong app Broadlink; cloud SDK chính thức nếu cần license riêng.
- RF, automation, lịch chạy, macro nhiều thiết bị, nhấn giữ/tự lặp nút.
- Thu trực tiếp bằng mic điện thoại/trình duyệt, nhiều mic hoặc nhiều người dùng đồng thời.
- Public Internet, triển khai service tự khởi động, app mobile native.

**Phạm vi nghiệm thu phần cứng:** một RM4 mini và một thiết bị gia dụng đầu tiên
với khoảng 3–5 thao tác hữu ích. Catalog và dữ liệu hỗ trợ thêm thiết bị; chưa thể
cam kết điều khiển model chưa thử trên phần cứng thật.

## 3. Cơ sở hiện tại và thông tin còn thiếu

| Thành phần repo | Cách tận dụng |
| --- | --- |
| `assistant` đang wake → đáp → STT/log | Chạy độc lập; phase dashboard không thêm nhánh thực thi thiết bị vào vòng này |
| `scripts/record_wake_samples.py` | Tách logic thu thành service; CLI và web dùng cùng quy tắc |
| `scripts/review_child_study.py` | Tái sử dụng kiểm tra WAV/hash, review và phân loại trạng thái |
| `scripts/evaluate_child_study.py` | Làm baseline evaluator cho standard/sensitive |
| `src/smart_hub/child_study.py` | Tận dụng schema/session/labels, mở rộng có version và migration rõ ràng |
| `src/smart_hub/stt_assets.py` | Tận dụng pin/checksum của model STT hiện tại |
| EfficientWord-Net, DTW và enrollment trong repo | Ứng viên so sánh bổ sung; cần adapter offline cùng giao thức đánh giá |
| Chưa có web app/provider | Thêm module riêng, import dependency tùy chọn theo tính năng |

Nguồn nội bộ: [thu âm](../scripts/record_wake_samples.py),
[review](../scripts/review_child_study.py),
[evaluator](../scripts/evaluate_child_study.py),
[dữ liệu child-study](../src/smart_hub/child_study.py),
[model assets](../src/smart_hub/stt_assets.py),
[enrollment](../src/smart_hub/engine.py).

Thông tin cần bổ sung trước buổi thử thật:

| Thông tin | Hiện trạng | Cần cho |
| --- | --- | --- |
| RM4 mini đã vào Wi-Fi/app chưa | Chưa xác nhận | Kết nối LAN |
| IP/MAC/devtype/firmware, mạng máy Smart Hub | Chưa kiểm kê | Xác thực đúng gateway |
| Thiết bị thử đầu tiên và hãng/model | Chưa chọn | Chọn catalog và nút thử |
| Có remote gốc không | Chưa xác nhận | Học IR nếu catalog không khớp |
| Điều khiển từ máy local hay điện thoại | Đề xuất local trước | Cách mở dashboard |
| Nguồn model wake mới cụ thể | Chưa chọn | Import/download và kiểm tra tương thích |

Thông tin chưa có không ngăn việc dựng UI/mock/service. Phần chưa được thử thật
phải hiện “Chưa kiểm chứng”, không tính là PASS phần cứng.

## 4. Bố cục và các màn hình

Thanh điều hướng: **Tổng quan · Thiết bị · Thu âm · Dữ liệu · Wake word · Kết quả**.

| Màn hình | Nội dung và thao tác chính |
| --- | --- |
| Tổng quan | RM4 đã kết nối/chưa kết nối; thiết bị đã lưu; mic rảnh/bận; mẫu chờ duyệt; tác vụ đang chạy |
| Kết nối Broadlink | Tìm trên LAN hoặc nhập IP; xem định danh; kiểm tra kết nối; lưu tên/phòng |
| Thêm thiết bị | Chọn gateway → loại → hãng → model/bộ mã → thử → đặt tên/lưu |
| Bộ điều khiển | Nút/preset được hỗ trợ; lần gửi gần nhất; nguồn bộ mã; sửa mapping hoặc học bổ sung |
| Học remote | Chọn nút → chờ remote gốc → nhận mã → thử → xác nhận → lưu phiên bản |
| Thu âm | Người nói, câu, nhãn, khoảng cách, điều kiện, split, số lượt; tín hiệu mic; bắt đầu/dừng |
| Dữ liệu | Lọc phiên/người nói/split/trạng thái; nghe WAV; xem QC; duyệt từng mẫu |
| Wake word | Model đã có, ứng viên, nguồn/phiên bản; import/tải; tạo bộ tham chiếu; chọn để đánh giá |
| Kết quả | So sánh ứng viên, người lớn/bé, điều kiện; xem lỗi từng mẫu; tải báo cáo |

Phác thảo bố cục:

~~~text
Smart Hub                         Mic: rảnh     RM4 mini: đã kết nối
[Tổng quan] [Thiết bị] [Thu âm] [Dữ liệu] [Wake word] [Kết quả]

Thiết bị / Thêm thiết bị
Gateway: RM4 phòng khách
Loại: [Điều hòa v]    Hãng: [Tìm hãng...]    Model: [Tìm model...]
Nguồn dữ liệu: Catalog đã nhập                [Học từ remote gốc]

Các bộ mã phù hợp             Bảng thử bộ mã đang chọn
- Model / Bộ mã A             [Preset có mã] [Tắt]
- Model / Bộ mã B             Kết quả gửi: ...
                             [Đúng chức năng] [Sai/không phản hồi]
                             [Lưu bộ điều khiển]
~~~

Đây là wireframe định hướng, không phải UI đã triển khai. Không đưa IP, raw IR,
hash hay tên thư viện lên các bước dùng thường ngày; đặt ở mục “Chi tiết”.

Quy tắc UI:

- Đầy đủ trạng thái đang tải, danh sách trống, lỗi, đang bận, thành công và kết quả chưa rõ.
- Nút thao tác thật có tên rõ “Gửi thử”, “Bắt đầu thu”; chọn model hoặc mở trang không phát lệnh.
- Khi chờ phản hồi, khóa nút liên quan và hiển thị tác vụ đang làm; không giấu lỗi sau spinner vô hạn.
- Refresh trang khôi phục tác vụ từ server, không tự gửi lại lệnh/thu lại.
- Lỗi có hành động cụ thể: kiểm tra IP, mở khóa local, thử bộ mã khác, học remote, duyệt lại mẫu.
- Hỗ trợ bàn phím, nhãn form, focus sau lỗi; màu không phải dấu hiệu trạng thái duy nhất.
- Kiểm tra giao diện desktop và chiều rộng khoảng 390 px; responsive không đồng nghĩa đã mở quyền truy cập LAN.

## 5. Luồng kết nối RM4 mini

### 5.1. Điều kiện và giới hạn phần cứng

RM4 mini là thiết bị hồng ngoại, dùng Wi-Fi 2,4 GHz. Phase này dùng đường IR;
không hiển thị chức năng học RF.
[Thông số BroadLink](https://www.ibroadlink.com/productinfo/762674.html).

App hãng có thể cần cho bước đưa thiết bị vào Wi-Fi. Cần kiểm tra device lock
để local control hoạt động; hướng dẫn thao tác theo trạng thái thực tế.
[Hướng dẫn Broadlink của Home Assistant](https://www.home-assistant.io/integrations/broadlink/).

### 5.2. Wizard kết nối

1. **Chuẩn bị:** RM4 có nguồn, đã vào Wi-Fi, máy Smart Hub truy cập được mạng đó.
2. **Tìm gateway:** bấm “Tìm Broadlink”; quét có timeout trên interface LAN đã chọn.
   Nếu broadcast không tới được, cho nhập IP để kiểm tra trực tiếp.
3. **Xem định danh:** hiển thị model/devtype thực đọc được, MAC, IP, thông tin firmware nếu SDK hỗ trợ.
   Không hardcode devtype chỉ từ tên RM4 mini.
4. **Kiểm tra local:** auth/health không phát IR; phân biệt không tìm thấy, timeout,
   bị khóa, không hỗ trợ và IP đang trỏ sang thiết bị khác.
5. **Đặt tên và lưu:** gateway có ID ổn định, tên/phòng, MAC làm định danh đối chiếu.
   Khi DHCP đổi IP, tìm lại theo định danh; không gửi sang IP cũ nếu MAC không khớp.
6. **Thêm thiết bị gia dụng:** mỗi TV/quạt/điều hòa là một logical device gắn gateway.

Không reset RM4, thay Wi-Fi hoặc xóa thiết bị trong app tự động. Dashboard chỉ
hướng dẫn bước cần thiết khi local control chưa dùng được.

### 5.3. Adapter Broadlink

Dùng `python-broadlink` làm ứng viên SDK; pin bản/dependency sau khi kiểm thử đúng
artifact trên Python 3.11 và RM4 thật. API discovery/auth/learn/send được bọc trong
adapter, không gọi SDK trực tiếp từ UI.
[Kho python-broadlink](https://github.com/mjg59/python-broadlink).

Một worker tuần tự trên mỗi gateway; học mã và phát mã không chạy chồng nhau.
Thao tác SDK có deadline và lỗi có cấu trúc. Lưu ý kiểm tra cơ chế retry bên trong
SDK trước khi cho phát nút toggle; việc HTTP chỉ gọi một lần chưa đủ để chứng minh
không gửi lại UDP.
[Mã transport của SDK](https://github.com/mjg59/python-broadlink/blob/master/broadlink/device.py).

Kết quả gửi IR gồm ba lớp riêng:

- **Yêu cầu:** bị từ chối / đang xử lý / hoàn tất / lỗi / chưa rõ.
- **Bằng chứng gateway:** có ACK, chưa có ACK hoặc chưa gửi.
- **Thiết bị gia dụng:** người dùng quan sát đúng/sai/chưa xác nhận.

UI dùng “Đã gửi tới RM4” khi nhận ACK. Không biến ACK thành trạng thái
“Điều hòa đang bật”; RM4 không cung cấp phản hồi trực tiếp từ điều hòa qua IR.

## 6. Catalog hãng/model và thử bộ mã

### 6.1. Nguồn dữ liệu khả thi

RM không lưu thư viện các remote giống danh mục trên app. Tài liệu hãng mô tả
luồng lấy loại thiết bị, hãng và bộ mã từ cloud rồi thử để ghép remote.
Vì vậy kết nối LAN thành công không tự đem theo kho hãng/model.
[Tài liệu IR BroadLink](https://tx.ibroadlink.com/public/configuration-sdk%2Bctc/rm_related_api/).

| Nguồn | Dùng ở phase này | Giới hạn/cách xử lý |
| --- | --- | --- |
| Bộ mã tự học | Có, nguồn local do người dùng tạo | Phải đặt tên nút và thử đúng thiết bị |
| Catalog cộng đồng tương thích Broadlink | Có, importer cho một định dạng xác định | Phạm vi theo dữ liệu có thật; kiểm tra nguồn, định dạng, quyền sử dụng và từng nút |
| Gói JSON local của người dùng | Có nếu đúng schema | Import tạo phiên bản mới, kiểm tra dữ liệu trước khi hiển thị nút thử |
| Kho cloud Broadlink chính thức | Khảo sát khả năng tiếp cận; chưa là dependency MVP | Cần xác minh license, quyền API, region, định dạng và cách chuyển thành payload LAN |
| Đọc ngược remote đang cài trong app | Chưa cam kết | Không coi python-broadlink có sẵn chức năng đồng bộ app |

**Nguồn khảo sát ưu tiên:** dữ liệu của SmartIR. Dự án có danh sách thiết bị,
hãng/model và JSON mã cho nhiều controller; importer chỉ lấy dữ liệu tương thích
Broadlink. Đây là sử dụng một nguồn dữ liệu, không yêu cầu cài Home Assistant.
Trước khi đóng gói phải kiểm tra license/attribution ở revision cụ thể.
[Kho SmartIR](https://github.com/smartHomeHub/SmartIR),
[danh mục điều hòa](https://github.com/smartHomeHub/SmartIR/blob/master/docs/CLIMATE.md),
[license](https://github.com/smartHomeHub/SmartIR/blob/master/LICENSE).

SDK cloud chính thức mô tả yêu cầu license khi tích hợp. **Chưa có bằng chứng
tài khoản/app hiện tại của người dùng cho phép dashboard dùng toàn bộ kho cloud.**
Khả năng đó là điều cần xác minh, không phải tính năng được hứa sẵn.
[Tài liệu SDK BroadLink](https://docs.ibroadlink.com/public/appsdk_en/appsdk_01/).

### 6.2. Quy trình nhập catalog

1. Chọn nguồn/revision cụ thể; ghi URL, license và checksum.
2. Đọc metadata category/brand/supported models/controller/encoding.
3. Chỉ nhận IR payload định dạng Broadlink đã hỗ trợ; dữ liệu MQTT, RF hay encoding
   khác phải báo không tương thích, không tự đoán cách chuyển.
4. Kiểm tra JSON/schema, Base64, kích thước và cấu trúc packet; giới hạn mức lặp
   trong payload theo định dạng đã xác minh.
5. Chuẩn hóa thành schema nội bộ `CodeSet`; mã nguồn giữ nguyên để truy vết.
6. Lưu bản import bất biến, tạo index tìm kiếm; chưa gán “đã xác minh trên máy này”.
7. Update catalog là thao tác riêng; remote đã lưu vẫn trỏ revision cũ cho đến khi người dùng đổi.

Không tải toàn bộ Internet hoặc scrape app. Bản đầu có một catalog đã kiểm tra
và chức năng import cùng định dạng. Chọn brand chỉ liệt kê các bản ghi có trong nguồn.

### 6.3. Luồng chọn và thử

1. Chọn **TV / quạt / điều hòa**; chỉ hiện loại có dữ liệu hoặc template học phù hợp.
2. Lọc **hãng**; tìm **model**. Nếu không có model cụ thể, hiển thị
   “Bộ mã ứng viên A/B” thay vì tự đặt tên model.
3. Xem số nút/preset có sẵn và nguồn dữ liệu.
4. Chọn một nút thử; UI nhắc đúng thiết bị và chức năng sẽ phát.
5. Bấm “Gửi thử” một lần; người dùng quan sát rồi chọn đúng/sai/chưa rõ.
6. Xác minh từng chức năng quan trọng. Một nút power đúng không xác nhận toàn bộ remote.
7. Lưu tên thiết bị/phòng và bộ mã đã chọn; chỉ mở các nút được xác minh trong remote thường dùng.
8. Nút chưa xác minh được thử ở màn thiết lập; nút thiếu mã có lựa chọn học bổ sung.

Khi catalog không có thiết bị cần dùng, UI nói rõ và dẫn thẳng sang học remote.
Catalog browsing là chức năng phải có; số hãng/model hiển thị phụ thuộc dữ liệu
đã nhập. Luồng học từ remote vẫn dùng được khi không có Internet.

### 6.4. Điều hòa cần xử lý riêng

- Một mã thường biểu diễn cả mode, nhiệt độ, tốc độ quạt, swing và trạng thái nguồn.
- UI chỉ cho chọn tổ hợp đã có mã; dữ liệu chỉ có vài preset thì hiển thị preset.
- Chỉnh nhiệt độ trên UI chưa phát ngay; bấm “Gửi” để phát một trạng thái đã chọn.
- Không suy ra mã 21°C bằng sửa mã 20°C khi chưa có encoder giao thức đã kiểm chứng.
- Nếu chỉ có nút bật/tắt dạng toggle, ghi “Đổi bật/tắt”, không dựng hai nút on/off giả.
- Không tự quét tuần tự hàng loạt mã hay bật/tắt máy nén liên tục trong wizard.


## 7. Học từ remote gốc và lưu bộ điều khiển

Luồng dùng cho thiết bị chưa có trong catalog hoặc bổ sung nút còn thiếu:

1. Tạo thiết bị, nhập loại/hãng/model nếu biết; có thể ghi “chưa rõ model”.
2. Chọn tên nút hoặc preset, ví dụ “Tăng tốc quạt”, “Tắt”, “Lạnh 26°C – quạt Auto”.
3. Bấm “Bắt đầu học”; backend giữ quyền sử dụng gateway, vào learning mode
   và hiển thị thời gian còn lại, đề xuất 30 giây/lượt.
4. Người dùng hướng remote gốc vào RM4 và nhấn đúng một nút.
5. Backend poll có giới hạn, nhận mã, kiểm tra rồi lưu **bản nháp** có checksum.
6. Người dùng bấm “Gửi thử” và quan sát thiết bị.
7. Chọn “Đúng chức năng” để xác minh mã cho nút đó, hoặc học lại.
8. Lưu remote và mở bảng điều khiển.

Timeout, hủy học và nhận dữ liệu không hợp lệ có trạng thái riêng. Hủy chỉ dừng
tác vụ có thể dừng; nếu SDK/firmware không có lệnh thoát học ngay thì báo đang chờ
kết thúc, giữ khóa đến khi có thể dùng lại gateway.

Mỗi lần học lại tạo revision mới. Mã đã dùng vẫn được giữ để quay lại; chưa xác minh
revision mới thì mapping cũ vẫn hoạt động. Không ghi đè âm thầm mã của nút khác.

Remote đã lưu gồm:

- Tên, phòng, loại thiết bị và gateway.
- Hãng/model do nguồn catalog hoặc người dùng cung cấp.
- Danh sách chức năng, mapping mã và mức xác minh **theo từng nút/preset**.
- Nguồn mã: import hay tự học; thời gian, checksum, revision.
- Nhật ký gửi và quan sát, không suy diễn trạng thái hiện tại từ lần gửi trước.

Các capability lõi: `remote.press`, `power.set` khi có mã on/off riêng,
`climate.apply_preset` khi có đầy đủ preset. Provider khác sau này có thể trả
trạng thái thực; contract phải phân biệt trạng thái quan sát được với lệnh vừa gửi.

## 8. Dashboard thu âm và duyệt thủ công

### 8.1. Bảo toàn các phiên đã có

Lần mở đầu đọc `recordings/`, manifest và child-study metadata hiện có:

- Giữ đường dẫn/bytes WAV và model gốc; không normalize, trim hoặc resample tại chỗ.
- Quét là chỉ đọc. File thiếu, hash lệch, manifest lỗi hoặc ID trùng hiện thành vấn đề dữ liệu.
- Không tự tạo một dataset rỗng thay cho metadata lỗi và không tự nhận mẫu là đã duyệt.
- Bản ghi cũ thiếu bằng chứng duyệt/người nói cần được kiểm tra lại trước khi đưa vào benchmark mới.
- Khi người dùng duyệt, chỉ cập nhật metadata liên quan, có revision và bản sao trước thay đổi.
- File thu lỗi clipping vẫn được giữ để xem; không tự bỏ qua lỗi rồi báo phiên thành công.

Nguồn dữ liệu chuẩn của các mẫu tiếp tục là WAV/manifest và các tệp child-study.
SQLite của dashboard không trở thành một bộ nhãn thứ hai có thể lệch với CLI.

### 8.2. Tạo phiên thu

Form tương ứng lệnh đang dùng:

| Trường | Giao diện/giá trị |
| --- | --- |
| Nhóm người nói | Người lớn / Bé |
| Mã người nói | Ví dụ `adult_01`, `child_01`; người dùng xác nhận |
| Câu cần nói | Mặc định “Maika ơi”; có preset câu âm tính hiện tại |
| Nhãn | Positive/negative và expected events được kiểm tra nhất quán |
| Tập | `pilot` / `dev` / `test`, giải thích ngay cạnh lựa chọn |
| Điều kiện | Quiet normal voice, tiếng ồn… kèm ghi chú |
| Khoảng cách | Số mét, dương và hữu hạn |
| Số lượt | Mặc định 5, giới hạn hợp lý ở server |
| Chế độ | Mặc định chờ người dùng bấm trước từng lượt, tương đương manual advance |
| Thiết bị thu | Mic trên máy Smart Hub theo cấu hình hiện tại, hiển thị tên thực |

`speaker_id` là nhãn do con người khai báo, không phải kết quả nhận diện danh tính.

### 8.3. Luồng thu và đồng bộ tiếng tít

State machine đề xuất:

~~~text
kiểm tra mic → ổn định → chờ sẵn sàng
  → người dùng bấm bắt đầu → phát tít → loại phần tít/đuôi âm
  → thu 5 giây → lưu WAV + hash + thống kê
  → chờ lượt tiếp theo / hoàn tất / dừng vì lỗi
~~~

- Mic và tiếng tít đều ở máy Smart Hub. Trình duyệt là giao diện điều khiển;
  không xin quyền mic trình duyệt trong phase này.
- Giữ warmup, mono 16 kHz/16 bit và thời lượng take hiện tại.
- Tiếng tít và điểm bắt đầu lấy mẫu do worker audio quyết định; không dùng thời điểm
  animation trên browser làm mốc cắt audio.
- Khi chờ người dùng, worker vẫn drain dữ liệu hoặc đóng/mở capture theo thiết kế
  đã kiểm thử. Không để `input()` chặn làm tích lũy audio cũ.
- Hiển thị mức mic/thời gian thu, RMS/peak/clipping sau mỗi lượt.
- Dừng player nghe lại và khóa thao tác phát mẫu trong các tab dashboard khi thu;
  tránh để tiếng nghe lại lọt vào take mới. Không coi khóa này kiểm soát được app audio ngoài repo.
- Nếu clipping > 1%, dừng phiên như CLI hiện tại và hiển thị hướng kiểm tra.
  Dashboard không tự đổi gain, mic boost, profile hoặc trọng số.
- “Thu lại” tạo take mới; take cũ được đánh dấu cần review/từ chối, không xóa.
- Bấm “Dừng” lưu các take đã hoàn thành và trạng thái phiên bị ngắt.
- Mất kết nối browser: hoàn tất take đang thu, không tự bắt đầu take mới; chờ reconnect
  trong thời gian giới hạn rồi giải phóng mic. Refresh không mở thêm phiên.

### 8.4. Bước duyệt thủ công bắt buộc

Đây là yêu cầu người dùng đã xác nhận. Mỗi mẫu có:

1. Player nghe WAV gốc; hiển thị người nói dự kiến, câu dự kiến và metadata.
2. Kết quả QC: đọc được file, đúng định dạng, checksum, clipping, có tín hiệu.
3. Ô xác nhận người nói thực tế và nội dung nghe được.
4. Ba quyết định: **Chấp nhận · Từ chối · Cần xem lại**, kèm ghi chú.
5. Người duyệt, thời điểm, revision và nguồn thao tác UI/CLI.

**Chấp nhận** cần con người xác nhận đúng người nói, đúng nội dung/nhãn và mẫu
đủ chất lượng. Câu nghe được phải do người duyệt xác nhận; transcript máy chỉ là gợi ý.
Nếu chỉnh câu/nhãn từ positive sang negative, metadata và expected events phải được
kiểm tra lại trước khi lưu.

QC tự động chỉ đạt `technical_pass`. Điều kiện vào bộ đánh giá chính thức:
`accepted` + `speaker_confirmed=true` + WAV/hash hợp lệ và đúng split/phạm vi.
Lỗi file phát hiện lúc đánh giá phải được báo là lỗi, không âm thầm làm đẹp tỷ lệ.

V1 không có nút “Chấp nhận tất cả” bỏ qua việc nghe/xác nhận từng mẫu. Có thể lọc
danh sách chờ duyệt và chuyển nhanh sang mẫu tiếp theo bằng phím.

### 8.5. Quản lý split và dữ liệu dùng làm tham chiếu

- Giữ schema `pilot/dev/test` đang có; không tự đổi split của dữ liệu cũ.
- Chọn/dev để tinh chỉnh; test giữ riêng theo phiên thu, chỉ dùng sau khi chốt ứng viên.
- Mẫu dùng tạo enrollment không được đồng thời tính là mẫu đánh giá độc lập.
- Registry thí nghiệm lưu vai trò enrollment/evaluation bằng danh sách sample ID
  và hash; không cần ép thêm `train` vào schema cũ ngay.
- Nếu cùng người nói xuất hiện ở dev/test, báo cáo mô tả là kiểm tra trên phiên khác
  của người đó; không gọi là đo khả năng tổng quát sang người nói mới.
- Theo dõi riêng người lớn/bé, điều kiện và session; không gộp hai người thành một
  tỷ lệ rồi suy ra chất lượng giọng bé.

## 9. Quản lý và so sánh model wake word

### 9.1. Phân biệt engine, artifact và cấu hình thử

Registry lưu ba lớp:

| Lớp | Ví dụ | Ý nghĩa |
| --- | --- | --- |
| Engine/adapter | STT wake, EfficientWord-Net, DTW | Cách xử lý audio và tạo wake event |
| Artifact | ONNX, tokens, VAD, NPZ tham chiếu | File cụ thể, phiên bản, hash, nguồn |
| Candidate | Artifact + profile/threshold/alias/reference IDs | Một cấu hình có thể đánh giá và tái lập |

`standard` và `sensitive` hiện là hai profile của cùng pipeline/model STT,
không phải hai model được huấn luyện riêng cho người lớn và bé.

Trang model hiển thị câu gọi/ngôn ngữ phù hợp, phiên bản, dung lượng, nguồn,
license, engine được hỗ trợ, trạng thái đã kiểm tra và các lần benchmark.
Không gắn nhãn “tốt hơn cho bé” khi chưa có kết quả trên dữ liệu đã duyệt.

### 9.2. Danh sách khởi đầu

| Ứng viên | Việc làm trong phase | Điều kiện |
| --- | --- | --- |
| STT tiếng Việt + keyword hiện tại | Đăng ký baseline; so sánh standard/sensitive và alias thử nghiệm riêng | Tận dụng evaluator hiện có, pin toàn bộ artifact/config |
| EfficientWord-Net + reference NPZ | Adapter offline; tạo reference mới từ mẫu đã duyệt, so sánh với baseline | Kiểm tra định dạng và nguồn enrollment, không ghi vào file đang dùng |
| DTW hiện có | Baseline đối chiếu nếu artifact hợp lệ | Cùng hợp đồng event và dữ liệu đầu vào |
| Gói model mới do người dùng thêm | Import/download vào registry; chạy khi adapter và đầu vào tương thích | Có nguồn, license, hash và khai báo câu gọi/ngôn ngữ |
| openWakeWord | Đăng ký ứng viên khảo sát; tích hợp runnable nếu có gói phù hợp “Maika ơi” | Không coi model tiếng Anh có sẵn là model tiếng Việt |
| sherpa-onnx KWS | Khảo sát model/tokenizer đúng ngôn ngữ và định dạng KWS | ASR tiếng Việt hiện có không tự động trở thành KWS model |

README openWakeWord hiện mô tả hỗ trợ tiếng Anh; license mã nguồn và pretrained
weights khác nhau. Do đó cần kiểm tra **từng gói trọng số** và khả năng nhận “Maika ơi”.
[README chính thức openWakeWord](https://github.com/dscripka/openWakeWord).

Tài liệu sherpa KWS liệt kê các model tiếng Anh/Trung và keyword tùy chỉnh;
chưa đủ cơ sở kết luận dùng tốt tiếng Việt chỉ bằng đổi keywords.
[Tài liệu sherpa-onnx KWS](https://k2-fsa.github.io/sherpa/onnx/kws/index.html).

**Cam kết phase:** có registry, thêm gói tương thích và so sánh các engine hiện có.
Việc tìm model mới có chất lượng tốt hơn là kết quả thử nghiệm, không thể hứa trước.
Model chưa đủ điều kiện vẫn được ghi nhận với lý do, nhưng không có nút chạy giả.

### 9.3. Luồng thêm model và tạo reference

**Import/download:**

1. Chọn engine/định dạng đã hỗ trợ; chọn gói local hoặc nguồn tải đã khai báo.
2. Xem nguồn, phiên bản, license, dung lượng, câu gọi/ngôn ngữ và dependency.
3. Tải/import vào thư mục staging mới; kiểm tra cấu trúc, kích thước và hash.
4. Với archive: chặn path traversal/symlink và giới hạn kích thước giải nén.
5. Không nạp Python/pickle hoặc chạy script tùy ý trong gói.
6. Chạy smoke test offline trong worker phù hợp; lưu thành artifact bất biến.
7. Tạo candidate mới, chờ benchmark; không đổi runtime mặc định.

Không cho backend tải URL tùy ý từ form. Nguồn mạng có allowlist và kiểm tra redirect;
gói không có checksum công bố được ghi rõ hash tính tại import chỉ giúp kiểm tra
tính toàn vẹn về sau, chưa chứng minh nguồn gốc đáng tin.

**Tạo bộ mẫu tham chiếu:**

1. Chọn engine hiện có hỗ trợ enrollment.
2. Chọn mẫu accepted/confirmed, loại bỏ test và kiểm tra trùng với tập đánh giá.
3. Lưu snapshot sample IDs/hash và tham số tiền xử lý.
4. Tạo reference mới vào đường dẫn riêng; artifact chỉ được đăng ký sau khi lưu thành công.
5. Đánh giá trên các phiên khác; giữ reference cũ làm baseline.

Đây là tạo dữ liệu tham chiếu cho engine hỗ trợ chức năng đó; không gọi là
fine-tune trọng số ASR. Thu thêm mẫu cho STT wake vẫn có ích cho đánh giá/tinh chỉnh,
nhưng không tự làm model ASR học thêm.

### 9.4. Quy trình benchmark trên dashboard

1. Chọn bộ dữ liệu đã duyệt và chụp snapshot danh sách/hash/label revision.
2. Chọn baseline và candidate, profile/alias/threshold được ghi rõ.
3. Kiểm tra không lẫn enrollment với evaluation; từ chối chạy khi tập rỗng.
4. Chạy offline từng candidate với cùng danh sách đầu vào; một worker model nặng tại một thời điểm.
5. Reset trạng thái detector giữa các clip độc lập, giữ sample rate/hợp đồng audio nhất quán.
6. Lưu event, transcript nếu có, lỗi, thời gian decode và cấu hình/version/artifact hash.
7. Mở bảng tổng hợp và drill-down từng mẫu; xuất JSON/Markdown.
8. Chốt candidate trên dev rồi đánh giá test. Muốn tinh chỉnh tiếp sau khi xem test
   phải ghi rõ test đã bị sử dụng để lựa chọn, không tiếp tục gọi là holdout chưa thấy.

Báo cáo tối thiểu:

| Chỉ số | Cách đọc |
| --- | --- |
| Positive detected / tổng positive | Tách người lớn/bé, có số đếm; không chỉ hiện phần trăm |
| False reject | Positive không có wake; hiển thị lỗi xử lý riêng |
| False accept trên negative | Số clip/event phát nhầm và tổng negative |
| Wake lặp | Nhiều event cho một lần gọi |
| Lỗi/clipping/mẫu loại | Hiển thị lý do và mẫu số; không tự bỏ khỏi báo cáo |
| RTF và thời gian xử lý | Đo offline; không gọi thời gian decode là độ trễ end-to-end |
| CPU/RAM | Ghi máy/chế độ đo, đo tuần tự để so sánh |
| Độ trễ wake | Chỉ báo khi có mốc tham chiếu/đánh dấu câu gọi phù hợp; nếu thiếu ghi N/A |

False accepts/giờ chỉ có ý nghĩa khi có bộ negative liên tục với thời lượng xác định;
không suy từ vài clip 5 giây thành chất lượng nghe cả ngày. Số liệu người dùng tự báo
85–95% chưa phải baseline đo trên dataset của dashboard.

Bảng mẫu chỉ là cấu trúc, không điền kết quả giả:

| Candidate | Bé phát hiện | Người lớn phát hiện | Negative phát nhầm | Lỗi | RTF |
| --- | --- | --- | --- | --- | --- |
| Baseline | Chưa chạy | Chưa chạy | Chưa chạy | — | — |
| Candidate A | Chưa chạy | Chưa chạy | Chưa chạy | — | — |

### 9.5. Tách thử nghiệm khỏi runtime hiện tại

- Khởi động dashboard không load thêm detector vào `assistant` đang nghe.
- Candidate có config/artifact riêng; không đổi `config.json`, file enrollment hay gain.
- V1 xuất cấu hình ứng viên/báo cáo, không có nút âm thầm thay model production.
- Model cần dependency xung đột chạy trong môi trường riêng.
- Benchmark nặng không chạy cùng phiên thu. Nếu assistant đang hoạt động, dashboard
  báo điều kiện tài nguyên và yêu cầu sắp xếp phiên thử riêng; không tự dừng assistant.
- Không kết luận “không ảnh hưởng giọng người lớn” chỉ vì model lưu riêng:
  phải kiểm tra hồi quy trên cùng mẫu người lớn và tránh tranh CPU/mic khi chạy thực tế.


## 10. Kiến trúc triển khai

### 10.1. Công nghệ đề xuất

- **Backend:** Python + FastAPI, REST JSON, Uvicorn một process.
- **Frontend:** HTML/CSS và JavaScript module, serve cùng origin; không cần React
  hoặc một frontend dev server riêng cho phạm vi dashboard này.
- **Cập nhật tiến độ:** polling job API; khoảng 250–500 ms khi thu/học, chậm hơn khi
  xem tổng quan. Audio/cue không phụ thuộc độ trễ polling.
- **Dữ liệu giao dịch mới:** SQLite cho gateway, device, code revision, model registry,
  job và command ledger.
- **Media/dataset:** file local và schema child-study hiện có, có lớp ghi chung.
- **Môi trường:** `.venv-dashboard` riêng cho web/Broadlink; worker audio/STT baseline
  dùng Python của `.venv` hiện tại. Gói model mới có thể có worker environment riêng.

FastAPI có cơ chế serve static files; Uvicorn cho cấu hình host/port/worker.
Lựa chọn một process là quyết định của dự án để đơn giản hóa quyền sở hữu hardware,
không phải giới hạn của framework.
[FastAPI static files](https://fastapi.tiangolo.com/tutorial/static-files/),
[Uvicorn settings](https://www.uvicorn.org/settings/).

Dependency mới được pin sau smoke test; không nâng cấp hàng loạt packages trong
`.venv` audio. Worker gọi Python/module bằng argv cố định, không ghép shell command
từ nội dung người dùng.

### 10.2. Sơ đồ

~~~mermaid
flowchart TD
    UI["Dashboard trình duyệt"] --> API["API local và job manager"]
    API --> DEV["Device service và catalog IR"]
    DEV --> BP["Broadlink provider"]
    BP --> RM["RM4 mini"]
    RM --> IR["Thiết bị gia dụng qua IR"]
    DEV -. "phase sau" .-> TP["Tuya provider"]
    API --> REC["Recording và review service"]
    REC --> AW["Audio worker dùng mic máy Smart Hub"]
    API --> EVAL["Model registry và evaluation service"]
    EVAL --> MW["Worker benchmark offline"]
    REC --> DATA["WAV, manifest và labels hiện có"]
    MW --> DATA
    API --> DB["SQLite: jobs, registry, ledger"]
    CLI["CLI"] --> REC
    CLI --> DEV
    CLI --> EVAL
    AS["Assistant hiện tại: tiến trình độc lập"]
~~~

Không có đường tự động từ transcript/wake event tới Device service trong phase này.

### 10.3. Module và trách nhiệm

| Module đề xuất | Trách nhiệm |
| --- | --- |
| `dashboard` | API, validation, session local, UI/static assets, job status |
| `devices` | Catalog gateway/appliance, capability, hành động, command ledger |
| `devices/providers/broadlink` | SDK transport, auth, identity, health, IR learn/send |
| `devices/catalogs` | Chuẩn hóa catalog import; giữ tách nguồn mã khỏi provider điều khiển |
| `recording` | State machine thu, cue, lưu take, kết thúc phiên |
| `child_study` | Labels/review/filter/dataset, tiếp tục dùng từ CLI và API |
| `wake_lab` | Model/artifact/candidate registry, adapter đánh giá, experiment snapshot |
| `jobs` và `resource_locks` | Quyền sở hữu worker/mic/gateway, timeout/cancel/recovery |

Contract provider chung tối thiểu: connection health, capabilities, execute action,
state với mức độ quan sát nếu có, close. Discovery/learning là khả năng setup riêng
của Broadlink, không buộc Tuya sau này phải giả lập học IR.

Core nhận `device_id/action/params/request_id`; MAC/raw IR, auth key hay Tuya DP nằm
trong adapter/config riêng. Catalog nguồn mã là một service riêng vì các provider khác
có thể không dùng mã IR.

### 10.4. Tác vụ và quản lý tài nguyên

| Tài nguyên | Chính sách |
| --- | --- |
| Một RM4 | Một job học/gửi tại một thời điểm; đang bận trả BUSY |
| Mic/loa dùng cho cue hoặc nghe lại trên host | Khóa chung giữa CLI và dashboard của repo |
| Model CPU | Một benchmark nặng một lúc; có giới hạn tải và nút hủy |
| Metadata dataset | Khóa ghi liên tiến trình, kiểm tra revision để tránh mất cập nhật |
| App instance | Một instance sở hữu data directory; instance thứ hai báo rõ lỗi |

Khóa mic cần được dùng ở điểm mở capture chung để bao phủ các lệnh assistant/listen/
record của repo. Đây là thay đổi về quyền sở hữu tài nguyên, không đổi thuật toán nghe.
Ứng dụng ngoài repo hoặc tiến trình cũ chưa dùng khóa không được coi là đã kiểm soát;
trước nghiệm thu thu âm phải kiểm tra các tiến trình audio liên quan. Không tự kill chúng.

Browser chỉ giữ `job_id`; state chuẩn ở backend. Job có các trạng thái
`queued/running/waiting_user/completed/failed/cancelled/interrupted`.
Kết quả “đã gửi nhưng chưa rõ phản hồi” nằm trong kết quả hành động, không bị gộp
thành thành công chỉ vì job đã kết thúc.

Worker audio/model chạy tiến trình riêng với thông điệp JSON có version, ID và
sequence; log chẩn đoán tách khỏi kênh dữ liệu. CLI cũng gọi các service này;
không parse chuỗi `[SAVED]` hoặc `[WAKE TEXT]` để điều khiển quy trình web.

- `cancel` dừng công việc kế tiếp và yêu cầu worker kết thúc có kiểm soát.
- Không nhả khóa gateway chỉ vì HTTP timeout nếu SDK vẫn đang chạy.
- Shutdown lưu trạng thái, kết thúc child worker, giữ file đã hoàn tất.
- Sau crash, job đang chạy chuyển thành interrupted; không tự thu lại/học lại/phát lại.
- Chỉ phục hồi các dữ liệu đã kiểm tra; file tạm được gắn trạng thái cần xử lý.

### 10.5. Chống gửi lặp và trạng thái không chắc chắn

Mỗi lần người dùng thực sự bấm gửi tạo một `request_id`. UI retry/poll dùng lại ID;
double-click trong lúc chờ không tạo hai action.

Server ghi ledger trước I/O: `prepared → dispatching → terminal`. Cùng ID/cùng payload
trả kết quả đã biết; cùng ID/khác payload bị từ chối. Sau restart, action từng ở
dispatching mà chưa có kết quả trở thành **unknown**, không tự phát lại.

SDK retry cũng phải được kiểm thử ở mức packet. Với nút toggle, yêu cầu tránh tự
gửi lặp khi mất ACK; nếu thư viện không cấu hình được thì cần adapter có cơ chế phù hợp
và test trước khi bật thao tác đó. Không hứa “exactly once” cho đường IR.

Sau “Chưa rõ kết quả”, UI hướng người dùng quan sát trước khi bấm một lệnh mới.
Refresh, reconnect hoặc restart dashboard không được phát lại lệnh cũ.

## 11. API và dữ liệu dự kiến

### 11.1. Nhóm endpoint

Các route dưới đây là contract dự kiến, chưa tồn tại:

| Nhóm | Route tiêu biểu | Quy tắc |
| --- | --- | --- |
| Hệ thống | `GET /api/health`, `GET /api/status` | Không mở mic hay phát IR; health không kích hoạt discovery |
| Gateway | `POST /api/gateway-discoveries`, `POST /api/gateways`, `POST /api/gateways/{id}/checks` | Discovery/check có timeout và định danh kết quả |
| Catalog | `GET /api/catalog/brands`, `GET /api/catalog/code-sets`, `POST /api/catalog/imports` | Có filter loại/hãng/model, nguồn và revision |
| Thiết bị | `GET/POST /api/devices`, `GET /api/devices/{id}` | Thay mapping cần revision khớp |
| Thử/gửi | `POST /api/devices/{id}/actions` | request ID, code revision, action/params; không nhận raw IR tùy ý |
| Học mã | `POST /api/devices/{id}/learning-jobs`, `POST /api/code-revisions/{id}/observations` | Candidate được thử riêng trước khi gán vào remote thường dùng |
| Phiên thu | `GET/POST /api/recording-sessions`, `POST /api/recording-sessions/{id}/advance` | Advance chỉ hợp lệ khi waiting_user, có take sequence |
| Review | `GET /api/samples`, `GET /api/samples/{id}/audio`, `PATCH /api/samples/{id}/review` | Stream bằng sample ID; review có expected revision |
| Model | `GET /api/wake/models`, `POST /api/wake/imports`, `POST /api/wake/enrollments` | Không kích hoạt runtime khi import |
| Đánh giá | `POST /api/evaluations`, `GET /api/evaluations/{id}` | Dataset/candidate snapshot bất biến |
| Job | `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` | Idempotent; status refresh không tạo side effect |

Job dài trả `202 + job_id`. Input sai trả `400/422`, xung đột tài nguyên/revision trả
`409`, thiếu bản ghi trả `404`. Kết quả hành động phần cứng có outcome riêng để không
đồng nhất HTTP 200 với thiết bị đã làm đúng.

### 11.2. Schema lõi

| Thực thể | Trường cần có |
| --- | --- |
| Gateway | ID, provider, model, identity/MAC, IP gần nhất, devtype, last check, status |
| Appliance | ID, tên/phòng, loại, brand/model, gateway ID, capability, mapping revision |
| CodeSet | ID/revision, loại, hãng, model list, source URL/revision/license, encoding, hash |
| CodeRevision | ID, code set/nút/preset, payload reference/hash, nguồn học/import, validation |
| Observation | Thiết bị + code revision + chức năng + đúng/sai/chưa rõ + người/thời gian |
| RecordingSession/Sample | Giữ schema hiện có, ID/hash/split/người nói/condition/review; version bổ sung khi cần |
| ModelArtifact | ID, engine, files/hash/size, language/phrase, nguồn/license, compatibility status |
| WakeCandidate | Artifact IDs, reference IDs, profile/alias/threshold, config hash |
| Evaluation | Snapshot dataset/candidate, code/runtime version, metrics, per-sample errors/events |
| Job/ActionRequest | ID, loại, state, tài nguyên, timestamps, payload digest, result/evidence |

Phân biệt model registry với remote catalog bằng namespace/type riêng. Không dùng
một bảng `models` chung chứa cả điều hòa và trọng số wake.

### 11.3. Vị trí lưu

~~~text
smart-hub/
  dashboard.local.json             # cấu hình web/đường dẫn worker, không commit
  .local/dashboard/
    app.sqlite                    # registry mới, jobs, ledger
    ir-codes/                     # mã học/import theo revision
    catalogs/                     # source snapshots và index
    wake-candidates/              # config/reference mới, tách baseline
    imports/                      # staging có giới hạn
    locks/
    backups/
  recordings/                     # dữ liệu cũ giữ nguyên vị trí
    <session>/                    # WAV + manifest
    child-study/
      sessions.json
      labels.jsonl
      results/
  models/                         # artifact baseline giữ nguyên
    candidates/                   # artifact mới nếu được cấu hình tại đây
~~~

Tên thư mục là đề xuất. Khi triển khai phải thêm ignore cho config local,
`.venv-dashboard/`, `.local/` và artifact mới trước khi sinh dữ liệu.

Thư mục riêng tư 0700, tệp dữ liệu riêng tư 0600. Dùng ghi file tạm + atomic rename
cho metadata; giữ hash toàn bộ file WAV theo quy ước hiện tại. Với thao tác ghi nhiều
tệp session/labels/manifest, cần journal hoặc quy trình phục hồi nhất quán, không
giả định từng atomic rename riêng là một transaction chung.

SQLite dùng transaction/version migration; index cache có thể xây lại từ nguồn chuẩn.
Khi CLI sửa review, dashboard reload theo revision, không dùng nhãn cũ trong cache
để bắt đầu benchmark mới.

### 11.4. Quyền truy cập local

Dashboard có quyền mở mic, đọc ghi âm và gửi IR nên loopback vẫn cần giới hạn request:

- Serve UI/API cùng origin; kiểm tra Host/Origin, không mở CORS `*`.
- Phiên truy cập local với mã khởi tạo một lần hoặc token; không lưu token trong log/URL query.
- Mutation có bảo vệ CSRF, chỉ POST/PATCH; không tạo side effect từ GET.
- Stream recording qua sample ID đã kiểm tra, không nhận arbitrary file path.
- Giới hạn kích thước upload và tên/path import; không cho gọi shell từ API.
- Không dùng CDN/analytics hoặc gửi audio ra cloud trong các workflow này.
- Secret/auth key chỉ ở backend; log thông thường không in raw audio, IR payload hay thông tin xác thực.

MVP không cần hệ thống tài khoản nhiều người. Truy cập điện thoại qua LAN chỉ mở
sau khi cấu hình auth, bind, firewall và URL truy cập rõ ràng; không tự đổi sang `0.0.0.0`.

## 12. Backlog và thứ tự thực hiện

Các vai trò PM/BA/Backend/Frontend/QC là góc nhìn phụ trách; không bắt buộc nhiều
người hay nhiều agent. Chia theo lát cắt có demo được, không hoàn tất hết backend
rồi mới bắt đầu kiểm tra giao diện.

| ID | Phần việc | Đầu ra/tiêu chí chấp nhận | Phụ thuộc |
| --- | --- | --- | --- |
| DB-01 | Khảo sát và chốt dữ liệu đầu vào | Xác nhận RM4/network/appliance, catalog mẫu và danh sách baseline model; ghi rõ mục chưa biết | Duyệt kế hoạch |
| DB-02 | Nền web và mock UI | Điều hướng, trạng thái loading/empty/error, session local, health; mở trang không mở mic/phát IR | DB-01 |
| BL-01 | Contract/provider và kết nối | UI discover/nhập IP → auth → lưu gateway; lỗi timeout/identity/lock phân biệt được | DB-02 |
| BL-02 | Học và thử nút IR | Học một mã → thử → quan sát → lưu revision; timeout/cancel không làm hỏng mã cũ | BL-01 |
| BL-03 | Catalog theo hãng/model | Import nguồn thật hợp lệ, tìm loại/hãng/model, hiển thị nguồn và mã chưa kiểm chứng | DB-01, DB-02 |
| BL-04 | Wizard ghép remote và panel | Thử từng nút, lưu mapping, mở remote sau restart; thiếu mã dẫn sang học | BL-02, BL-03 |
| BL-05 | Kết quả gửi/ledger | Double-click/retry/restart không tự phát lại; timeout sau gửi báo unknown; test retry SDK | BL-01, trước nghiệm thu BL-04 |
| AU-01 | Service thu và khóa audio | CLI còn hoạt động; web dùng cùng logic; state/cue/drain/cancel ổn định | DB-02 |
| AU-02 | Form/phiên thu | Người lớn/bé, split/label, manual advance, take mới, thống kê và clipping stop | AU-01 |
| AU-03 | Dữ liệu và review thủ công | Import metadata chỉ đọc, nghe từng mẫu, accepted/confirmed có audit, báo corruption | AU-01; dùng được với dữ liệu cũ |
| WK-01 | Registry baseline/candidate | Nhận đúng artifact/profile, hash/version, không sửa baseline | DB-02 |
| WK-02 | Import/download/enrollment | Gói hợp lệ mới được chạy; reference mới tách file cũ; chặn trùng tập đánh giá | AU-03, WK-01 |
| WK-03 | Evaluator chung | STT baseline và engine hiện có chạy cùng dataset; lỗi và mẫu số rõ; export báo cáo | AU-03, WK-01 |
| WK-04 | UI so sánh | Tách người lớn/bé/negative; drill-down; chọn dev/test và đóng băng candidate | WK-02, WK-03 |
| QC-01 | Hồi quy và phục hồi | API/UI/service/worker tests, giữ bytes dữ liệu cũ, crash/reconnect/lock đầy đủ | Các lát cắt liên quan |
| REL-01 | Runbook và demo thật | RM4 + appliance, thu + review, benchmark; ghi PASS/FAIL/NOT TESTED và rollback | QC-01 |
| NEXT | Voice dispatch/Tuya/remote access mở rộng | Backlog riêng, không tính là xong trong phase dashboard | Phase này đạt |

### Các mốc có thể duyệt/demo

**M0 — Nền và khảo sát:** UI mock chạy local, nguồn catalog và đường kết nối RM4
được xác minh. Catalog bị chặn quyền truy cập thì ghi rõ nguồn thay thế; không treo
luồng học remote theo cloud.

**M1 — Broadlink độc lập dùng được:** dashboard kết nối RM4, học mã, duyệt mã,
chọn hãng/model từ catalog đã nhập, thử và lưu remote. Tắt/mở dashboard vẫn dùng lại
remote đã lưu. Chưa cần bật assistant.

**M2 — Thu âm và dữ liệu:** các mẫu cũ xem/nghe được, thu phiên mới qua dashboard,
review thủ công và giữ bytes WAV cũ.

**M3 — Model và so sánh:** registry + enrollment/import + benchmark chạy được;
có ít nhất baseline STT và một engine hiện có khác nếu artifact đủ điều kiện.
Artifact thiếu/sai được xử lý rõ; không dùng kết quả mock làm kết quả giọng thật.

**M4 — Nghiệm thu:** demo xuyên suốt, kiểm tra hồi quy/khôi phục và hướng dẫn vận hành.

Thứ tự ưu tiên M1 trước M2/M3 theo yêu cầu quay lại Broadlink. Chưa chốt số ngày
vì còn phụ thuộc appliance thật, nguồn mã và tương thích model; cập nhật dự toán
sau M0 thay vì giả định cloud catalog đã sẵn sàng.

### Điều kiện bắt đầu và hoàn tất một lát cắt

- **Sẵn sàng:** có mục tiêu màn hình, contract dữ liệu, lỗi cần xử lý và mẫu kiểm thử;
  hạng mục hardware có thiết bị/remote và các nút thử đã chọn.
- **Hoàn tất:** code/review/test đạt AC, UI đã kiểm tra trong browser, dữ liệu giữ
  đúng contract, dependency và hạn chế được ghi lại.
- Mỗi demo ghi ngắn kết quả, lỗi còn lại, quyết định phạm vi và bài học cho lát tiếp theo.
  Không đóng hạng mục phần cứng chỉ dựa vào mock test.

## 13. Kế hoạch kiểm chứng và tiêu chí nghiệm thu

### 13.1. Các ca phải có

| Nhóm | Kiểm chứng cần làm |
| --- | --- |
| Kết nối | Không có gateway, sai IP, đổi IP, sai MAC, local bị khóa, timeout; thao tác health không phát IR |
| Catalog | Brand/model có thật, không có kết quả, schema/encoding sai, hash lệch, revision mới không đổi remote cũ |
| Học IR | Nhận đúng/không nhận/hủy; học lại giữ revision cũ; học và gửi đồng thời bị chặn |
| Remote | Gửi đúng mapping, capability không có bị từ chối, AC thiếu tổ hợp bị khóa, power toggle có nhãn đúng |
| Delivery | ACK không biến thành observed state; mất ACK/SDK retry/double-click/restart không tự gửi lặp |
| Thu âm | Manual advance, cue/drain, warmup, clipping >1%, mic bận, ngắt tiến trình, tab refresh/disconnect |
| Review | Technical pass không thành accepted; người nói/nội dung phải được xác nhận; concurrent edit báo conflict |
| Dataset | WAV/hash cũ không đổi; malformed JSONL, path traversal, trùng ID và file mất báo rõ |
| Models | Artifact thiếu/sai hash/sai engine; import không overwrite baseline; dependency mới không phá môi trường cũ |
| Evaluation | Cùng snapshot, tách speaker, không bỏ lỗi khỏi báo cáo, không trùng enrollment/test, empty dataset thất bại rõ |
| Web | Request trái origin, thiếu phiên/CSRF, đọc file ngoài data root, import quá lớn; không có side effect qua GET |
| Recovery | Server/worker chết giữa job; restart đánh dấu interrupted/unknown, không phát lệnh hoặc mở mic lại |
| UI | Trạng thái lỗi/bận/empty; bàn phím; desktop/mobile width; tiếng Việt và các nút thật hoạt động |

Dùng fake gateway/capture/worker cho các lỗi có thể tái lập. Test hardware chỉ chạy
khi chọn riêng; bộ test mặc định không quét LAN, mở mic hoặc điều khiển đồ gia dụng.

### 13.2. Hồi quy repo

Trước refactor lập baseline trên HEAD triển khai thực tế, sau đó chạy nhóm liên quan
và bộ kiểm tra bắt buộc của repo:

~~~bash
python3 scripts/run_tests.py --mock
.venv/bin/python scripts/run_tests.py --runtime
.venv/bin/python scripts/run_tests.py --stt
.venv/bin/python scripts/run_tests.py
~~~

Danh sách trên là lệnh dự kiến cho giai đoạn triển khai, **chưa chạy trong lần viết plan**.
Không lấy số PASS từ báo cáo cũ làm kết quả của thay đổi mới.

Bổ sung test cho contract/API, concurrency, crash recovery và regression do refactor.
Kiểm tra browser thực cho luồng học, thu, review và bảng kết quả; không viết test
chỉ lặp lại HTML/tên field mà không chứng minh hành vi.

### 13.3. Demo nghiệm thu với người dùng

1. Bật dashboard, kết nối đúng RM4; thử khi gateway mất kết nối thấy lỗi có hướng xử lý.
2. Lọc catalog theo hãng/model; thử một bộ mã. Nếu không khớp, học từ remote gốc.
3. Xác minh khoảng 3–5 chức năng của thiết bị được chọn; lưu, restart và dùng lại.
   Mỗi chức năng ghi riêng ACK và quan sát thực tế.
4. Mở một phiên thu cũ; đối chiếu hash trước/sau thao tác xem và nghe.
5. Thu 5 take mới có manual advance; dừng giữa phiên vẫn giữ các take đã xong.
6. Nghe và duyệt từng take; technical_pass/chưa xác nhận không xuất hiện trong tập chính thức.
7. Chạy ít nhất hai candidate trên cùng snapshot hợp lệ; xem riêng người lớn/bé
   và negative, tải báo cáo; nếu thiếu mẫu thì UI nói thiếu, không hiển thị tỷ lệ giả.
8. Chạy lại luồng CLI hiện có, kiểm tra cấu hình/model/gain và bytes WAV được bảo toàn.
9. Demo refresh/cancel/reconnect không tạo thêm lệnh IR hoặc lượt thu.

Dữ liệu smoke test chứng minh chức năng dashboard, chưa đủ kết luận model tốt hơn.
Đợt đo chất lượng tiếp theo nên có nhiều phiên độc lập, positive của cả người lớn/bé
và negative/câu gần giống; chốt số lượng cùng thời gian thu thực tế. Nếu dữ liệu ít,
báo cáo số đếm và hạn chế thay vì khẳng định tỷ lệ tổng quát.

## 14. Triển khai local, rủi ro và quay lại bản cũ

### 14.1. Cách chạy dự kiến

Ví dụ sau chỉ mô tả giao diện CLI sẽ bổ sung:

~~~bash
.venv-dashboard/bin/python scripts/smart_hub.py dashboard \
  --host 127.0.0.1 \
  --port 8765 \
  --audio-python .venv/bin/python
~~~

`8765` là port ứng viên, **chưa được xác minh còn trống**. Trước cấu hình/chạy:

1. Kiểm kê bằng `ss -lntup`, xác nhận port rảnh hoặc đúng tiến trình dự kiến.
2. Nếu đã bị dịch vụ khác dùng, chọn port rảnh và cập nhật đồng bộ URL/config/health.
3. Bind loopback, một instance, không bật auto-reload khi thử hardware.
4. Sau khởi động kiểm tra lại socket/process và `/api/health` với retry ngắn.
5. Dashboard mở thành công không tự discovery, thu âm, tải model hay phát IR.

Đường UDP tới RM4 là kết nối của provider trên interface LAN; nó độc lập với HTTP
loopback của dashboard. Không mở public HTTP chỉ để discovery Broadlink.

### 14.2. Rủi ro và cách xử lý

| Rủi ro | Cách xử lý trong kế hoạch |
| --- | --- |
| Kho hãng/model không đầy đủ | Hiển thị nguồn/phạm vi, import được và học remote luôn có sẵn |
| Cloud SDK yêu cầu quyền riêng | Tách khỏi MVP local; chỉ triển khai khi API/quyền/định dạng đã xác minh |
| Mã cùng hãng nhưng không đúng model | Thử từng nút, giữ trạng thái chưa xác minh; không suy từ power đúng |
| Một IR phát tới nhiều thiết bị cùng vùng | Chọn vị trí/gateway và phạm vi thử thực tế; tên phòng không tạo định tuyến vật lý cho IR |
| Không đọc được trạng thái appliance | Hiển thị last command và observed confirmation tách riêng |
| Assistant và recording tranh mic/CPU | Khóa hợp tác, không tự stop; benchmark có lịch/giới hạn tài nguyên |
| Web refactor làm lệch CLI | Shared service + test parity, flags/format và đường dữ liệu cũ được bảo toàn |
| Model mới không hỗ trợ tiếng Việt/bé | Registry ghi compatibility; đánh giá trước, không thay baseline tự động |
| Review sai/lẫn người nói | Duyệt thủ công từng mẫu; metadata và phân tích theo session/speaker |
| JSONL/SQLite/index lệch nhau | Một nguồn chuẩn cho nhãn, version/lock/journal; cache có thể tái tạo |
| Crash sau khi gửi IR | Ledger ghi trước gửi, trạng thái unknown, không tự replay |
| Đổi dependency ảnh hưởng adult wake | Web env riêng, worker baseline giữ môi trường cũ, test lại CLI |

### 14.3. Rollout và rollback

- Sau duyệt mới tạo branch triển khai riêng, dự kiến `codex/local-dashboard-broadlink`;
  bảo toàn thay đổi đang có của người dùng.
- Trước thay đổi metadata: backup có version, checksum tập file baseline và cấu hình.
  Không cần nhân bản toàn bộ WAV nếu giữ bất biến và đã có danh sách hash.
- Triển khai từng mốc; mã/import/model mới vào đường dẫn mới.
- Muốn quay lại: dừng dashboard, kết thúc worker của dashboard, kiểm tra giải phóng
  tài nguyên, chạy CLI từ revision/môi trường baseline.
- Nếu cần phục hồi schema metadata, dùng backup/migration có kiểm tra; không ghi đè
  các phiên thu mới phát sinh sau backup.
- Remote và model registry có thể quay revision; dữ liệu mới được giữ.
- Rollback phần mềm không hoàn tác tác động IR đã gửi; không tự phát “lệnh ngược”.

## 15. Các quyết định còn cần chốt khi triển khai

| Quyết định | Mặc định của kế hoạch |
| --- | --- |
| Thiết bị gia dụng thử đầu tiên | Người dùng chọn và cung cấp hãng/model hoặc remote gốc ở M0 |
| Catalog khởi đầu | Import một revision cộng đồng tương thích Broadlink, lưu nguồn/license |
| Điều hòa chưa có catalog đúng | Học các preset cần dùng, panel chỉ có preset đã xác minh |
| Truy cập điện thoại | Responsive trước; chỉ mở LAN theo cấu hình triển khai riêng |
| Model wake mới | Baseline STT + engine hiện có; model bên ngoài thêm theo compatibility |
| Duyệt mẫu | **Đã chốt: bắt buộc có bước duyệt thủ công** |
| Voice điều khiển thiết bị/Tuya | Phase sau |

Phạm vi để duyệt là **M0–M4: dashboard Broadlink độc lập, thu âm/review thủ công,
registry và so sánh wake model**. Những đề xuất công nghệ, schema và API ở đây có
thể tinh chỉnh trong triển khai nếu vẫn đạt mục tiêu và giữ các giới hạn đã chốt.

Khi nghiệm thu sẽ có runbook dùng dashboard, hướng dẫn import catalog/model,
hướng dẫn học remote/thu/review, kết quả validation và các mục chưa kiểm chứng.
