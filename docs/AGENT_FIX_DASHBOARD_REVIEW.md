# Playbook cho agent sửa dashboard theo review `afe7b35`

Ngày lập: 2026-09-19  
Repo: `smart-hub`  
Commit đang review: `afe7b353b81a48a192ce14b9b82eefc8200a8db7`  
Plan đối chiếu: [`docs/broadlink-wake-dashboard-plan.md`](broadlink-wake-dashboard-plan.md)  
Phạm vi: dashboard Broadlink RM4 mini độc lập, thu âm/review thủ công và Wake Word Lab.  
Ngoài phạm vi đợt này: voice dispatch vào thiết bị, TuyaSmart, public Internet và app mobile.

## Mục tiêu

Đưa code hiện tại về trạng thái có thể kiểm chứng theo các mốc M0–M4 của plan. Agent phải sửa lỗi theo thứ tự an toàn trước chức năng: không được để dashboard phát IR thật khi identity, retry, idempotency hoặc revision chưa an toàn; không được đưa mẫu âm thanh chưa QC vào benchmark; không được hiển thị kết quả benchmark nếu ứng viên chưa thực sự được chạy.

Đây là playbook triển khai, không phải xác nhận release. Không đánh dấu một hạng mục `PASS` chỉ vì mock test chạy qua. Mọi phần chưa có RM4/appliance/mic thật phải ghi `NOT TESTED`.

## Quy tắc bắt buộc khi làm việc

1. Đọc plan và code liên quan trước khi sửa: `docs/broadlink-wake-dashboard-plan.md`, `docs/dashboard-runbook.md`, `src/smart_hub/devices`, `src/smart_hub/recording`, `src/smart_hub/wake_lab`, `src/smart_hub/dashboard`, và các test hiện có.
2. Bảo toàn runtime wake/STT và dữ liệu WAV/model cũ. Không đổi `config.json`, gain, alias hoặc model production chỉ để làm dashboard chạy.
3. Không mở mic, loa, LAN discovery hoặc phát IR trong test mặc định. Phần cứng chỉ chạy trong ca manual được đánh dấu rõ `hardware`.
4. Mock phần cứng chỉ được bật khi có cấu hình tường minh như `SMART_HUB_MOCK_HARDWARE=1` hoặc flag test. Không tự fallback từ provider thật sang mock.
5. Trước khi chạy web server phải kiểm kê port bằng `ss -lntup`; mặc định bind `127.0.0.1`; không tự dừng tiến trình khác và không tự đổi sang `0.0.0.0`.
6. Khi chạy/restart server, kiểm tra lại port, PID sở hữu port và `GET /api/health` với retry ngắn. Nếu port dự kiến bận, chọn port rảnh và cập nhật đồng bộ lệnh kiểm tra.
7. Không nâng cấp hàng loạt dependency. Nếu cần thay transport Broadlink, phải pin version, ghi lý do và thêm test đếm packet.
8. Không xóa WAV, model, code revision, ledger hoặc review history để làm test xanh. Test phải dùng thư mục/database tạm.
9. Không sửa plan để hạ tiêu chí. Nếu một khả năng chưa thể thực hiện, API/UI phải báo `unsupported` hoặc `NOT TESTED`, không tạo kết quả giả.
10. Mỗi lỗi hành vi phải có kiểm chứng hồi quy có ý nghĩa, ưu tiên tái hiện fail trước khi sửa. Không viết test chỉ lặp lại implementation hoặc cho chỉnh sửa tài liệu/style nhỏ.
11. Dùng skill `agile-software-delivery` theo quy mô công việc; không cần clone workflow vào repo hay tạo đội subagent chỉ để thực hiện playbook này. PM/BA/Backend/Frontend/QC là các góc nhìn kiểm tra. Không tự thêm hệ thống quản lý dự án mới.
12. Agent được giao thực thi playbook phải tiếp tục các phần sửa local, reversible và test không phần cứng đã được giao; không hỏi lại cho từng file. Thiếu hardware hoặc nguồn catalog chỉ chặn nghiệm thu phần phụ thuộc đó, không chặn các phần phần mềm độc lập.
13. Không tự commit, push, merge hay deploy nếu nhiệm vụ thực thi chưa cho phép. Tuân thủ policy branch hiện có; không reset/clean hoặc quay repo về commit review. Các số dòng bên dưới là tại commit review, cần tìm lại symbol trên HEAD thực tế.

## Trạng thái hiện tại cần nhớ

Review đã tái hiện hoặc xác nhận bằng đối chiếu source các vấn đề sau:

- `loadOverview()` được gọi từ `app.js` nhưng không tồn tại, làm lỗi callback `DOMContentLoaded`.
- Evaluator gọi `load_labels(root=root)` dù hàm hiện tại chỉ nhận `labels_file`; endpoint benchmark trả lỗi 400.
- Evaluator gộp candidate theo profile/aliases và bỏ qua engine, artifact, threshold, reference.
- Enrollment chỉ ghi `candidate_meta.json`, chưa tạo artifact/reference thực.
- Catalog seed dùng payload IR sinh giả dưới các hãng/model thật.
- `send_data()` của Broadlink SDK pin hiện tại tự retry; giả lập mất ACK đã đếm 3 packet cho một lần gọi.
- Double-click UI tạo hai request ID khác nhau.
- Send/learn không revalidate MAC/devtype tại thời điểm I/O.
- Revision mới chưa verify được chọn làm active ngay.
- Review API chấp nhận file thiếu/hash sai và không giữ lịch sử quyết định.
- Recording mở capture mới sau cue, warmup 2 giây rồi bỏ thêm 0,1 giây; tổng cộng bỏ 2,1 giây đầu.
- API recording chưa ràng buộc speaker/split/label/distance/takes.
- `DeviceStorage()` tự recovery ledger mỗi lần tạo instance, nên GET/health có thể biến lệnh đang `dispatching` thành `unknown`.
- Bảng kết quả JS dùng schema phẳng không khớp schema metrics lồng nhau của evaluator.
- Provider tự chuyển sang mock khi thiếu SDK.
- Host/Origin dùng `startswith("127.")`, chấp nhận các hostname như `127.evil.test`.

137 test khác nhau đã PASS trong lượt review: `--mock` có 111, nhóm devices/locks/recording/wake-lab có 18, dashboard API có 8. 34 test child-study đã nằm trong 111 nên không cộng thêm. Đây là bằng chứng trên commit cũ, phải chạy lại trên thay đổi mới; không phải acceptance cho các luồng chưa được test.

### Bảng đối chiếu đủ 20 phát hiện

ID `R01–R20` giữ thứ tự trong báo cáo review. Mỗi ID chỉ được đóng khi có test/bằng chứng tương ứng; một thay đổi có thể xử lý nhiều ID nhưng phải ghi rõ.

