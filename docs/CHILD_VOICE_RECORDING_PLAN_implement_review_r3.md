Bạn đang làm việc trong repo `nguyenduchoan/smart-hub`.

Branch cần sửa:

```text
codex/voice-assistant-plan
```

HEAD hiện tại:

```text
9deb3c8c62fb3b0ea1ad8fb4bcd9177fbfbc6ec8
Fix remaining child-study evaluation edge cases
```

Commit này đã xử lý đúng hầu hết yêu cầu trong:

```text
docs/CHILD_VOICE_RECORDING_PLAN_implement_review_r2.md
```

Không rewrite hoặc amend các commit cũ. Hãy tạo **một follow-up commit nhỏ** chỉ để hoàn thiện các edge case còn lại.

Không cherry-pick hoặc merge branch:

```text
codex/voice-assistant-plan-r2-implementation
```

Implementation hiện tại trên `codex/voice-assistant-plan` đang có kiến trúc tốt hơn vì giữ `src/smart_hub/child_study.py` là nguồn sự thật duy nhất.

---

# Mục tiêu

Hoàn thiện R2 mà không mở rộng scope.

Tập trung vào:

1. CLI reviewer phải fail đúng khi thiếu selector.
2. Technical QC thiếu provenance không được tự động biến sample thành rejected.
3. README phải mô tả chính xác denominator/integrity semantics.
4. Aggregate report không được trình bày condition-specific acceptance target như overall target.
5. Thêm regression tests tương ứng.
6. Chạy toàn bộ validation hiện có.

Không thay wake profile, model, threshold, VAD/STT logic hoặc recording format.

---

# 1. Reviewer CLI — bắt buộc đúng một selector

Hiện parser cho phép:

```bash
python3 scripts/review_child_study.py review
```

sau đó `review_session()` chỉ print `[ERROR]` rồi return, dẫn đến caller có thể nhận exit code thành công.

Sửa parser của subcommand `review` để bắt buộc **exactly one** trong:

```text
--session-id
--dir
```

Ưu tiên argparse native:

```python
selector = rev.add_mutually_exclusive_group(required=True)
selector.add_argument("--session-id", ...)
selector.add_argument("--dir", ...)
```

Không cần giữ runtime check thừa nếu parser đã enforce, trừ khi helper `review_session()` vẫn có thể được gọi trực tiếp từ Python.

Nếu gọi helper trực tiếp không selector, helper nên trả failure rõ ràng thay vì silent success.

Expected behavior:

```bash
python3 scripts/review_child_study.py review
# exit 2 từ argparse

python3 scripts/review_child_study.py review --session-id S01
# valid

python3 scripts/review_child_study.py review --dir recordings/foo
# valid

python3 scripts/review_child_study.py review --session-id S01 --dir recordings/foo
# exit 2
```

`summary` và invocation không subcommand vẫn giữ behavior hiện tại:

```bash
python3 scripts/review_child_study.py summary
python3 scripts/review_child_study.py
```

đều chỉ hiển thị summary.

### Tests bắt buộc

Thêm parser regression tests:

```text
review                   -> SystemExit 2
review --session-id S01  -> parse OK
review --dir DIR         -> parse OK
review --session-id S01 --dir DIR -> SystemExit 2
summary                  -> parse OK
no command               -> summary semantics không regression
```

---

# 2. Technical QC — thiếu SHA phải là needs_review, không phải rejected

Hiện:

```python
verify_audio_file(..., expected_sha="")
```

đúng khi trả technical failure vì không có provenance.

Nhưng `--auto-qc` hiện có thể biến mọi technical failure thành:

```text
review_status = rejected
```

Đây là semantics quá mạnh đối với trường hợp legacy metadata thiếu checksum.

Phân biệt:

## Integrity không chứng minh được

Ví dụ:

```text
source_sha256 missing
```

=> trạng thái:

```text
review_status = needs_review
speaker_confirmed = false
```

và note ghi rõ:

```text
Technical QC incomplete: missing original source_sha256
```

Không tự backfill SHA.

Không `accepted`.

Không `rejected` chỉ vì metadata provenance thiếu.

