# Thử nghiệm bộ điều phối lượt — Phase 1B

Mã ở đây chỉ phục vụ quyết định kiến trúc, không được import bởi `assistant`.
Chỉ đọc PCM tổng hợp/fixture đã có; không mở microphone hoặc loa.
Môi trường Pipecat riêng nằm trong `.experiments/phase1b/`, được Git bỏ qua.

Kết quả và quyền sở hữu lifecycle được ghi trong
[`docs/decisions/conversation-orchestration.md`](../../docs/decisions/conversation-orchestration.md).

Chạy từ thư mục project:

```bash
.venv/bin/python experiments/orchestration/test_probe.py
.venv/bin/python experiments/orchestration/probe.py --owner local --idle-seconds 5
.experiments/phase1b/bin/python experiments/orchestration/test_probe.py --pipecat
.venv/bin/python experiments/orchestration/compare.py --output /tmp/phase1b-new.json
```

`compare.py` chạy tuần tự ba lượt mỗi phương án, lưu JSON mới và không ghi
đè file đã có. `local_probe.py` dùng graph Silero hiện có và giữ phần dư
320 → 512 sample. `pipecat_probe.py` dùng đúng controller của Pipecat
1.10.0 với strategy explicit; cả hai đi qua cùng bridge và hợp đồng test.
Các lệnh probe/test chặn kết nối mạng bằng Python audit hook. Trong môi
trường có sandbox chặn cả socket nội bộ asyncio, chạy từ terminal host.

Tái tạo môi trường Pipecat đã khóa là thao tác **có tải package**, riêng với
các probe offline ở trên:

```bash
.venv/bin/python experiments/orchestration/prepare_pipecat.py \
  --manifest docs/benchmarks/orchestration-2026-09-15.json
```

Script chỉ cài trong `.experiments/phase1b`, kiểm tra checksum 58 archive
đã ghi nhận (135,65 MiB tải, khoảng 530 MiB site-packages), gồm 56 runtime
package và 2 công cụ build `docopt`. `.venv` của ứng dụng không bị cập nhật.
Model thiếu phải cài bằng quy trình model của project; probe không tải model.
