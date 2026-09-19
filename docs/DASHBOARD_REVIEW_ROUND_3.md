# Dashboard Review Round 3 — 2026-09-19

**Kết luận: NEEDS_FIX — chưa nên merge codex/local-dashboard-broadlink vào nhánh chính.**

Tài liệu này là prompt/task cho vòng sửa tiếp theo sau khi review commit:

~~~text
Branch: codex/local-dashboard-broadlink
HEAD đã review: 89d38c592698fb81a80ff148b37a9b2f277198d5
Parent: 522401d3364e22daa9e1b82d84e3081c92a2b8e0
Commit message của HEAD đã review:
fix(dashboard): resolve review round 2 findings V2-01 to V2-19 and AC-28
~~~

Round 2 đã sửa được nhiều vấn đề quan trọng, đặc biệt: AlsaCapture.drain(), technical QC khi review sample, tách transcript khỏi ground truth, binding revision/appliance/button, idempotency digest cơ bản, verified remote revisions, cue playback failure, sync failure state, input validation, dataset lock/revision/audit, cross-split checks, mock provenance và provider preflight.

**Không làm lại toàn bộ Round 2.** Chỉ sửa các finding dưới đây và thêm regression tests để khóa behavior.

Các invariant bổ sung cần được khóa bằng regression tests trong R3:

- request advance phải có đủ `session_id` và `take_sequence`, không chấp nhận request thiếu binding;
- idempotency claim phải atomic, cùng `request_id` không được gửi provider quá một lần;
- upsert CodeSet/CodeRevision phải giữ `code_set_id` và các cờ quarantine sau reload;
- provider không xác định hoặc không tương thích phải fail-closed trước hardware I/O, kể cả khi AC-28 vẫn ở trạng thái partial.

Không amend/rewrite commit 89d38c5. Tạo **một follow-up commit mới** trên chính branch codex/local-dashboard-broadlink.

---

## Mức độ ưu tiên

- **P1:** phải sửa trước khi merge/use luồng bị ảnh hưởng.
- **P2:** nên sửa trước khi đóng acceptance của dashboard.
- **P3:** cleanup/architecture, không được tuyên bố hoàn tất capability khi implementation chưa end-to-end.

---

# V3-01 — P1: Manual advance UI và backend lệch take_sequence

## Hiện trạng

UI trong:

~~~text
src/smart_hub/dashboard/static/app.js
~~~

khi bấm nút advance đang gửi:

~~~js
payload.take_sequence = currentRecordingStatus.current_take;
~~~

Trong khi backend ở:

~~~text
src/smart_hub/recording/service.py
~~~

đang kiểm:

~~~python
if take_sequence is not None and (self.current_take + 1) != take_sequence:
    raise RuntimeError(...)
~~~

Khi session đang WAITING_USER ở take 1:

~~~text
UI gửi take_sequence = 1
backend kỳ vọng 2
=> HTTP 409
~~~

Mặc định dashboard dùng manual_advance=true, nên lỗi này chặn luồng thu thực tế ngay take đầu.

## Yêu cầu sửa

Dùng **một semantics duy nhất** xuyên suốt UI/API/service:

~~~text
current_take = take đang chờ được bắt đầu
take_sequence request = chính current_take đó
~~~

Ví dụ:

~~~text
WAITING_USER take 1 -> advance(session_id=S, take_sequence=1) -> OK
stale duplicate take_sequence=1 sau khi đã sang take 2 -> 409
WAITING_USER take 2 -> take_sequence=2 -> OK
wrong session_id -> 409
~~~

Không dùng current_take + 1 nếu current_take đã đại diện cho lượt đang waiting.

Request từ dashboard phải gửi đủ hai binding:

~~~text
session_id: bắt buộc, chuỗi không rỗng, đúng phiên đang active
take_sequence: bắt buộc, số nguyên dương, đúng current_take đang WAITING_USER
~~~

POST /api/recording/advance không chấp nhận body trống, thiếu trường, null hoặc binding sai kiểu/giá trị. Các request này phải trả 422 trước khi set event hoặc tạo side effect; không ép chuỗi, float hoặc boolean thành take_sequence. Binding đúng schema nhưng sai phiên, stale/future take hoặc sai trạng thái trả 409.

Nếu CLI/internal caller còn cần advance() không tham số, giữ compatibility ở lớp nội bộ; endpoint dashboard không được bỏ qua binding. UI chỉ cho advance khi đã có status WAITING_USER cùng session_id/current_take hợp lệ.

## Regression tests bắt buộc

Thêm test qua cả service và API contract:

