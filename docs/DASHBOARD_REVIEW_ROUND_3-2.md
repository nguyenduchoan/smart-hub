# Dashboard Review Round 3.2 — 2026-09-20

**Mục tiêu: micro-fix cuối để đóng review software của branch dashboard. Không mở thêm refactor lớn.**

## Phạm vi

Branch: codex/local-dashboard-broadlink

HEAD đã review:

    549e82a9e69dac52c681e3f6b6057b2a1a237a4d
    fix(dashboard): close review round 3.1 gaps

Không amend/rewrite commit 549e82a. Tạo một follow-up commit nhỏ chỉ để đóng các finding dưới đây.

---

# Trạng thái đã đạt

Giữ nguyên các behavior đang đúng:

- manual advance dùng take_sequence == current_take;
- request invalid -> 422; stale/wrong session -> 409;
- DTW missing/corrupt/SHA mismatch/truncated/feature invalid/similarity error -> sample ERROR;
- DTW ERROR giữ denominator;
- Markdown DTW detail dùng N/A cho decode metrics không áp dụng;
- quarantine flags persist/load/upsert đúng;
- lease timeout giữ INTERRUPTED;
- strict same-origin scheme/host/effective port;
- atomic command claim + route-level single provider dispatch;
- generic_fake bị chặn ở real mode;
- AC-28 được ghi partial / interface foundation;
- subprocess DTW ERROR trả envelope ok + completed_with_errors;
- dashboard recording test đã redirect child-study sync sang temp root.

Không regression các behavior trên.

---

# V3.2-01 — P2: session_id collision check vẫn đọc child-study dataset mặc định

## Hiện trạng

File: src/smart_hub/recording/service.py

RecordingService đã hỗ trợ child_study_dir injectable và _sync_child_study() ghi đúng vào self.child_study_dir.

Nhưng trong start_session(), khi caller truyền session_cfg.session_id, code vẫn làm:

    existing = load_sessions()

nên đọc global ROOT/recordings/child-study/sessions.json thay vì dataset mà service sẽ sync vào.

## Tác động

- Duplicate session_id trong temp/custom dataset có thể không bị phát hiện.
- Session ID chỉ tồn tại ở production dataset có thể chặn nhầm custom isolated dataset.
- Dependency injection chưa dùng cùng một source of truth từ validation đến persistence.

## Yêu cầu sửa

Khi kiểm collision phải đọc đúng target dataset, ví dụ:

    sessions_file = self.child_study_dir / "sessions.json"
    existing = load_sessions(sessions_file=sessions_file)

Không dùng global SESSIONS_FILE trong RecordingService nếu child_study_dir đã injectable.

Semantics bắt buộc:

- duplicate trong target child_study_dir -> fail trước tạo session directory, thread hoặc capture;
- không duplicate trong target child_study_dir -> được phép;
- collision ở dataset khác không được ảnh hưởng custom isolated dataset.

## Regression tests bắt buộc

1. Temp child_study_dir có S_DUP_TEMP; start_session(session_id=S_DUP_TEMP) phải raise ValueError.
2. Fail xảy ra trước worker/capture và không tạo session directory mới.
3. Session ID chỉ tồn tại ở dataset khác không được chặn custom dataset.
4. Patch smart_hub.recording.service.load_sessions và assert sessions_file chính xác bằng service.child_study_dir / sessions.json.

---

# V3.2-02 — P3/Test gap: parent evaluator test chưa đi qua WakeEvaluator.run_evaluation()

## Hiện trạng

Test subprocess worker hiện đã chứng minh:

- worker.py returncode == 0;
- envelope.status == ok;
- eval_data.status == completed_with_errors;
- has_processing_errors == true;
- denominator và errors đúng.

Nhưng phần parent evaluator hiện gọi trực tiếp evaluator.registry.save_evaluation(...), nên chỉ chứng minh registry lưu được dữ liệu đã dựng sẵn.

Chưa chứng minh chuỗi thật:

    WakeEvaluator.run_evaluation()
      -> build worker payload
      -> launch subprocess
      -> parse envelope
      -> receive completed_with_errors
      -> format Markdown
      -> save registry
      -> return response

## Yêu cầu

Thêm integration test gọi WakeEvaluator.run_evaluation() thật.

Test setup:

1. Temp root / temp registry.
2. DTW candidate/artifact thật.
3. Dataset dev có 1 accepted valid sample và 1 accepted sample bị integrity error, ví dụ missing WAV.
4. Dùng audio_python trỏ tới interpreter có numpy.
5. Gọi evaluator.run_evaluation(candidate_ids=[...], split="dev", mode="official", name="...").
6. Không gọi registry.save_evaluation() thủ công trong test.

