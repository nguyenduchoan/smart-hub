Bạn đang làm việc trong repo `nguyenduchoan/smart-hub`, branch `codex/voice-assistant-plan`.

HEAD hiện tại cần sửa dựa trên commit:

`32bd43d68285ba3cb32af67816882bfe50781372`

Commit này là follow-up của `79a4d9c` và đã xử lý phần lớn các finding trong `docs/CHILD_VOICE_RECORDING_PLAN_implement_review_r1.md`.

**Giữ nguyên file R1** vì đây là lịch sử review/chỉ dẫn có chủ đích. Không xóa, rename hoặc rewrite `docs/CHILD_VOICE_RECORDING_PLAN_implement_review_r1.md`.

Hãy thực hiện một vòng sửa R2 nhỏ, tập trung đúng các lỗi còn sót dưới đây. Tạo **một follow-up commit mới**, không amend/rewrite `32bd43d` hoặc `79a4d9c`.

Mục tiêu chính vẫn là pipeline:

`record -> review -> labels/sessions -> offline evaluation -> report`

phải fail-closed, không làm đẹp metric khi dữ liệu hỏng, report phải tái lập được, trạng thái recorder phải phản ánh đúng giai đoạn lỗi và tài liệu phải chạy đúng với CLI thực tế.

Không mở microphone thật. Không thu giọng thật. Không upload WAV/data. Không tải model/dependency mới. Không thay threshold/profile chỉ để test pass. Không xóa dữ liệu trong `recordings/`.

---

## 1. P1 BLOCKER — Integrity failure của sample đã duyệt phải nằm trong denominator

Đây là lỗi quan trọng nhất của R2.

Hiện `filter_samples()` / `is_sample_eligible_for_official_benchmark()` đang loại sample khỏi `eligible_samples` nếu:

- WAV bị mất;
- SHA-256 hiện tại không khớp `source_sha256`;
- không đọc được file.

Sau đó `evaluate_dataset()` chỉ nhận các sample còn lại. Điều này vẫn có thể tạo kết quả sai kiểu:

```text
10 sample đã accepted + speaker_confirmed
9 WAV tốt
1 WAV bị mất hoặc SHA mismatch

=> filter còn 9
=> evaluator báo 9/9 = 100%
=> errors = 0
=> exit 0
```

Điều này trái với quy tắc trong `CHILD_VOICE_RECORDING_PLAN.md`: lỗi file/runner/integrity phải là `ERROR`, vẫn nằm trong mẫu số, ví dụ `9/10 + 1 ERROR`, không được biến thành `9/9`.

### Yêu cầu thiết kế

Tách rõ hai lớp:

### A. Dataset selection / benchmark eligibility

Dùng để quyết định sample nào thuộc bài benchmark:

- đúng split;
- đúng session/speaker/label filter;
- `review_status == "accepted"`;
- `speaker_confirmed is True`;
- label hợp lệ;
- expected-events contract hợp lệ;
- metadata định danh tối thiểu hợp lệ.

### B. Runtime/data integrity evaluation

Được kiểm tra **sau khi sample đã được chọn vào benchmark**:

- source tồn tại;
- source SHA-256 tồn tại;
- SHA hiện tại khớp expected SHA;
- WAV đọc được;
- WAV đúng format;
- decode/VAD/STT chạy được;
- clipping semantics.

Các lỗi ở lớp B phải tạo sample result `ERROR` hoặc `CLIPPED`, **không được loại sample khỏi benchmark trước khi evaluate**.

Ví dụ bắt buộc sau patch:

```text
10 accepted + confirmed samples
9 good
1 SHA mismatch

=> eligible_total = 10
=> processed_total = 9
=> errors = 1
=> accurate = 9
=> accurate_rate = 90%
=> command saves diagnostic report
=> command exits non-zero
```

`evaluate_sample()` hiện đã có logic SHA mismatch -> `ERROR` trước khi backend chạy; ưu tiên reuse logic này thay vì duplicate integrity check ở filter.

### Không được regression

- pending/rejected/unconfirmed sample vẫn không đi vào official benchmark;
- backend không được chạy nếu SHA mismatch;
- ad-hoc `--allow-unreviewed` vẫn hoạt động rõ ràng như hiện tại.

### Tests bắt buộc

Thêm regression test end-to-end ở level filter + evaluate/CLI:

