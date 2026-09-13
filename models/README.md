# Mẫu wake word cá nhân

Mặc định dùng `efficientwordnet-int8.onnx` (backbone Apache-2.0, tải bằng
`scripts/download_model.py`), `maika_oi_neural.npz` (5 nhóm embedding của lời
“Maika ơi” người dùng đã nói) và `maika_oi_negative.npz` (143 embedding âm tính).
File `maika_oi.npz` chứa MFCC cho baseline DTW cũ, không phải model mặc định.

Đây không phải model của OLLI hoặc model đã được huấn luyện chuyên biệt để nhận
“Maika ơi” cho mọi người. Ba lượt lời nói mới gần nhất đều có đúng một event,
nhưng chưa đủ bằng chứng hoạt động đa giọng/đa môi trường hoặc trong nhiều giờ.

Đặc trưng giọng nói cũng là dữ liệu cá nhân. File `.npz` được bỏ qua trong Git.
Chỉ dùng file model do bạn tạo hoặc từ nguồn tin cậy. Khi đổi cụm từ hoặc
microphone, tạo cấu hình và bộ mẫu mới; chỉ đổi nhãn không tạo ra model mới.

Enrollment không giữ WAV microphone. Code tải bằng `allow_pickle=False`, kiểm
tra nhãn, phiên bản và checksum backbone. Mẫu thiếu/sai nhãn gây lỗi rõ ràng.
Model TTS tùy chọn nằm riêng trong `.voice-tools/vieneu-nano`; listener không
load model TTS. License và nguồn của WAV nằm ở thư mục `assets`.
