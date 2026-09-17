Bạn đang làm việc trong repo `nguyenduchoan/smart-hub`, branch `codex/voice-assistant-plan`.

HEAD hiện tại cần sửa dựa trên commit:

`79a4d9c25432035e7e4f0f81e923b2cc44def3fe`

Commit này triển khai CV-01/CV-04/CV-05 cho child voice study. Hãy review lại implementation hiện tại, sửa các vấn đề dưới đây và tạo **một follow-up commit mới**, không rewrite/amend commit `79a4d9c`.

Mục tiêu chính: pipeline

`record -> review -> labels/sessions -> offline evaluation -> report`

phải đảm bảo data integrity, không vô tình dùng test data để tune, benchmark phải tương đương logic runtime `listen-stt --wav`, lỗi processing không được làm metric đẹp giả, và kết quả phải reproducible.

Không mở microphone thật. Không thu giọng thật. Không upload WAV/data. Không thay model hoặc config production. Không xóa dữ liệu recordings hiện có.

## 1. P0/P1 — Chỉ benchmark dữ liệu đã được duyệt

Hiện `scripts/evaluate_child_study.py` load tất cả entries từ `labels.jsonl`, kể cả pending/rejected, và `--dir` bypass labels bằng manifest thô.

Sửa để benchmark chính thức mặc định CHỈ dùng sample thỏa tất cả:

* `review_status == "accepted"`
* `speaker_confirmed is True`
* có `source`
* có `source_sha256`
* label hợp lệ
* split hợp lệ
* WAV tồn tại
* SHA-256 file hiện tại đúng với `source_sha256`

Không được suy `captured_pending_review` thành accepted.

Nếu vẫn muốn giữ chế độ exploratory cho `--wav` hoặc `--dir`, phải tách rõ:

* đánh dấu result là `evaluation_mode = "ad_hoc"` hoặc `"unreviewed"`
* không được coi là acceptance benchmark
* phải cần explicit option như `--allow-unreviewed`
* report phải in cảnh báo rõ
* mặc định không cho manifest pending đi vào benchmark chính thức.

Tạo helper dùng chung để select/validate eligible samples thay vì rải logic trong CLI.

## 2. P0/P1 — Fix SHA-256 integrity end-to-end

Hiện recorder lưu SHA của toàn file WAV nhưng `evaluate_wav()` tính SHA riêng PCM.

Chuẩn hóa toàn bộ pipeline:

* `source_sha256` luôn là SHA-256 của **toàn bộ file WAV bytes**
* dùng `compute_file_sha256(path)`
* evaluator phải verify checksum trước khi chạy model
* reviewer phải verify checksum trước khi cho phép `accepted`
* SHA mismatch phải không được coi là MISS/CORRECT_REJECT
* trả về trạng thái integrity error rõ ràng, ví dụ `ERROR`
* ghi expected hash và actual hash vào result/error diagnostics
* backend không được chạy nếu checksum đã mismatch.

Thêm test chứng minh backend/transcribe không được gọi khi SHA mismatch.

## 3. P0/P1 — Không được âm thầm nuốt JSON/JSONL corruption

Hiện:

* `load_sessions()` catch mọi exception rồi return `[]`
* `load_labels()` bỏ qua malformed JSONL lines
* lần save tiếp theo có thể rewrite file và làm mất record lỗi.

Sửa thành fail-fast.

Yêu cầu:

* malformed `sessions.json` phải raise exception có path/context
* malformed JSONL phải báo file + line number
* không rewrite source file sau parse error
* giữ atomic replace khi write
* tuyệt đối không biến corrupted data thành empty dataset.

Có thể tạo exception riêng như `ChildStudyDataError` nếu hợp lý.

Thêm tests:

* corrupted sessions JSON -> raise, original bytes unchanged
* malformed labels line -> raise với line number, original bytes unchanged
* gọi `save_session/save_label` trên corrupted store không được xóa dữ liệu.

## 4. P0/P1 — Detect session/sample ID collision thay vì overwrite

Hiện:

* default session ID chỉ đến precision giây
* `save_session()` replace theo `session_id`
* `save_label()` replace nếu `sample_id` OR `source` match
* reuse `--session-id S01` có thể làm batch sau overwrite batch trước.

Thiết kế identity rõ ràng.

Tối thiểu:

* auto-generated session ID phải collision-resistant, ví dụ microseconds hoặc random suffix
* recorder phải check session ID đã tồn tại
* nếu user đưa `--session-id` đã tồn tại, không được silently overwrite
* hoặc implement explicit resume semantics đúng; không tự suy resume
* `save_label` phải detect conflict:

  * sample_id trùng nhưng source khác => error
  * source trùng nhưng sample_id khác => error
  * chỉ update khi đúng cùng identity
