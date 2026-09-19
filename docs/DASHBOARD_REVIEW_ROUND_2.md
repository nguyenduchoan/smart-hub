# Review dashboard vòng 2 — 2026-09-19

**Kết luận: NEEDS_FIX — chưa đủ điều kiện nghiệm thu theo `AGENT_FIX_DASHBOARD_REVIEW.md`.**

Đã review mã nguồn hiện tại, đối chiếu R01–R20 và acceptance criteria, chạy các bộ test có sẵn và tái hiện thêm bằng fixture tạm. Một số lỗi cũ đã được sửa, nhưng còn lỗi làm treo thu âm, tạo artifact không dựa trên âm thanh, báo kết quả sai engine, sai nhãn dữ liệu và gửi mã IR không đúng ràng buộc thiết bị.

## Phạm vi và bằng chứng

- Branch: `codex/local-dashboard-broadlink`.
- HEAD nền: `afe7b353b81a48a192ce14b9b82eefc8200a8db7`.
- Review cả thay đổi chưa commit và file mới `src/smart_hub/wake_lab/worker.py`.
- SHA-256 của `git diff --binary` lúc review: `2e73b3cab6d6950ebfc47fff3d78405e3ed4b47a65356210140dcb51945b2713`. Hash này không bao gồm file untracked.
- Trong lượt review chỉ thêm tài liệu này; không sửa implementation, model, cấu hình nhận giọng hoặc các bản thu của người dùng.
- Các ca tái hiện dùng SQLite/WAV/labels ở thư mục tạm, provider/capture giả lập. Không mở microphone/loa, quét LAN hay phát IR thật.
- P1: cần sửa trước khi dùng luồng bị ảnh hưởng với dữ liệu/thiết bị thật. P2: cần hoàn tất trước khi đóng acceptance criteria tương ứng.

## Các lỗi đã xác nhận

### V2-01 — P1: `AlsaCapture.drain()` lặp vô hạn với pipe thật của `Popen`