| ID | Mức | Vị trí tại commit review | Hiện tượng/điều kiện tái hiện | Đợt sửa |
| --- | --- | --- | --- | --- |
| R01 | P1 | `src/smart_hub/devices/providers/broadlink_provider.py:176` | Socket giả lập mất ACK: SDK pin gửi 3 packet cho một `send_data()` | 1B |
| R02 | P1 | `src/smart_hub/dashboard/static/app.js:430` | Hai click trước khi response hoàn tất: 2 ID, có thể 2 lần phát | 1C, 2C |
| R03 | P1 | `src/smart_hub/devices/providers/broadlink_provider.py:176` | Hello trả MAC khác gateway đã lưu nhưng vẫn auth/send; đường learn cũng thiếu kiểm tra | 1A |
| R04 | P1 | `src/smart_hub/devices/catalogs/seed_data.py:10` | Catalog mang hãng/model thật nhưng payload do `_make_dummy_ir_b64()` sinh | 1E, migration |
| R05 | P1 | `src/smart_hub/recording/service.py:411` | Bỏ 105 frame/2,1 giây sau cue trước khi giữ 250 frame | 3B |
| R06 | P1 | `src/smart_hub/dashboard/static/app.js:65` | `DOMContentLoaded` dừng tại `loadOverview is not defined` | 2A |
| R07 | P1 | `src/smart_hub/wake_lab/evaluator.py:64` | Candidate hợp lệ vẫn lỗi `load_labels() got an unexpected keyword argument 'root'` | 4A |
| R08 | P1 | `src/smart_hub/wake_lab/evaluator.py:93` | 3 candidate gồm DTW chỉ thành 2 profile STT, alias bị trộn | 4B |
| R09 | P1 | `src/smart_hub/dashboard/routes/samples.py:141` | WAV thiếu/hash sai vẫn accepted; transcript đổi nhưng nhãn/events không đổi | 3D |
| R10 | P1 | `src/smart_hub/dashboard/security.py:32` | Host/Origin `127.evil.test` qua middleware; chưa thử DNS rebinding trong browser | 2D |
| R11 | P1 | `src/smart_hub/dashboard/static/app.js:593` | Trường JSON catalog đi vào `innerHTML` không escape; xác nhận bằng source-to-sink | 2C |
| R12 | P2 | `src/smart_hub/dashboard/static/index.html:540` | Form bắt buộc hãng + code set; không tạo remote trống để học hãng chưa có catalog | 1F |
| R13 | P2 | `src/smart_hub/devices/storage.py:381` | Rev 1 verified + rev 2 pending: normal remote chọn rev 2 ngay | 1D |
| R14 | P2 | `src/smart_hub/devices/storage.py:33` | Mở instance storage mới làm command đang dispatching thành unknown dù không restart | 1C, 5 |
| R15 | P2 | `src/smart_hub/dashboard/static/app.js:1120` | Renderer nhận metrics thật và lỗi `.toFixed()` trên undefined | 2B, 4E |
| R16 | P2 | `src/smart_hub/wake_lab/enrollment.py:60` | Enrollment chỉ tạo JSON; bảng artifacts vẫn rỗng, không có reference mới | 4C |
| R17 | P2 | `src/smart_hub/cli.py:397`, `dashboard/routes/wake.py` | `--audio-python` không được dùng; web env thiếu numpy/sherpa/onnxruntime | 4D |
| R18 | P2 | `src/smart_hub/recording/service.py:128` | Chỉ dashboard dùng audio lock; CLI/assistant không dùng; eval không loại trừ recording | 3C, tài nguyên |
| R19 | P2 | `src/smart_hub/dashboard/static/app.js:771`, `recording/service.py:288` | Refresh không restore session; chờ advance vô hạn, không lease/recovery | 3C, 5 |
| R20 | P2 | `src/smart_hub/dashboard/routes/devices.py:32` | Thiếu SDK, không bật mock nhưng provider vẫn mock và trả ACK | 1A |

## Thứ tự triển khai

Ưu tiên đóng P1 trước UI polish và demo phần cứng. Không có phát hiện P0 trong review này. Mỗi phase phải chạy kiểm chứng phù hợp và cập nhật checklist. Các phần độc lập được làm xen kẽ; tránh gộp refactor lớn khiến không biết lỗi nào đã được sửa.

### Phase 0 — Baseline, branch và test harness

**Mục tiêu:** tạo đường chạy có thể lặp lại và không làm bẩn dữ liệu.

- [ ] Xác nhận working tree, branch và commit base; không ghi đè thay đổi có sẵn.
- [ ] Ghi HEAD thực tế và phân biệt lỗi đã được người khác sửa sau review; chỉ đóng ID sau khi chạy ca hồi quy của lỗi đó.
- [ ] Trước khi test, cô lập fixture của các test hiện tại: `test_recording_service.py` và `test_wake_lab.py` từng ghi vào `recordings/` và `.local/`. Không chạy nguyên bộ này trên dữ liệu thật khi chưa patch root/test harness.
- [ ] Lập danh sách hash của WAV/model/config quan trọng trước và sau đợt sửa; chỉ hash/metadata cần thiết, không đưa audio hoặc transcript riêng tư vào Git.
- [ ] Tạo fixture tạm cho gateway, appliance, code revision, WAV hợp lệ, WAV thiếu, hash sai và labels.
- [ ] Bổ sung helper test để redirect `ROOT`, DB, `LOCKS_DIR`, recordings và candidate directory vào `TemporaryDirectory`.
- [ ] Chạy baseline:

  ```bash
  python3 scripts/run_tests.py --mock
  python3 -m unittest tests/test_devices.py tests/test_locks.py tests/test_recording_service.py tests/test_wake_lab.py
  .venv-dashboard/bin/python -m unittest tests/test_dashboard_api.py
  ```

- [ ] Ghi số test thực tế, không cộng trùng test giữa các suite.
- [ ] Thêm test runner riêng cho module mới nếu suite mặc định không thu nhận test đó.

**Đầu ra:** baseline log và fixture không nằm trong `recordings/`, `.local/` hoặc Git.

### Phase 1 — Chặn phát IR sai/lặp (P1, bắt buộc trước mọi hardware)

#### 1A. Provider thật và identity

Files chính: `src/smart_hub/dashboard/routes/gateways.py`, `routes/devices.py`, `devices/providers/broadlink_provider.py`, `devices/base.py`.

- [ ] `get_provider()` chỉ trả `MockDeviceProvider` khi mock được bật tường minh. Thiếu `broadlink` trong chế độ thật phải trả lỗi có mã `dependency_unavailable`/HTTP 503; tuyệt đối không báo ACK giả.
- [ ] Tách `ProviderUnavailableError`, `GatewayIdentityError`, `GatewayTimeoutError`, `GatewayLockedError` hoặc contract tương đương để UI phân biệt lỗi.
- [ ] Trước `send_code()` và `enter_learning()`, revalidate gateway bằng hello/auth với IP hiện tại và đối chiếu tối thiểu MAC + devtype với bản ghi lưu. Nếu mismatch, không phát/không học.
- [ ] Không suy ra appliance state từ ACK. Kết quả phải tách `request_state`, `gateway_ack`, `appliance_observation`.
- [ ] Nếu provider trả `False` hoặc outcome không chắc chắn, không ghi `DELIVERED`/`ACK_RECEIVED`.
- [ ] Discovery/check phải có deadline hữu hạn và giữ phân loại timeout, lock, identity mismatch, unsupported, offline.

#### 1B. Transport không retry write ngoài policy

- [ ] Đọc đúng implementation của `broadlink==0.19.0` đang pin; không giả định `send_data()` chỉ gửi một packet.
- [ ] Thiết kế adapter/transport có test đếm packet. Với write IR, một request của adapter không được tự retry sau mất ACK. Nếu chưa có transport đáp ứng điều kiện, chặn write trước I/O với `unsupported/transport_not_ready` và 0 packet. Không gọi transport retry rồi đổi nhãn kết quả thành unknown để coi là đã sửa.
- [ ] Timeout phải là deadline toàn request, không để retry nội bộ kéo dài vô hạn.
- [ ] Thêm test fake socket: ACK, timeout, malformed reply, mất ACK sau packet đầu; assert số packet và outcome.
- [ ] Không tuyên bố `exactly once` cho IR nếu chưa chứng minh ở transport; ghi `at-most-one adapter attempt` và `unknown` khi mất bằng chứng.
- [ ] Tách packet discovery/auth có thể retry hữu hạn khỏi packet phát IR cần một attempt. Đếm packet chứa write IR, không cộng hello/auth để kết luận nhầm là phát lặp.
- [ ] Tái sử dụng framing/encryption/checksum SDK khi có thể; nếu phải thay đường transport, test cả tạo packet và xác thực reply, không chỉ mock `send_data()` ở mức cao.

#### 1C. Ledger, idempotency và concurrency

Files chính: `devices/storage.py`, `dashboard/routes/devices.py`, `devices/base.py`.