## File thực sự không đạt kỹ thuật

Ví dụ:

```text
SHA mismatch
WAV malformed
wrong sample rate/channel/sample width
truncated/empty WAV
file missing
```

Có thể giữ semantics hiện tại nếu project muốn technical failure trở thành `rejected`, nhưng phải nhất quán và có test.

Ưu tiên an toàn:

```text
missing provenance -> needs_review
actual corrupted/tampered file -> rejected
```

Nếu implementation đơn giản hơn thì mọi technical failure có thể `needs_review`; điều quan trọng là **missing SHA không được tự động thành rejected**.

Manual action `accept` vẫn phải bị block nếu:

```text
source_sha256 missing
SHA mismatch
invalid WAV
file missing
```

### Tests bắt buộc

Có ít nhất:

```text
correct SHA + valid WAV + auto-qc
=> technical_pass
=> speaker_confirmed false

missing SHA + auto-qc
=> needs_review
=> speaker_confirmed false
=> NOT rejected
=> NOT technical_pass

wrong SHA + manual accept
=> blocked

missing SHA + manual accept
=> blocked
```

---

# 3. README — sửa semantics official benchmark

README hiện vẫn có wording theo kiểu official runner yêu cầu:

```text
accepted + confirmed + đúng SHA
```

Wording này dễ hiểu thành SHA mismatch bị loại khỏi benchmark trước scoring.

Semantics chính xác sau R2 phải là:

## Selection

Official dataset membership dựa trên:

```text
review_status == accepted
speaker_confirmed == true
valid identity/label/expected-events metadata
matching requested split/filter
```

## Integrity evaluation

Sau khi đã được chọn vào benchmark:

```text
missing WAV
missing source SHA
SHA mismatch
invalid WAV
processing error
```

phải trở thành:

```text
ERROR
```

và sample **vẫn nằm trong denominator**.

README nên đưa ví dụ:

```text
10 accepted + confirmed samples
9 xử lý đúng
1 SHA mismatch

=> eligible = 10
=> accurate = 9
=> errors = 1
=> 9/10, không phải 9/9
```

Đồng bộ wording giữa:

```text
README.md
docs/CHILD_VOICE_RECORDING_PLAN.md
```

Không tạo thêm một document R3 riêng nếu không cần.

Giữ nguyên:

```text
docs/CHILD_VOICE_RECORDING_PLAN_implement_review_r1.md
docs/CHILD_VOICE_RECORDING_PLAN_implement_review_r2.md
```

---

# 4. Aggregate Markdown report — không dùng condition-specific acceptance target như overall target

Hiện R2 đã sửa positive accuracy và FRR thành dạng:

```text
N/A - xem acceptance theo nhóm
```

Hãy kiểm tra toàn bộ bảng aggregate.

Không được khiến người đọc hiểu rằng các target như:

```text
quiet 10/10
far >=9/10
noise >=9/10
adult 10/10
negative 0 false alarm
```

là target của một metric aggregate chung.

Ưu tiên:

Đổi header cuối từ:

```text
Mục tiêu pilot
```

thành:

```text
Ghi chú
```

hoặc tương đương.

Các metric condition-specific:

```text
Positive accuracy
FRR
```

ghi:

```text
Xem acceptance theo từng nhóm
```

Duplicate/FAR có thể giữ thông tin policy nếu nó thực sự global, nhưng wording phải rõ.

RTF/max decode là diagnostic performance metrics, không phải bằng chứng profile đã PASS child acceptance.

Có thể dùng wording:

```text
Diagnostic threshold
```

hoặc:

```text
Chỉ số hiệu năng, không phải overall acceptance
```

Không implement automatic PASS/FAIL/WINNER giữa `standard` và `sensitive`.

Không tự chọn profile.

---

# 5. Giữ kiến trúc một nguồn sự thật

Không tạo:

```text
child_study_r2.py
child_study_v2.py
child_study_new.py
```

Tiếp tục sửa trực tiếp:

```text
src/smart_hub/child_study.py
```

để:

```text
filter_samples
evaluate_sample
evaluate_dataset
get_reproducibility_metadata
format_evaluation_markdown
```