**Vị trí:** [audio.py:95](../src/smart_hub/audio.py#L95), liên quan [recording/service.py:396](../src/smart_hub/recording/service.py#L396).

`AlsaCapture.__enter__()` tạo `Popen(..., bufsize=0)`, nên `stdout` là `FileIO`, không có `read1()`. `drain()` gọi `read1()` ở dòng 107, bắt exception rồi chỉ thoát vòng `for`. Vòng `while True` bên ngoài tiếp tục thấy pipe readable và lặp lại mãi. Trường hợp stderr readable cũng không được tiêu thụ hoặc xử lý.

**Tái hiện:** dùng `os.pipe()`, mở đầu đọc bằng `os.fdopen(..., buffering=0)`, ghi 640 byte PCM, đăng ký selector rồi gọi `drain()` trong subprocess. Kết quả: `FileIO has_read1=False`; subprocess không thoát sau deadline 1 giây.

**Tác động:** phiên thu dashboard có thể kẹt ở cue/drain ngay lượt đầu, giữ audio lock; nút Stop chỉ đặt event nên không giải phóng vòng lặp này.

**Cần sửa/test:** dùng API đọc tương thích với pipe thực, xử lý EOF/stderr/error và bảo đảm vòng drain kết thúc; test bằng raw pipe giống `Popen`, không chỉ fake capture có sẵn hàm `drain()`. Bao phủ stop khi đang drain. Liên quan R05, AC-09/26.

### V2-02 — P1: Enrollment tạo artifact từ sample ID, không từ WAV

**Vị trí:** [enrollment.py:60](../src/smart_hub/wake_lab/enrollment.py#L60).

`templates.bin` được tạo bằng `sha256(f"{sid}:{i}")`; không đọc WAV, kiểm hash nguồn hoặc trích xuất đặc trưng âm thanh. File ZIP có tên `template.npz` chỉ chứa `meta.json` và `templates.bin`, sau đó được đăng ký với `compatibility_status="verified"`.

**Tái hiện:** truyền một label accepted/confirmed có `source` trỏ tới WAV không tồn tại. Hàm vẫn tạo candidate DTW, artifact `verified`, với 128 byte template cho một sample.

**Tác động:** người dùng tưởng đã tạo model từ giọng bé nhưng artifact không chứa thông tin âm thanh và chưa được engine kiểm chứng.

**Cần sửa/test:** enrollment thật cho engine được hỗ trợ; đọc/hash WAV, tạo artifact đúng định dạng và kiểm tra load/inference. Engine không hỗ trợ phải báo unavailable/unsupported. Test WAV thiếu, hash lệch, thay đổi nội dung WAV và khả năng load artifact. R16, AC-15.

### V2-03 — P1: Mọi candidate vẫn chạy STT dù báo engine là DTW hoặc engine khác

**Vị trí:** [worker.py:32](../src/smart_hub/wake_lab/worker.py#L32).

Worker chỉ lấy `engine` để ghi vào kết quả. Tất cả candidate đều gọi `child_study.evaluate_dataset()`, tức đường VAD/STT/keyword hiện tại. Artifact, reference, threshold và preprocessing của candidate không được đưa vào inference.

**Tái hiện:** candidate `engine="dtw"`, `threshold=0.99`, `model_id="missing_artifact"` vẫn gọi evaluator STT; kết quả lại ghi `engine="dtw"`. Spy thấy chỉ truyền profile, aliases, split, mode và root.

**Tác động:** bảng so sánh model có thể gắn tên DTW cho số đo STT; thay threshold/artifact chưa thực sự thay model được đo. Việc tách alias giữa candidate chưa đủ để đóng R08.

**Cần sửa/test:** dispatch theo adapter engine; pin và xác minh artifact/config/reference; reject engine chưa hỗ trợ. Test hai candidate cùng profile nhưng khác threshold/artifact, và DTW phải gọi adapter DTW. R08, AC-14.

### V2-04 — P1: Review vẫn accepted mẫu thiếu hash gốc hoặc clipping nghiêm trọng

**Vị trí:** [samples.py:141](../src/smart_hub/dashboard/routes/samples.py#L141).

Điều kiện `if expected_sha and actual_sha != expected_sha` bỏ qua kiểm tra khi thiếu hash. QC chỉ đọc header WAV, không kiểm PCM/clipping hoặc bảo đảm toàn bộ nội dung đọc đủ.

**Tái hiện qua route và hàm lưu label thật trên fixture tạm:** WAV chuẩn 5 giây nhưng không có `source_sha256` vẫn accepted; WAV 5 giây có toàn bộ sample bằng 32767, hash đúng, cũng accepted. Cả hai được lưu nhãn positive.

**Cần sửa/test:** dùng cùng technical QC với CLI; thiếu hash gốc phải needs_review, clipping vi phạm phải chặn acceptance, kiểm đọc đủ dữ liệu và lưu kết quả QC có version. Không backfill hash hiện tại rồi xem đó là bằng chứng gốc. R09, AC-11/27.

### V2-05 — P1: Sửa transcript tự động làm sai nhãn benchmark

**Vị trí:** [samples.py:172](../src/smart_hub/dashboard/routes/samples.py#L172).

API tự gán positive/negative và expected_events bằng so sánh chuỗi `.lower()` với wake word. Không có trường yêu cầu người duyệt xác nhận lại nhãn/events.

**Tái hiện:** duyệt một mẫu positive với transcript `Maika ơi!` cho kết quả `accepted`, `label="negative"`, `expected_events=0`. Chỉ thêm dấu câu đã làm đổi ground truth.

**Cần sửa/test:** transcript và quyết định nhãn/events là dữ liệu riêng; yêu cầu thao tác explicit khi đổi nhãn, lưu audit. Test dấu câu, khoảng trắng, wake phrase nằm trong câu dài và người duyệt sửa transcript. Không dùng phép so chuỗi để quyết định ground truth. R09, phần 3D, T10.

### V2-06 — P1: API có thể gửi revision của thiết bị/nút khác

**Vị trí:** [devices.py:190](../src/smart_hub/dashboard/routes/devices.py#L190).

Khi request có `code_revision_id`, API lấy revision theo ID nhưng không kiểm tra `appliance_id`, `button_key` hoặc hash bytes với `payload_hash`. Kiểm base64/định dạng IR không thay thế được kiểm ràng buộc này.

**Tái hiện:** gọi action cho thiết bị B, nút `different_button`, truyền revision thuộc thiết bị A/nút `power`. Provider giả được gọi với payload của A và API trả `delivered`.

**Cần sửa/test:** kiểm toàn bộ binding và hash trước ledger dispatch/network; test revision khác appliance/nút/hash lệch đều trả lỗi với 0 lần gọi provider. T23, hợp đồng ledger/code integrity.

### V2-07 — P1: Cùng request ID nhưng khác payload vẫn trả kết quả thành công cũ

**Vị trí:** [devices.py:179](../src/smart_hub/dashboard/routes/devices.py#L179), [storage.py:471](../src/smart_hub/devices/storage.py#L471).

API chỉ kiểm tồn tại `request_id`; ledger không lưu/so payload digest. Check và insert tách rời, nên race cùng ID cũng chưa có đường xử lý conflict nhất quán.

**Tái hiện:** gửi ID X cho thiết bị A/nút power, sau đó dùng X cho thiết bị B/nút khác. Lần hai trả `delivered` và ACK cũ, không trả 409. Không có lần gửi thứ hai nhưng UI có thể hiểu nhầm B đã được gửi lệnh.

**Cần sửa/test:** digest appliance/gateway/button/revision ID + hash/action params, claim nguyên tử; cùng ID+cùng digest trả cùng kết quả, khác digest trả 409; test hai request đồng thời. R02/1C, AC-05.

### V2-08 — P1: Catalog API vẫn tự seed mã giả khi mock tắt, chưa có quarantine dữ liệu cũ

**Vị trí:** [catalog.py:17](../src/smart_hub/dashboard/routes/catalog.py#L17), [app.py:25](../src/smart_hub/dashboard/app.py#L25).

Startup chỉ ngăn seed mới khi mock tắt, nhưng các GET catalog vẫn gọi `_ensure_seeds_loaded()` vô điều kiện. Vì vậy, DB production mới chỉ cần mở danh sách hãng/code set là bị seed mã giả. Đồng thời không có quarantine/migration seed và revision đã có; catalog list/create/send không có rào cản tương ứng để loại payload giả đã lưu.

**Tái hiện 1:** DB tạm rỗng, `SMART_HUB_MOCK_HARDWARE=0`; gọi `catalog.list_code_sets()` trả 5 code set và lưu cả 5 vào DB. Không cần từng chạy mock trước đó.

**Tái hiện 2:** trên cùng DB tạm, startup mock tạo 5 code set; startup tiếp theo với `SMART_HUB_MOCK_HARDWARE=0` vẫn trả cả 5 code set. Đổi tên provenance thành mock không tự chặn đường phát.

**Cần sửa/test:** bỏ side effect seed giả trên GET production; migration có backup, nhận diện đúng seed giả và revision đã copy; chặn runnable/binding tương ứng, giữ nguyên mã tự học hợp lệ và lịch sử. Test GET catalog với DB production rỗng, fixture DB cũ và ca chuyển mock→real. R04, AC-07/24.

### V2-09 — P2: Remote trên dashboard vẫn gửi revision pending thay cho bản verified

**Vị trí:** [devices.py:146](../src/smart_hub/dashboard/routes/devices.py#L146), [app.js:510](../src/smart_hub/dashboard/static/app.js#L510), [storage.py:395](../src/smart_hub/devices/storage.py#L395).

Hàm storage đã ưu tiên verified, nhưng GET device detail vẫn chọn revision mới nhất bất kể trạng thái. UI lấy danh sách này làm nút remote và luôn gửi `code_revision_id` explicit, nên bỏ qua active lookup đã sửa. Ngoài ra, nếu chưa có verified, hàm active vẫn fallback về revision mới nhất bất kể trạng thái.

**Tái hiện 1:** lưu rev1 verified + rev2 pending cùng nút. Storage active trả rev1; GET device detail trả nút rev2; request đúng payload mà UI tạo gửi rev2 và provider giả được gọi một lần. Đây chính là trường hợp cũ R13 qua đường người dùng thực tế.

**Tái hiện 2:** chỉ lưu một revision `is_verified=False`; `get_active_code_revision()` trả revision đó, normal action không chỉ định revision cũng gửi được.

**Cần sửa/test:** normal remote phải lấy binding đã xác nhận xuyên suốt storage→API→UI; thử candidate phải là thao tác setup explicit. Test qua API/UI với rev1 verified + rev2 pending, remote mới, unknown/inaccurate và restart. Test hiện tại `test_code_revision_and_verification` còn khẳng định pending được trả làm active, cần sửa kỳ vọng. R13, AC-06.

### V2-10 — P2: Lỗi phát cue bị nuốt và phiên vẫn tiếp tục thu

**Vị trí:** [recording/service.py:387](../src/smart_hub/recording/service.py#L387).

`subprocess.run(..., check=False)` không kiểm return code; mọi exception bị `pass`. Tái hiện timeout playback bằng mock: `_play_cue_tone()` vẫn trả bình thường.

**Tác động:** không có tiếng tít nhưng hệ thống vẫn lấy take, gây sai thời điểm nói và dữ liệu thu. CLI có kiểm lỗi playback, dashboard hiện khác hành vi.

**Cần sửa/test:** timeout/nonzero phải failed với lỗi rõ, không ghi take giả thành công và giải phóng lock. R05, AC-09/27.

### V2-11 — P2: Đồng bộ metadata thất bại nhưng API vẫn báo completed

**Vị trí:** [recording/service.py:335](../src/smart_hub/recording/service.py#L335).

State đã chuyển `COMPLETED` trước `_sync_child_study()`. Khi sync lỗi, chỉ sửa manifest; `error_message` và state API không đổi.

**Tái hiện:** `_sync_child_study()` raise `OSError`; manifest là `sync_failed`, nhưng `/status` tương ứng trả `state="completed"`, `error=null`.

**Cần sửa/test:** state/API/UI phải phản ánh sync_failed, bảo toàn take và có cách retry an toàn không ghi đè review cũ. AC-23, T24/T29.

### V2-12 — P2: Recording API chưa validate dữ liệu đầu vào trước khi mở phiên

**Vị trí:** [recording.py:15](../src/smart_hub/dashboard/routes/recording.py#L15).

Schema chỉ dùng str/int/float và route chỉ kiểm preset. Đã tái hiện schema chấp nhận speaker/split/label không hợp lệ, speaker_id rỗng, `takes_planned=-5`, `distance_m=-1`. `SessionConfig` không bổ sung validation; lỗi metadata có thể xuất hiện sau khi đã lấy lock/tạo thư mục/thu.

**Cần sửa/test:** enum, chuỗi không rỗng, số hữu hạn và giới hạn hợp lý; validate toàn bộ trước side effect. Test request invalid phải 4xx với 0 capture/0 thư mục/0 lock giữ lại. AC-10.

### V2-13 — P2: Audio lock chưa bảo vệ runtime assistant và evaluation

**Vị trí:** [assistant.py:441](../src/smart_hub/assistant.py#L441), [audio_pump.py:76](../src/smart_hub/audio_pump.py#L76), [evaluator.py:97](../src/smart_hub/wake_lab/evaluator.py#L97).

Tìm toàn bộ source cho thấy chỉ recording service và recorder CLI dùng `audio_lock`; assistant/AudioPump và các lối capture khác vẫn mở mic độc lập. Evaluator chỉ lấy `eval_lock`, không phối hợp với phiên thu đang chạy.

**Tác động:** dashboard có thể cho bắt đầu thu trong lúc assistant đang chiếm mic; indicator lock cũng không phản ánh đúng. Test lock đơn lẻ không chứng minh các entrypoint thật dùng cùng lock.

**Cần sửa/test:** ownership chung ở entrypoint/capture phù hợp; test hai process qua chính đường assistant và recording, cộng chính sách eval/recording. Không đổi gain/engine đang chạy để giải quyết tranh chấp. R18, AC-19/26.

### V2-14 — P2: Review chưa có audit/revision; các writer metadata chưa dùng lock chung

**Vị trí:** [samples.py:134](../src/smart_hub/dashboard/routes/samples.py#L134), [child_study.py:276](../src/smart_hub/child_study.py#L276), [recording/service.py:260](../src/smart_hub/recording/service.py#L260).

`expected_status` là optional và không phát hiện thay đổi cùng trạng thái. `save_label()` thay nguyên bản ghi; chưa có lịch sử old/new hoặc revision bất biến. Chỉ API review lấy dataset lock, còn recording sync/CLI writer không tham gia cùng lock.

**Tác động:** hai reviewer hoặc sync chạy cùng lúc có thể ghi đè quyết định/metadata; không truy được quyết định trước. Atomic rename một file không ngăn lost update giữa các writer.

**Cần sửa/test:** revision/ETag bắt buộc cho update, audit append-only và lock/transaction chung cho toàn bộ writers. Test hai reviewer sửa cùng status và sync đồng thời review; fault injection giữa sessions/labels. AC-12/23/26.

### V2-15 — P2: Recording job chưa có recovery/lease và advance chưa gắn phiên/lượt

**Vị trí:** [recording.py:11](../src/smart_hub/dashboard/routes/recording.py#L11), [recording.py:77](../src/smart_hub/dashboard/routes/recording.py#L77), [service.py:190](../src/smart_hub/recording/service.py#L190).

Job nằm trong singleton RAM; constructor/startup không phục hồi manifest phiên dở dang. Waiting_user không có lease/deadline. API advance không nhận `session_id`/`take_sequence`, nên request cũ đến muộn khi take sau đang waiting có thể bắt đầu nhầm lượt.

**Cần sửa/test:** persistent session identity/state, restore sau refresh, restart→interrupted, lease hữu hạn và advance idempotent theo phiên/lượt. Test stale advance, refresh, disconnect và kill/restart. R19, AC-18/26.

### V2-16 — P2: Origin mới kiểm hostname; CSRF vẫn là token toàn process

**Vị trí:** [security.py:68](../src/smart_hub/dashboard/security.py#L68), [security.py:9](../src/smart_hub/dashboard/security.py#L9).

Đã chặn `127.evil.test`. Tuy nhiên code bỏ scheme/port của Origin rồi chỉ kiểm hostname; token không gắn phiên local có issuance/expiry/rotation như kế hoạch.

**Tái hiện:** request tới `http://127.0.0.1:8765`, Origin `https://127.0.0.1:9999`, kèm token hợp lệ vẫn tới handler và trả 200. Đây là bằng chứng thiếu kiểm origin chính xác; chưa chứng minh một trang bên ngoài lấy được token hoặc khai thác end-to-end.

**Cần sửa/test:** allowlist origin đầy đủ scheme/host/port, local session/init-token flow theo kế hoạch; test origin sai port/scheme, thiếu/sai/hết hạn token. R10, AC-17.

### V2-17 — P2: Benchmark chưa chặn đủ overlap và thiếu snapshot cấu hình

**Vị trí:** [evaluator.py:124](../src/smart_hub/wake_lab/evaluator.py#L124).

Overlap chỉ được kiểm khi `split=="test"` và chỉ theo sample ID. Enrollment dev rồi official evaluation cùng dev không bị chặn; copy cùng WAV sang ID khác cũng không bị phát hiện. Snapshot hash chỉ gồm sample ID + source SHA, không gồm label/review revision/speaker/session/config/artifact.

**Tác động:** chưa thể dùng báo cáo này làm bằng chứng đánh giá độc lập hoặc tái lập đầy đủ theo plan. Sửa nhãn trên cùng file vẫn có thể tạo cùng dataset snapshot hash.

**Cần sửa/test:** phân biệt evaluation độc lập/exploratory, kiểm reference overlap theo ID/hash/session trên mọi split thích hợp, pin đầy đủ dataset/candidate snapshot. T26/T27/T34, AC-25.

### V2-18 — P2: Mẫu mock không có provenance để chặn official benchmark

**Vị trí:** [recording.py:27](../src/smart_hub/dashboard/routes/recording.py#L27), [service.py:164](../src/smart_hub/recording/service.py#L164), [child_study.py:419](../src/smart_hub/child_study.py#L419).

Client có thể yêu cầu `mock=true`; service tạo PCM tổng hợp nhưng manifest/labels không lưu dấu nguồn mock. Official filter cũng không kiểm provenance/QC schema. Nếu được manual accepted, mẫu tổng hợp có thể đi vào official dataset như mẫu thu thật.

**Cần sửa/test:** đánh dấu nguồn xuyên suốt recording→review→enrollment→evaluation, tách dữ liệu mock và chặn ở official filter. T28, AC-22.

### V2-19 — P2: Thiếu SDK để lại command ở dispatching dù chưa gọi provider

**Vị trí:** [devices.py:208](../src/smart_hub/dashboard/routes/devices.py#L208).

Code ghi ledger và chuyển `DISPATCHING` rồi mới gọi `get_provider()`, còn lời gọi này nằm ngoài khối try dispatch. Khi thiếu SDK, HTTP 503 được ném ra nhưng ledger không được hoàn tất failed/preflight-rejected; request ID đó có thể trả lại trạng thái dispatching ở lần sau.

**Cần sửa/test:** dependency/preflight trước claim dispatch, hoặc ghi trạng thái failed đúng nguyên nhân trước I/O; test 503 + ledger + 0 send, không chỉ test trực tiếp `get_provider()`. R20/1C, AC-01.

## Đối chiếu các mục R01–R20

| Mục | Kết quả vòng 2 |
| --- | --- |
| R01 | Có adapter gửi một lần; test hiện tại mock `send_data` timeout chưa chứng minh số UDP packet của SDK/transport. Chưa đóng T01/AC-03. |
| R02 | Đã disable nút trong lúc gửi; còn V2-07, thiếu browser concurrency test. |
| R03 | Có revalidate MAC/devtype trước send/learn. Test hiện có qua; vẫn cần đủ cả hai trường và hai đường send/learn theo T03. |
| R04 | Startup đã kiểm mock, nhưng GET catalog vẫn seed giả vô điều kiện; migration/quarantine chưa có — V2-08. |
| R05 | Đã mở capture trước cue, nhưng lỗi drain mới làm treo; cue error còn bị nuốt — V2-01/10. |
| R06 | Đã bổ sung `loadOverview`; JS syntax qua. Chưa có browser smoke để đóng toàn bộ tiêu chí UI. |
| R07 | `load_labels(root=...)` đã hỗ trợ; unit/API tests qua. Benchmark đúng engine còn bị chặn bởi V2-03. |
| R08 | Alias được tách nhưng engine/artifact/threshold chưa có hiệu lực — V2-03. |
| R09 | Đã chặn hash mismatch/header sai; còn missing hash, clipping, đổi nhãn tự động và audit — V2-04/05/14. |
| R10 | Đã chặn hostname giả `127.evil.test`; còn V2-16. |
| R11 | Nhiều trường catalog đã escape; chưa có browser XSS regression. Vẫn có `innerHTML` không escape tại app.js:300/310/321/330/335 cần rà luồng dữ liệu. Không coi riêng các sink đó là exploit đã xác nhận. |
| R12 | Có form/route tạo remote không chọn code set và hãng tự nhập; cần demo học/thử/xác nhận hoàn chỉnh. |
| R13 | Storage ưu tiên verified nhưng UI bỏ qua và vẫn gửi pending; còn fallback pending khi remote mới — V2-09. |
| R14 | Recovery đã chuyển khỏi constructor sang startup; regression test tương ứng qua. |
| R15 | Có sửa renderer theo metrics; chưa chạy browser fixtures đầy đủ null/empty/error. |
| R16 | Artifact mới là dữ liệu tạo từ ID — V2-02. |
| R17 | Có truyền audio interpreter và worker; preflight mới import module, chưa đủ kiểm native dependencies/model/compatibility và cancel/crash theo T18. |
| R18 | Có lock recorder CLI; assistant/eval/metadata writers còn thiếu — V2-13/14. |
| R19 | Recovery/lease/advance identity chưa hoàn tất — V2-15. |
| R20 | Không fallback âm thầm sang mock khi SDK thiếu; trạng thái ledger còn sai — V2-19. |

Ngoài ra, AC-28 chưa hoàn tất: `BaseDeviceProvider` vẫn bắt mọi implementation có `enter_learning` và `send_code`; chưa có capability/action/state contract trung lập để thêm provider không dùng IR. Đây là phần còn thiếu so với plan, không phải yêu cầu triển khai Tuya ngay.

CLI recorder vẫn là implementation riêng và gọi `input()` khi capture đang mở (`scripts/record_wake_samples.py:285`), chưa drain liên tục lúc waiting_user. Cần bổ sung test chờ lâu trước advance và parity CLI/service theo T05/AC-27; lượt review này chưa đo bằng mic thật để kết luận mức lỗi âm thanh của trường hợp đó.

## Kết quả validation

| Kiểm tra | Kết quả |
| --- | --- |
| `python3 -m unittest tests.test_devices tests.test_locks tests.test_recording_service tests.test_wake_lab` | 28 tests PASS |
| `.venv-dashboard/bin/python -m unittest -v tests.test_dashboard_api` | 12 tests PASS trên bản sao cô lập |
| `.venv/bin/python scripts/run_tests.py --mock` | 111 tests PASS trên bản sao cô lập |
| `.venv/bin/python scripts/run_tests.py --runtime` | 67 tests PASS, gồm fixture/native model; không mic thật |
| `.venv/bin/python scripts/run_tests.py --stt` | 31 tests PASS với model local; không mic thật |
| `python3 -m compileall -q src/smart_hub` | PASS |
| `node --check src/smart_hub/dashboard/static/app.js` | PASS |
| `git diff --check` | FAIL nhỏ: trailing whitespace ở app.js:576 |
| Tái hiện bổ sung | Xác nhận drain hang; enrollment không cần WAV; DTW gọi STT; accepted thiếu hash/clipped; dấu câu đổi nhãn; cross-device revision; idempotency conflict; GET catalog tự seed khi mock tắt; seed mock tồn tại sau real startup; origin sai scheme/port; cue error bị nuốt; sync_failed báo completed; input schema thiếu ràng buộc |

Không cộng các số test thành số case độc lập: mock/runtime/STT có phần trùng nhau. Test có sẵn PASS không đóng các lỗi tái hiện bổ sung.

Một số suite async/TestClient treo trong sandbox, nên đã chạy lại ngoài sandbox với bản sao ở `/tmp` và fixture tạm. Lượt mock đầu trên bản sao có một failure do symlink model khiến đường dẫn resolved khác ROOT; đã sửa cách bố trí bản sao, không sửa source/model gốc, rồi chạy lại đủ 111 test PASS.

Chưa thực hiện browser smoke/XSS/concurrency, số UDP packet qua transport thật với fake socket, restart/fault injection đầy đủ, migration dữ liệu cũ, hoặc hardware acceptance RM4 mini/mic thật. Kết quả tự động hiện tại không chứng minh độ chính xác giọng bé/người lớn trong phòng thật.

## Thứ tự sửa đề nghị cho agent

1. V2-01: khôi phục đường thu dashboard có thể hoàn tất; thêm regression raw pipe trước.
2. V2-02/03/04/05: bảo đảm artifact, benchmark và dữ liệu manual review phản ánh đúng âm thanh/nhãn.
3. V2-06/07/08/09/19: chốt binding, ledger, quarantine catalog và trạng thái dispatch trước khi thử RM4 mini thật.
4. Hoàn tất V2-10–18 và các acceptance/test chưa đóng trong bảng R01–R20.
5. Chạy lại regression rồi mới làm browser/hardware acceptance có ghi nhận ACK và phản ứng thiết bị riêng biệt.

Không đóng issue chỉ bằng việc bổ sung trường metadata hoặc assert rằng file tồn tại. Mỗi mục cần test hành vi bên ngoài hoặc kiểm artifact/inference tương ứng; giữ nguyên model/cấu hình/bản thu đang dùng làm baseline.