- [ ] Ledger ghi trước I/O theo state `prepared -> dispatching -> delivered/failed/unknown`.
- [ ] Lưu `payload_digest` gồm appliance ID, button key, code revision ID/hash, gateway ID và action params.
- [ ] Cùng `request_id` + cùng digest: trả kết quả cũ, không I/O lần hai.
- [ ] Cùng `request_id` + khác digest: trả `409 idempotency_conflict`, không trả delivered cũ.
- [ ] Xử lý race hai request đồng thời: claim/insert atomically; không để SQLite `IntegrityError` lộ thành 500.
- [ ] `code_revision_id` phải thuộc đúng appliance và `button_key`; revision không được trỏ sang gateway/appliance khác.
- [ ] Double-click UI phải disable nút trong lúc chờ. Retry/poll phải giữ cùng request ID; không tạo ID mới cho cùng action.
- [ ] Có test hai thread/request song song chứng minh chỉ có một lần dispatch.
- [ ] Gateway đang bận phải trả 409/BUSY trước dispatch, không xếp lệnh cũ chờ phát sau khi gateway rảnh. Giữ khóa cho đến khi SDK/worker thực sự dừng, không nhả khóa chỉ vì HTTP timeout hoặc client disconnect.
- [ ] Acquire resource và claim ledger theo một thứ tự nhất quán. Duplicate đang running được poll bằng ID; response không được nói “đã gửi thành công” chỉ vì ledger entry đã tồn tại.
- [ ] Chỉ startup sở hữu app-instance lock mới recovery ledger. Đã gửi mà chưa ghi terminal thì unknown; không bao giờ auto-replay. Action chắc chắn chưa có write phải được ghi lý do riêng, không suy thành ACK.

#### 1D. Active revision và mã chưa verify

- [ ] Revision học/import mới mặc định `pending`/`is_verified=false`.
- [ ] `get_active_code_revision()` chỉ trả binding đã verify hoặc binding cũ đang active. Revision pending không được thay thế active mapping.
- [ ] Cho phép nút “Gửi thử” tường minh trên candidate pending nếu plan cần, nhưng phải ghi đây là setup/test action và không làm đổi normal remote.
- [ ] Khi observation `accurate`, tạo/đổi binding active theo revision check; khi `inaccurate/unknown`, giữ binding cũ.
- [ ] Học lại không ghi đè mã cũ; restart vẫn giữ revision cũ và trạng thái pending.

#### 1E. Catalog an toàn

- [ ] Bỏ payload sinh giả khỏi catalog production/seed mặc định. Fixture giả chỉ nằm trong mock test và phải có metadata `mock=true`/`unverified`.
- [ ] Catalog thật phải lưu source URL, revision, license, content hash, encoding và compatibility status.
- [ ] Chỉ hiển thị hãng/model theo nguồn thật hoặc khai báo của người dùng. Catalog có nguồn hợp lệ nhưng chưa thử trên appliance local vẫn được browse, phải ghi “chưa kiểm chứng”; không biến dữ liệu sinh giả thành bộ mã hỗ trợ một model thật.
- [ ] Nếu chưa có catalog thật, UI hiển thị “chưa có nguồn đã kiểm tra” và dẫn sang học remote.
- [ ] Kiểm tra payload đầy đủ: Base64 strict, độ dài/header/length field/repeat theo encoding đã xác minh; giới hạn kích thước.
- [ ] Import không được ghi đè revision bất biến; cùng source revision/hash phải idempotent, revision mới tạo bản ghi mới.
- [ ] Xử lý dữ liệu seed đã tồn tại trong DB và code revision đã copy sang appliance, không chỉ bỏ hàm seed cho cài đặt mới; xem mục migration bên dưới.

#### 1F. Wizard học khi không có catalog — R12

- [ ] Có lựa chọn “Học từ remote gốc / Chưa có bộ mã” từ màn thêm thiết bị, dùng được khi catalog hoàn toàn rỗng.
- [ ] Cho tạo appliance với `code_set_id=null`; hãng/model là chuỗi người dùng nhập hoặc “chưa rõ”, không bắt buộc chọn option catalog.
- [ ] Category custom vẫn tạo được remote; không tự chọn mã của hãng khác để vượt validation.
- [ ] Appliance mới có remote trống và CTA học nút; học xong tạo pending revision, chỉ kích hoạt qua luồng thử + xác nhận.
- [ ] Test browser xuyên suốt: database không code set → tạo thiết bị → mock learn → test send có chủ đích → accurate → active mapping → restart → vẫn đúng mapping.

**Tiêu chí hoàn tất Phase 1:** không có đường production nào tự mock, phát sai identity, retry write, dùng pending revision làm active hoặc trả ACK giả; tất cả ca trên có test.

### Phase 2 — Bootstrap, security và UI contract (P1)

#### 2A. Bootstrap browser

- [ ] Xóa hoặc triển khai `loadOverview()`; khởi tạo phải chạy hết CSRF, navigation, modal, gateway/appliance/sample/candidate/evaluation loads.
- [ ] Thêm smoke test JS bằng Node/JSDOM hoặc browser automation: `DOMContentLoaded` không có `ReferenceError`, các API GET khởi động được.
- [ ] Lỗi một panel không được ngắt các panel khác; dùng `Promise.allSettled` hoặc bắt lỗi riêng.

#### 2B. Render schema metrics

- [ ] Chốt một schema metrics duy nhất. Khuyến nghị giữ nhóm lồng nhau `positive`, `negative`, `performance`, `groups` và dùng adapter view-model ở frontend.
- [ ] Sửa `app.js` để không đọc các field không tồn tại như `positive_accuracy_pct`, `decode_rtf_mean`.
- [ ] Hiển thị số đếm và tỷ lệ; người lớn/bé/negative phải có denominator đúng.
- [ ] Nếu metrics thiếu hoặc sample count bằng 0, hiển thị `N/A`/lý do, không gọi `.toFixed()` trên `undefined` và không tạo tỷ lệ giả.
- [ ] Test renderer với metrics thật từ `evaluate_dataset()` và với dataset rỗng/lỗi.

#### 2C. Double-click, loading và trạng thái

- [ ] Nút gửi/lưu/học/đánh giá có state `idle/loading/success/error`; khóa thao tác liên quan trong lúc job chạy.
- [ ] Refresh/reconnect chỉ poll trạng thái, không lặp side effect.
- [ ] Job dài trả `202 + job_id`; UI poll backend state và hiển thị `queued/running/waiting_user/completed/failed/cancelled/interrupted`.
- [ ] Không dùng `innerHTML` cho dữ liệu từ catalog, tên thiết bị, candidate, transcript, note hoặc lỗi server. Dùng `textContent`/DOM node; nếu cần HTML tĩnh thì escape mọi giá trị.
- [ ] Thêm test stored-XSS với catalog name, appliance name, transcript và error string; payload không được tạo node/script.

#### 2D. Host/Origin/CSRF

- [ ] Parse hostname/IP bằng thư viện chuẩn; chỉ chấp nhận đúng `localhost`, `::1`, `127.0.0.1` hoặc allowlist cấu hình chính xác. Không dùng `startswith("127.")`.
- [ ] Origin phải so scheme/host/port đúng allowlist; không chấp nhận hostname giả như `127.evil.test`.
- [ ] CSRF token gắn với session local hoặc cơ chế one-time/init token; không ghi token vào URL/log. Mutation không có token luôn bị từ chối.
- [ ] CORS không mở `*`; không load CDN/analytics/audio cloud.
- [ ] Test Host/Origin: loopback hợp lệ, hostname giả, port khác, origin null, token thiếu/sai.
- [ ] Parse đúng IPv6 dạng `[::1]:port`; không tách Host bằng `split(':')[0]`. `testserver` chỉ nằm trong cấu hình test, không là ngoại lệ production. Không tin `X-Forwarded-Host/Proto` từ client khi chưa cấu hình proxy tin cậy.
- [ ] GET bootstrap/CSRF cũng phải chịu kiểm tra Host và xác thực session local; không coi một token toàn process công khai là cơ chế đăng nhập. Phiên khởi tạo cần cơ chế cấp token local cụ thể, hết hạn/rotation có kiểm chứng và lỗi dễ hiểu khi refresh.