1. 10 accepted/confirmed, trong đó 1 missing WAV -> `eligible_total == 10`, `errors == 1`, accuracy denominator 10.
2. 10 accepted/confirmed, trong đó 1 SHA mismatch -> cùng semantics trên.
3. CLI official evaluation có integrity error -> vẫn save report và exit non-zero.
4. pending/rejected/unconfirmed không được tính vào eligible denominator.

Không chỉ test trực tiếp `evaluate_sample()`; phải test đường selection thực tế để tránh tái xuất hiện lỗi này.

---

## 2. P1/P2 — Đồng bộ tài liệu và CLI của `review_child_study.py`

README và `docs/CHILD_VOICE_RECORDING_PLAN.md` hiện hướng dẫn dạng:

```bash
.venv/bin/python scripts/review_child_study.py --auto-qc
.venv/bin/python scripts/review_child_study.py
```

nhưng implementation dùng subcommand `review`, nên `--auto-qc` thuộc parser của `review`, còn không truyền subcommand thì script chỉ in summary.

### Chọn một trong hai hướng và làm nhất quán

Ưu tiên patch nhỏ:

```bash
.venv/bin/python scripts/review_child_study.py review --session-id S01 --auto-qc
.venv/bin/python scripts/review_child_study.py review --session-id S01 --reviewer QC
```

hoặc `--dir ...` khi review theo thư mục.

Nếu muốn làm `review` thành default command thì phải implement + test đầy đủ, nhưng không cần thiết cho R2.

### Yêu cầu

- README phải dùng command thực sự chạy được;
- `CHILD_VOICE_RECORDING_PLAN.md` phải dùng command thực sự chạy được;
- ví dụ manual review phải chỉ rõ `review` subcommand và ít nhất một selector hợp lý (`--session-id` hoặc `--dir`);
- `summary` vẫn hoạt động;
- `--auto-accept` compatibility alias nếu giữ lại phải tiếp tục có semantics technical QC, không tự xác nhận speaker.

### Tests bắt buộc

- parser chấp nhận `review --session-id ... --auto-qc`;
- gọi `--auto-qc` ở top-level phải không được tài liệu quảng bá như command hợp lệ;
- summary path không bị regression.

---

## 3. P2 — Sửa reproducibility metadata: dùng đúng model bundle và hashes thực tế

`get_reproducibility_metadata()` hiện có hai vấn đề:

1. cố import `HASHES` từ `smart_hub.stt_assets`, nhưng module này không có `HASHES`;
2. hardcode một model bundle không phải bundle thực tế của repo.

`src/smart_hub/stt_assets.py` hiện có nguồn sự thật:

- `BUNDLE`
- `FILES`
- `ARCHIVE_SHA256`

### Yêu cầu

Không hardcode model name trong evaluator.

Dùng trực tiếp metadata từ `stt_assets.py`, ví dụ theo semantics:

```python
from .stt_assets import BUNDLE, FILES, ARCHIVE_SHA256
```

Report reproducibility phải chứa tối thiểu:

```json
{
  "stt_model_bundle": "<BUNDLE thực tế>",
  "stt_archive_sha256": "<ARCHIVE_SHA256>",
  "model_file_hashes": {
    "encoder.int8.onnx": "...",
    "decoder.onnx": "...",
    "joiner.int8.onnx": "...",
    "tokens.txt": "...",
    "silero_vad.onnx": "..."
  }
}
```

Lấy digest từ `FILES[name][1]`; không cần hash lại model runtime nếu không cần.

Nếu metadata import thất bại, phải ghi rõ error/unknown thay vì âm thầm tạo `{}` khiến người đọc tưởng không có model provenance.

### Tests bắt buộc

- `stt_model_bundle == stt_assets.BUNDLE`;
- file hashes trong result khớp `FILES`;
- không còn chuỗi bundle hardcode sai;
- metadata vẫn tạo được mà không mở model/microphone.

---

## 4. P2 — Phân biệt `sync_failed` với capture `interrupted`

Recorder hiện có flow:

1. thu WAV hoàn tất;
2. set `manifest.status = "captured_pending_review"`;
3. metadata sync fail -> set `sync_failed` rồi raise;
4. outer `except Exception` bắt lại và đổi status thành `interrupted`.

Như vậy lỗi metadata sau khi capture hoàn tất bị báo nhầm thành lỗi thu âm.

### Yêu cầu trạng thái