1. Start mock recording, manual advance, take 1.
2. Poll/status trả current_take == 1.
3. POST /api/recording/advance với session_id hiện tại và take_sequence=1 phải thành công.
4. Gửi lại stale take_sequence=1 khi service đã sang waiting take 2 phải fail 409.
5. take_sequence=2 ở take 2 phải thành công.
6. Wrong session_id phải fail 409.
7. Body trống, thiếu/null session_id hoặc take_sequence, session_id rỗng/chỉ có khoảng trắng -> 422.
8. take_sequence bằng 0, âm, chuỗi, float hoặc boolean -> 422.
9. Request bị từ chối không set advance event, không bắt đầu capture/cue và không đổi take.
10. Binding đúng schema nhưng future take hoặc service không ở WAITING_USER -> 409, không side effect.

Test phải đi qua cùng semantics mà app.js sử dụng, không chỉ gọi service.advance() không tham số.

---

# V3-02 — P1: DTW evaluation đang fail-open khi WAV thiếu/hỏng/integrity lỗi

## Hiện trạng

Trong:

~~~text
src/smart_hub/wake_lab/worker.py
~~~

nhánh DTW hiện làm kiểu:

~~~python
score = 0.0
if wav_path.is_file():
    try:
        ...
    except Exception:
        score = 0.0
~~~

Điều này biến lỗi xử lý thành kết quả model.

Ví dụ:

~~~text
negative sample + missing WAV
=> score=0
=> detected=False
=> TN
~~~

Benchmark có thể đẹp giả dù sample thực tế không được xử lý.

Với positive sample, missing/corrupt WAV bị biến thành FN thay vì ERROR.

Ngoài ra DTW path chưa verify source_sha256 trước inference như child-study evaluator.

## Yêu cầu sửa

DTW phải có cùng nguyên tắc fail-closed với official child-study benchmark.

### Selection

Sample accepted/confirmed đã được chọn vào benchmark thì **vẫn ở denominator**.

### Integrity/evaluation

Các lỗi sau phải là ERROR, không được biến thành TP/FP/TN/FN:

- file không tồn tại;
- thiếu source_sha256 trong official mode;
- SHA mismatch;
- WAV unreadable/corrupt;
- wrong channels/sample width/sample rate/compression;
- truncated/empty PCM;
- feature extraction exception/non-finite/invalid shape.

Backend/feature inference không được chạy tiếp khi checksum hoặc WAV contract fail.

## Schema đề xuất cho từng DTW sample

Ví dụ:

~~~json
{
  "status": "ERROR",
  "is_success": false,
  "error": "SHA256 mismatch ...",
  "score": null,
  "detected": null,
  "expected": true,
  "outcome": "ERROR"
}
~~~

### Metrics và denominator bắt buộc

ERROR được giữ trong eligible của đúng lớp positive/negative, nhưng không được cộng vào TP/FP/TN/FN. Các invariant:

~~~text
processed_total = tp + fp + tn + fn
errors = positive.errors + negative.errors
eligible_total = processed_total + errors
positive.processed = tp + fn
negative.processed = tn + fp
positive.eligible = positive.processed + positive.errors
negative.eligible = negative.processed + negative.errors
eligible_total = positive.eligible + negative.eligible
~~~

Lưu các số trên trong JSON; có thể thêm tp/fn hoặc tn/fp vào object của từng lớp. processed nghĩa là xử lý thành công, không đồng nghĩa dự đoán đúng.

Chốt công thức DTW dùng cho report chính:

~~~text
accuracy = (tp + tn) / eligible_total
recall = tp / positive.eligible
far = fp / negative.eligible
precision = tp / (tp + fp)
coverage = processed_total / eligible_total
error_rate = errors / eligible_total
~~~

accuracy/recall/FAR ở đây là tỷ lệ trên tập eligible, bao gồm sample ERROR trong denominator. precision chỉ tính trên các dự đoán positive đã xử lý được. F1 là trung bình điều hòa của precision và recall vừa định nghĩa; nếu cả hai bằng 0 thì F1 = 0. Ghi rõ semantics này trong report. Nếu cần tỷ lệ chỉ trên mẫu processed để chẩn đoán, dùng tên riêng, không thay thế metrics chính.

Denominator bằng 0 -> lưu null, render N/A. F1 cũng là null khi precision hoặc recall không xác định. Không silently bỏ ERROR hoặc render tỷ lệ 0 giả.

Ví dụ: 10 eligible, 9 processed, 1 ERROR -> eligible_total = 10, processed_total = 9, errors = 1, coverage = 90%, error_rate = 10%. Có ERROR thì FAR thấp không đủ chứng minh acceptance đạt.

### Trạng thái cuối và lưu report