### Phase 3 — Recording pipeline và dữ liệu (P1)

#### 3A. Input contract

Files: `dashboard/routes/recording.py`, `recording/service.py`, `child_study.py`.

- [ ] Dùng `Literal`/enum cho `speaker={adult,child}`, `split={pilot,dev,test}`, `label={positive,negative}`.
- [ ] `speaker_id` và phrase không rỗng; `distance_m` hữu hạn và `>0`; `takes_planned` nguyên dương với giới hạn hợp lý; timeout hữu hạn dương.
- [ ] Ép label/expected events nhất quán; không nhận label tùy ý rồi tự coi là positive.
- [ ] API reject trước khi lấy audio lock hoặc tạo thư mục nếu input sai.

#### 3B. Capture/cue đúng thời điểm

- [ ] Mở một capture owner sau warmup và giữ capture qua cue + từng take, hoặc thiết kế tương đương không warmup lại sau cue.
- [ ] Trong lúc phát cue phải drain audio; sau cue chỉ bỏ đúng tail đã đo (ví dụ 0,1 giây), sau đó lấy chính xác 5 giây.
- [ ] Nếu playback lỗi/non-zero/timeout, phiên phải `failed`, không thu như thể cue đã phát.
- [ ] Giữ RMS/peak/clipping/hash; clipping `>1%` dừng phiên và giữ take đã hoàn tất.
- [ ] Test fake capture đánh dấu frame: assert không mất 2 giây sau cue và số sample WAV đúng.
- [ ] Test cancel ở `waiting_user`, `cue`, `recording`, `processing`; không tạo take mới sau cancel.
- [ ] Chốt hành vi dừng take đang thu: ưu tiên hoàn tất take đang chạy, giữ đúng 5 giây rồi dừng; nếu cần hủy sớm vì capture lỗi thì đánh dấu partial/interrupted, không đăng ký nó như một take hoàn chỉnh. Ghi rõ chính sách trong API và UI.
- [ ] Khi waiting_user, capture phải được drain liên tục nếu còn mở. Test chờ lâu trước advance để WAV không chứa audio từ trước cue; không chỉ kiểm tra số sample cuối cùng.
- [ ] CLI và dashboard dùng chung service/rule cho cue, drain, duration, clipping và sync; giữ flags/schema/lối gọi CLI hiện có. Không để hai implementation copy tiếp tục lệch nhau.

#### 3C. Lock, reconnect và persistence

- [ ] Khóa audio ở điểm mở capture chung để CLI/dashboard/assistant cùng tuân thủ; cập nhật `scripts/record_wake_samples.py` và điểm mở capture tương ứng.
- [ ] Recording session/job phải có state bền hoặc recovery manifest rõ; restart chuyển job đang chạy thành `interrupted`, không tự thu lại.
- [ ] Refresh/mất browser không mất job ID; reconnect trong lease hữu hạn; hết lease giải phóng audio và giữ take đã hoàn tất.
- [ ] Dùng dataset lock chung khi `_sync_child_study`, CLI review và API review ghi `sessions.json`/`labels.jsonl`.
- [ ] Ghi provenance `mock` và không đưa mock sample vào official benchmark.
- [ ] Một chủ sở hữu giữ audio lock xuyên suốt warmup/cue/capture. Khi đưa khóa xuống tầng dùng chung, bỏ acquire lặp hoặc truyền ownership handle hợp lệ; không để service và `AlsaCapture` tự deadlock trên cùng file lock.
- [ ] Trước thu, dừng player dashboard đang phát trên máy host và khóa nút nghe lại trong các tab liên quan. Dùng trạng thái server + thông báo giữa tab khi cần; không tuyên bố kiểm soát được app audio ngoài repo.
- [ ] Dataset sync phải hoàn tất trước state completed. Sync lỗi phải hiện `failed/sync_failed` và error có hành động xử lý; giữ WAV/manifest để retry đồng bộ metadata, không thu lại hay duplicate labels.
- [ ] `advance` gắn `session_id` và `take_sequence`: lệnh lặp/trễ của take N không được vô tình bắt đầu take N+1. `stop`/`cancel` có tính idempotent, không làm trạng thái terminal quay lại running.

#### 3D. Review/QC/audit

- [ ] Acceptance chỉ hợp lệ khi technical QC đạt: file tồn tại, nằm trong recordings, WAV PCM 16 kHz/mono/16-bit, đọc được, SHA hiện tại khớp `source_sha256`, clipping không vi phạm.
- [ ] Lưu kết quả technical QC, thời điểm/checker và lỗi theo schema có version. Phân biệt kết quả QC với quyết định manual review: `technical_pass` một mình không có nghĩa accepted. Mẫu accepted bị hỏng sau snapshot phải thành ERROR trong benchmark, không tự loại khỏi denominator.
- [ ] Trước `accepted`, bắt buộc speaker confirmation và transcript thực tế; nếu transcript/label đổi thì cập nhật `label`, `expected_events` và kiểm tra lại contract.
- [ ] Lưu review history bất biến: reviewer, timestamp, trạng thái cũ/mới, transcript cũ/mới, note, source UI/CLI và revision.
- [ ] Dùng `expected_revision`/ETag hoặc số revision tăng đơn điệu bắt buộc khi ghi; `expected_status` một mình không đủ khi hai người cùng sửa mẫu đang accepted. Compare-and-write phải nằm trong cùng lock/transaction; mismatch trả 409 cùng revision hiện tại.
- [ ] Dùng `commonpath`/`Path.is_relative_to()` khi stream audio; chỉ sample ID đã tra trong labels mới được mở file.
- [ ] Có inventory dữ liệu cũ: manifest thiếu, WAV thiếu, hash lệch, sample ID trùng, metadata thiếu; hiển thị vấn đề thay vì tạo dataset rỗng.
- [ ] Không tự đổi nhãn dựa trên STT hoặc so chuỗi với wake word. Khi người duyệt sửa transcript, yêu cầu họ xác nhận nhãn/expected events đúng; cho reject/needs_review và sửa người nói qua thao tác explicit có audit nếu nghe ra sai người.
- [ ] Mẫu thiếu hash gốc không được “sửa” bằng cách băm bytes hiện tại rồi coi đó là bằng chứng ban đầu. Giữ trạng thái needs_review/provenance thiếu và có quy trình xác minh lại tường minh.
- [ ] Chỉ cập nhật metadata/nhãn/review history; không normalize/trim/resample WAV tại chỗ. History UI và CLI phải dùng chung nguồn dữ liệu, không tạo bộ nhãn thứ hai trong SQLite.

**Tiêu chí hoàn tất Phase 3:** không sample nào được `accepted` nếu thiếu QC hoặc xác nhận người; dữ liệu WAV cũ giữ nguyên bytes; manual review có audit và recovery.

### Phase 4 — Wake Word Lab và evaluator (P1/P2)

#### 4A. Sửa contract dataset

- [ ] Sửa `load_labels`/evaluator để nhận labels path hoặc `root` theo một contract duy nhất; thêm test gọi qua API với root tạm.
- [ ] Validate `split` và `mode` chỉ nhận giá trị hỗ trợ (`pilot/dev/test`, `official/all` nếu vẫn giữ all cho exploratory).
- [ ] Official benchmark chỉ chọn mẫu có bằng chứng accepted + speaker confirmed + QC/review theo schema hợp lệ. Mẫu legacy thiếu bằng chứng phải hiện needs_review qua inventory/migration có audit, không tự accepted. Sau khi chốt snapshot, file thiếu/hash sai/clipping phải có record lỗi và giữ trong denominator theo semantics hiện có.
- [ ] Snapshot gồm sample ID, source SHA, label/review revision, split, condition/speaker và hash snapshot đầy đủ.
- [ ] Chặn enrollment/evaluation overlap theo sample ID và hash WAV kể cả đổi tên/copy file; kiểm tra dev/pilot/test. Đánh giá chính thức reference-based engine trên session khác session enrollment theo plan; nếu có exploratory đo cùng session phải ghi rõ, không gọi là đánh giá độc lập.
- [ ] Giữ cùng danh sách đầu vào cho tất cả candidate trong lần so sánh. Nếu overlap làm tập không hợp lệ, reject run hoặc cho người dùng chọn một tập chung hợp lệ; không tự bỏ các mẫu khác nhau riêng cho từng candidate rồi so tỷ lệ.
- [ ] Không gọi khả năng tổng quát sang người nói mới khi cùng người nói có ở enrollment/dev/test. Báo riêng speaker/session và số lượng thực tế.

