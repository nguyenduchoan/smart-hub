# Tiếng xác nhận wake word

Mặc định `em_nghe_vieneu.wav`: “Em nghe.”. Có thể chọn `em_day_vieneu.wav`: “Em đây.”.
Các file được tạo bằng VieNeu-TTS v3 Nano, giọng preset Trúc Ly, local trên CPU.
Đây là giọng tổng hợp. Không clone giọng từ microphone của người dùng.

Nguồn model: [VieNeu-TTS-v3-Nano](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Nano),
revision `aba295eb96a6fa6003ebe417cc1f2802a7adc1dc`, model card ghi Apache-2.0.
Thuật toán inference và preset của Phạm Nguyễn Ngọc Bảo:
[VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS/tree/3206ed960e317e69bfe09f9d553aecbf1090f32e).
Xem `VIENEU-LICENSE.txt` và `vieneu-provenance.json` để tái hiện bản tạo.
Adapter của project chỉ hỗ trợ hai câu cố định, giảm biên độ trước khi xuất PCM,
thêm fade 10 ms và đệm im lặng 150 ms mỗi đầu. Không chạy TTS khi nghe wake word.

WAV PCM mono 16-bit/24 kHz; đỉnh −6,02 dBFS, clipping 0%. Người dùng đã nghe
`em_nghe_vieneu.wav`, xác nhận rõ/tự nhiên hơn và chọn giọng này làm mặc định.
File eSpeak cũ `em_nghe.wav` và `em_day.wav` được giữ để so sánh; người dùng đã
đánh giá giọng cũ máy móc/rè.

```bash
.venv/bin/python scripts/smart_hub.py test-feedback
```

Đổi `feedback_path` trong `config.json` để chọn file khác, hoặc đặt
`feedback_enabled: false` để chỉ ghi event.
