# Tình trạng giấy phép

Mã nguồn project chưa có giấy phép phân phối được chủ repo lựa chọn. Việc
repo có thể xem trên GitHub không tự gán MIT/Apache-2.0 cho toàn bộ project.
Chưa thêm top-level LICENSE để tránh tự quyết định quyền đối với mã của chủ
repo. Cần chốt giấy phép riêng trước khi công bố project dưới một giấy phép
mã nguồn mở cụ thể.

| Thành phần | Thông tin từ nguồn cung cấp |
| --- | --- |
| EfficientWord-Net | Apache-2.0; bản sao tại `models/EFFICIENTWORDNET-LICENSE.md` |
| VieNeu TTS dùng để tạo WAV | Apache-2.0; bản sao tại `assets/VIENEU-LICENSE.txt`, provenance trong assets và fixtures |
| sherpa-onnx / core | [Apache-2.0 theo repo upstream](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/LICENSE) |
| Zipformer tiếng Việt 30M | [Model card của hynt](https://huggingface.co/hynt/Zipformer-30M-RNNT-6000h) ghi CC BY-NC-ND 4.0 |
| Silero VAD | [Nguồn Silero VAD và LICENSE](https://github.com/snakers4/silero-vad/blob/master/LICENSE) |

Model STT/VAD tải từ release `asr-models` của k2-fsa/sherpa-onnx, được khóa
checksum tại `src/smart_hub/stt_assets.py`. Model và mẫu giọng cá nhân không
đưa vào Git. Các WAV trong `tests/fixtures/stt` là audio tổng hợp local bằng
VieNeu, không có bản ghi microphone thật; file provenance ghi preset, text,
revision model, seed và checksum. Giấy phép của model không thay thế giấy
phép của code hoặc mặc nhiên áp dụng cho cả repo.