#### 4B. Candidate/engine adapter

- [ ] Mỗi candidate phải pin `engine`, artifact IDs/hashes, profile, aliases, threshold, preprocessing, reference IDs/hashes và config hash.
- [ ] Evaluator chạy từng candidate độc lập; không dedupe profile giữa candidates, không union aliases của candidates khác.
- [ ] `WakeEngine` phải dispatch tới adapter tương ứng. Nếu DTW/EfficientWordNet chưa có artifact/adapter runnable, candidate phải là `untested/incompatible` và endpoint từ chối chạy, không chạy như STT.
- [ ] Kết quả có `candidate_id`, engine, artifact/config snapshot; không chỉ dùng key profile làm danh tính.
- [ ] Một worker model nặng tại một thời điểm; có cancel/timeout và state job bền.
- [ ] Reset detector/VAD/cooldown/buffer giữa các clip độc lập. Không truyền trạng thái từ cuối WAV trước sang đầu WAV sau.
- [ ] Threshold là tham số riêng của từng engine; STT baseline không có score threshold tương ứng thì không dựng một thanh ngưỡng vô tác dụng. Profile/alias của baseline phải phản ánh đúng config hiện tại, không tự thêm “mẹ ơi”, tên gọi thiếu từ hoặc preset negative vào baseline.
- [ ] M3 cần baseline STT và ít nhất một engine hiện có khác khi artifact hợp lệ. `unsupported` là cách xử lý thiếu điều kiện thật, không thay thế việc viết adapter cho artifact đã đủ điều kiện và không được tính là đóng toàn bộ WK-03.

#### 4C. Enrollment/import/artifact

- [ ] Enrollment phải đọc WAV/hash của các sample accepted, kiểm tra split và tạo reference/artifact thực theo engine hỗ trợ (ví dụ NPZ riêng). Không chỉ ghi `candidate_meta.json`.
- [ ] Artifact registry phải có file, size, content hash, source, license, language/phrase và compatibility status.
- [ ] Candidate chỉ được đánh dấu runnable/verified khi artifact thực tồn tại và hash/compatibility đã kiểm tra. Bản ghi cũ thiếu artifact có thể giữ ở trạng thái unavailable để bảo toàn lịch sử; không xóa kết quả hay giả tạo một artifact ID để lấp foreign key.
- [ ] Import local/download có staging, giới hạn kích thước, chống path traversal/symlink, không chạy pickle/script; nguồn mạng phải allowlist và redirect kiểm soát.
- [ ] Artifact mới không đổi runtime mặc định và không ghi đè candidate cũ.
- [ ] Form enrollment phải chọn engine thực sự hỗ trợ reference. STT profile/alias thử nghiệm được tạo như cấu hình mới, không gọi là đã học từ WAV và không bắt chọn sample tham chiếu nếu engine không dùng chúng.
- [ ] Chỉ nhận positive accepted đã xác nhận làm reference positive; negative/reference-negative phải có vai trò riêng mà engine hỗ trợ. Không gom mọi accepted sample thành positive enrollment.
- [ ] Archive extraction phải kiểm tra symlink/hardlink/path tuyệt đối/`..`, tổng bytes/file count/độ sâu, kích thước sau giải nén và checksum. NPZ dùng `allow_pickle=False`; không nạp object array từ gói ngoài.
- [ ] Có import local format được tài liệu hóa và luồng download từ nguồn đã khai báo nếu nguồn sẵn có; form không cho backend fetch URL tùy ý. Dependency của gói mới không được tự cài vào env audio baseline.

#### 4D. Worker environment

- [ ] Dùng worker Python `.venv` phù hợp theo plan, nối thật `--audio-python`/cấu hình interpreter với audio/STT service. Không “sửa” lỗi thiếu numpy bằng cách cài toàn bộ stack audio vào web env và bỏ thiết kế isolation mà không chứng minh được parity/tài nguyên.
- [ ] Preflight phải báo dependency/artifact thiếu với trạng thái unsupported trước khi chạy; không trả benchmark hoàn thành toàn ERROR do thiếu numpy/sherpa mà UI gọi là thành công.
- [ ] Test env thiếu dependency và env đầy đủ; kết quả phải phân biệt `not runnable` với model chạy nhưng sample lỗi.
- [ ] Gọi worker bằng argv cố định, `shell=False`, working directory/path được kiểm tra. Protocol stdout là JSON có `schema_version`, `job_id`, `sequence`, event type; stderr dành cho log, không parse log `[SAVED]` để điều khiển UI.
- [ ] Có ready/preflight/progress/result/error/cancelled và deadline cho từng giai đoạn. HTTP handler không chờ toàn benchmark; worker chết hoặc protocol sai phải ra failed/interrupted, không complete rỗng.
- [ ] Cancellation dừng công việc kế tiếp và chờ worker nhả tài nguyên; nếu cần terminate process thì có timeout hữu hạn và chỉ tác động worker dashboard sở hữu, không dừng assistant hoặc app ngoài.

#### 4E. Frontend results

- [ ] Chốt JSON schema giữa evaluator/API/UI bằng fixture contract test.
- [ ] Bảng hiển thị riêng candidate, adult/child, positive/negative, FRR/FAR, duplicate, errors, RTF; không gọi false accepts/hour từ vài clip 5 giây.
- [ ] Drill-down từng sample có status/error/reason; report JSON/Markdown chứa snapshot và artifact hashes.
- [ ] Không cho chọn `test` để tinh chỉnh rồi vẫn gọi là holdout; UI ghi rõ khi test đã dùng để lựa chọn.
- [ ] CPU/RAM có cách đo, thông tin máy và so sánh tuần tự. RTF là decode/audio theo denominator đã quy định; latency wake chỉ có khi có mốc ground truth, thiếu thì N/A.

### Phase 5 — Recovery, storage và vận hành (P2)

- [ ] `DeviceStorage()` không tự biến `prepared/dispatching` thành `unknown` ở mọi route. Recovery chỉ chạy một lần trong lifespan/startup hoặc qua lệnh recovery có kiểm soát.
- [ ] Có app-instance lock; instance thứ hai báo lỗi rõ.
- [ ] Learning/evaluation jobs lưu trạng thái hoặc phục hồi thành `interrupted`; shutdown đóng worker có deadline.
- [ ] Không giữ resource lock vô hạn khi browser biến mất; có heartbeat/lease và cleanup an toàn.
- [ ] Atomic file writes + journal/version cho manifest, sessions, labels và review history; backup trước migration metadata.
- [ ] Kiểm tra `--audio-python` thực sự chọn interpreter worker; interpreter không hợp lệ phải fail sớm và không ảnh hưởng config/model baseline.
- [ ] Runbook cập nhật lệnh thực tế, dependency, rollback, cờ mock, trạng thái phần cứng chưa test và số test không trùng.

## Contract cụ thể để tránh sửa lệch giữa các module

Tên class, route và bảng có thể tinh chỉnh theo code hiện tại. Các invariant bên dưới là bắt buộc; không cần một framework job/provider lớn hơn phạm vi phase.

### Action IR: thứ tự xử lý và kết quả

Luồng đề xuất:

1. Validate request và quyền truy cập; tra device/gateway và revision thuộc đúng button.
2. Tra `request_id`: nếu đã có, so request đã chuẩn hóa với request gốc rồi trả existing state hoặc 409 conflict. Không resolve lại sang active revision mới khi retry request cũ.
3. Lấy gateway lock không chờ dài; nếu BUSY, không phát và không giữ backlog tự chạy sau đó.
4. Trong transaction, chốt snapshot appliance/gateway/mapping/code hash và claim request duy nhất. Nếu có request khác thắng race, xử lý duplicate/conflict, không I/O thêm.
5. Revalidate identity/auth với deadline; failure ở giai đoạn này là chưa phát IR, không ACK.
6. Commit `dispatching` trước write; gọi transport write một lần với snapshot code đã kiểm hash.
7. ACK hợp lệ → delivered; lỗi có bằng chứng chưa gửi → failed/not_sent; đã gửi nhưng mất ACK hoặc không chứng minh được kết quả → unknown.
8. Lưu terminal outcome và nhả lock sau khi worker thực sự kết thúc. Nếu process chết sau bước 6, startup recovery không phát lại.

| Tình huống | Kết quả yêu cầu | IR write attempt |
| --- | --- | --- |
| Revision sai device/button/hash, identity mismatch, dependency thiếu | Reject/failed trước I/O, `gateway_ack=false` | 0 |
| Gateway bận | 409/BUSY, không enqueue | 0 |
| ID trùng, payload khác | 409/idempotency_conflict | 0 lần bổ sung |
| ID trùng, payload giống | Trạng thái/kết quả của request gốc | 0 lần bổ sung |
| Reply ACK hợp lệ sau write | delivered, ACK có bằng chứng; appliance vẫn chưa xác nhận | 1 |
| Write đã attempt, ACK mất/không hợp lệ | unknown, hướng dẫn quan sát trước action mới | 1 |
| Restart sau dispatching chưa terminal | unknown; không auto-replay | 0 lần bổ sung |
| Người dùng bấm action mới sau khi quan sát | Request ID mới và một hành động có chủ đích | Tối đa 1 |

`is_verified` của code không đồng nghĩa trạng thái appliance. Observation cần chứa code revision, button, appliance và request thử liên quan nếu có, cùng người/thời điểm/notes. Callback quan sát của nút trước không được xác minh nhầm nút vừa gửi; UI phải giữ context đúng request.

Core nên nhận action/capability typed thay vì mọi provider bắt buộc nhận raw IR. Tách setup `discover/learn` khỏi execute chung, thêm fake provider có observed state để chứng minh contract không lệ thuộc Broadlink. Không triển khai SDK Tuya trong đợt này.

### Quyền sở hữu tài nguyên

| Công việc | Tài nguyên cần giữ | Khi xung đột |
| --- | --- | --- |
| Send/learn/check cùng RM4 | Gateway identity ổn định | BUSY trước side effect; gateway khác vẫn dùng được |
| Assistant/listen/CLI record/dashboard record | Audio capture ownership chung | Báo chủ sở hữu/bận; không tự kill hay tiếp tục mở mic |
| Cue/host playback dashboard | Audio session có quyền phát trong giai đoạn phù hợp | Không nghe WAV review chồng lên take |
| Benchmark nặng | Model/CPU reservation và exclusion với recording/assistant | Reject hoặc yêu cầu sắp xếp phiên; không tự dừng assistant |
| Ghi session/labels/review | Dataset writer lock dùng chung CLI/API | Deadline hữu hạn + revision conflict nếu snapshot cũ |
| Dashboard instance | App data-directory lock | Instance thứ hai fail trước recovery/jobs |

- [ ] Dùng cùng tên/path resource khi cùng data root; test hai process thật trên thư mục tạm, không chỉ hai object trong cùng process.
- [ ] Lock order được ghi rõ và nhất quán để tránh deadlock khi job cần nhiều resource. Không chờ holding metadata lock suốt benchmark hoặc chờ người dùng duyệt.
- [ ] `is_locked()` không được báo free nếu không kiểm tra được lock do permission/I/O lỗi; báo unknown/error có lý do.
- [ ] Không dùng `os.umask()` thay đổi rồi khôi phục tùy tiện trong nhiều thread cho private files. Tạo file/directory với quyền cụ thể; kiểm tra metadata/audio/IR private theo plan 0600/0700.

### Dataset, candidate và result snapshot

Một evaluation record tối thiểu phải lưu:

```text
evaluation_id, schema_version, state, created_at, completed_at
dataset_snapshot: ID/hash/label/review_revision/speaker/session/split/role của từng sample
candidate_snapshots: candidate_id, engine, artifact hashes, reference IDs/hashes, config
code_revision/git_commit, worker interpreter/runtime/package versions
per_sample: candidate_id, sample_id, outcome, events, transcript nếu có, error, timings
metrics: positive/negative/groups/performance với định nghĩa mẫu số rõ ràng
limitations: dữ liệu thiếu, same-speaker evaluation, test-exposed, latency N/A nếu thiếu mốc
```

- [ ] `config_hash` tính trên config/artifact có hiệu lực theo serialization ổn định; không lấy candidate name/ID ngẫu nhiên làm bằng chứng nội dung cấu hình.
- [ ] Một lần chạy sử dụng snapshot cố định dù người dùng đổi review hoặc tạo candidate mới trong lúc job chạy. Chỉnh metadata tạo revision mới, không làm đổi report cũ.
- [ ] Phân biệt `positive_detected` (có ít nhất một event), `positive_accurate` (đúng số event), `duplicate`, `missed`, `clipped`, `errors`; không đổi ý nghĩa cột để làm tỷ lệ đẹp hơn.
- [ ] Negative report có số clip false accept, tổng eligible negative và event count khi cần. Dataset không có negative thì FAR=N/A, không tuyên bố 0% false accept.
- [ ] Gói/artifact không runnable làm job fail preflight; file lỗi giữa lúc chạy tạo error record. Không gộp hai tình huống này thành “model chất lượng thấp”.

## Migration và bảo toàn dữ liệu đã có

Phần này phải được làm khi thay schema/storage, không hoãn tới sau khi người dùng đã dùng phiên bản mới.

1. **Inventory chỉ đọc trước sửa:** xác định DB/schema version, code sets/revisions đang active, artifacts, manifest/labels, jobs chưa terminal; ghi checksum/config cần đối chiếu. Dataset lỗi phải báo lỗi cụ thể; không khởi tạo đè file rỗng.
2. **Backup nhất quán:** dừng writer của dashboard hoặc giữ lock phù hợp, dùng SQLite backup API/transaction phù hợp WAL thay vì copy riêng `app.sqlite` đang ghi. Backup metadata có version và hash; không sao chép toàn bộ WAV nếu đã giữ bất biến.
3. **Schema migration có version:** dùng transaction và migration ID; chạy lại không duplicate records. Tránh `INSERT OR REPLACE` làm cascade mất revisions/bindings/observations. Test trên DB fresh và DB dạng `afe7b35` có dữ liệu thật giả lập.
4. **Quarantine seed giả:** nhận diện theo code-set provenance/ID/hash và các revision đã copy; đánh dấu không runnable, vô hiệu hóa binding trỏ đúng payload giả sau khi backup. Giữ bản ghi/lịch sử để audit; không đụng mã tự học hợp lệ chỉ vì cùng hãng/tên nút. Nếu provenance mơ hồ thì báo needs_review và chặn phát phần chưa xác minh, không tự đoán.
5. **Review legacy:** không bịa người duyệt/thời điểm hoặc tự backfill manual confirmation. QC có thể tính lại khi người dùng yêu cầu, nhưng thiếu bằng chứng acceptance thì đánh dấu needs_review. Mẫu accepted hợp lệ không tự đổi split hay source bytes.
6. **Candidate/artifact legacy:** candidate chỉ có JSON phải hiện artifact_missing/not_enrolled, kết quả cũ dùng profile gộp phải được ghi hạn chế/invalid comparison; không đổi chúng thành benchmark mới. Giữ report cũ để truy vết.
7. **Mock data:** dữ liệu mới có provenance bắt buộc; dữ liệu cũ chưa biết thật/mock ghi unknown cần review. Không dựa riêng vào tên `child_test` để xóa file người dùng.
8. **Fault injection:** mô phỏng lỗi disk write, lỗi giữa replace manifest/labels/sessions, process chết giữa transaction; lần khởi động sau phục hồi trạng thái nhất quán hoặc báo interrupted có hướng xử lý.
9. **Rollback:** khôi phục version cấu hình/metadata tương thích có kiểm tra; không ghi đè các take hoặc review mới phát sinh sau backup. Ledger đã gửi vẫn giữ, không phát IR ngược để “hoàn tác”.