- eval_data.status = completed nếu không có processing error; completed_with_errors nếu bất kỳ candidate nào có ERROR.
- eval_data.has_processing_errors phản ánh cùng điều kiện; giữ số lỗi riêng của từng candidate.
- Official run có ERROR không đủ điều kiện acceptance, dù các tỷ lệ khác đạt ngưỡng.
- Worker xử lý xong và tạo được kết quả vẫn trả envelope status=ok, exit 0, kèm eval_data có trạng thái trên. Parent phải lưu JSON/Markdown rồi mới trả kết quả. Không dùng exit khác 0 cho sample ERROR khiến evaluator bỏ mất report.
- API POST /api/wake/evaluations trả HTTP 200 khi kết quả đã được lưu, nhưng phải có status và has_processing_errors tương ứng ở response cùng evaluation ID. UI phải hiển thị processing error khi completed_with_errors.
- GET lại evaluation sau reload phải giữ trạng thái, số lỗi và report đó. Lỗi worker không tạo được kết quả hoặc lỗi lưu report vẫn là lỗi thực thi, không trả completed.
- CLI official benchmark giữ policy lưu report trước rồi exit khác 0 khi có processing error; không đồng nhất exit code của worker nội bộ với CLI cuối cùng.

## Regression tests bắt buộc

- positive missing WAV -> ERROR, không FN.
- negative missing WAV -> ERROR, không TN.
- positive SHA mismatch -> ERROR.
- negative SHA mismatch -> ERROR.
- malformed WAV -> ERROR.
- backend/features không được gọi sau SHA mismatch.
- 10 sample, 9 valid + 1 ERROR -> denominator vẫn 10.
- Test riêng ERROR ở lớp positive và negative; đối chiếu eligible/processed/errors và công thức metrics của từng lớp.
- Dataset không có positive, không có negative hoặc không có positive prediction -> metric thiếu denominator là null/N/A.
- official evaluation có ERROR -> lưu report/result, trả completed_with_errors + has_processing_errors=true; đọc lại sau reload vẫn đầy đủ.
- Kiểm tra cả in-process và subprocess worker để sample ERROR không làm parent bỏ qua bước lưu report.
- Run không có ERROR -> completed + has_processing_errors=false.

Không bắt lỗi bằng except Exception: score=0.

---

# V3-03 — P1: Markdown report không tương thích metrics schema của DTW

## Hiện trạng

run_candidate_evaluation() cho DTW trả metric dạng:

~~~text
tp
fp
tn
fn
precision
recall
f1
accuracy
threshold
~~~

Nhưng src/smart_hub/wake_lab/evaluator.py vẫn gọi child_study.format_evaluation_markdown(eval_data).

Formatter này đọc schema STT:

~~~text
positive.accurate
positive.eligible
negative.false_alarm
performance.decode_rtf
groups
~~~

Kết quả DTW JSON có thể đúng nhưng Markdown lại hiển thị 0/0, 0%, RTF 0.000 hoặc số không có ý nghĩa.

## Yêu cầu sửa

Không ép các engine khác schema vào formatter STT cũ.

Chọn một trong hai hướng:

### Hướng A — unified normalized metrics

Normalize mọi engine sang contract chung đủ để formatter render đúng:

~~~text
eligible_total
processed_total
errors
positive.{eligible,processed,accurate,missed,errors,...}
negative.{eligible,processed,correct_reject,false_alarm,errors,...}
coverage
error_rate
performance (optional / N/A)
~~~

DTW-specific values như score/threshold/F1 có thể thêm riêng.

### Hướng B — engine-aware report

Tạo report generator của Wake Lab biết từng engine:

- STT: accuracy/FRR/FAR/duplicate/RTF.
- DTW: TP/FP/TN/FN, precision, recall, F1, accuracy, FAR, threshold, eligible/processed/ERROR, coverage và error_rate theo V3-02.
- Unsupported metric phải ghi N/A, không ghi 0 giả.

Ưu tiên code rõ ràng, không dùng string replacement sau khi formatter chạy.

## Regression tests bắt buộc

Tạo fixture DTW với số dễ kiểm:

~~~text
eligible=10
processed=9
errors=1
positive.eligible=6
positive.processed=5
positive.errors=1
negative.eligible=4
negative.processed=4
negative.errors=0
tp=4
fn=1
tn=3
fp=1
~~~

Markdown phải chứa đúng các số đó hoặc normalized equivalent, cùng status completed_with_errors. Các tỷ lệ sau làm tròn một chữ số thập phân:

~~~text
accuracy=70.0%
precision=80.0%
recall=66.7%
F1=72.7%
FAR=25.0%
coverage=90.0%
error_rate=10.0%
~~~

Thêm fixture chuyển ERROR sang lớp negative: positive.eligible=5, negative.eligible=5, positive.errors=0, negative.errors=1; giữ TP/FP/TN/FN. Khi đó recall=80.0%, FAR=20.0%, F1=80.0%; accuracy và coverage không đổi.

Không được có fake STT 0/0 hoặc RTF 0.000 nếu metric đó không áp dụng.

