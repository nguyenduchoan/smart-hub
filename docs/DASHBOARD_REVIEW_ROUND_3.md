# Dashboard Review Round 3 — 2026-09-19

**Kết luận: NEEDS_FIX — chưa nên merge codex/local-dashboard-broadlink vào nhánh chính.**

Tài liệu này là prompt/task cho vòng sửa tiếp theo sau khi review commit:

~~~text
Branch: codex/local-dashboard-broadlink
HEAD đã review: 89d38c592698fb81a80ff148b37a9b2f277198d5
Parent: 522401d3364e22daa9e1b82d84e3081c92a2b8e0
Commit message hiện tại:
fix(dashboard): resolve review round 2 findings V2-01 to V2-19 and AC-28
~~~

Round 2 đã sửa được nhiều vấn đề quan trọng, đặc biệt: AlsaCapture.drain(), technical QC khi review sample, tách transcript khỏi ground truth, binding revision/appliance/button, idempotency digest cơ bản, verified remote revisions, cue playback failure, sync failure state, input validation, dataset lock/revision/audit, cross-split checks, mock provenance và provider preflight.

**Không làm lại toàn bộ Round 2.** Chỉ sửa các finding dưới đây và thêm regression tests để khóa behavior.

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

Chọn **một semantics duy nhất** và dùng xuyên suốt UI/API/service.

Khuyến nghị:

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

## Regression tests bắt buộc

Thêm test qua cả service và API contract:

1. Start mock recording, manual advance, take 1.
2. Poll/status trả current_take == 1.
3. POST /api/recording/advance với session_id hiện tại và take_sequence=1 phải thành công.
4. Gửi lại stale take_sequence=1 khi service đã sang waiting take 2 phải fail 409.
5. take_sequence=2 ở take 2 phải thành công.
6. Wrong session_id phải fail 409.

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

Metrics phải tách:

~~~text
eligible_total
processed_total
errors
tp
fp
tn
fn
~~~

Và accuracy/recall/FAR phải dùng denominator đã định nghĩa rõ, không silently bỏ ERROR.

Tối thiểu:

~~~text
10 eligible
9 processed correctly
1 integrity ERROR
=> eligible_total = 10
=> processed_total = 9
=> errors = 1
~~~

Không được biến thành 9 eligible.

## Regression tests bắt buộc

- positive missing WAV -> ERROR, không FN.
- negative missing WAV -> ERROR, không TN.
- positive SHA mismatch -> ERROR.
- negative SHA mismatch -> ERROR.
- malformed WAV -> ERROR.
- backend/features không được gọi sau SHA mismatch.
- 10 sample, 9 valid + 1 ERROR -> denominator vẫn 10.
- official evaluation có ERROR phải lưu report/result nhưng trả/final status thể hiện benchmark có processing error theo policy hiện có.

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
positive.{eligible,accurate,missed,...}
negative.{eligible,correct_reject,false_alarm,...}
performance (optional / N/A)
~~~

DTW-specific values như score/threshold/F1 có thể thêm riêng.

### Hướng B — engine-aware report

Tạo report generator của Wake Lab biết từng engine:

- STT: accuracy/FRR/FAR/duplicate/RTF.
- DTW: TP/FP/TN/FN, precision, recall, F1, accuracy, threshold, ERROR count.
- Unsupported metric phải ghi N/A, không ghi 0 giả.

Ưu tiên code rõ ràng, không dùng string replacement sau khi formatter chạy.

## Regression tests bắt buộc

Tạo fixture DTW với số dễ kiểm:

~~~text
eligible=10
processed=9
errors=1
tp=4
fn=1
tn=3
fp=1
~~~

Markdown phải chứa đúng các số đó hoặc normalized equivalent.

Không được có fake STT 0/0 hoặc RTF 0.000 nếu metric đó không áp dụng.

Thêm test evaluation có cả STT + DTW trong cùng report và cả hai phải hiển thị đúng schema.

---

# V3-04 — P1/P2: Quarantine mock seed chưa persist end-to-end

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

Nếu requirement là **exact scheme/host/port**, implementation hiện chưa đủ.

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

Không nới allowed host chỉ để test xanh.

---

# V3-07 — P2: Legacy ledger có payload_digest=NULL vẫn có thể replay sai payload

## Hiện trạng

Migration thêm cột command_ledger.payload_digest nhưng row cũ có thể NULL.

Logic hiện tại chỉ conflict khi:

~~~python
existing.payload_digest and existing.payload_digest != current_digest
~~~

Nếu digest cũ NULL, cùng request_id có thể được request mới reuse và route trả ledger result cũ mà không chứng minh cùng appliance/gateway/button/revision.

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

## Regression tests bắt buộc

Fixture DB legacy với payload_digest=NULL:

1. same request_id + same binding -> deterministic documented behavior.
2. same request_id + different appliance -> 409.
3. different gateway -> 409.
4. different button -> 409.
5. different revision -> 409.
6. provider không được gọi trong conflict cases.

Thêm race test nếu logic claim thay đổi.

---

# V3-08 — P3 / AC-28: Provider capability interface chưa phải provider-neutral end-to-end

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

Do đó **không được tuyên bố AC-28 hoàn tất end-to-end** nếu acceptance yêu cầu nhiều provider hoặc neutral routing.

## Yêu cầu

Không cần tích hợp Tuya/MQTT thật trong R3 nếu ngoài scope.

Nhưng phải làm một trong hai:

### Option A — hoàn tất neutral provider factory

Tạo resolver/factory:

~~~python
get_provider_for_gateway(gateway)
~~~

dựa vào gateway.provider, capabilities và explicit mock mode.

Unknown provider -> 422/501/503 rõ ràng, không silently dùng Broadlink.

Route send/learn/check dùng neutral contract/capability check thay vì hardcode Broadlink path.

### Option B — scope AC-28 trung thực

Nếu chưa muốn refactor provider routing trong R3:

- giữ interface hiện tại;
- cập nhật status/docs rằng AC-28 mới là “interface foundation / partial”;
- không đánh dấu completed.

Không giả lập hỗ trợ provider chưa có.

## Tests nếu chọn Option A

- gateway provider=broadlink -> Broadlink provider.
- provider=mock chỉ khi policy cho phép explicit mock.
- unknown provider -> error rõ, 0 hardware calls.
- provider thiếu IR_SEND -> send bị chặn.
- provider thiếu IR_LEARN -> learning bị chặn.
- route không cần biết concrete provider class.

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

Nếu môi trường project có dependencies dashboard:

~~~bash
python3 -m unittest discover -s tests -p 'test_recording_service.py' -v
python3 -m unittest discover -s tests -p 'test_dashboard_api.py' -v
python3 -m unittest discover -s tests -p 'test_devices.py' -v
python3 -m unittest discover -s tests -p 'test_wake_lab.py' -v
~~~

Sau đó chạy suite mock hiện có:

~~~bash
python3 scripts/run_tests.py --mock
~~~

Nếu repo có .venv, ưu tiên interpreter có đủ dependency:

~~~bash
.venv/bin/python -m unittest discover -s tests -p 'test_recording_service.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_dashboard_api.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_devices.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_wake_lab.py' -v
.venv/bin/python scripts/run_tests.py --mock
~~~

Nếu Node có sẵn:

~~~bash
node --check src/smart_hub/dashboard/static/app.js
~~~

Không cài/download gì chỉ để chạy test. Nếu dependency thiếu, ghi rõ SKIP/NOT RUN và lý do.

---

# Acceptance checklist Round 3

Trước khi coi R3 hoàn tất, phải chứng minh:

- [ ] Dashboard manual advance take 1 hoạt động với session_id + take_sequence=1.
- [ ] Stale advance không thể kích nhầm take sau.
- [ ] DTW missing/corrupt/SHA-mismatch sample -> ERROR, không TP/FP/TN/FN.
- [ ] DTW ERROR vẫn giữ denominator.
- [ ] DTW Markdown report hiển thị đúng metrics, không render fake STT 0/0.
- [ ] Mixed STT + DTW report không làm mất metrics của engine nào.
- [ ] Quarantine flags survive save/reload/restart.
- [ ] Mock/quarantined code không thể dispatch tới real hardware dù ID không có _seed.
- [ ] Lease timeout kết thúc ở INTERRUPTED, không bị finalize thành COMPLETED.
- [ ] Origin host phải exact với Host hostname, ngoài scheme/port.
- [ ] Legacy ledger NULL digest + binding khác -> 409.
- [ ] Provider capability status được xử lý trung thực: factory end-to-end hoặc AC-28 ghi partial.
- [ ] Không regression V2-01..V2-19 đã sửa đúng.
- [ ] git diff --check sạch.
- [ ] Compileall sạch.
- [ ] Targeted test suites pass hoặc có skip có lý do rõ.
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
4. Semantics cuối của recording take_sequence.
5. Semantics DTW ERROR/denominator.
6. Report schema cho STT/DTW.
7. Cách quarantine được persist sau reload.
8. Cách xử lý legacy ledger NULL digest.
9. AC-28 là completed hay partial, với bằng chứng.
10. Exact commands đã chạy.
11. Pass/fail/skip counts.
12. Test nào không chạy và lý do.
13. git diff --check result.
14. git diff --stat.
15. git status --short.

Nếu test fail, sửa nguyên nhân rồi chạy lại. Không xóa/nới assertion chỉ để suite xanh.