## Assertions bắt buộc

Result từ run_evaluation():

- status == completed_with_errors;
- has_processing_errors == true;
- evaluation id tồn tại;
- eligible_total giữ nguyên;
- candidate errors == 1.

Registry sau call:

- get_evaluation(evaluation_id) tồn tại;
- status == completed_with_errors;
- has_processing_errors == true;
- report_markdown tồn tại;
- report chứa error sample;
- report chứa N/A cho DTW decode metrics;
- results_json giữ errors và eligible denominator.

Có thể patch input data source của evaluator một cách hẹp nếu cần, nhưng vẫn phải để WakeEvaluator.run_evaluation() tự launch worker, parse response, format report và save registry.

Không bypass parent evaluator bằng save_evaluation thủ công.

---

# V3.2-03 — P3 non-blocking: WAV fixture của dashboard API vẫn nằm dưới ROOT/recordings

tests/test_dashboard_api.py vẫn tạo WAV fixture dưới ROOT/recordings/_test_dashboard_tmp rồi xóa trong tearDown.

Child-study metadata đã được redirect sang temp root nên rủi ro contamination chính đã được xử lý.

Nếu patch nhỏ, chuyển WAV fixture sang TemporaryDirectory. Nếu việc này kéo scope lớn do source resolver cần ROOT-relative path thì có thể defer và ghi rõ trong final report.

Không refactor lớn chỉ để đóng V3.2-03.

---

# Files dự kiến

Tối thiểu:

    src/smart_hub/recording/service.py
    tests/test_recording_service.py
    tests/test_wake_lab.py

Có thể thêm tests/test_dashboard_api.py nếu đóng V3.2-03.

Không cần sửa provider, DTW worker, child_study formatter hoặc storage nếu không có regression mới.

---

# Validation bắt buộc

Chạy:

    git diff --check
    python3 -m compileall -q src scripts tests

Targeted:

    .venv-dashboard/bin/python -m unittest discover -s tests -p 'test_recording_service.py' -v
    .venv/bin/python -m unittest discover -s tests -p 'test_wake_lab.py' -v

Nếu sửa dashboard API test:

    .venv-dashboard/bin/python -m unittest discover -s tests -p 'test_dashboard_api.py' -v

Sau đó:

    .venv/bin/python scripts/run_tests.py --mock

Không cài/download dependency mới.

---

# Acceptance checklist R3.2

- [ ] RecordingService explicit session_id collision check dùng đúng self.child_study_dir.
- [ ] Duplicate trong injected temp dataset bị chặn trước worker/capture.
- [ ] Session ID chỉ tồn tại ở dataset khác không làm custom dataset bị chặn sai.
- [ ] Test khóa exact sessions_file path truyền vào load_sessions.
- [ ] WakeEvaluator integration test gọi run_evaluation() thật.
- [ ] Parent evaluator tự launch worker subprocess.
- [ ] Parent evaluator tự save completed_with_errors vào registry.
- [ ] Reload evaluation giữ has_processing_errors/errors/eligible/report.
- [ ] Test không gọi registry.save_evaluation() thủ công để giả lập parent path.
- [ ] Không regression V3/V3.1.
- [ ] git diff --check sạch.
- [ ] compileall sạch.
- [ ] targeted suites pass.
- [ ] mock suite pass.
- [ ] không hardware/network side effect thật.
- [ ] không ghi child-study production metadata.

---

# Commit yêu cầu

Tạo một follow-up commit mới trên codex/local-dashboard-broadlink.

Không amend 549e82a9e69dac52c681e3f6b6057b2a1a237a4d.

Commit message đề xuất:

    fix(dashboard): close review round 3.2 test gaps

---

# Final report agent phải trả

1. Commit SHA mới.
2. Files changed.
3. Exact change cho session collision lookup.
4. Test chứng minh injected child_study_dir là source of truth.
5. Test chứng minh cross-dataset session ID không bị chặn sai.
6. Cách WakeEvaluator.run_evaluation() integration test được dựng.
7. Bằng chứng evaluator tự launch worker và save registry.
8. Status/errors/eligible/report sau reload.
9. V3.2-03 đã đóng hay deferred.
10. Exact commands đã chạy.
11. Pass/fail/skip counts.
12. git diff --check result.
13. git diff --stat.
14. git status --short.

Nếu test fail, sửa nguyên nhân rồi rerun. Không bypass parent evaluator bằng save_evaluation thủ công.