Thêm test evaluation có cả STT + DTW trong cùng report và cả hai phải hiển thị đúng schema.

---

# V3-04 — P1: Quarantine mock seed và liên kết dữ liệu chưa được bảo toàn end-to-end

## Hiện trạng

Migration đã thêm:

~~~text
code_sets.is_quarantined
code_revisions.is_mock_seed
~~~

nhưng model/storage hiện chưa mang các field này xuyên suốt:

- CodeSet dataclass không có is_quarantined.
- CodeRevision không có is_mock_seed.
- _row_to_code_set() bỏ is_quarantined.
- _row_to_code_revision() bỏ is_mock_seed.
- save_code_set() không persist is_quarantined.
- save_code_revision() không persist is_mock_seed.

Do đó getattr(rev, "is_mock_seed", False) không phản ánh DB flag sau khi reload.

Built-in seed thường còn bị chặn nhờ ID *_seed, nhưng imported/mock catalog được quarantine dựa trên provenance mà ID không có suffix có thể lọt qua.

Ngoài ra INSERT OR REPLACE có thể tạo lại row với default is_quarantined=0 nếu flag không được persist.

Đã tái hiện thêm: save_code_set() trên cùng ID làm code_revisions.code_set_id thành NULL do thao tác REPLACE kích hoạt ON DELETE SET NULL. appliances.code_set_id cũng có cùng kiểu khóa ngoại; REPLACE revision có thể xóa observations qua ON DELETE CASCADE. Chỉ thêm cột flag vào INSERT không giải quyết việc mất liên kết/lịch sử này.

## Yêu cầu sửa

Quarantine phải là thuộc tính dữ liệu thật, không dựa chủ yếu vào naming convention.

### CodeSet

Persist/load:

~~~text
is_quarantined: bool
~~~

### CodeRevision

Persist/load:

~~~text
is_mock_seed: bool
~~~

Khi copy revision từ quarantined/mock CodeSet sang appliance, revision phải kế thừa marker mock/quarantine phù hợp.

Real hardware dispatch phải chặn nếu:

~~~text
revision.is_mock_seed == true
OR source code set is quarantined
~~~

Suffix _seed chỉ có thể là defense-in-depth, không phải source of truth.

### Save/upsert phải bảo toàn liên kết và lịch sử

- Dùng UPDATE hoặc INSERT ... ON CONFLICT DO UPDATE phù hợp; không xóa rồi tạo lại row để cập nhật cùng ID.
- Save/update CodeSet phải giữ code_set_id của appliance và revision con.
- Save/update CodeRevision phải giữ appliance_id/code_set_id đúng binding và các observation liên quan.
- Cờ đã được quarantine không tự hạ từ true về false khi re-import hoặc save object cũ có giá trị mặc định false. Nếu cần bỏ cách ly, phải có quy trình tường minh riêng; không làm việc đó ngầm trong R3.
- Giữ payload, hash, verification và lịch sử ngoài các thay đổi cần thiết cho finding.

## Regression tests bắt buộc

1. Save mock CodeSet không có suffix _seed.
2. Run quarantine.
3. Reload storage/process.
4. get_code_set() vẫn biết is_quarantined=True.
5. Revision copied từ nó vẫn is_mock_seed=True.
6. Real hardware dispatch chặn trước provider send.
7. Save/update lại CodeSet/Revision không reset quarantine flag.
8. Normal legitimate learned revision vẫn chạy.
9. Existing DB migration giữ dữ liệu và flags đúng.
10. Tạo CodeSet có appliance/revision con, save lại cùng ID -> cả hai code_set_id giữ nguyên.
11. Revision đã có observation, save lại cùng ID -> observation và binding còn nguyên.
12. Save/re-import object được đọc trước khi quarantine -> không xóa cờ vừa được bật trong DB.

Không chỉ test list_code_sets(include_quarantined=False) ngay sau UPDATE trong cùng process.

---

# V3-05 — P2: Lease timeout bị Finalize ghi đè thành COMPLETED

## Hiện trạng

Trong manual advance lease timeout:

~~~python
self.state = RecordingState.INTERRUPTED
self.manifest["status"] = "interrupted"
break
~~~

Sau khi thoát loop, Finalize hiện làm:

~~~python
if self._stop_event.is_set():
    interrupted
else:
    captured_pending_review
    COMPLETED
~~~

Lease timeout không set _stop_event, nên trạng thái interrupted có thể bị đổi lại thành COMPLETED.

## Yêu cầu sửa

Finalize phải dựa trên state/outcome thật, không chỉ _stop_event.

Lease timeout phải giữ:

~~~text
state = interrupted
manifest.status = interrupted
error_message = lease timeout...
~~~

Không sync như một session completed bình thường nếu policy không cho phép.