* không làm mất label cũ.

Thêm regression test cho hai sessions trùng ID và hai labels identity-conflict.

## 5. P1 — Offline clipping semantics phải tương đương runtime

Đối chiếu với `src/smart_hub/stt_wake.py::STTSession`.

Runtime hiện tăng clipping counter cho:

* clipped input frame
* clipped VAD/STT segment

và validation với `--expect-events` không PASS khi `clipped != 0`.

`evaluate_wav()` hiện chỉ tăng `clipped_frames` ở input frame; clipped segment bị bỏ qua mà không tăng counter và sample vẫn có thể được chấm success.

Refactor để semantics offline tương đương runtime.

Yêu cầu:

* track frame-level clipping
* track segment-level clipping
* có tổng clipping failures
* reset backend giống runtime ở frame clipping
* sample có clipping theo runtime validation không được coi `ACCURATE` hay `CORRECT_REJECT`
* report phải thể hiện rõ audio invalid/clipped
* không để clipped negative trở thành false “CORRECT_REJECT”.

Ưu tiên dùng shared helper nếu có thể để tránh runtime/evaluator drift trong tương lai, nhưng không over-engineer.

## 6. P1 — Fix Ctrl+C/SIGTERM exit status của recorder

`scripts/record_wake_samples.py` hiện catch `KeyboardInterrupt` trong `main()`, set manifest `interrupted`, nhưng không propagate; process có thể exit 0.

Sửa để:

* manifest vẫn được save với `status="interrupted"`
* captured clips vẫn còn nguyên
* child-study sync nếu phù hợp vẫn idempotent
* Ctrl+C trả exit code `130`
* SIGTERM không được báo thành successful completed capture
* output không in `[DONE]`
* automated caller phân biệt completed và interrupted.

Viết unit test bằng mock, không mở mic thật.

## 7. P1 — Bảo vệ held-out test split

Theo `docs/CHILD_VOICE_RECORDING_PLAN.md`, CV-05 dùng dev để so candidate và test chỉ được mở sau khi candidate đã chốt.

Hiện evaluator default `--split all`.

Đổi default thành:

`--split dev`

Không được mặc định đụng `test`.

Có thể giữ explicit `--split test` cho final evaluation.

Nếu giữ `--split all`, phải là lựa chọn explicit và report phải chỉ rõ đang bao gồm test; tốt hơn là tránh `all` cho candidate-selection workflow.

Không tự động đánh dấu candidate winner; chỉ xuất số liệu.

## 8. P1/P2 — Fix ERROR accounting để không tạo accuracy ảo

Hiện `ERROR` bị `continue` trước khi tăng positive/negative denominator.

Thiết kế metrics rõ ràng:

* `eligible_total`
* `processed_total`
* `error_total`

và per label:

* positive eligible/processed/error
* negative eligible/processed/error

Các accuracy/FRR/FAR phải nói rõ denominator.

Không bao giờ được có tình huống:

10 eligible samples
1 ERROR
9 correct
=> report chỉ hiện 9/9 và khiến người đọc tưởng bài đủ 100%.

Report nên hiển thị kiểu:

`9/10 eligible correct; 1 ERROR`

hoặc equivalent.

Nếu có processing/integrity ERROR, evaluation command mặc định nên trả non-zero sau khi vẫn lưu report, để automation biết run chưa hoàn chỉnh.

Nếu không có eligible sample, exit non-zero. Không exit 0.

## 9. P2 — Fix RTF semantics

Runtime tính RTF bằng:

`decode_seconds / decoded_segment_audio_seconds`

Evaluator hiện dùng full WAV duration.

Đổi result để lưu riêng ít nhất:

* `file_audio_seconds`
* `decoded_audio_seconds`
* `decode_seconds`
* `decode_rtf = decode_seconds / decoded_audio_seconds`

Không gọi `decode_seconds / 5-second-WAV` là cùng RTF với runtime.

Nếu muốn giữ throughput theo toàn WAV thì đặt tên khác rõ ràng, ví dụ `wall_decode_per_file_audio`, nhưng report chính phải dùng metric tương đương runtime.

Thêm test với fake backend/segments để chứng minh denominator đúng.

## 10. P2 — Validate label / expected_events contract

Plan hiện định nghĩa:

* positive => expected_events = 1
* negative => expected_events = 0

Không cho metadata mâu thuẫn.

Sửa validation để reject:

* invalid label
* positive + expected_events != 1
* negative + expected_events != 0
* expected_events < 0
* empty phrase khi recorder yêu cầu phrase
* invalid/non-finite/negative distance
* empty sample/session/speaker IDs.

