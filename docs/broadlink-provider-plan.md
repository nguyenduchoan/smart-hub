# Kế hoạch Broadlink provider — RM4 mini

> **Bản kế hoạch cũ:** phạm vi và thứ tự triển khai đã được thay thế bởi
> [Kế hoạch dashboard Broadlink, thu âm và wake word](broadlink-wake-dashboard-plan.md).
> Phase mới làm dashboard điều khiển Broadlink độc lập, kèm thu âm, duyệt thủ công
> và so sánh model wake word; nối câu lệnh giọng nói vào thiết bị chuyển sang phase sau.
> Nội dung dưới đây được giữ làm tài liệu tham khảo, không phải phạm vi hiện hành.

Ngày: **2026-09-19**. Trạng thái: **DỰ THẢO — chờ người dùng duyệt, chưa triển khai**.

Người dùng đã xác nhận thiết bị **Broadlink RM4 mini**. Việc cải thiện nhận giọng bé
tạm dừng. Tài liệu này chỉ lập kế hoạch kết nối thiết bị, không mở mic, quét LAN,
cài dependency, học/phát IR hay thay cấu hình đang chạy.

## 1. Đề xuất để duyệt

Triển khai **Broadlink provider điều khiển trực tiếp qua LAN**, dùng RM4 mini làm
bộ phát hồng ngoại. Bắt đầu từ CLI để chứng minh kết nối, học mã và điều khiển
đúng thiết bị; sau đó mới nối vào câu lệnh STT đang có.

Phần chung gồm danh mục thiết bị, hành động, kiểm tra đầu vào và kết quả thực thi.
Chi tiết Broadlink nằm trong adapter riêng. Khi thêm TuyaSmart, viết adapter và
ánh xạ capability của Tuya vào cùng phần chung.

Kết quả cuối của đợt này:

1. Tìm và đăng ký đúng RM4 mini, xác thực local, chẩn đoán lỗi kết nối.
2. Học mã từ remote và lưu riêng cho từng thiết bị gia dụng.
3. Gửi một lệnh có tên đến đúng thiết bị bằng CLI, có dry-run.
4. Khi bật tính năng điều khiển bằng giọng nói: câu lệnh rõ ràng sau wake tạo
   đúng một yêu cầu tới Broadlink.
5. Báo trung thực “đã gửi lệnh”, “không gửi được” hoặc “chưa xác định kết quả”.
6. Giữ wake/STT hoạt động như hiện tại khi tính năng thiết bị bị tắt hoặc Broadlink lỗi.

**Phạm vi đợt này:** một RM4 mini, một thiết bị gia dụng đầu tiên và khoảng
3–5 thao tác hữu ích đã được người dùng chọn. Kiến trúc hỗ trợ khai báo thêm
thiết bị, nhưng nghiệm thu ban đầu không hứa hỗ trợ mọi loại remote.

**Chưa làm:** Tuya thật, Home Assistant, RF, điều khiển hàng loạt, lịch tự động,
LLM quyết định hành động, web dashboard, TTS động hoặc huấn luyện STT.

## 2. Cơ sở từ repo và những gì đã biết

| Hạng mục | Hiện trạng / hệ quả |
| --- | --- |
| Runtime | `assistant` có vòng wake → đáp → nghe lệnh → `[COMMAND]`; chưa thực thi thiết bị |
| Điểm nối | `CommandRecognized` có `WorkContext(session_id, generation_id, turn_id)` |
| Audio | `AudioPump` và worker model/loa đã tách khỏi event loop; giữ nguyên quyền sở hữu |
| Cấu hình | `Config` hiện là dataclass có trường cố định; không nhét JSON provider vào `config.json` trước khi có parser riêng |
| Phụ thuộc | Repo dùng requirements có pin; dependency Broadlink phải là phần tùy chọn |
| Kế hoạch trước | Phase 5 từng dự kiến Home Assistant; yêu cầu mới ưu tiên Broadlink trực tiếp |
| Phạm vi phase | Đây là nhánh tích hợp thiết bị riêng; không coi Phase 3/4/5 của kế hoạch tổng thể đã hoàn tất |

