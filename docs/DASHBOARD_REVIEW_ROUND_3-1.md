# Dashboard Review Round 3.1 — 2026-09-19

**Mục tiêu: follow-up nhỏ để đóng nốt các finding còn lại sau Round 3. Không làm lại kiến trúc hoặc các phần đã pass.**

## Phạm vi

Branch:

~~~text
codex/local-dashboard-broadlink
~~~

HEAD đã review:

~~~text
dbb6c64fd214e97681f68df4a438afe8a2ef9c9d
fix(dashboard): resolve review round 3 correctness gaps
~~~

Parent trực tiếp:

~~~text
a65e475bee2a7fa8a7e946867e3d876cf9fbe91e
~~~

Round 3 đã xử lý đúng phần lớn V3-01..V3-08. Không amend/rewrite commit dbb6c64. Hãy tạo **một follow-up commit nhỏ** chỉ để đóng các finding dưới đây.

Không cherry-pick/merge implementation khác.

---

# Kết luận review hiện tại

Các phần đã ổn và phải được giữ nguyên:

- V3-01 manual advance: UI/API/service đã thống nhất take_sequence == current_take.
- Request advance thiếu/null/sai type -> 422.
- Wrong session/stale/future take -> 409.
- V3-04 quarantine flags đã persist/load.
- save_code_set/save_code_revision dùng upsert, không còn REPLACE phá FK/history.
- V3-05 lease timeout không còn bị finalize thành completed.
- V3-06 strict same-origin đã so scheme + exact hostname + effective port.
- V3-07 command claim đã phân biệt claimed_new/existing và unique request_id.
- V3-02 DTW đã chuyển missing file/SHA mismatch/corrupt WAV cơ bản thành ERROR.
- V3-03 aggregate DTW report đã tách schema hợp lý hơn.
- Registry giữ được completed_with_errors.

Không regression các behavior trên.

---

# V3.1-01 — P1: DTW vẫn chưa fail-closed hoàn toàn với truncated WAV và similarity exception

## Hiện trạng

File:

~~~text
src/smart_hub/wake_lab/worker.py
~~~

DTW đã kiểm missing file, source_sha256, SHA mismatch, WAV header, channels/sample width/sample rate/compression, empty PCM và feature extraction exception.

Nhưng vẫn còn các gap.

### A. Truncated PCM chưa bị phát hiện

Hiện tại:

~~~python
pcm = wf.readframes(nframes)
if len(pcm) == 0:
    integrity_error = ...
~~~

Nếu header khai nframes lớn nhưng file bị cắt ngắn, readframes() có thể trả ít byte hơn expected nhưng khác 0.

Phải kiểm:

~~~python
expected_pcm_bytes = nframes * channels * sampwidth
actual_pcm_bytes = len(pcm)

if actual_pcm_bytes != expected_pcm_bytes:
    integrity_error = ...
~~~

Không cho truncated WAV đi tiếp vào feature extraction/inference.

### B. Feature shape validation chưa chặt

Enrollment đang yêu cầu gần như:

~~~text
ndim == 2
shape[1] == 26
len(feat) >= 10
all finite
~~~

Evaluation hiện chỉ kiểm ndim + finite.

DTW evaluation phải dùng contract tương thích với artifact/enrollment:

~~~text
feat.ndim == 2
feat.shape[1] == 26
feat.shape[0] >= minimum supported frames
all values finite
~~~

Nếu engine có constant/metadata riêng thì dùng cùng source of truth, không hardcode hai nơi khác nhau nếu dễ tránh.

### C. similarity() exception vẫn có thể abort cả evaluation

Đoạn:

~~~python
sims = sorted([similarity(feat, t) for t in templates], reverse=True)
~~~

đang ngoài per-sample error guard.

Nếu một template sai shape hoặc similarity raise, cả run_candidate_evaluation() có thể throw. Khi đó parent có thể không lưu JSON/Markdown completed_with_errors như policy R3 yêu cầu.

## Yêu cầu sửa

Toàn bộ processing của **mỗi sample DTW** phải fail-closed thành sample-level ERROR.

Các lỗi sau không được abort toàn run nếu vẫn có thể tạo report:

- truncated PCM;
- invalid feature shape;
- feature too short;
- non-finite feature;
- template invalid shape;
- similarity exception;
- similarity trả NaN/inf;
- không có score hợp lệ.

Kết quả sample:

~~~json
{
  "status": "ERROR",
  "is_success": false,
  "outcome": "ERROR",
  "score": null,
  "detected": null,
  "error": "..."
}
~~~

ERROR:

- vẫn nằm trong eligible denominator;
- không cộng TP/FP/TN/FN;
- tăng positive.errors hoặc negative.errors;
- làm eval_data.status = completed_with_errors;
- không ngăn parent lưu report nếu worker vẫn tạo được eval_data.

Candidate artifact không load được hoàn toàn vẫn có thể coi là candidate-level fatal error; nhưng lỗi của một sample cụ thể không được biến thành whole-run crash.

## Regression tests bắt buộc

Trong tests/test_wake_lab.py:

1. Truncated WAV:
   - header nframes > bytes thực tế;
   - SHA đúng với file truncated hiện tại;
   - outcome phải ERROR.
2. Feature invalid shape:
   - patch features() trả shape (N, 25) hoặc 1D;
   - sample ERROR.
3. Feature too short:
   - shape (5, 26);
   - sample ERROR.
4. features() raise:
   - sample ERROR.
5. similarity() raise:
   - sample ERROR;
   - evaluation vẫn trả completed_with_errors.
6. similarity() trả NaN:
   - sample ERROR.
7. 10 eligible, 9 xử lý, 1 similarity error:
   - eligible_total=10;
   - processed_total=9;
   - errors=1.
8. Với negative sample similarity error:
   - không được TN.
9. Với positive sample similarity error:
   - không được FN.

Không dùng broad except để biến lỗi thành score=0.

---

# V3.1-02 — P2: DTW detail Markdown vẫn render decode metrics giả 0.000

## Hiện trạng

File:

~~~text
src/smart_hub/child_study.py
~~~

Trong bảng chi tiết từng WAV hiện có logic mặc định 0.0 cho decode_seconds/decode_rtf.

Với DTW result không có hai metric này, report có thể hiển thị 0.000 dù metric không áp dụng.

## Yêu cầu sửa

Nếu field không tồn tại hoặc là None:

~~~text
Decode (s) = N/A
Decode RTF = N/A
~~~

Chỉ render số khi thực sự có metric.

Ví dụ:

~~~python
decode_seconds = res.get("decode_seconds")
dec = f"{decode_seconds:.3f}" if decode_seconds is not None else "N/A"

decode_rtf = res.get("decode_rtf")
rtf_val = f"{decode_rtf:.3f}" if decode_rtf is not None else "N/A"
~~~

DTW không được xuất hiện fake STT performance metric.

## Regression tests bắt buộc

1. Pure DTW sample detail:
   - report chứa N/A ở Decode/RTF;
   - không có 0.000 do field absent.
2. Mixed report:
   - STT row vẫn render decode metrics thật;
   - DTW row render N/A.
3. DTW ERROR row vẫn render error message + N/A decode.

---

# V3.1-03 — P1: Dashboard API tests vẫn có thể ghi sessions/labels thật vào child-study data

## Hiện trạng

File:

~~~text
tests/test_dashboard_api.py
~~~

setUp() hiện đã đổi:

~~~python
RECORDING_SERVICE.recordings_root = temp_dir
~~~

nhưng RecordingService._sync_child_study() vẫn gọi các helper child-study dùng global:

~~~text
ROOT/recordings/child-study/sessions.json
ROOT/recordings/child-study/labels.jsonl
ROOT/recordings/child-study/audit.jsonl
~~~

Test V3-01 chạy session tới COMPLETED với:

~~~text
no_sync=False
mock=True
~~~

nên test có thể append child_v301_api vào metadata thật của repo/máy dev.

Điều này vi phạm test isolation và có nguy cơ làm benchmark dataset bị nhiễm dữ liệu test.

## Yêu cầu sửa

Toàn bộ dashboard recording API tests phải cô lập:

- recording session directory;
- child-study sessions.json;
- labels.jsonl;
- audit.jsonl;
- results nếu code path chạm tới;
- locks nếu path-based lock dùng ROOT.

### Hướng khuyến nghị

Refactor RecordingService để dependency-inject child-study base dir hoặc sync callback/path bundle.

Ví dụ:

~~~python
RecordingService(
    recordings_root=...,
    child_study_dir=...,
)
~~~

hoặc một sync backend injectable.

Production default vẫn dùng ROOT/recordings/child-study.

Test dùng temp root hoàn toàn.

Nếu refactor lớn quá, patch đúng module-level constants/functions trong fixture; nhưng phải chứng minh không chạm file thật.

## Test safety bắt buộc

Trong test setup:

1. Snapshot mtime/hash của các file thật nếu tồn tại:
   - recordings/child-study/sessions.json
   - recordings/child-study/labels.jsonl
   - recordings/child-study/audit.jsonl
2. Chạy recording API lifecycle.
3. Assert temp child-study files được tạo/thay đổi.
4. Assert file thật không đổi.

Tốt hơn là không đọc/ghi file thật trong test từ đầu.

## Dọn dữ liệu test đã lỡ ghi

Trước khi chạy lại suite, kiểm tra:

~~~bash
grep -n "child_v301_api" recordings/child-study/sessions.json || true
grep -n "child_v301_api" recordings/child-study/labels.jsonl || true
~~~

Nếu có dữ liệu test, **không xóa bằng grep/sed mù** nếu file có dữ liệu thật.

Hãy:

- backup file;
- parse JSON/JSONL;
- chỉ bỏ record có speaker_id/session_id/sample_id thuộc fixture test rõ ràng;
- giữ nguyên dữ liệu người dùng.

Không commit dữ liệu recording thật.

## Regression tests bắt buộc

- API recording completed với mock=True ghi metadata vào temp child-study root.
- Production child-study files không đổi.
- TearDown dọn temp root.
- Test interrupted/sync_failed cũng không ghi ra ROOT thật.

---

# V3.1-04 — P2/P3: generic_fake không được hoạt động như production provider ngầm

## Hiện trạng

File:

~~~text
src/smart_hub/devices/providers/factory.py
~~~

Hiện:

~~~python
if provider_name == "generic_fake":
    return GenericFakeProvider()
~~~

không phụ thuộc SMART_HUB_MOCK_HARDWARE=1.

Trong khi GenericFakeProvider là provider giả để chứng minh T35/AC-28.

Nó có thể check gateway và trả ONLINE, execute_action và trả delivered=True, trả observed state giả.

Nếu gateway.provider="generic_fake" tồn tại trong production DB, check path có thể báo thiết bị giả online.

## Yêu cầu sửa

Policy khuyến nghị:

~~~text
SMART_HUB_MOCK_HARDWARE=1
AND provider == generic_fake
=> allowed
~~~

Real mode:

~~~text
provider == generic_fake
=> UnsupportedProviderError / 422
~~~

Không silent fake ACK/ONLINE trong real mode.

Nếu muốn giữ generic_fake cho architecture demo, nó chỉ là test provider.

## Regression tests bắt buộc

1. mock mode + generic_fake -> allowed.
2. real mode + generic_fake -> rejected.
3. rejection xảy ra trước action/check I/O.
4. không tạo ledger/job khi rejected.
5. existing gateway generic_fake trong DB production không được check thành online giả.

---

# V3.1-05 — P3: AC-28 chưa có bằng chứng provider-neutral end-to-end

## Hiện trạng

Agent đã thêm:

~~~text
get_provider_for_gateway()
GenericFakeProvider
ProviderCapability
execute_action()
~~~

Nhưng test hiện chủ yếu gọi factory và GenericFakeProvider trực tiếp.

Các route chính vẫn thiên về IR:

~~~text
/devices/{id}/actions
validate_broadlink_payload
CodeRevision IR payload
IR_SEND
IR_LEARN
~~~

Do đó chưa đủ bằng chứng T35/AC-28 completed nếu định nghĩa là core provider-neutral end-to-end.

## Yêu cầu

Không cần build Tuya/MQTT thật.

Chọn một trong hai:

### Option A — AC-28 partial

Đơn giản và an toàn hơn cho R3.1:

- cập nhật docs/status rằng AC-28 = partial/foundation;
- giữ provider abstraction/factory;
- không tuyên bố completed;
- generic_fake chỉ test/mock;
- không mở thêm production route chỉ để đạt checklist.

### Option B — hoàn tất một neutral core action path

Nếu muốn AC-28 completed, phải có route/service core kiểu:

~~~text
gateway provider + capability + action_type + params
=> provider.execute_action()
=> observed_state/result
~~~

và test generic_fake qua core route/service, không gọi class trực tiếp.

Không bắt IR payload/MAC cho action non-IR.

Unknown provider/capability mismatch fail trước I/O.

Chỉ làm Option B nếu scope ban đầu thực sự yêu cầu; không mở rộng dự án quá mức.

## Acceptance

Trong final report phải nói rõ:

~~~text
AC-28: completed
~~~

hoặc:

~~~text
AC-28: partial
~~~

và đưa bằng chứng tương ứng.

Không ghi completed chỉ vì factory test pass.

---

# V3.1-06 — P2/Test gap: idempotency race cần test qua route với provider spy

## Hiện trạng

Storage claim hiện nhìn đúng:

~~~text
request_id UNIQUE
claim_command()
is_claimed_new
race IntegrityError path
~~~

Route cũng chỉ dispatch nếu is_claimed == True.

Nhưng test race hiện tại chủ yếu kiểm storage rồi suy ra số provider call từ is_claimed_new.

Đây chưa phải bằng chứng route thật chỉ gọi send_code một lần.

## Yêu cầu test

Thêm route-level concurrency test với:

- temp SQLite thật;
- cùng request_id;
- cùng appliance/gateway/revision/digest;
- provider spy/fake;
- Event/Barrier để tạo race deterministic;
- hai request API đồng thời.

Assert:

~~~text
provider.send_code.call_count == 1
ledger rows for request_id == 1
one request owns dispatch
other returns existing/in-progress result
~~~

Phải đếm call thật, không suy ra từ is_claimed_new.

Thêm case:

~~~text
same request_id + different binding/digest
=> một request claim
=> request kia 409
=> provider total calls <= 1
~~~

Và retry existing states:

- PREPARED;
- DISPATCHING;
- DELIVERED;
- UNKNOWN;

không được dispatch lần nữa.

---

# V3.1-07 — P2: DTW worker/report validation cần đúng cả in-process và subprocess path

## Hiện trạng

R3 yêu cầu sample ERROR không làm worker process exit failure và parent phải lưu report.

Test hiện có mạnh ở in-process nhưng chưa thấy đủ bằng chứng cho toàn chuỗi subprocess với DTW ERROR.

## Yêu cầu

Thêm test subprocess worker bằng interpreter có numpy.

Input gồm ít nhất:

~~~text
1 valid sample
1 integrity-error sample
1 DTW candidate
split=dev
mode=official
~~~

Assert process:

~~~text
returncode == 0
envelope.status == ok
eval_data.status == completed_with_errors
eval_data.has_processing_errors == true
errors == 1
eligible denominator giữ nguyên
~~~

Sau đó ít nhất một test WakeEvaluator parent path:

- nhận eval_data completed_with_errors;
- save_evaluation() được gọi/ghi registry;
- GET/reload trả status + errors đúng;
- report Markdown vẫn tồn tại.

Nếu test subprocess không chạy vì env thiếu numpy, final report phải ghi NOT RUN, và finding chưa được coi fully validated.

---

# Scope guard

Không thay đổi:

- wake phrase;
- wake thresholds;
- STT model bundle;
- VAD;
- audio gain;
- Broadlink packet format;
- real recordings;
- held-out test samples;
- acceptance metrics chỉ để test xanh.

Không:

- biến similarity exception thành score=0;
- bỏ ERROR khỏi denominator;
- dùng generic_fake như production fallback;
- ghi test fixture vào recordings/child-study thật;
- xóa dữ liệu người dùng khi dọn fixture test.

---

# Files dự kiến có thể cần sửa

Ưu tiên patch nhỏ trong:

~~~text
src/smart_hub/wake_lab/worker.py
src/smart_hub/child_study.py
src/smart_hub/devices/providers/factory.py
tests/test_wake_lab.py
tests/test_dashboard_api.py
tests/test_devices.py
~~~

Có thể sửa RecordingService nếu cần dependency injection child-study root:

~~~text
src/smart_hub/recording/service.py
~~~

Nếu chọn AC-28 partial, chỉ update docs/status cần thiết; không tạo thêm provider architecture không cần thiết.

Không tạo module v2/v3 song song.

---

# Validation bắt buộc

Chạy:

~~~bash
git diff --check
python3 -m compileall -q src scripts tests
~~~

Targeted suites:

~~~bash
.venv-dashboard/bin/python -m unittest discover -s tests -p 'test_dashboard_api.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_devices.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_wake_lab.py' -v
.venv-dashboard/bin/python -m unittest discover -s tests -p 'test_recording_service.py' -v
~~~

Mock suite:

~~~bash
.venv/bin/python scripts/run_tests.py --mock
~~~

Nếu Node có:

~~~bash
node --check src/smart_hub/dashboard/static/app.js
~~~

Không download/cài dependency chỉ để chạy test.

Nếu interpreter path khác, ghi rõ command thực tế.

---

# Safety check dữ liệu thật

Trước và sau targeted API tests:

~~~bash
git status --short
grep -n "child_v301_api" recordings/child-study/sessions.json || true
grep -n "child_v301_api" recordings/child-study/labels.jsonl || true
~~~

Nếu fixture mới dùng ID khác, kiểm cả ID đó.

Không để test tạo untracked/modified recording metadata thật.

---

# Acceptance checklist R3.1

- [ ] Truncated WAV -> DTW ERROR.
- [ ] Feature shape invalid/too short -> ERROR.
- [ ] similarity exception/NaN -> ERROR, không abort whole evaluation.
- [ ] ERROR giữ denominator.
- [ ] DTW detail report dùng N/A cho decode_seconds/decode_rtf không áp dụng.
- [ ] STT detail vẫn render decode metrics thật.
- [ ] Dashboard API recording tests không ghi sessions/labels/audit thật.
- [ ] child_v301_api hoặc fixture tương tự không xuất hiện trong production child-study files sau test.
- [ ] generic_fake bị chặn ở real mode.
- [ ] AC-28 được ghi completed hoặc partial trung thực.
- [ ] Route-level concurrent same request_id -> provider call count = 1.
- [ ] Conflict race -> 409 và không gửi thêm.
- [ ] Subprocess DTW ERROR path trả envelope ok + completed_with_errors.
- [ ] Parent evaluator lưu/reload report có processing error.
- [ ] Không regression V3-01..V3-08 đã đúng.
- [ ] git diff --check sạch.
- [ ] compileall sạch.
- [ ] targeted/mock suites pass hoặc report rõ NOT RUN.
- [ ] không hardware/network side effect thật.
- [ ] không dữ liệu recording thật bị sửa bởi test.

---

# Commit yêu cầu

Tạo một follow-up commit mới trên:

~~~text
codex/local-dashboard-broadlink
~~~

Không amend dbb6c64.

Commit message đề xuất:

~~~text
fix(dashboard): close review round 3.1 gaps
~~~

---

# Final report agent phải trả

1. Commit SHA mới.
2. Files changed.
3. Cách detect truncated WAV.
4. Cách similarity failure trở thành sample ERROR.
5. Feature validation contract cuối.
6. DTW detail Markdown N/A semantics.
7. Cách cô lập child-study metadata trong tests.
8. Kết quả kiểm tra child_v301_api / fixture leakage.
9. generic_fake policy cuối.
10. AC-28 final status: completed hay partial.
11. Route-level race test và provider call_count thực.
12. Subprocess DTW ERROR test result.
13. Exact validation commands.
14. Pass/fail/skip counts.
15. Test không chạy và lý do.
16. git diff --check.
17. git diff --stat.
18. git status --short.

Nếu test fail, sửa nguyên nhân rồi rerun. Không nới assertion hoặc bỏ test chỉ để xanh.