Nếu muốn sync partial takes để bảo toàn dữ liệu, session record phải ghi trạng thái interrupted/partial rõ ràng.

## Regression tests bắt buộc

Không chờ thật 120 giây. Refactor lease timeout injectable/constant hoặc patch monotonic/wait để test nhanh.

Test:

~~~text
manual_advance=True
không advance
lease timeout
=> final state INTERRUPTED
=> manifest interrupted
=> không bị overwritten COMPLETED
=> audio lock released
~~~

Nếu có partial take trước timeout, take phải được bảo toàn nhưng session không được coi completed.

---

# V3-06 — P2: Origin validation chưa exact host

## Hiện trạng

V2-16 đã kiểm scheme và port, nhưng hostname chỉ được kiểm là thuộc allowed hosts.

Ví dụ request:

~~~text
Host: localhost:8765
Origin: http://127.0.0.1:8765
~~~

cả hai đều allowed, scheme và port giống nhau, nên request vẫn qua dù origin host khác Host header.

Yêu cầu trước đó diễn đạt bằng allowlist scheme/host/port; allowlist không tự đồng nghĩa với Origin trùng Host. R3 chốt policy **strict same-origin** cho mutation có Origin: so khớp chính xác scheme/hostname/effective port của request, đồng thời vẫn kiểm allowed hosts.

## Yêu cầu sửa

Đối với mutation request có Origin:

~~~text
origin.scheme == request scheme
origin.hostname == request host hostname
origin.effective_port == request effective port
~~~

Chuẩn hóa hợp lý:

- lowercase hostname;
- IPv6 bracket;
- default ports 80/443;
- không coi localhost và 127.0.0.1 là cùng exact origin.

Giữ CSRF validation hiện tại.

## Regression tests bắt buộc

- Host localhost + Origin 127.0.0.1 cùng port -> 403.
- Host 127.0.0.1 + Origin localhost -> 403.
- same host/scheme/port -> pass security layer.
- wrong scheme -> 403.
- wrong port -> 403.
- IPv6 exact loopback form được normalize đúng.
- Host không ghi port và Origin ghi port mặc định 80/443 tương ứng -> pass; Origin dùng port khác -> 403.

Không nới allowed host chỉ để test xanh.

Test cũ đang dùng Host=testserver nhưng Origin=127.0.0.1 và kỳ vọng được chấp nhận phải đổi fixture thành cùng origin cho ca hợp lệ. Giữ/thêm ca cross-host bị chặn; đây là cập nhật test theo policy R3, không nới assertion để giữ behavior cũ.

---

# V3-07 — P1: Race idempotency gửi lặp và legacy ledger NULL digest trả kết quả sai binding

## Hiện trạng

Migration thêm cột command_ledger.payload_digest nhưng row cũ có thể NULL.

Logic hiện tại chỉ conflict khi:

~~~python
existing.payload_digest and existing.payload_digest != current_digest
~~~

Nếu digest cũ NULL, cùng request_id có thể được request mới reuse và route trả ledger result cũ mà không chứng minh cùng appliance/gateway/button/revision.

Ngoài legacy row, đã tái hiện hai request đồng thời cùng request_id và cùng payload gọi provider.send_code() hai lần. Cả hai qua lần lookup đầu khi chưa có ledger; prepare_command() có thể trả existing entry cho request thứ hai, nhưng route bỏ qua giá trị trả về rồi tiếp tục DISPATCHING/send. Gateway lock chỉ tuần tự hóa hai lần gửi, không bảo đảm gửi một lần.

## Yêu cầu sửa

Fail-closed cho legacy row.

Nếu existing row có payload_digest is NULL, phải ít nhất so toàn bộ binding lưu trong ledger:

~~~text
gateway_id
appliance_id
button_key
code_revision_id
~~~

Nếu bất kỳ field khác request hiện tại -> 409 Conflict.

Nếu tất cả binding giống nhau:

- có thể trả existing result;
- hoặc migrate/backfill digest một cách an toàn nếu có đủ dữ liệu;
- không được đoán digest cho payload hiện tại khi không chứng minh code revision/payload history.

Ưu tiên behavior an toàn hơn backward compatibility.

### Atomic claim và quyền dispatch

Áp dụng cho cả row mới và legacy:

- Claim request_id phải atomic ở storage; kết quả phải phân biệt rõ claimed_new với existing entry.
- Chỉ request claim thành công mới được chuyển DISPATCHING và gọi provider. Request nhận existing entry chỉ trả trạng thái/kết quả ledger, kể cả khi entry còn PREPARED/DISPATCHING hoặc đã UNKNOWN.
- Cùng request_id + cùng binding/digest -> tối đa một provider call trong toàn bộ các request đồng thời/retry. Request đến sau có thể thấy trạng thái đang xử lý, không cần chờ hardware để giả định đã DELIVERED.
- Cùng request_id + binding/digest khác -> 409, không provider call bổ sung.
- So sánh binding/digest phải áp dụng ở cả lookup ban đầu và nhánh tranh chấp INSERT/unique constraint. Legacy NULL digest luôn theo quy tắc đối chiếu binding bên trên.
- Route phải chuyển lỗi conflict do storage trả về thành 409; không để ValueError hoặc ngoại lệ conflict tương đương rơi thành 500.
- Không tự replay row tồn tại để xử lý việc worker chết giữa claim và dispatch; giữ recovery hiện có.

## Regression tests bắt buộc

Fixture DB legacy với payload_digest=NULL:

1. same request_id + same binding -> deterministic documented behavior.
2. same request_id + different appliance -> 409.
3. different gateway -> 409.
4. different button -> 409.
5. different revision -> 409.
6. provider không được gọi trong conflict cases.

Race test là bắt buộc:

7. Dùng Event/barrier để hai request cùng request_id, cùng digest đều thấy lookup ban đầu chưa có row -> chỉ một claim mới, một ledger row và provider call count = 1.
8. Hai request hợp lệ cùng request_id nhưng khác binding/digest -> request thắng claim được xử lý, request còn lại 409; provider call count = 1.
9. Existing entry PREPARED/DISPATCHING/DELIVERED/UNKNOWN -> retry không tạo provider call mới.
10. Conflict từ storage trong nhánh race -> API trả 409, không 500.

Chạy qua route và storage thật với DB tạm, provider giả lập; không mock claim thành công cho mọi request rồi kết luận đã chống race. Không dùng sleep để hy vọng tạo được interleaving.

---

# V3-08 — P2 routing guard / P3 AC-28: Provider capability interface chưa phải provider-neutral end-to-end

## Hiện trạng

BaseDeviceProvider đã có:

~~~text
ProviderCapability
capabilities
has_capability()
execute_action()
~~~

Đây là foundation tốt.

Tuy nhiên route hiện vẫn chọn:

~~~text
SMART_HUB_MOCK_HARDWARE=1 -> MockDeviceProvider
else -> BroadlinkProvider
~~~

và chưa dispatch theo gateway.provider.

AC-28 trong [kế hoạch gốc](AGENT_FIX_DASHBOARD_REVIEW.md) yêu cầu core có capability/action/state contract không bắt mọi provider dùng IR. T35 yêu cầu generic fake provider có observed state, không MAC/raw IR. Vì vậy factory chỉ chọn được Broadlink/Mock chưa đủ bằng chứng AC-28 completed.

## Yêu cầu

Không cần tích hợp Tuya/MQTT thật trong R3 nếu ngoài scope.

### Routing guard bắt buộc cho cả hai option

- Provider không được hỗ trợ/không xác định -> lỗi rõ trước hardware I/O; không silently chọn Broadlink.
- Trong real mode khi chỉ hỗ trợ Broadlink, gateway.provider khác broadlink phải bị từ chối. Gateway provider=mock chỉ được dùng trong explicit mock mode; mock mode không tự hợp thức hóa provider không xác định.
- Action send/learn phải được kiểm capability trước dispatch/học mã; thiếu capability -> lỗi rõ và 0 I/O. Nếu check gateway không được hỗ trợ thì trả lỗi trước gọi adapter.
- Guard phải áp dụng cho send, learn và check. Không tạo ledger DISPATCHING hoặc learning job bị treo khi bị từ chối.

Guard này là P2 cần đóng trong R3. Phần mở rộng kiến trúc AC-28 là P3, chọn một trong hai hướng sau:

### Option A — hoàn tất provider routing và chứng minh contract AC-28

Tạo resolver/factory:

~~~python
get_provider_for_gateway(gateway)
~~~

dựa vào gateway.provider, capabilities và explicit mock mode.

Unknown provider -> 422/501/503 rõ ràng, không silently dùng Broadlink.

Route send/learn/check dùng neutral contract/capability check thay vì hardcode Broadlink path.

Thêm generic fake provider không cần MAC/raw IR, thực hiện một action không phải IR và trả observed state qua cùng contract của core. Test phải đi qua luồng xử lý chung, không chỉ instantiate provider hoặc assert factory trả đúng class. Đây là bằng chứng T35 bắt buộc trước khi đánh dấu AC-28 completed.

### Option B — scope AC-28 trung thực

Nếu chưa muốn refactor provider routing trong R3:

- giữ interface hiện tại;
- hoàn tất routing guard bắt buộc ở trên, không cần xây factory tổng quát;
- cập nhật status/docs rằng AC-28 mới là “interface foundation / partial”;
- không đánh dấu completed.

Không giả lập hỗ trợ provider chưa có.

