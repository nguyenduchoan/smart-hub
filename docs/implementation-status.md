# Tiến độ triển khai toàn bộ kế hoạch

Cập nhật **2026-09-17**. Mục tiêu vẫn là triển khai toàn bộ
[SMART_HUB_PLAN.md](SMART_HUB_PLAN.md), không thu hẹp thành chỉ Phase 2.
Theo yêu cầu mới nhất của người dùng: **hoàn tất phần đang làm rồi tạm
dừng**. Đã hoàn tất phần mềm/kiểm thử tự động Phase 2; tạm dừng tại đây và
chờ người dùng tiếp tục. Chưa bắt đầu triển khai Phase 3.

| Giai đoạn | Trạng thái và bằng chứng |
| --- | --- |
| Phase 0–1 | Wake/đáp và runtime cơ bản đã có; giữ hồi quy. Kiểm tra nhiều người/tiếng nền dài hạn còn chờ |
| Phase 1B | Đã chọn controller local; [quyết định](decisions/conversation-orchestration.md) |
| Phase 2 | Đã tích hợp `--capture-only`, controller/VAD, bounded PCM, deadline, debug WAV opt-in; [kiểm chứng](turn-capture.md) |
| Nghiệm thu Phase 2 | Chưa có 10 câu mới qua mic/loa và đối chiếu âm tiết; không đánh dấu đạt bằng test tổng hợp |
| Phase 3 | Chưa triển khai; tiếp theo là benchmark STT/TTS local và nối luồng một lượt |
| Phase 4 | Chưa triển khai hội thoại nhiều lượt với LLM thật |
| Phase 5 | Chưa triển khai router/tool và Home Assistant |
| Phase 6 | Chưa triển khai AEC/nói chen; tùy chọn, cần nghiệm thu riêng |
| Phase 7 | Chưa triển khai Codex/MCP/ứng dụng |
| Phase 8 | Chưa triển khai service 24/7 và phục hồi lỗi |
| Phase 9 | Chưa nghiệm thu/soak/phát hành |

Branch hiện tại: `codex/voice-assistant-plan`. Có các thay đổi từ giai đoạn
trước chưa commit trong cùng worktree; đã giữ lại, không reset/ghi đè chúng.
Không commit/push hoặc triển khai service trong checkpoint Phase 2.

Kiểm tra toàn bộ ngày 2026-09-17: **138/138 PASS**, không skip, 47,249 s.
Lệnh: `.venv/bin/python scripts/run_tests.py`. Không bật mic/loa thật, không
đổi gain audio, không tải thêm model/dependency trong Phase 2.

Bộ mock chạy riêng bằng Python hệ thống: **77/77 PASS**, 11,963 s
(`python3 scripts/run_tests.py --mock`). Kiểm tra dependency, liên kết tài
liệu và `git diff --check` đều đạt.