## Ca hồi quy cụ thể cần agent viết/chạy

Dùng fake clock, Event/barrier và transport giả lập để điều khiển race; tránh sleep dài và test chỉ assert hàm đã được gọi mà không kiểm chứng outcome. Tất cả fixture đặt dưới temp root. Bảng này bổ sung các test có sẵn, không yêu cầu viết lại suite cũ.

| Test ID | Thiết lập và thao tác | Kết quả bắt buộc | Bao phủ |
| --- | --- | --- | --- |
| T01 | SDK pin + socket giả: write, sau đó recv timeout tới deadline | Đúng 1 packet write, unknown; không retry | R01 |
| T02 | Hold response của click đầu; click lần hai trước completion | Chỉ 1 action; nút/observation context không lệch | R02 |
| T03 | Hello trả MAC khác, rồi devtype khác; thử cả send và learn | 0 write/learn; lỗi identity rõ | R03 |
| T04 | DB đã có seed giả, appliance đã copy revision, và một code tự học hợp lệ | Migration chặn đúng seed; giữ code tự học và history | R04 |
| T05 | Fake capture đánh số frame, cue xác định, chờ manual lâu rồi advance | Giữ đúng 80.000 sample mới sau cue; không bỏ thêm 2 giây, không audio cũ | R05 |
| T06 | Browser load với API fixture, sau đó mở từng tab | Không JS error; các load/poll cần thiết chạy | R06 |
| T07 | Root tạm có labels accepted và backend thật/adapter test hợp lệ; gọi API benchmark | Job thực hiện đến terminal/report, không keyword TypeError | R07 |
| T08 | Hai candidate cùng profile nhưng alias/threshold khác + một engine DTW | Mỗi candidate chạy đúng adapter/config, baseline không đổi theo tập chọn | R08 |
| T09 | File thiếu, hash sai, WAV stereo/8 kHz, clipped, thiếu speaker confirmation | Không accepted; QC reason + status rõ, bytes giữ nguyên | R09 |
| T10 | Sửa transcript positive thành câu negative và submit review | Require nhãn/events explicit; metadata nhất quán và có audit | R09 |
| T11 | Host hợp lệ, `127.evil.test`, IPv6 `[::1]`, Origin sai port/scheme, token thiếu/sai | Accept đúng allowlist/session; reject các case sai trước handler | R10 |
| T12 | Import model/name/note chứa markup; mở preview và danh sách trong browser | Chỉ hiển thị text, không thực thi handler hay phát sinh mutation | R11 |
| T13 | Catalog trống, tạo category custom rồi học/thử/xác nhận | Không bắt chọn brand/codeset; active chỉ sau xác nhận | R12 |
| T14 | Rev1 verified đang active; học rev2 rồi unknown/inaccurate; restart | Normal remote vẫn dùng rev1; rev2 pending còn nguyên | R13 |
| T15 | Giữ command dispatching bằng barrier, gọi GET status/devices và tạo storage mới | Vẫn dispatching; không ghi lý do restart; startup recovery riêng mới đổi | R14 |
| T16 | Render metrics chuẩn, chỉ positive, chỉ negative, thiếu một nhóm, toàn errors | Không undefined/NaN; eligible denominator đúng; N/A đúng | R15 |
| T17 | Enrollment từ positive accepted dev với WAV thật, sau đó sửa WAV nguồn | Artifact riêng có hash/snapshot; mismatch được phát hiện, không đổi baseline | R16 |
| T18 | Worker interpreter đúng, không tồn tại, env thiếu numpy/model; cancel/crash | Đúng interpreter, preflight fail rõ, không report completed giả | R17 |
| T19 | Process A giữ shared audio lock như assistant; process B start recording/eval | B báo BUSY; không mở capture/worker nặng, không kill A | R18 |
| T20 | Start manual session, reload browser, advance; disconnect vượt lease; restart server | Restore đúng session; không thêm take ngoài ý định; terminal interrupted/cleanup đúng | R19 |
| T21 | Bỏ SDK import, mock flag false rồi true | Thật: dependency unavailable; mock: ghi rõ mock và không sửa dữ liệu thật | R20 |
| T22 | Cùng request ID khác action/revision/device; 2 request trùng concurrent | Conflict 409 hoặc reuse đúng; tối đa 1 attempt | Ledger |
| T23 | Revision của appliance khác hoặc payload hash bị đổi | Reject trước network, 0 packet | Ledger/code integrity |
| T24 | Hai review cùng expected_revision và cùng review_status hiện tại | Chỉ một commit; cái sau 409; history không mất | Dataset race |
| T25 | CLI review đồng thời với API review và recording sync | Không mất label/review; không tmp-file collision; locks dùng chung | Dataset writer |
| T26 | Mẫu đã vào snapshot bị xóa/đổi bytes giữa evaluation | ERROR trong đúng mẫu số; không tự lọc khỏi report | Benchmark integrity |
| T27 | Copy cùng WAV sang sample ID khác, dùng làm enrollment và evaluation | Chặn overlap hash ở mọi split; không chỉ kiểm ID/test | Leakage |
| T28 | Mock recording từ form, sau đó duyệt tay | Provenance giữ nguyên; official benchmark vẫn không nhận sample mock | Dataset provenance |
| T29 | `aplay` nonzero/timeout, capture lỗi, metadata sync lỗi | Không complete giả; giữ take đã hoàn tất, release lock đúng | Audio errors |
| T30 | Browser gửi lại advance(session S, take N) khi server đã chờ N+1 | Không bắt đầu N+1; reject stale hoặc return kết quả take N | Idempotent advance |
| T31 | Hai dashboard process cùng data root + startup recovery | Instance thứ hai fail, không đổi ledger/job của instance đầu | App ownership |
| T32 | Một gateway đang học, bấm cancel rồi send khi worker chưa kết thúc | Send vẫn BUSY cho tới tài nguyên thật rảnh; không overlap | Gateway cancellation |
| T33 | Import archive traversal/symlink/zip bomb, JSON/payload quá lớn | Reject trước publish; không ghi ngoài staging/root, không để artifact runnable | Imports |
| T34 | Sửa review/candidate khi evaluation đang chạy | Report vẫn dùng snapshot ban đầu và lưu config/hash đó | Reproducibility |
| T35 | Generic fake provider có observed state, không MAC/raw IR | Core nhận cùng action/result contract, Broadlink setup không bị ép vào provider khác | Provider seam |

Các test tách lớp để chẩn đoán lỗi: T07 kiểm endpoint + dataset contract; T08 kiểm dispatch/config; T16 kiểm renderer. Không mock tất cả chúng trong cùng một test rồi kết luận benchmark hoạt động end-to-end.

## Ma trận acceptance bắt buộc