## Tests bắt buộc cho cả hai option

- real mode, gateway provider=broadlink -> Broadlink adapter.
- provider=mock chỉ khi explicit mock được bật; real mode từ chối với 0 hardware calls.
- unknown/unsupported provider -> error rõ ở send/learn/check, 0 hardware calls, không có ledger/job mắc kẹt.
- provider thiếu IR_SEND -> send bị chặn.
- provider thiếu IR_LEARN -> learning bị chặn.

## Tests bổ sung nếu chọn Option A

- route không cần biết concrete provider class.
- generic fake provider không có MAC/raw IR -> core nhận action/result/observed state đúng contract T35.
- Chỉ có test factory Broadlink/Mock pass -> chưa đủ để đánh dấu AC-28 completed.

---

# Không regression các fix Round 2 đã đúng

Giữ nguyên behavior tốt đã có ở 89d38c5:

- AlsaCapture.drain() không dùng read1() trên raw pipe và không treo.
- cue playback timeout/nonzero -> session FAILED.
- sync metadata failure -> state FAILED + manifest sync_failed.
- accepted review bắt buộc SHA gốc + WAV 16k mono 16-bit PCM + clipping/RMS QC.
- transcript edit không tự đổi label/expected_events.
- revision explicit phải đúng appliance + button + payload hash.
- normal remote chỉ dùng verified revision.
- same request_id + different known digest -> 409.
- GET catalog không auto-seed.
- recording inputs invalid fail trước side effects.
- review update dùng dataset lock + revision/ETag/audit.
- official benchmark loại mock provenance.
- enrollment không dùng test split/mock sample cho model thật.
- cross-split leakage checks đã có phải tiếp tục pass.
- provider preflight failure không để ledger stuck dispatching.

Không xóa hoặc làm yếu test Round 2 để suite xanh.

Riêng fixture Origin hợp lệ được cập nhật theo strict same-origin như V3-06; các ca từ chối origin không hợp lệ phải được giữ hoặc siết chặt.

---

# Test strategy

Ưu tiên test deterministic, không đụng hardware thật.

Không:

- mở microphone thật;
- phát loa thật;
- gửi IR thật;
- scan LAN;
- download model/dependency;
- dùng sleep 120 giây thật.

Dùng temp DB/temp WAV/mock provider/mock capture.

Fixture recording phải dùng temp root cho recordings, manifest, labels/sessions và lock. Không để constructor/recovery hoặc singleton khi import quét/ghi recordings thật. Suite recording hiện có còn tạo dữ liệu trong recordings của repo; chuyển các fixture đó sang temp root khi bổ sung test R3.

Mốc kiểm tra trên mã nguồn 89d38c5 trước khi sửa R3: 51 targeted tests (recording 6, API 16, devices 17, wake lab 12) và 111 mock tests đều pass, không skip. Các ca tái hiện R3 vẫn lỗi; mốc 162 test này không chứng minh R3 đã hoàn tất và không thay thế regression tests mới.

## Targeted tests tối thiểu

~~~text
tests/test_recording_service.py
tests/test_dashboard_api.py
tests/test_devices.py
tests/test_wake_lab.py
~~~

Bổ sung test vào file hiện có; không tạo một test framework song song nếu không cần.

---

# Validation bắt buộc

Chạy trước khi commit:

~~~bash
git diff --check
python3 -m compileall -q src scripts tests
~~~

Repo hiện tách hai môi trường: .venv-dashboard có FastAPI/Starlette/httpx nhưng không có numpy; .venv có numpy nhưng không có dashboard dependencies. Dùng đúng interpreter để tránh suite API bị skip hoặc DTW không chạy:

~~~bash
.venv-dashboard/bin/python -m unittest discover -s tests -p 'test_recording_service.py' -v
.venv-dashboard/bin/python -m unittest discover -s tests -p 'test_dashboard_api.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_devices.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_wake_lab.py' -v
.venv/bin/python scripts/run_tests.py --mock
~~~

Nếu dùng môi trường khác, kiểm tra dependencies trước và ghi rõ interpreter thực tế. Test tích hợp API + DTW có thể dùng dashboard interpreter gọi audio worker trong .venv; không mock bỏ toàn bộ chuỗi chỉ vì hai môi trường tách biệt.

Nếu Node có sẵn:

~~~bash
node --check src/smart_hub/dashboard/static/app.js
~~~

Không cài/download gì chỉ để chạy test. Nếu dependency thiếu, ghi rõ SKIP/NOT RUN và lý do.

Ghi nhận SKIP/NOT RUN không đồng nghĩa acceptance tương ứng đã đạt. Test cần chứng minh một finding mà chưa chạy được thì finding đó vẫn thiếu validation; không đánh dấu R3 completed chỉ dựa vào các suite còn lại xanh.

