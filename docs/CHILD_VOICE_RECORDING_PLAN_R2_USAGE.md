# Child Voice Study — R2 usage

Tài liệu này ghi lại cú pháp CLI đúng của implementation R2 trên branch `codex/voice-assistant-plan-r2-implementation`.

## Quy trình

`record -> technical QC -> manual review -> offline evaluation -> report`

## Technical QC

Chọn rõ session hoặc thư mục cần kiểm tra:

```bash
.venv/bin/python scripts/review_child_study.py review \
  --session-id S01 \
  --auto-qc \
  --reviewer QC
```

hoặc:

```bash
.venv/bin/python scripts/review_child_study.py review \
  --dir recordings/20260917-... \
  --auto-qc \
  --reviewer QC
```

`--auto-accept` vẫn là alias tương thích của `--auto-qc`, nhưng **không** xác nhận người nói và không tạo `accepted`.

Nếu sample thiếu `source_sha256`, sai SHA, sai WAV format hoặc không đọc được file, technical QC không được coi là pass.

## Manual review

```bash
.venv/bin/python scripts/review_child_study.py review \
  --session-id S01 \
  --reviewer Hoan
```

Action `accept` chỉ được phép khi:

- có checksum gốc `source_sha256`;
- file hiện tại khớp checksum;
- WAV đúng PCM 16 kHz mono signed 16-bit và đọc đầy đủ;
- reviewer đã nghe/xác nhận người nói và nội dung.

Thiếu checksum không được tự động backfill trong review.

## Summary

```bash
.venv/bin/python scripts/review_child_study.py summary
```

Không truyền subcommand cũng chỉ hiển thị summary; không phải manual review.

## Official evaluation

Mặc định đánh giá `dev`:

```bash
.venv/bin/python scripts/evaluate_child_study.py
```

Test split phải explicit:

```bash
.venv/bin/python scripts/evaluate_child_study.py --split test
```

R2 tách hai khái niệm:

1. **Benchmark selection**: split/filter + `accepted` + `speaker_confirmed` + metadata label hợp lệ.
2. **Runtime/data integrity**: file tồn tại, checksum, WAV format, clipping, VAD/STT processing.

Sample đã thuộc benchmark không bị loại chỉ vì WAV bị mất hoặc SHA mismatch. Các lỗi đó thành `ERROR` và vẫn nằm trong denominator.

Ví dụ:

```text
10 accepted + confirmed
9 xử lý thành công
1 SHA mismatch

=> eligible_total = 10
=> errors = 1
=> nếu 9 positive còn lại accurate thì 9/10 = 90%
=> report vẫn được lưu
=> evaluator exit non-zero
```

## Ad-hoc evaluation

```bash
.venv/bin/python scripts/evaluate_child_study.py \
  --allow-unreviewed \
  --wav recordings/path/to/take-01.wav \
  --label positive
```

hoặc:

```bash
.venv/bin/python scripts/evaluate_child_study.py \
  --allow-unreviewed \
  --dir recordings/20260917-...
```

Kết quả ad-hoc không phải official acceptance benchmark.

## Reproducibility

Report R2 lấy trực tiếp model provenance từ `src/smart_hub/stt_assets.py`:

- `BUNDLE`
- `ARCHIVE_SHA256`
- digest của từng file trong `FILES`

Không hardcode tên model khác với model thực tế của repo.

## Recorder failure semantics

- Ctrl+C/SIGTERM giữa capture: `interrupted`, exit 130.
- Capture/audio failure: `interrupted`, exit 1.
- Capture hoàn tất nhưng sync child-study metadata lỗi: `sync_failed`, exit 1.
- Capture + sync hoàn tất: `captured_pending_review`, exit 0.

`sync_failed` không được đổi thành `interrupted`; các WAV đã thu vẫn được giữ lại.

## Report semantics

Bảng aggregate không được dùng để suy overall PASS. Acceptance phải đối chiếu theo từng speaker/condition/split. `ERROR` và `CLIPPED` luôn được hiển thị và làm giảm tỷ lệ thành công tương ứng.