| ID | Điều kiện đạt | Bằng chứng tối thiểu |
| --- | --- | --- |
| AC-01 | Thiếu SDK không thể phát IR giả | API 503/unsupported; test provider không gọi mock |
| AC-02 | MAC/devtype mismatch không gửi/học | Test fake gateway; ledger không có dispatch thành công |
| AC-03 | Một request mất ACK không auto-retry write | Fake socket đếm đúng policy; outcome `unknown` khi cần |
| AC-04 | Double-click chỉ một action | UI/endpoint concurrency test; một packet attempt |
| AC-05 | ID giống payload khác trả 409 | Idempotency conflict test |
| AC-06 | Pending revision không active | Learn/relearn/restart test |
| AC-07 | Catalog production có nguồn/hash/license | Catalog fixture kiểm tra metadata; seed không có payload giả |
| AC-08 | Startup dashboard không lỗi | JS/browser smoke test; API GET khởi động đủ |
| AC-09 | Recording bắt đầu đúng sau cue | Fake frame timing + WAV sample count |
| AC-10 | Input recording invalid bị reject sớm | API validation test |
| AC-11 | Accepted cần technical pass + manual confirmation | Missing WAV/hash/format/transcript tests |
| AC-12 | Review có audit/revision và chống race | Hai reviewer test + history assertions |
| AC-13 | Benchmark endpoint chạy được với root tạm | Integration test không `TypeError` |
| AC-14 | Mỗi candidate chạy engine/cấu hình riêng | Spy adapter thấy đúng candidate/artifact/aliases/threshold |
| AC-15 | Artifact/reference tồn tại và hash đúng | Enrollment/import test |
| AC-16 | Metrics UI đúng schema | Fixture evaluator -> render không `undefined`/`NaN` |
| AC-17 | Host/Origin/CSRF chặt | Host `127.evil.test`, origin sai, token sai đều bị chặn |
| AC-18 | Restart/reconnect không replay | Job/ledger/recovery tests |
| AC-19 | CLI/dashboard không tranh mic âm thầm | Shared audio lock integration test |
| AC-20 | Hardware acceptance tách ACK và appliance observation | Runbook ghi từng nút, ACK, quan sát và giới hạn |
| AC-21 | Tạo/học remote được khi catalog rỗng | T13 |
| AC-22 | Nguồn mock không lọt official benchmark | T28 |
| AC-23 | Review/sync error không bị báo thành công | T24, T25, T29 |
| AC-24 | Migration giữ dữ liệu và vô hiệu đúng payload giả cũ | T04, fixture DB version cũ, hash trước/sau |
| AC-25 | Snapshot benchmark bất biến, chặn reference leakage | T26, T27, T34 |
| AC-26 | Lock/cancel/single-instance hoạt động liên tiến trình | T19, T20, T31, T32 |
| AC-27 | CLI dùng cùng quy tắc thu/review và giữ flags cũ | Parity test CLI/service/API |
| AC-28 | Core có capability/action/state contract không bắt mọi provider dùng IR | T35 (partial / interface foundation) |

## Test plan sau khi sửa

### Test tự động không phần cứng

```bash
python3 -m unittest tests/test_devices.py tests/test_locks.py tests/test_recording_service.py tests/test_wake_lab.py
.venv-dashboard/bin/python -m unittest tests/test_dashboard_api.py
python3 scripts/run_tests.py --mock
python3 -m compileall -q src/smart_hub
```

Bổ sung hoặc cập nhật tối thiểu các nhóm sau:

- `test_broadlink_transport.py`: packet count, timeout, malformed reply, MAC/devtype.
- `test_command_ledger.py`: idempotency digest, concurrent claim, recovery only at startup.
- `test_dashboard_bootstrap.js` hoặc browser smoke: `DOMContentLoaded`, XSS-safe rendering, double-click.
- `test_recording_service.py`: cue timing, no warmup loss, input validation, cancel/reconnect.
- `test_samples_review.py`: QC, SHA, WAV format, label/expected-events consistency, audit history, race.
- `test_wake_lab.py`: evaluator root contract, per-candidate engine dispatch, artifact/hash snapshot, leakage.
- `test_dashboard_security.py`: exact Host/Origin/CSRF allowlist.

Sau khi các lát cắt ổn định, chạy hồi quy runtime/STT thực trong `.venv` và full suite một lần nếu dependency/artifact có sẵn:

```bash
.venv/bin/python scripts/run_tests.py --runtime
.venv/bin/python scripts/run_tests.py --stt
.venv/bin/python scripts/run_tests.py
```

Đọc test discovery trước khi chạy full suite để chắc rằng không có ca mở mic/network thật mới được thêm. Nếu env audio không cài dependency web, API tests có thể SKIP; phải chạy suite API riêng trong `.venv-dashboard` và báo skip trung thực. Không tính các suite overlap nhiều lần vào tổng test duy nhất. Không cài dependency thiếu vào env baseline một cách âm thầm.

Lượt review gặp asyncio/TestClient treo trong sandbox nhưng chạy ngoài sandbox đạt. Nếu gặp lại, lấy traceback/timeout để phân biệt lỗi code và giới hạn môi trường; dùng quy trình escalation của công cụ khi cần, không sửa thuật toán để né sandbox. Không lặp vô hạn test đang treo và không tuyên bố FAIL code khi chưa tách được nguyên nhân.

### Manual/browser không phần cứng

1. Kiểm kê port, khởi động loopback trên port rảnh.
2. Mở trang bằng browser, kiểm tra console không có `ReferenceError`.
3. Kiểm tra loading/empty/error/retry cho mọi tab.
4. Import catalog fixture có chuỗi HTML và xác nhận không có DOM injection.
5. Chạy recording mock ở thư mục/database tạm; xác nhận mock provenance không vào official benchmark.
6. Chạy evaluator với fixture WAV hợp lệ và dependency đầy đủ; kiểm tra từng candidate và report.
7. Restart server giữa job/ledger/recording; xác nhận state `interrupted/unknown` đúng và không replay side effect.

### Manual hardware có điều kiện

Chỉ thực hiện khi có RM4 mini, mạng LAN phù hợp, remote gốc, appliance và người quan sát. Trước khi chạy phải ghi model/devtype/MAC/IP/SDK version và xác nhận port HTTP loopback. Kiểm tra theo thứ tự:

1. Discovery/health không phát IR.
2. IP đổi hoặc MAC mismatch bị chặn.
3. Học một nút, timeout/cancel, revision cũ vẫn giữ.
4. Gửi thử một lần, ghi riêng ACK và quan sát appliance.
5. Xác nhận thao tác một lần trên phần cứng phù hợp. Mất ACK/retry/crash đã kiểm bằng fake transport; không cần gây mất mạng nhà hoặc chạy vòng bật/tắt máy nén để tái hiện.
6. Xác minh 3–5 thao tác hữu ích, restart rồi dùng lại binding đã verify.
7. Gateway offline: lỗi hữu hạn, không replay khi online lại.

Nếu không có hardware, ghi `NOT TESTED`, không dùng mock ACK để đánh dấu M1/M4.

## Definition of Done cho mỗi phase

- [ ] Code/review/test đúng phạm vi phase, không có refactor không liên quan.
- [ ] Acceptance criteria tương ứng có test hoặc bằng chứng manual.
- [ ] Error/timeout/cancel/restart/concurrency đã xét.
- [ ] Không lộ raw IR, auth key, audio hoặc transcript nhạy cảm trong log.
- [ ] Dữ liệu cũ và rollback path được bảo toàn.
- [ ] README/runbook và trạng thái implementation cập nhật nếu hành vi user-facing đổi.
- [ ] Không tuyên bố release-ready khi còn P1 hoặc hardware criterion chưa test.

## Mẫu báo cáo agent sau khi hoàn thành

```text
Commit/branch:
Phạm vi đã sửa:
P1/P2 đã đóng: [ID]
Files chính:
Tests tự động: [lệnh + số PASS/FAIL]
Browser/manual: [kết quả]
Hardware: [PASS / FAIL / NOT TESTED + lý do]
Migration/config/dependency:
Known limitations:
Rollback:
Mốc M0–M4 hiện tại:
```

Không ghi “171/171” nếu các test bị cộng trùng; báo số test thực tế theo từng suite và tổng số test duy nhất.