Nguồn trong repo: [runtime](../src/smart_hub/assistant.py),
[events](../src/smart_hub/events.py), [worker](../src/smart_hub/worker.py),
[config](../src/smart_hub/config.py), [kiến trúc](architecture.md),
[Phase 5 trong kế hoạch tổng thể](SMART_HUB_PLAN.md#phase-5--router-tool-registry-and-home-assistant-smart-home-fast-path).

### 2.1. Đặc điểm RM4 mini ảnh hưởng thiết kế

RM4 mini là bộ điều khiển **IR**, Wi-Fi 2,4 GHz, hồng ngoại 38 kHz; không chọn
luồng RF của RM4 Pro. Cảm biến nhiệt độ/độ ẩm là phụ kiện tùy chọn, không mặc
định coi máy đang có cảm biến. [Thông số BroadLink](https://www.ibroadlink.com/productinfo/762674.html).

RM không phải danh mục TV/quạt/điều hòa đã cài trong app. Tài liệu hãng mô tả
mã IR được lưu ở app/cloud, không lưu thành thư viện remote trong RM. Vì vậy
discovery RM4 mini **không tự lấy được mọi nút đã có trong app**. MVP ưu tiên
học từ remote gốc; import mã có nguồn rõ ràng là tùy chọn về sau.
[Tài liệu IR của BroadLink](https://tx.ibroadlink.com/public/configuration-sdk%2Bctc/rm_related_api/).

Thiết bị mới có thể cần app hãng để vào Wi-Fi; device lock có thể chặn local
control. Setup phải kiểm tra hai điều kiện này, không tự reset máy hoặc đổi
tài khoản/app của người dùng.
[Hướng dẫn tích hợp Broadlink](https://www.home-assistant.io/integrations/broadlink/).

### 2.2. Lựa chọn thư viện

Đề xuất `python-broadlink`, một thư viện cộng đồng, không phải SDK chính thức
của BroadLink. Nó có API discovery/auth và lớp `rm4mini` với các thao tác học,
đọc mã, gửi mã. Không viết lại toàn bộ giao thức.
[Kho thư viện](https://github.com/mjg59/python-broadlink),
[mã remote](https://github.com/mjg59/python-broadlink/blob/master/broadlink/remote.py).

Tại thời điểm tra cứu, PyPI có `broadlink==0.19.0`, phát hành 2024-04-17.
Đây là **ứng viên để kiểm thử**, chưa phải kết luận tương thích với máy thật.
BL-01 phải đối chiếu chính artifact sẽ pin, dependency `cryptography` và Python
3.11 của repo; không suy rằng mã `master` giống hệt bản phát hành.
[Thông tin package](https://pypi.org/project/broadlink/).

## 3. Thông tin còn thiếu và giả định

| Thông tin | Đã biết / giả định | Cần trước bước nào |
| --- | --- | --- |
| Model gateway | **RM4 mini — người dùng xác nhận** | Đã đủ để chốt nhánh IR |
| IP, MAC, devtype và firmware | Chưa biết; đọc bằng inventory, không đoán devtype từ tên | BL-01 / kết nối thật |
| App Broadlink đã setup Wi-Fi chưa | Chưa xác nhận | BL-01 |
| Thiết bị gia dụng đầu tiên | Chưa chọn: quạt / TV / điều hòa | BL-04 học mã |
| Hãng/model appliance, remote gốc | Chưa biết | BL-04 |
| Nút power là on/off riêng hay toggle | Chưa biết, phải đo bằng remote | BL-04 / mapping |
| Vị trí, mạng LAN/VLAN | Giả định máy Smart Hub truy cập được RM4 mini | BL-01 |
| Điều khiển giọng nói | Đề xuất làm sau khi CLI đạt; mặc định tắt | BL-06 |

Các tên phòng, IP và mã trong ví dụ là minh họa. Thông tin còn thiếu không cản
việc dựng contract/mock; chưa được dùng để giả lập kết quả phần cứng là PASS.

## 4. Kiến trúc và ranh giới provider

~~~mermaid
flowchart TD
    CLI["CLI: lệnh có cấu trúc"] --> Service["DeviceService: kiểm tra, lập kế hoạch, chống lặp"]
    Voice["CommandRecognized sau wake"] --> Router["Router câu lệnh hữu hạn"]
    Router --> Service
    Catalog["Catalog: thiết bị, capability, tên gọi"] --> Service
    Service --> Registry["ProviderRegistry"]
    Registry --> Broadlink["BroadlinkProvider"]
    Registry -. "giai đoạn sau" .-> Tuya["TuyaProvider"]
    Broadlink --> Worker["Worker riêng và khóa theo RM4 mini"]
    Worker --> SDK["Transport Broadlink"]
    SDK --> RM["RM4 mini qua LAN"]
    RM --> IR["IR tới quạt / TV / điều hòa"]
    Broadlink --> Result["Kết quả gửi và bằng chứng trạng thái"]
    Result --> Output["CLI / log kết quả"]
~~~

### 4.1. Phân biệt ba cấp

- **Provider:** cách kết nối, ví dụ `broadlink`; sau này là `tuya`.
- **Gateway:** phần cứng chuyển tiếp, ví dụ `rm4_living_room`.
- **Target:** thiết bị người dùng muốn điều khiển, ví dụ `fan_living_room`.

Một gateway có thể điều khiển nhiều target. “Bật quạt” phải resolve tới quạt,
không tới bản thân RM4 mini. Target id giữ ổn định; tên tiếng Việt/alias và phòng
là dữ liệu catalog. Discovery chỉ tìm gateway, không tự phát hiện appliance IR.

### 4.2. Contract runtime tối thiểu

Đây là phác thảo contract, chưa phải code có sẵn:

~~~python
class DeviceProvider(Protocol):
    provider_id: str

    def health(self, connection_id: str, *, deadline: float) -> HealthResult: ...
    def capabilities(self, target_id: str) -> CapabilitySet: ...
    def get_state(self, target_id: str, *, deadline: float) -> StateSnapshot: ...
    def execute(self, action: DeviceAction, *, deadline: float) -> ActionResult: ...
    def close(self) -> None: ...
~~~

Các phương thức blocking chạy trên worker riêng. `DeviceService` là lớp async
điều phối; không gọi SDK trực tiếp trong event loop hoặc worker STT.

Discovery, đăng ký gateway và learning thuộc `BroadlinkSetup`/CLI quản trị.
Không bắt Tuya phải có discovery broadcast hoặc học IR để implement contract.

| Object | Trường chính / ý nghĩa |
| --- | --- |
| `DeviceDescriptor` | target id, tên, phòng, alias, provider id, connection reference, capability |
| `DeviceAction` | request id, target id, operation, tham số typed, context nếu từ voice, thời hạn |
| `ExecutionPlan` | action đã kiểm tra, config revision, binding revision/hash; ở core là opaque provider plan |
| `ActionResult` | request id, outcome, dispatch state, evidence, error code, duration |
| `StateSnapshot` | giá trị, nguồn, observed_at, chất lượng `observed / assumed / unknown` |
| `HealthResult` | gateway có truy cập được không; không suy ra appliance đang bật |

Core không chứa MAC, raw IR, auth key hoặc Tuya data-point code. Provider mới
không được yêu cầu thêm nhánh `if provider == ...` vào parser tiếng Việt.

### 4.3. Hành động và capability

| Hành động chung | Ánh xạ với RM4 mini | Điều kiện |
| --- | --- | --- |
| `power.set(state)` | Mã on/off riêng đã xác minh | Không ánh xạ cả on/off vào cùng mã toggle |
| `remote.press(button)` | Một nút có tên trong danh sách được phép | Nút được xác minh; phân biệt toggle/relative |
| `climate.apply_preset(preset_id)` | Mã IR cho toàn bộ cấu hình đã học | Chỉ các preset thật sự có mã |
| `state.read` | RM4 mini thường không biết trạng thái appliance | Trả unknown/unsupported, không bịa trạng thái |

Chỉ quảng bá capability thật sự có binding hợp lệ cho target đó. Không bắt mọi
provider hỗ trợ mọi capability. Tuya có thể hỗ trợ `power.set` và trạng thái
quan sát được, dù không hỗ trợ `remote.press`.

## 5. Kết nối local, dependency và mạng

1. Thêm `requirements-devices.txt` cho Broadlink và dependency đã kiểm chứng;
   không nâng cấp các pin audio/STT như một tác dụng phụ.
2. Lazy import SDK: provider tắt thì không cần cài Broadlink, không tạo socket.
3. Dùng thông tin IP/MAC/devtype đã xác minh; devtype có thể khác giữa revision
   RM4 mini nên không hard-code một mã lấy từ ví dụ RM mini đời khác.
4. Discovery có thời hạn và chỉ chạy khi người vận hành yêu cầu. Hỗ trợ chỉ định
   IP đích/interface nếu broadcast không đi qua VLAN; không quét toàn mạng định kỳ.
5. Khi kết nối lại, đối chiếu danh tính gateway; IP đổi không được khiến lệnh gửi
   tới một MAC/devtype khác. DHCP reservation là lựa chọn đề xuất.
6. Auth/session key giữ trong RAM, auth lại khi tạo session hoặc khi phiên hết
   hiệu lực. MVP không cần lưu key lâu dài hoặc lấy tài khoản Broadlink cloud.
7. Health dùng hello/auth hoặc truy vấn được model hỗ trợ; không lấy gửi IR làm
   health check. Cảm biến tùy chọn không phải điều kiện health thành công.
8. Mất kết nối: báo unavailable và kết thúc request hữu hạn; không tạo hàng đợi
   lệnh để gửi dồn khi mạng phục hồi.

Discovery/giao tiếp Broadlink dùng UDP tới thiết bị, thông thường cổng đích 80.
Đó không phải yêu cầu Smart Hub mở HTTP server ở port 80.
[Giao thức Broadlink](https://github.com/mjg59/python-broadlink/blob/master/protocol.md).

Trước khi cấu hình/chạy integration thật: kiểm kê `ss -lntup`, route/interface,
các dịch vụ đang dùng; không dừng hoặc chiếm port của dịch vụ khác. Client UDP
dùng cổng nguồn tạm thời do hệ điều hành cấp, đóng khi xong. Discovery cần
interface LAN phù hợp; nguyên tắc loopback áp dụng cho server nội bộ nếu phát
sinh sau này, không áp dụng máy móc cho client phải tới RM4 mini.

Không tạo API server/service thường trực trong MVP. Nếu phát sinh nhu cầu,
chọn port rảnh, bind loopback mặc định và kiểm tra port/process/health sau start.

“Local” ở đây là đường điều khiển của Smart Hub. Không hứa RM4 mini không bao
giờ liên hệ cloud. Kiểm thử offline WAN là hạng mục riêng, không tự chặn Internet
hoặc đổi firmware. Mã SDK có ghi chú watchdog/keepalive ở một số firmware; chỉ
bổ sung khi thử đúng máy chứng minh cần, có lifecycle rõ ràng.
[Mã device của SDK](https://github.com/mjg59/python-broadlink/blob/master/broadlink/device.py).

## 6. Học mã IR và quản lý thiết bị gia dụng

### 6.1. Quy trình học

1. Người dùng chọn target, remote gốc và nút; việc học thực hiện ngoài phiên
   listener để không nhầm IR learning với thu giọng nói.
2. Khóa riêng gateway, kiểm tra kết nối, vào learning mode.
3. Nhắc người dùng bấm một nút trên remote; poll mã với deadline, xử lý
   “chưa có dữ liệu” như đang chờ, lỗi mạng/auth như lỗi thực sự.
4. Kiểm tra payload là dạng IR được adapter hỗ trợ, độ dài hữu hạn, checksum
   file và metadata. Không tự nhận raw payload tùy ý từ câu nói.
5. Lưu candidate mới, quyền private; chưa cho voice dùng ngay.
6. Người dùng chủ động phát thử và quan sát appliance. Ghi rõ nút/cấu hình thực
   sự đã tác động; checksum không chứng minh nội dung mã đúng.
7. Đánh dấu verified và liên kết operation tới revision mã đã duyệt.
8. Khi thay mã, tạo revision mới và chuyển binding sau kiểm thử; giữ revision cũ
   để quay lại. Timeout/ngắt learning không được làm mất mã đã dùng.

Giới hạn learning đề xuất 30 giây, cho phép hủy; nếu SDK không có cancel IR
learning thì đóng phiên và chờ timeout của thiết bị, không dùng factory reset.
Phiên gửi IR khác nhận `BUSY` trong lúc gateway đang được học mã.

API học/gửi tham khảo từ
[`enter_learning / check_data / send_data`](https://github.com/mjg59/python-broadlink/blob/master/broadlink/remote.py).

### 6.2. Mã toggle và mã điều hòa

**TV/quạt:** nút Power có thể chỉ đảo trạng thái. Khi đó expose
`remote.press(power_toggle)`; không giả làm `power.set(on)` hoặc `power.set(off)`
dựa vào lần gửi trước. Remote gốc cũng có thể được dùng ngoài Smart Hub.

**Điều hòa:** giả định cần xác minh là một mã có thể mang toàn bộ power, mode,
temperature, fan, swing. Bắt đầu bằng vài preset chính xác, ví dụ
`cool_26_auto` và `off`, nếu remote/model thực tế hỗ trợ.

- Ghi đủ các giá trị remote khi học; không đặt tên “26 độ” cho mã chưa biết mode.
- Chỉ điều khiển nhiệt độ đã có preset; không tự nội suy bytes, gửi tăng/giảm
  nhiều lần hoặc lấy trạng thái cache làm trạng thái thật.
- Câu thiếu mode/fan chỉ dùng preset mặc định nếu đã được người dùng cấu hình;
  nếu chưa có mapping rõ thì không gửi.
- Nếu chỉ có toggle hoặc mã phức tạp chưa kiểm chứng, CLI học/kiểm tra vẫn làm
  được nhưng chưa bật voice cho thao tác đó.

Kho mã mẫu:

~~~json
{
  "schema_version": 1,
  "target_id": "ac_living_room",
  "command_id": "cool_26_auto",
  "revision": 1,
  "transport": "ir",
  "format": "broadlink_base64",
  "payload_ref": "codes/ac_living_room/cool_26_auto-v1.b64",
  "sha256": "CHECKSUM_CUA_MA_THAT",
  "effect": {
    "power": "on",
    "mode": "cool",
    "temperature_c": 26,
    "fan": "auto"
  },
  "verification": "pending"
}
~~~

Ví dụ mô tả schema, không phải mã có thể phát. Một lượt learning hoặc checksum
đúng không tự chuyển `verification` thành `verified`.

Với format trên, file chứa base64; SHA-256 tính trên toàn bộ bytes của file.
Trước phát phải kiểm tra hash, decode base64 nghiêm ngặt, giới hạn kích thước
và kiểm tra cấu trúc IR. Đường dẫn chỉ được trỏ vào kho mã đã cấu hình.

## 7. Kết quả, timeout, gửi lặp và hủy

### 7.1. Kết quả thống nhất

| Outcome | Ý nghĩa | Câu trả lời mẫu |
| --- | --- | --- |
| `rejected` | Không hợp lệ/không hỗ trợ, chưa gửi | “Chưa có lệnh tắt riêng cho quạt này.” |
| `failed` | Biết chắc chưa gửi thành công hoặc gateway từ chối | “Không kết nối được bộ điều khiển.” |
| `succeeded` + `gateway_ack` | RM4 mini chấp nhận yêu cầu, appliance chưa được xác nhận | “Đã gửi lệnh tới điều hòa phòng khách.” |
| `succeeded` + `observed_state` | Có nguồn trạng thái độc lập, mới, phù hợp | Chỉ dùng khi provider/thiết bị thực sự hỗ trợ |
| `unknown` | Có thể đã phát IR nhưng mất phản hồi/tiến trình | “Chưa xác định lệnh đã thực hiện; bạn kiểm tra thiết bị.” |

Không dùng chung một boolean `success` cho cả truyền gói và trạng thái appliance.
`assumed_state` nếu có phải ghi nguồn/thời gian; không dùng nó làm căn cứ cho
on/off, retry hoặc lời khẳng định trạng thái thật. Nhiệt độ phòng từ phụ kiện
RM4 mini không phải nhiệt độ cài đặt của điều hòa.

### 7.2. Cần kiểm soát retry bên trong SDK

Mã `send_packet` đã khảo sát có vòng gửi lại UDP khi chờ phản hồi hết hạn.
Vì vậy chỉ tắt retry ở `DeviceService` **chưa đủ** để bảo đảm một lần phát.
[Mã gửi gói Broadlink](https://github.com/mjg59/python-broadlink/blob/master/broadlink/device.py).

BL-01/BL-02 bắt buộc kiểm tra chính version pin:

- Kiểm thử mất ACK ở mức socket và đếm lần gửi gói write.
- Ưu tiên tùy chọn “một lần gửi” nếu version được chọn hỗ trợ.
- Nếu không có, dùng transport override nhỏ, riêng adapter, tái sử dụng framing/
  crypto của bản pin; review và test riêng. Không monkeypatch toàn bộ socket
  hoặc SDK toàn tiến trình. Nếu phải mang theo mã upstream, giữ license/attribution
  và ghi rõ khác biệt với bản pin.
- Nếu chưa kiểm soát được retransmission, chưa bật gửi thật cho toggle/relative
  và chưa tuyên bố chống duplicate đạt.

Chính sách ứng dụng: reads có tối đa một retry trong deadline; write không
auto-retry. Sau khi đã thử dispatch, lỗi timeout/response không hợp lệ phải xử
lý bảo thủ là `unknown` trừ khi có bằng chứng rõ gateway đã từ chối. Không
auth lại rồi tự phát lại cùng lệnh sau timeout.

Đề xuất timeout ban đầu: connect/auth 3 giây mỗi bước, gửi tối đa 3 giây, tổng
request 8 giây gồm chờ khóa; learning 30 giây. Đây là budget cần đo, không phải
cam kết latency. Timer dùng monotonic; hết hạn trước dispatch thì không gửi.

### 7.3. Dedup, concurrency và cancellation

- Một worker riêng cho mỗi gateway; không dùng worker model/loa. Bận thì trả
  `BUSY`, không xếp hàng vô hạn hoặc giữ câu lệnh để phát muộn.
- Có khóa giữa CLI và runtime trên cùng máy để tránh learn/send đồng thời;
  khóa trong process không ngăn được process khác.
- `request_id` ổn định theo command event, không tạo id mới mỗi callback.
  Cùng id/cùng nội dung trả kết quả đã có; cùng id/khác nội dung bị từ chối.
  Hai lần người dùng chủ động ra lệnh giống nhau vẫn là hai request khác nhau.
- Lưu ledger tối thiểu bằng SQLite stdlib tại state directory:
  id, action fingerprint, target, revision, `prepared / dispatching / terminal`.
  Ghi `dispatching` trước I/O. Sau crash, request còn `dispatching` thành
  `unknown`; không phát lại khi restart.
- Không tuyên bố exactly-once trên thiết bị IR. Ledger chỉ chặn lặp ở ứng dụng;
  kiểm soát transport và phản hồi thiết bị vẫn có giới hạn.
- Hủy trước dispatch: không gửi. Hủy sau dispatch: không thể rút lại tia IR;
  lưu outcome dù kết quả hiển thị bị hủy. Hủy await không có nghĩa thread đã dừng.
- Giữ khóa/worker bận tới khi SDK thực sự kết thúc. Mọi completion cập nhật ledger;
  chỉ phần phản hồi theo phiên mới bị lọc bởi session/generation/turn.

## 8. Cấu hình và dữ liệu local

Dùng file riêng `devices.local.json` và mẫu `devices.example.json`. Không trộn
`device=pipewire` của audio với danh mục thiết bị gia dụng. Parser thiết bị có
schema riêng, validate version, id trùng, target mapping, alias mơ hồ, tham số,
path, revision và capability. Cấu hình lỗi làm thiết bị disabled hoặc CLI fail,
không âm thầm sửa config âm thanh.

~~~json
{
  "schema_version": 1,
  "enabled": false,
  "voice_execution": false,
  "providers": {
    "broadlink": {
      "gateways": [
        {
          "id": "rm4_living_room",
          "model": "RM4 mini",
          "host": "192.0.2.10",
          "mac": "00:11:22:33:44:55",
          "devtype": null
        }
      ]
    }
  },
  "targets": [
    {
      "id": "ac_living_room",
      "name": "điều hòa phòng khách",
      "aliases": ["máy lạnh phòng khách"],
      "provider": "broadlink",
      "connection_id": "rm4_living_room",
      "bindings_ref": "bindings/ac_living_room.json"
    }
  ]
}
~~~

IP là địa chỉ tài liệu, MAC giả, `devtype=null` cố ý chưa xác minh. Chỉ được enable
sau inventory và validation; không lấy ví dụ này làm cấu hình thật.

State root đề xuất: `smart-hub/.local/devices/`, có tùy chọn đổi đường dẫn.
Cần thêm `.local/` và `devices.local.json` vào gitignore khi triển khai.

~~~text
.local/devices/
  catalog.json
  broadlink/
    bindings/
    codes/
    revisions/
  requests.sqlite3
  backups/
~~~

Quyền directory 0700/file 0600; atomic replace cho JSON/mã, transaction cho
ledger. Backup trước khi đổi binding; restore phải kiểm tra checksum/schema.
Không lưu key auth dài hạn mặc định. Log chỉ request id, target id, operation,
duration, outcome và error code; không dump raw packet/key/mã IR/transcript.

Revision config/code được chốt trong execution plan; sửa registry trong lúc
chờ xác nhận không được đổi âm thầm hành động đã được duyệt.

## 9. CLI trước, giọng nói sau

**Các lệnh sau là giao diện dự kiến, CHƯA có trong repo và không chạy ở lượt lập kế hoạch.**

~~~bash
# Chỉ kiểm tra cấu hình; không mở mạng
.venv/bin/python scripts/smart_hub.py devices validate --config devices.local.json

# Setup local sau khi được triển khai
.venv/bin/python scripts/smart_hub.py devices discover --provider broadlink --timeout 5
.venv/bin/python scripts/smart_hub.py devices register --provider broadlink --host RM4_IP --gateway rm4_living_room
.venv/bin/python scripts/smart_hub.py devices health --gateway rm4_living_room

# Học mã thành candidate; verify là lượt phát thử có chủ đích
.venv/bin/python scripts/smart_hub.py devices learn --target ac_living_room --command cool_26_auto
.venv/bin/python scripts/smart_hub.py devices verify-code --target ac_living_room --command cool_26_auto

# Lập kế hoạch không I/O; gửi thật tường minh bằng --execute
.venv/bin/python scripts/smart_hub.py devices press --target ac_living_room --command cool_26_auto --dry-run
.venv/bin/python scripts/smart_hub.py devices press --target ac_living_room --command cool_26_auto --execute

# Nối vào assistant là bước sau; chọn preview để chỉ hiện hành động dự kiến
.venv/bin/python scripts/smart_hub.py assistant --devices-config devices.local.json --device-mode preview
.venv/bin/python scripts/smart_hub.py assistant --devices-config devices.local.json --device-mode execute
~~~

`register` đối chiếu hello/model/MAC rồi lưu candidate được chọn; không tự reset,
setup Wi-Fi hoặc mở lock. `verify-code` cho người vận hành biết target/mã trước
khi phát; xác nhận tác động vật lý là bước đánh dấu verified, không suy từ ACK.

Các subcommand `devices` mặc định đọc `devices.local.json` ở gốc repo; tùy chọn
`--config` trong nhóm này trỏ tới schema thiết bị, không phải schema audio của
CLI cấp cao. Gửi thật cần provider `enabled=true`; gửi bằng giọng nói còn cần
`voice_execution=true` và `--device-mode execute`. `preview`/`dry-run` chỉ đọc
catalog, không auth hoặc mở kết nối tới thiết bị.

### 9.1. Nối vào runtime hiện tại

1. Observer/dispatcher nhận `CommandRecognized` hợp lệ sau wake. Không dùng
   `[WAKE TEXT]` làm lệnh và không xử lý transcript từ phiên lỗi/clipping.
2. Router nhận một tập mẫu câu ngắn, đầy đủ target/operation. Giữ từ phủ định:
   “đừng bật quạt”, “không tắt TV”, câu hướng dẫn như “nói bật quạt nào” không
   được khớp bằng tìm substring “bật quạt”.
3. Giới hạn một target/một hành động. Câu mơ hồ, ngoài tập hỗ trợ hoặc “bật cả…”
   trả kết quả cần làm rõ; chưa gửi và chưa chuyển qua LLM/provider khác.
4. Preview in `[DEVICE PLAN]`; execute in `[DEVICE RESULT]` theo outcome.
   Hành vi mặc định của assistant vẫn chỉ log câu lệnh.
5. Thêm trạng thái `EXECUTING_DEVICE` có deadline; audio capture tiếp tục được
   drain, worker mạng không chặn loop. Khi xong/lỗi quay về SLEEPING.
6. `WorkContext` cho action phải giữ ổn định qua dispatch/completion. Hiện
   runtime vô hiệu hóa generation khi kết thúc command; BL-06 phải sửa đúng
   ranh giới lifecycle, không gắn callback rồi vô tình coi mọi kết quả là cũ.
7. MVP phản hồi kết quả bằng terminal. WAV “em nghe” vẫn chỉ xác nhận đã wake,
   không có nghĩa thiết bị đã được điều khiển. TTS đọc kết quả là hạng mục sau.

### 9.2. Chính sách thực thi vừa đủ

MVP cho phép thao tác gia dụng rõ ràng đã allowlist và mã đã verified, khi người
dùng bật execute. Không yêu cầu xác nhận lại từng lệnh bật quạt thông thường.

Hành động chưa phân loại, target không được đăng ký, mã pending, giá trị ngoài
preset hoặc nhiều thiết bị đều không dispatch. Học/thay mã là thao tác CLI setup.
Các hành động có tác động cao chưa có flow xác nhận phù hợp thì để unsupported;
không tự gán mọi chỉnh nhiệt độ là “nguy hiểm” hoặc xây hệ thống phân quyền lớn.

## 10. Mở rộng TuyaSmart sau này

Tuya đi qua `DeviceProvider` riêng; catalog chọn provider theo target. Với Tuya,
adapter cần ánh xạ chức năng/trạng thái thật của từng thiết bị, không giả định
mọi thiết bị có cùng một mã data point. Tài liệu Tuya phân biệt instruction set
và status set theo thiết bị/sản phẩm.
[Tài liệu instruction/status Tuya](https://developer.tuya.com/en/docs/iot/frequent_used_paremeter?id=Katrvksxs7q4w).

Điểm mở rộng phải có ngay:

- Capability theo target, typed arguments và lỗi `unsupported`.
- Provider state có nguồn và thời điểm, hỗ trợ cả push/poll trong tương lai.
- Core không yêu cầu “mọi lệnh là mã IR”, “mọi provider có MAC” hoặc “mọi write
  có thể đọc lại trạng thái”.
- Không tự chuyển một request lỗi từ Broadlink sang Tuya.
- Contract tests chạy với fake provider có state để chứng minh không lệ thuộc RM.

**Chưa quyết định** Tuya local/cloud, app linking, credential, phí hay quyền API.
Không cài SDK, tạo tài khoản hoặc viết adapter Tuya trong đợt này. Cũng không hứa
chỉ thay provider name là dùng được Tuya mà không cần mapping/chứng thực.

## 11. Backlog Agile và thứ tự giao

Áp dụng skill global `agile-software-delivery` theo quy mô tính năng. PM/BA/
Backend/QC là vai trò kiểm tra công việc; không cần tạo đội agent hay clone
workflow vào repo chỉ để lập bản kế hoạch này. Frontend là CLI, chưa làm web.

| ID | Kết quả bàn giao | Phụ thuộc | Vai trò chính | Acceptance criteria |
| --- | --- | --- | --- | --- |
| BL-01 | Inventory, đánh giá SDK/dependency và retry trên artifact pin | Kế hoạch được duyệt; thông tin mạng | BA + Backend | RM4 mini/devtype được xác minh; list thông tin còn thiếu; chứng minh số lần send khi mất ACK |
| BL-02 | Contract, catalog, cấu hình tách biệt, fake provider, ledger | BL-01 phần SDK có thể chạy song song phần contract | Backend + QC | disabled không import SDK/mở socket; fake stateful provider dùng chung contract; id conflict bị chặn |
| BL-03 | Discovery/register/auth/health và per-gateway worker/lock | BL-01, BL-02 | Backend | đúng MAC/devtype; auth fail/network timeout hữu hạn; health không phát IR |
| BL-04 | Learning, revision mã, verify và binding appliance đầu tiên | BL-03 + chọn appliance/remote | Backend + người dùng | code mới pending; phát thử có quan sát; không mất mã cũ khi timeout/ngắt |
| BL-05 | Dry-run, send once, mapping outcome, rollback catalog | BL-02–04 | Backend + QC | dry-run không I/O; ACK mất → unknown; không gửi lại; toggle không giả làm on/off |
| BL-06 | Router nhỏ và voice preview/execute | CLI BL-05 đạt | BA + Backend | một event một action; phủ định/mơ hồ không gửi; đúng lifecycle/cancellation, provider lỗi không phá mic |
| BL-07 | Bộ kiểm thử và demo trên RM4 mini thật | Test đi cùng mỗi mục; tổng hợp sau BL-06 | QC + người dùng | đạt ma trận mục 12, có bằng chứng và giới hạn, chưa đạt ghi NOT TESTED/FAIL |
| BL-08 | README/runbook, dependency pin, review và rollback | BL-07 | Backend + PM | người dùng vận hành/disable/restore được; không đổi audio/model |
| TUYA-01 | Khảo sát và adapter Tuya | Deferred, yêu cầu riêng | Chưa thực hiện | dùng lại contract, có mapping/xác thực riêng |

Các đợt bàn giao:

- **Đợt A — kết nối và nền tảng:** BL-01–03; demo gateway online/offline, chưa phát IR.
- **Đợt B — điều khiển CLI:** BL-04–05; demo một appliance với mã đã xác minh.
- **Đợt C — giọng nói có chủ đích:** BL-06–08; preview trước, execute sau khi test CLI đạt.

Không gán số ngày/story point khi chưa biết mạng, remote và appliance. Sau BL-01
mới ước lượng dựa trên tương thích thực tế. Khi kế hoạch đã được duyệt, thực hiện
liên tục các hạng mục được cho phép; không xin duyệt lại từng file. Kiểm thử vật lý
cần appliance/thao tác được chọn và người dùng có thể quan sát.

## 12. Kế hoạch kiểm thử và tiêu chí nghiệm thu

### 12.1. Tự động, không dùng phần cứng thật

| Nhóm | Tình huống bắt buộc | Điều kiện đạt |
| --- | --- | --- |
| Contract/config | disabled, alias/target trùng, provider thiếu, schema sai | reject rõ; không socket và không ảnh hưởng audio config |
| Catalog/code | code pending, checksum sai, file thiếu, thay revision, write bị ngắt | không phát mã sai; giữ bản cũ |
| Discovery/auth | lock, sai MAC/devtype, malformed reply, IP đổi | không gửi IR; lỗi phân loại được |
| Transport | lost ACK, timeout, SDK internal retry | đếm gói write; một lần attempt; unknown không auto-resend |
| Concurrency | learn/send, hai CLI, worker bận, deadline | đúng lock; không backlog vô hạn/late dispatch |
| Dedup/crash | callback lặp, id trùng khác action, crash sau dispatching | không tự phát lại; trạng thái unknown bảo toàn |
| IR semantics | toggle, relative, preset AC thiếu, state stale | không giả confirmed; không tự ráp trạng thái |
| Router | câu đúng, phủ định, câu hướng dẫn, tên mơ hồ, ngoài allowlist | các câu bị từ chối gửi **0** lần |
| Runtime | cancel trước/sau dispatch, late completion, mạng chậm | ledger đúng; không kết quả phiên cũ; capture không bị block |
| Optional deps | không cài Broadlink, mock thuần Python | wake/mock vẫn chạy; hướng dẫn thiếu dependency khi enable |
| Tuya seam | fake provider có observed state | route/result không cần code Broadlink |

Giữ unittest và fake transport phù hợp repo; thêm nhóm `--devices` vào test
runner khi triển khai. Bộ mặc định không được tự broadcast hoặc điều khiển nhà.

Lệnh hồi quy hiện có:

~~~bash
python3 scripts/run_tests.py --mock
.venv/bin/python scripts/run_tests.py --runtime
.venv/bin/python scripts/run_tests.py --stt
.venv/bin/python scripts/run_tests.py
~~~

Chọn nhóm phù hợp mỗi thay đổi; full suite một lần ở cuối, không lặp vô ích.
Các lệnh trên chưa được chạy cho việc lập tài liệu này.

### 12.2. Nghiệm thu RM4 mini/appliance thật

1. Ghi model, firmware nếu đọc được, network/interface, MAC/devtype, version SDK.
2. Discovery/health đúng gateway; không phát IR trong bước này.
3. Học và xác minh từng nút đã chọn với người quan sát.
4. Chạy dry-run, xác nhận target/operation/mã; thiết bị không đổi trạng thái.
5. Gửi từng thao tác, ghi riêng: transport outcome và appliance có làm đúng
   hay không. Không lấy ACK để điền hộ cột appliance.
6. Thử gateway offline: lỗi hữu hạn, assistant còn hoạt động, không phát lệnh
   cũ sau khi thiết bị online.
7. Với giọng nói: ít nhất 5 câu rõ thuộc tập hỗ trợ và 5 câu âm tính/mơ hồ;
   từng câu đúng tạo một action; câu âm tính tạo 0 dispatch. Ghi riêng lỗi STT,
   router, truyền lệnh và appliance, không gộp thành một tỷ lệ.
8. Thử disable provider và mở lại assistant để xác nhận đường audio cũ vẫn dùng.
9. Test mất ACK, duplicate và crash bằng fake transport; không cần gây lỗi mạng
   nhà hoặc bắt điều hòa bật/tắt liên tục để kiểm tra những nhánh này.

Số lần phát thử phải phù hợp appliance và hướng dẫn của nó. Không đưa vòng bật/
tắt máy nén liên tục vào tiêu chí benchmark. Nếu chưa có remote/máy thật, chỉ
nghiệm thu phần mềm và ghi hardware **NOT TESTED**, không đánh dấu toàn MVP đạt.

### 12.3. Definition of Done

- Contract, mapping và các nhánh lỗi bắt buộc có kiểm thử.
- Một appliance thật đạt CLI; voice execute chỉ bật sau CLI.
- Không gửi ngoài allowlist, không auto-retry write hoặc phát lại request cũ.
- RM4 mini không bị mô tả như nguồn trạng thái thật của appliance.
- Tính năng disabled giữ hành vi wake/STT; không sửa gain/model/alias.
- Mã và cấu hình local không vào Git; không lộ key/payload trong log.
- README/runbook ghi setup, phạm vi, mã hỗ trợ, lỗi, rollback và giới hạn.
- Demo/review có số liệu thực tế; không có lỗi nghiêm trọng chưa xử lý trong
  đường dispatch. Ghi vài điểm cải thiện sau demo, không kéo Tuya vào đợt này.

## 13. File dự kiến khi triển khai

Cấu trúc đề xuất, chỉ tạo các phần thực sự cần:

~~~text
src/smart_hub/devices/
  contracts.py
  catalog.py
  config.py
  service.py
  router.py
  ledger.py
  providers/
    broadlink.py
    broadlink_transport.py
    broadlink_setup.py
src/smart_hub/cli.py                 # subcommand devices, cờ assistant opt-in
src/smart_hub/assistant.py           # hook command và lifecycle thực thi
src/smart_hub/state.py               # EXECUTING_DEVICE nếu nối voice
src/smart_hub/events.py              # action/result event nếu cần
tests/test_mock_devices.py
tests/test_mock_broadlink.py
tests/test_mock_device_runtime.py
scripts/run_tests.py                 # nhóm devices không I/O thật
requirements-devices.txt
devices.example.json
docs/broadlink-provider.md           # runbook sau triển khai
~~~

Bản kế hoạch nằm ở `docs/broadlink-provider-plan.md`. Sau duyệt mới cập nhật
README, architecture và implementation-status để ghi Broadlink là nhánh ưu tiên
mới; không sửa lịch sử các phase cũ thành “đã triển khai”.

## 14. Rủi ro chính và phương án

| Rủi ro | Xử lý trong kế hoạch |
| --- | --- |
| Mã đã cài trong app không lấy được qua LAN | Học remote gốc; thiếu remote thì đánh dấu phụ thuộc, không hứa tải toàn bộ |
| Device lock/VLAN/IP sai | Inventory, host/interface explicit, kiểm tra identity; không reset hay sửa firewall tự động |
| SDK âm thầm retransmit | BL-01 đo actual sends; adapter single-send trước khi bật writes |
| IR bị che/đặt xa | Quan sát appliance và chỉnh vị trí; ACK không được tính thành trạng thái confirmed |
| Toggle/lệnh tương đối | Capability và mapping trung thực; không dùng cache đoán bật/tắt |
| Điều hòa cần cả state | Preset giới hạn; ghi đủ power/mode/temp/fan/swing |
| Provider chặn loop | Worker riêng, deadline, lock giữ tới completion |
| STT nghe sai câu điều khiển | Preview, tập câu nhỏ, phủ định/ambiguity reject; tách lỗi STT khỏi lỗi provider |
| Dependency làm hỏng audio | Cài thử môi trường cô lập, pin; không nâng package STT để thỏa SDK |
| Tuya làm phình abstraction | Chỉ contract/fake stateful provider; triển khai thật ở yêu cầu riêng |

## 15. Rollout, rollback và nội dung cần duyệt

Rollout: **disabled → CLI inventory → mã verified → CLI execute → voice preview
→ voice execute cho target được chọn**. Mỗi bước ghi bằng chứng; không enable
tất cả target sau khi chỉ thử một mã.

Rollback phần mềm: tắt device mode/provider, quay lại dependency/config được
pin trước đó và revision binding cũ. Ledger request đã gửi vẫn giữ. Rollback
không tự phát lệnh ngược để “hoàn tác” appliance, nhất là toggle/unknown.

Branch đề xuất khi bắt đầu code: `codex/broadlink-provider` từ trạng thái repo đã
được xác nhận; giữ mọi thay đổi đang có. Không commit/push/deploy trong lượt
lập kế hoạch này.

Những điểm cần duyệt:

- [ ] Broadlink LAN trực tiếp, provider trung lập; Tuya chưa triển khai.
- [ ] RM4 mini IR; chọn một appliance đầu tiên và các nút cần dùng.
- [ ] Quy trình học/verify mã và giữ revision; không mặc định lấy mã từ app.
- [ ] Kết quả “đã gửi lệnh” cho IR một chiều, unknown khi không rõ.
- [ ] CLI trước, voice preview/execute sau; không đổi wake/STT đang ổn.
- [ ] Backlog BL-01–08; thông tin mạng, remote và appliance bổ sung trước test thật.

**Việc đã thực hiện khi lập tài liệu:** đọc repo và nguồn kỹ thuật, xác nhận model
qua người dùng, thiết kế phương án. **Chưa thực hiện:** cài SDK, discovery/auth với
RM4 mini, học/phát IR, sửa runtime hay thay cấu hình thiết bị.
