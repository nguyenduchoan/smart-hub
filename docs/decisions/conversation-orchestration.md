# Phase 1B — Chọn bộ điều phối lượt hội thoại

Ngày quyết định: **2026-09-15**. Trạng thái: **đã chọn local**, đủ điều kiện
bắt đầu triển khai Phase 2. Người dùng yêu cầu tạm bỏ qua thu thập thêm
thông tin và làm bước tiếp theo. Phần thu mẫu từng người được để lại sau;
checkpoint này dùng model và WAV tổng hợp sẵn có.

## Quyết định

Dùng **một bộ điều phối lượt của Smart Hub với Silero ONNX local**. Giữ
`AudioPump` làm chủ nguồn PCM và `SerialWorker` xử lý công việc blocking.
Pipecat được hoãn. Mã thử nghiệm ở `experiments/orchestration/`; ứng dụng
`assistant` chưa import mã này. Phase 2 sẽ triển khai thu lượt hội thoại
theo quyết định này, gồm buffer, deadline và chế độ `--capture-only`.

Lý do chọn local là tái sử dụng được model/dependency đã có, đáp ứng các
hợp đồng đã thử với cầu nối nhỏ. Pipecat cũng vượt qua cùng kiểm chứng,
nhưng cần đổi hai core pin và thêm nhiều package trước khi mang lại lợi ích
cho luồng một máy, nghe/phát luân phiên hiện tại. Đây là quyết định cho
checkpoint này, không phải kết luận Pipecat thiếu khả năng hội thoại.

## Phạm vi thử nghiệm và tiêu chí chấp nhận

- Cùng PCM mono S16_LE, 16 kHz, frame 320 sample/20 ms và model Silero
  `models/stt/silero_vad.onnx` đã khóa SHA-256 trong `stt_assets.py`.
- Bản sao model giữ `h/c` riêng. Ghép frame thành block 512 sample, giữ
  phần dư; không padding từng frame. Reset state/phần dư khi gián đoạn.
- Một chính sách VAD chung: threshold 0,5; xác nhận giọng 8 block = 0,256 s;
  im lặng kết đoạn 22 block = 0,704 s. Đây là thông số của spike, chưa phải
  nghiệm thu thông số Phase 2 hoặc cấu hình wake mới.
- Mỗi câu mẫu có đúng một `start` và một `end`; im lặng không tạo lượt.
- Audio khi playback giả lập bị bỏ trước VAD; mất audio hủy bằng chứng của
  lượt cũ. Queue của `AudioPump` có giới hạn, cầu nối không xếp thêm PCM.
- Hủy/generation đổi/overflow phải loại kết quả worker cũ; timeout không
  cho chạy tiếp các bước STT/LLM/TTS giả lập. Shutdown không còn task/worker.
- Không dùng mic/loa thật, không ghi audio mới, không gọi provider thật.
  Python audit hook chặn DNS, `socket.connect` và `socket.bind` trong probe.
  Model thiếu hoặc sai checksum gây lỗi offline; không tự tải model.

## Pipecat được thử như thế nào

Đã thử **`pipecat-ai==1.10.0`**, hỗ trợ Python >=3.11. Môi trường riêng
`.experiments/phase1b/` được Git bỏ qua, tách khỏi `.venv` của ứng dụng.
Archive tải từ PyPI có URL/checksum được lưu trong báo cáo. Không chọn extra
transport, Whisper, Smart Turn, GPU hoặc giao diện.

Cầu nối gọi trực tiếp `UserTurnController.process_frame`, truyền
`InputAudioRawFrame` và các cạnh VAD từ cùng graph Silero. Chỉ cấu hình:

```python
UserTurnStrategies(
    start=[VADUserTurnStartStrategy(enable_interruptions=False)],
    stop=[SpeechTimeoutUserTurnStopStrategy(
        user_speech_timeout=0.0,
        wait_for_transcript=False,
    )],
)
```

VAD chung đã xác nhận 0,704 s im lặng. Với Pipecat 1.10.0,
`user_speech_timeout` là thời gian chờ **thêm sau** cạnh VAD, nên đặt 0 để
không cộng hai lần. Probe đầu tiên dùng 0,704 ở cả hai chỗ đã không kết
lượt trước khi fixture kế tiếp reset; đã sửa theo source của đúng release
và chạy lại cả fixture/test. Không để strategy mặc định tự chọn
`LocalSmartTurnAnalyzerV3`; không phụ thuộc transcript để kết lượt.