`--preset` luôn là negative; nếu user đồng thời truyền option mâu thuẫn như `--label positive`, parser phải báo lỗi thay vì silently ignore.

Không để evaluator ghi `expected_events=2` nhưng status vẫn dựa vào hardcoded 1.

## 11. P2 — Reviewer phải thực sự bảo vệ accepted data

Trong `scripts/review_child_study.py`:

* verify WAV format: PCM, mono, 16-bit, 16 kHz
* verify file readable/full
* verify SHA trước review
* SHA mismatch phải block accept
* file missing/block format error phải không accepted
* reviewer không được tự động xác nhận danh tính người nói chỉ vì file tồn tại.

`--auto-accept` hiện set `speaker_confirmed=True` mà không nghe file. Hãy loại bỏ behavior này hoặc đổi semantics để auto mode chỉ làm technical QC và KHÔNG set speaker confirmation.

Một `accepted` benchmark sample phải có human confirmation.

Khi manual reviewer accept/reject/needs_review:

* lưu `reviewer`
* lưu `reviewed_at`
* update session summary/status tương ứng nếu có session record
* không để `sessions.json` mãi `reviewer="pending"` sau khi review xong.

Không lưu tên thật của trẻ; giữ ID pseudonymous hiện tại.

## 12. P2 — Persist speaker class riêng với speaker_id

Recorder biết `--speaker child|adult` nhưng labels chủ yếu dựa trên `speaker_id`.

Thêm field ổn định, ví dụ:

`speaker_label: "child" | "adult"`

hoặc tên tương đương.

Evaluator `--speaker child` phải filter field này, không dùng:

`speaker_id.startswith("child")`

Custom `--speaker-id kid_01` phải vẫn được filter đúng là child.

Giữ backward compatibility cho label cũ nếu có thể, nhưng fallback phải rõ ràng và không silently misclassify.

## 13. P2 — Reproducibility metadata trong evaluation result

Mỗi JSON report phải chứa snapshot đủ để chạy lại:

* git HEAD commit
* trạng thái worktree clean/dirty nếu lấy được local
* wake word
* aliases
* cooldown
* profiles thực sự chạy
* exact `WAKE_PROFILES` settings cho từng profile
* STT model bundle name
* model file hashes / pinned hashes từ `stt_assets.py`
* config path hoặc relevant config snapshot
* timestamp
* dataset selection/filter
* count accepted/skipped/errors.

Không cần upload gì.

Nếu không lấy được git metadata, ghi explicit `"unknown"`/error thay vì đoán.

## 14. P2 — Result files không được overwrite âm thầm

`save_evaluation_results()` hiện filename chỉ đến giây và `write_text()` overwrite.

Sửa:

* default filename collision-resistant, ví dụ timestamp microseconds
* default save dùng create-exclusive hoặc unique suffix
* explicit `--output` nếu tồn tại phải fail trừ khi có explicit `--force`
* tốt nhất JSON và Markdown dùng temp + atomic replace/create phù hợp
* không được làm mất baseline report trước đó.

Thêm overwrite-protection test.

## 15. P2 — Report phải dynamic theo profiles thực sự chạy

Không hardcode hai profile vào table.

Nếu chạy:

`--profile standard`

report chỉ hiện standard hoặc sensitive column là `N/A (not evaluated)`, tuyệt đối không hiện zero như một measurement.

Tương tự với sensitive.

Report phải:

* hiển thị ERROR count
* hiển thị denominator rõ
* hiển thị split
* hiển thị evaluation mode
* hiển thị integrity status
* escape transcript để không phá Markdown table (`|`, newline nếu cần)
* không tuyên bố PASS cho group chưa đủ số mẫu.

Các acceptance threshold theo plan phải áp dụng theo đúng condition/group, không gộp child/adult/quiet/noise rồi so với một threshold chung.

## 16. P2 — CLI source modes và exit codes

Trong `evaluate_child_study.py`:

* `--wav` và `--dir` phải mutually exclusive
* labels dataset là mode mặc định
* filters không được silently ignored trong mode khác
* zero eligible samples => non-zero
* malformed dataset => non-zero
* processing errors => save diagnostic report rồi non-zero
* usage error => argparse error.

Giữ script hoàn toàn offline.

## 17. P2 — Sync recorder phải idempotent và failure phải rõ

Hiện `sync_to_child_study()` swallow mọi Exception và chỉ print WARN.

Không để trạng thái:

* session đã save nhưng chỉ một phần labels save
* sync fail nhưng recorder báo success hoàn toàn.

Thiết kế sync idempotent.

Có thể:

* validate toàn batch trước
* detect collisions trước khi write
* batch-save labels/session
* nếu sync lỗi, WAV/manifest vẫn được bảo toàn nhưng process phải báo non-zero hoặc một explicit incomplete state.

Không được xóa WAV vì lỗi metadata sync.

## 18. Tests bắt buộc

Mở rộng `tests/test_mock_child_study.py` hoặc chia file hợp lý.

Phải có regression test cho tối thiểu:

1. pending label không đi vào official evaluation
2. rejected label không đi vào official evaluation
3. `speaker_confirmed=False` không eligible
4. accepted + confirmed + correct SHA eligible
5. SHA mismatch => ERROR trước khi backend chạy
6. corrupt `sessions.json` không bị biến thành []
7. corrupt `labels.jsonl` báo đúng line và không mất dữ liệu
8. duplicate session ID bị reject
9. conflicting sample_id/source bị reject
10. recorder Ctrl+C => manifest interrupted + exit 130
11. clipped frame không success
12. clipped segment không success
13. ERROR không biến 9/10 thành 9/9
14. RTF denominator dùng decoded segment duration
15. positive/negative expected_events mismatch bị reject
16. `--wav` + `--dir` parser error
17. no eligible samples => non-zero
18. default split không expose test
19. custom child speaker ID vẫn filter đúng bằng speaker label
20. single-profile report không tạo số đo giả cho profile chưa chạy
21. output file existing không bị overwrite
22. review SHA mismatch không thể accepted
23. session reviewer/status được update sau manual review
24. sync failure không bị coi là hoàn thành sạch.

Các test logic phải chạy mà không cần microphone.

## 19. Optional STT tests phải giữ convention hiện tại

`tests/test_child_study.py` hiện chạy real model trực tiếp nhưng không guard dependency/model.

Repo đã có pattern:

`HAVE_STT`
+
`@unittest.skipUnless(HAVE_STT, ...)`

trong `tests/test_stt.py`.

Áp dụng pattern tương tự cho test cần Sherpa/model thật.

Các unit tests về filtering, metrics, checksum, clipping, report phải dùng fake/mock backend và chạy được không cần model.

Không biến `python3 scripts/run_tests.py --mock` thành phụ thuộc Sherpa/numpy/model STT ngoài dependency hiện tại của mock suite.

## 20. Regression / validation commands

Sau khi sửa, chạy ít nhất:

```bash
git diff --check
python3 -m compileall -q src scripts tests
python3 scripts/run_tests.py --mock
```

Nếu `.venv` và dependencies repo đã có:

```bash
.venv/bin/python scripts/run_tests.py
```

Nếu `sherpa_onnx` + STT models đã cài sẵn:

```bash
.venv/bin/python scripts/run_tests.py --stt
```

Không download dependency/model trong task này.

Nếu real STT tests không chạy được vì dependency/model không tồn tại, ghi rõ là SKIPPED/NOT RUN; không tuyên bố đã pass.

Không chạy test cần microphone thật.

## 21. Documentation

Cập nhật `docs/CHILD_VOICE_RECORDING_PLAN.md` và README chỉ ở chỗ implementation mới làm thay đổi cách dùng.

Tài liệu phải nói rõ:

* official evaluation chỉ dùng accepted/confirmed/SHA-matched labels
* dev là mặc định cho candidate comparison
* test chỉ chạy explicit sau khi candidate đã chốt
* ad-hoc WAV/manifest không phải acceptance dataset
* Ctrl+C recorder là interrupted/non-zero
* cách review trước evaluate
* cách đọc ERROR/eligible/processed denominator
* RTF offline là decode RTF, không phải live latency.

Kiểm tra lại mô tả “khoảng 7 giây chuẩn bị” so với implementation thực tế; code và docs phải thống nhất.

## 22. Không làm ngoài phạm vi

Không:

* đổi wake word
* thay threshold/profile values chỉ để làm test pass
* thêm cloud service
* upload audio
* fine-tune model
* thay model
* thêm database/framework nặng
* tự động chọn standard/sensitive dựa trên test set
* xóa backward-compatible public CLI nếu không cần thiết.

Ưu tiên patch nhỏ, rõ, testable.

## 23. Deliverable cuối cùng

Sau khi hoàn tất, trả về:

1. summary ngắn các lỗi đã sửa
2. danh sách files thay đổi
3. giải thích data-integrity rules sau patch
4. exact test commands đã chạy
5. số test pass/skip/fail
6. những test không thể chạy và lý do
7. `git diff --stat`
8. `git status --short`
9. SHA của follow-up commit mới.

Commit message đề xuất:

`Fix child-study data integrity and evaluation correctness`

Không amend `79a4d9c`. Tạo commit mới trên branch hiện tại.