Tối thiểu phải phân biệt:

```text
Ctrl+C / SIGTERM giữa capture     -> interrupted, exit 130
capture/audio failure             -> interrupted hoặc capture_failed, exit 1
capture hoàn tất + metadata sync fail -> sync_failed, exit 1
capture + sync hoàn tất           -> captured_pending_review, exit 0
```

Không được overwrite `sync_failed` thành `interrupted` ở outer exception handler.

WAV và manifest đã thu phải được giữ nguyên khi sync fail.

Log phải phản ánh đúng loại lỗi, ví dụ metadata sync failure không in như thể microphone/capture bị lỗi.

### Tests bắt buộc

Thay test helper-level hiện tại bằng ít nhất một recorder-level regression test:

- mock capture hoàn tất;
- mock `save_session_and_labels()` fail;
- manifest cuối cùng là `sync_failed`;
- process/main path báo failure;
- WAV/takes vẫn tồn tại;
- không đổi thành `interrupted`.

Giữ test Ctrl+C hiện có.

---

## 5. P2 — Reviewer không được `accepted` sample thiếu checksum gốc

Hiện `verify_audio_file(path, expected_sha=None)` sẽ kiểm tra format và tính SHA hiện tại, nhưng nếu label không có `source_sha256` thì không có gì để so sánh. Manual reviewer vẫn có thể đánh dấu sample `accepted` + `speaker_confirmed=True`.

Sau đó official evaluator lại từ chối sample vì thiếu checksum.

Không tạo trạng thái mâu thuẫn:

```text
review_status = accepted
speaker_confirmed = true
nhưng sample không đủ integrity provenance để benchmark
```

### Yêu cầu

Để manual review chuyển một sample thành `accepted`, bắt buộc:

- có `source_sha256` gốc không rỗng;
- SHA hiện tại khớp checksum đó;
- WAV technical validation pass.

Nếu thiếu checksum:

- block action `accept`;
- giữ `needs_review`/pending hoặc yêu cầu migration riêng;
- không tự backfill SHA rồi giả định file hiện tại chính là file lịch sử đã được thu.

Nếu cần hỗ trợ legacy data, tạo helper/action migration riêng có semantics rõ ràng và audit note; không cần triển khai migration trong R2 nếu không thực sự cần.

Có thể tăng strictness của `validate_label_entry()` cho official/review path, nhưng lưu ý backward compatibility với dữ liệu pending legacy. Không làm loader fail toàn bộ chỉ vì một pending legacy record thiếu SHA nếu chưa cần.

### Tests bắt buộc

- correct SHA -> manual accept được;
- wrong SHA -> accept bị block;
- missing SHA -> accept bị block;
- auto QC với missing SHA không được tạo trạng thái có vẻ đã integrity-verified.

---

## 6. P2 — Acceptance targets phải được trình bày theo group, không gắn vào aggregate metric

Markdown report hiện đặt target như:

```text
100% yên tĩnh, ≥90% nhiễu/xa
```

bên cạnh một tỷ lệ positive aggregate. Điều này dễ gây hiểu sai vì target trong plan là theo từng condition/group:

- child quiet normal: 10/10;
- child far: >= 9/10;
- child normal noise: >= 9/10;
- adult regression: 10/10;
- negative phrases: 0 false alarm.

Một aggregate 93% không chứng minh từng group đạt acceptance.

### Yêu cầu

Ở bảng tổng hợp profile:

- bỏ cột target aggregate; hoặc
- ghi `N/A - xem acceptance theo nhóm`.

Ở phần group detail:

- hiển thị rõ từng group/condition;
- nếu implementation có đủ mapping chắc chắn giữa condition và acceptance rule thì có thể hiển thị target tương ứng;
- nếu không chắc mapping, chỉ báo raw count/rate và để người đọc đối chiếu plan;
- tuyệt đối không tự tuyên bố overall PASS/WINNER giữa standard/sensitive.

Không làm logic recommendation/chọn profile tự động trong R2.

### Tests bắt buộc

- report không còn đặt một target condition-specific cạnh aggregate positive rate;
- group rows vẫn có raw numerator/denominator;
- ERROR/CLIPPED vẫn hiển thị rõ.

---

## 7. Test quality cleanup nhỏ

R2 không cần mở rộng scope lớn, nhưng sửa hai test yếu hiện tại:

### `test_18_default_split_is_dev_not_test`

Test phải thực sự assert default parser là `dev`, không chỉ chạy CLI rồi thấy `SystemExit`.

Có thể refactor parser creation thành helper như:

```python
build_parser()
```

để test parser defaults trực tiếp mà không cần monkeypatch vòng vo.

### Sync failure test

Test hiện chỉ gọi `save_session_and_labels()` với invalid data. Thay/bổ sung bằng recorder-level test như mục 4 để xác minh manifest state thực tế.

Ưu tiên tests deterministic, không microphone/model thật.

---

## 8. Không sửa ngoài phạm vi

Giữ nguyên những phần R1 đã làm đúng, đặc biệt:

- default official split = `dev`;
- `test` chỉ chạy explicit;
- pending/rejected/unconfirmed không đi vào official benchmark;
- SHA là SHA toàn file WAV;
- malformed JSON/JSONL fail-fast;
- identity/session collision protection;
- frame + segment clipping semantics;
- decode RTF dùng decoded segment audio;
- single-profile dynamic report;
- output overwrite protection;
- speaker_label riêng với speaker_id;
- Ctrl+C trả 130;
- optional real STT tests dùng `skipUnless(HAVE_STT)`;
- ad-hoc watermark;
- `docs/CHILD_VOICE_RECORDING_PLAN_implement_review_r1.md` phải được giữ nguyên.

Không:

- đổi wake word;
- đổi `WAKE_PROFILES` values;
- thay model;
- thêm database/framework;
- thêm cloud service;
- upload recordings;
- chạy microphone thật;
- tự động chọn `standard` hoặc `sensitive`;
- dùng test split để tune;
- xóa R1.

---

## 9. Validation commands

Sau khi sửa, chạy ít nhất:

```bash
git diff --check
python3 -m compileall -q src scripts tests
python3 scripts/run_tests.py --mock
```

Nếu `.venv` và dependencies hiện có:

```bash
.venv/bin/python scripts/run_tests.py
```

Nếu Sherpa + STT models đã tồn tại sẵn:

```bash
.venv/bin/python scripts/run_tests.py --stt
```

Không download model/dependency trong task này.

Không chạy microphone tests.

Nếu real STT tests không chạy được, ghi chính xác `NOT RUN`/`SKIPPED` và lý do; không tuyên bố PASS.

Ngoài automated tests, chạy CLI parser/help smoke tests không cần model/mic, tối thiểu:

```bash
python3 scripts/review_child_study.py --help
python3 scripts/review_child_study.py review --help
python3 scripts/evaluate_child_study.py --help
```

Nếu test review/evaluator cần data, dùng temporary fixture/mock; không dùng hoặc sửa recordings thật.

---

## 10. Acceptance checklist R2

Chỉ coi R2 hoàn tất khi tất cả điều sau đúng:

- [ ] 1 accepted sample bị mất file/SHA mismatch vẫn nằm trong denominator và thành `ERROR`.
- [ ] 9 good + 1 integrity error được báo 9/10, không phải 9/9.
- [ ] evaluation có ERROR vẫn save report và exit non-zero.
- [ ] README và plan dùng đúng `review` subcommand.
- [ ] reproducibility metadata dùng đúng `stt_assets.BUNDLE` và hashes từ `FILES`.
- [ ] sync metadata failure giữ `status=sync_failed`, không bị đổi thành `interrupted`.
- [ ] reviewer không thể accept sample thiếu/wrong SHA.
- [ ] aggregate report không gắn acceptance target condition-specific gây hiểu sai.
- [ ] default split test thực sự assert `dev`.
- [ ] mock/unit tests không cần microphone thật.
- [ ] R1 vẫn còn nguyên.

---

## 11. Deliverable cuối cùng

Sau khi hoàn tất, trả về:

1. summary ngắn từng finding R2 đã sửa;
2. danh sách files thay đổi;
3. giải thích semantics mới của `eligible`, `ERROR`, `CLIPPED` và denominator;
4. exact test commands đã chạy;
5. số test pass/skip/fail;
6. test nào không chạy được và lý do;
7. `git diff --check` result;
8. `git diff --stat`;
9. `git status --short`;
10. SHA của follow-up commit mới.

Commit message đề xuất:

`Fix remaining child-study evaluation edge cases`

Không amend `32bd43d`. Tạo commit mới trên branch hiện tại.