Đây là cầu nối tối thiểu vào controller, chưa tích hợp `PipelineWorker`,
transport hay các service Pipecat. Vì vậy số đo là chi phí của đường đã
thử, không đại diện toàn bộ framework khi chạy hội thoại thực tế.

Nguồn đối chiếu:
[metadata release](https://pypi.org/pypi/pipecat-ai/1.10.0/json),
[turn strategies](https://github.com/pipecat-ai/pipecat/blob/v1.10.0/src/pipecat/turns/user_turn_strategies.py),
[speech timeout](https://github.com/pipecat-ai/pipecat/blob/v1.10.0/src/pipecat/turns/user_stop/speech_timeout_user_turn_stop_strategy.py),
[controller](https://github.com/pipecat-ai/pipecat/blob/v1.10.0/src/pipecat/turns/user_turn_controller.py).

## Dependency và dung lượng

| Hạng mục | Local | Pipecat trong môi trường riêng |
| --- | --- | --- |
| Package cần cài thêm vào ứng dụng | 0 | Không cài vào ứng dụng |
| Package trong môi trường thử Pipecat trống | Không tạo môi trường mới | 56 runtime + 2 công cụ build |
| Archive tải xuống | 0 | 135,65 MiB, gồm công cụ build |
| Dung lượng file trong site-packages | Dùng lại `.venv` | 530,00 MiB, gồm core package và công cụ build |
| NumPy | 2.2.6 | 2.4.6 |
| ONNX Runtime | 1.23.2 | 1.24.4; Pipecat yêu cầu `~=1.24.3` |
| protobuf | 7.36.1 | 6.33.6; Pipecat yêu cầu `>=5.29.6,<7` |

Các nhóm phụ thuộc gồm xử lý audio (`resampy`, `soundfile`, `soxr`,
`loudness`), NumPy/Numba/llvmlite, HTTP/async, Pydantic, NLTK và OpenAI SDK.
Các SDK được cài theo dependency của Pipecat nhưng không được gọi tới
provider. `docopt==0.6.2` chỉ có source distribution phù hợp; đã build riêng
bằng setuptools/wheel trong môi trường thử, sau khi xác minh archive.
Không cài PyTorch, CUDA hay PyAudio.

Danh sách đầy đủ 58 archive, phiên bản, URL và SHA-256 nằm trong trường
`dependencies.archives` của [báo cáo JSON](../benchmarks/orchestration-2026-09-15.json).
Không lấy dung lượng cả môi trường riêng làm số tăng RAM của ứng dụng.
Hai xung đột pin trên khiến không thể thêm release này mà giữ nguyên toàn
bộ `requirements.txt`; quyết định local tránh migration đó ở Phase 2.

## Số đo

Ba lượt cho mỗi phương án, xen kẽ local/Pipecat, mỗi lượt tạo một tiến
trình Python mới. Mỗi lượt chạy 5 s PCM im lặng qua `AudioPump`/VAD,
sau đó 10 WAV tiếng Việt tổng hợp từ `tests/fixtures/commands/`.
Không chạy đồng thời bộ hồi quy trong lúc đo.

| Trung vị của 3 lượt | Local | Pipecat |
| --- | ---: | ---: |
| Startup từ đầu script tới owner/VAD sẵn sàng | 0,111 s | 0,238 s |
| RSS đỉnh tiến trình | 62,38 MiB | 76,88 MiB |
| CPU trong 5 s im lặng, tính theo một core | 6,34% | 6,84% |
| Độ trễ lịch event loop p95, heartbeat 5 ms | 0,94 ms | 0,97 ms |
| Trung vị độ trễ lớn nhất mỗi lượt | 5,74 ms | 1,28 ms |
| Mỗi lượt: một capture mở/đóng, queue đỉnh, mất frame | 1/1; 1 frame; 0 mất | 1/1; 1 frame; 0 mất |
| Câu mẫu tạo đúng một lượt, mỗi lần chạy | 10/10 | 10/10 |
| Task/worker còn lại khi đóng | 0 | 0 |

CPU bao gồm heartbeat và cầu nối của probe. NumPy/ONNX Runtime khác phiên
bản giữa hai môi trường, nên không quy toàn bộ chênh lệch cho controller.
Độ trễ lớn nhất local dao động hơn trong lượt đo ngắn; không kết luận local
nhanh hơn ở mọi khía cạnh. Đây không phải CPU idle của toàn bộ `assistant`,
độ trễ từ miệng tới loa, kiểm tra tiếng TV hoặc benchmark chạy 24/7.
Số đo từng lượt và hash mã probe nằm trong báo cáo JSON.

## Quyền sở hữu khi triển khai Phase 2

| Thành phần | Chủ sở hữu |
| --- | --- |
| Capture, format/offset PCM, queue và mất frame | `AudioPump` của Smart Hub |
| Hoạt động giọng và thời gian im lặng | Một VAD local, một bộ thông số cho lượt đang nghe |
| Bắt đầu/kết thúc/hủy lượt, deadline không nói và giới hạn độ dài | Một turn controller local của Smart Hub |
| Phiên, wake gating, chuyển trạng thái và chính sách tool | Runtime Smart Hub |
| Công việc STT/LLM/TTS và kết quả muộn | Worker/provider theo cùng session/generation/turn context |
| PCM playback, tiếng vọng và sổ theo dõi phần đã phát | Playback/runtime Smart Hub |

Runtime được phép phản ánh trạng thái lượt, nhưng không thêm một đồng hồ
kết câu độc lập. STT của Phase 2/3 nhận lượt PCM đã chốt, không chạy thêm VAD
lệnh để chốt lần nữa. `--wake-only`/luồng log một câu hiện tại vẫn là hành vi
đã có; cần chế độ riêng cho thu hội thoại trước khi thay hành vi mặc định.
Chưa kích hoạt barge-in; sau này một owner cũng phải sở hữu quyết định ngắt.

## Kiểm chứng

| Kiểm tra trên Debian hiện tại | Kết quả |
| --- | --- |
| Hồi quy trước thay đổi | PASS 108/108, không skip, 38,297 s |
| Hồi quy sau thay đổi | PASS 108/108, không skip, 38,734 s |
| Hợp đồng bridge local | PASS 15/15, 0,197 s |
| Cùng hợp đồng bridge Pipecat | PASS 15/15, 0,345 s |
| WAV mẫu qua model VAD thật | PASS 10/10 cho mỗi phương án trong cả 3 lượt |
| `pip check` core và môi trường riêng | PASS |
| Mic/loa thật, giọng trẻ em, phòng ồn và soak | Chưa kiểm thử trong checkpoint này |

Các test gồm ghép 320 → 512 sample giữ phần dư, đoạn im lặng/tiếng ngắn/
ngập ngừng, một final, audio bị chặn khi playback, reset do gap, overflow
vô hiệu hóa inference đang chạy, hủy trước kết quả VAD, generation đổi
trước bước provider tiếp theo, timeout và cleanup. Các provider là hàm giả,
không suy ra STT/TTS/LLM thật đã được tích hợp.

Lệnh tái hiện từ thư mục project, chạy ở terminal Debian thông thường:

```bash
.venv/bin/python scripts/run_tests.py
.venv/bin/python experiments/orchestration/test_probe.py
.experiments/phase1b/bin/python experiments/orchestration/test_probe.py --pipecat
.venv/bin/python experiments/orchestration/compare.py --output /tmp/phase1b-new.json
```

`compare.py` không ghi đè file kết quả đã có. Các test async trong phiên phát
triển được chạy ngoài sandbox vì sandbox chặn socket nội bộ của asyncio;
không cần `sudo`. Cài lại đúng môi trường Pipecat đã thử là bước **có mạng**
riêng, không phải thao tác khởi động assistant:

```bash
.venv/bin/python experiments/orchestration/prepare_pipecat.py \
  --manifest docs/benchmarks/orchestration-2026-09-15.json
```

Phase 1B kết thúc tại quyết định này. Phase 2 còn phải hoàn thiện giới hạn
lượt 30 s, chờ nói 8–10 s, pre-roll/buffer, câu quá dài không hoàn chỉnh,
clipping và capture-only CLI, rồi mới nghiệm thu câu nói thật. Không coi
15 test của spike là nghiệm thu toàn bộ Phase 2.