là implementation canonical.

Không duplicate evaluator/report logic sang module khác.

---

# 6. Kiểm tra các semantics R2 hiện tại không regression

Không làm mất các behavior đã đúng trong `9deb3c8`:

```text
accepted + confirmed missing WAV
=> selected
=> ERROR
=> denominator giữ nguyên

accepted + confirmed SHA mismatch
=> selected
=> ERROR
=> denominator giữ nguyên

9 good + 1 ERROR
=> 9/10
=> errors=1
=> CLI save report
=> exit non-zero
```

Giữ đúng:

```text
default evaluation split = dev
test split chỉ explicit

pending/rejected/unconfirmed
=> không thuộc official denominator

missing SHA của accepted sample
=> ERROR trong evaluator

backend không chạy khi integrity error

model provenance:
BUNDLE
ARCHIVE_SHA256
FILES hashes

decode RTF:
decode_seconds / decoded_audio_seconds

sync failure after capture:
manifest.status = sync_failed
không overwrite thành interrupted

Ctrl+C:
interrupted
exit 130
```

---

# 7. Test cleanup

Không tạo test suite song song không cần thiết.

Tiếp tục bổ sung vào:

```text
tests/test_mock_child_study.py
```

Giữ test deterministic, không microphone thật, không Sherpa/model thật.

Ngoài các tests ở mục 1 và 2, đảm bảo vẫn có regression test:

```text
10 samples, 1 missing WAV => 9/10 + 1 ERROR

10 samples, 1 SHA mismatch => 9/10 + 1 ERROR

CLI with integrity error:
report saved
exit code 1

default split == dev

reproducibility metadata == stt_assets values

sync failure:
status == sync_failed
WAV preserved
```

Nếu test hiện có đã cover đúng thì không duplicate test mới.

---

# 8. Validation bắt buộc

Chạy:

```bash
git diff --check

python3 -m compileall -q src scripts tests

python3 scripts/run_tests.py --mock
```

Nếu `.venv` hiện hữu:

```bash
.venv/bin/python scripts/run_tests.py
```

Nếu STT dependency/model đã có sẵn:

```bash
.venv/bin/python scripts/run_tests.py --stt
```

Không download dependency/model.

Không chạy mic thật.

Ngoài ra chạy smoke tests:

```bash
python3 scripts/review_child_study.py --help
python3 scripts/review_child_study.py review --help
python3 scripts/review_child_study.py summary
python3 scripts/evaluate_child_study.py --help
```

Và xác nhận parser failure:

```bash
python3 scripts/review_child_study.py review
```

phải exit non-zero.

Không sửa recordings thật trong test.

---

# 9. Scope guard

Không thay:

```text
WAKE_PROFILES
wake threshold
VAD parameters
model bundle
wake word
keyword aliases
recording duration
sample format
cooldown
runtime assistant behavior
```

Không thêm:

```text
database
cloud
upload
fine tuning
new framework
automatic candidate selection
```

Không dùng held-out `test` để tune.

Không cherry-pick commit từ branch implementation khác chỉ để lấy toàn bộ code.

---

# 10. Commit

Sau khi toàn bộ validation phù hợp, tạo đúng một follow-up commit trên:

```text
codex/voice-assistant-plan
```

Không amend:

```text
9deb3c8c62fb3b0ea1ad8fb4bcd9177fbfbc6ec8
```

Commit message đề xuất:

```text
Polish child-study review and reporting semantics
```

---

# 11. Final report

Khi hoàn tất, trả lại chính xác:

1. Commit SHA mới.
2. Files changed.
3. Giải thích reviewer parser mới.
4. Giải thích `missing SHA` trong auto-QC xử lý thế nào.
5. Giải thích final denominator semantics.
6. Các thay đổi README/plan.
7. Exact validation commands đã chạy.
8. Test pass/fail/skip counts.
9. Tests nào không chạy và lý do.
10. `git diff --check` result.
11. `git diff --stat`.
12. `git status --short`.

Nếu một test fail, sửa nguyên nhân rồi chạy lại; không bỏ test hoặc nới assertion chỉ để suite xanh.