---

# Acceptance checklist Round 3

Trước khi coi R3 hoàn tất, phải chứng minh:

- [ ] Dashboard manual advance take 1 hoạt động với session_id + take_sequence=1.
- [ ] Advance thiếu/rỗng/null/sai kiểu binding -> 422, không side effect; wrong session/stale/future take -> 409.
- [ ] Stale advance không thể kích nhầm take sau.
- [ ] DTW missing/corrupt/SHA-mismatch sample -> ERROR, không TP/FP/TN/FN.
- [ ] DTW ERROR vẫn giữ denominator.
- [ ] DTW accounting và metrics đúng công thức V3-02 cho ERROR ở từng lớp; denominator bằng 0 -> null/N/A.
- [ ] In-process/subprocess đều lưu JSON/Markdown khi có sample ERROR; POST và GET sau reload giữ completed_with_errors cùng has_processing_errors=true.
- [ ] DTW Markdown report hiển thị đúng metrics, không render fake STT 0/0.
- [ ] Mixed STT + DTW report không làm mất metrics của engine nào.
- [ ] Quarantine flags survive save/reload/restart.
- [ ] Save/upsert cùng ID không làm mất code_set_id/binding/observation; stale object/re-import không reset cờ quarantine.
- [ ] Mock/quarantined code không thể dispatch tới real hardware dù ID không có _seed.
- [ ] Lease timeout kết thúc ở INTERRUPTED, không bị finalize thành COMPLETED.
- [ ] Origin host phải exact với Host hostname, ngoài scheme/port.
- [ ] Legacy ledger NULL digest + binding khác -> 409.
- [ ] Hai request đồng thời cùng request_id chỉ có một claim mới và một provider call; khác binding/digest -> 409, không gửi bổ sung.
- [ ] Existing ledger ở các trạng thái đã nêu không bị retry dispatch; conflict từ storage không trở thành 500.
- [ ] Provider capability status được xử lý trung thực: factory end-to-end hoặc AC-28 ghi partial.
- [ ] Cả hai option V3-08 đều chặn unknown/unsupported provider và capability thiếu trước I/O, không có ledger/job mắc kẹt.
- [ ] Nếu AC-28 completed, có test T35 qua core với generic fake provider không MAC/raw IR và có observed state.
- [ ] Không regression V2-01..V2-19 đã sửa đúng.
- [ ] git diff --check sạch.
- [ ] Compileall sạch.
- [ ] Targeted/mock suites và regression tests R3 pass; finding có test SKIP/NOT RUN vẫn ghi rõ thiếu validation.
- [ ] Fixture recording nằm trong temp root, không quét/ghi dữ liệu thu thật khi test.
- [ ] Không có hardware/network side effect trong test.

---

# Scope guard

Không thay đổi ngoài scope nếu không cần cho finding:

- wake word phrase;
- STT model bundle;
- VAD thresholds;
- child wake profile thresholds;
- audio gain;
- Broadlink packet format;
- recordings thật của người dùng;
- held-out test dataset;
- acceptance target chỉ để làm test pass.

Không “sửa” DTW benchmark bằng cách loại sample integrity lỗi khỏi eligible set.

Không coi missing WAV là negative detection.

Không backfill checksum hiện tại rồi gọi đó là checksum gốc.

Không cho mock/quarantine chạy trên hardware thật vì convenience.

---

# Commit yêu cầu

Sau khi fix và validation, tạo **một follow-up commit mới** trên:

~~~text
codex/local-dashboard-broadlink
~~~

Không amend:

~~~text
89d38c592698fb81a80ff148b37a9b2f277198d5
~~~

Commit message đề xuất:

~~~text
fix(dashboard): resolve review round 3 correctness gaps
~~~

---

# Final report agent phải trả

Khi hoàn tất, báo:

1. Commit SHA mới.
2. Danh sách files changed.
3. Cách fix V3-01 đến V3-08.
4. Semantics recording take_sequence, binding bắt buộc và HTTP 422/409.
5. Semantics DTW ERROR/denominator, công thức metrics và coverage/error_rate.
6. Report schema STT/DTW, status/has_processing_errors của worker/API và bằng chứng report còn đủ sau reload.
7. Cách quarantine được persist sau reload/upsert, giữ binding và observation history.
8. Cách xử lý legacy ledger NULL digest, atomic claim và provider call count trong race tests.
9. AC-28 là completed hay partial; bằng chứng routing guard và T35 nếu completed.
10. Exact commands đã chạy.
11. Pass/fail/skip counts.
12. Test nào không chạy và lý do.
13. git diff --check result.
14. git diff --stat.
15. git status --short.

Nếu test fail, sửa nguyên nhân rồi chạy lại. Không xóa/nới assertion chỉ để suite xanh.
