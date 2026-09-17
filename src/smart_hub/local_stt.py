"""Vietnamese Zipformer + Silero on CPU. No runtime downloads or cloud calls."""
import time
import math

import numpy as np

from .audio import RATE
from .stt_assets import MODEL_DIR, verify_models


# Standard wake preserves the accepted settings. Sensitive wake is opt-in.
WAKE_PROFILES = {
    "standard": {"threshold": 0.5, "min_speech": 0.25, "preroll": 0.32, "max_gain": 1.0},
    "sensitive": {"threshold": 0.35, "min_speech": 0.10, "preroll": 0.64, "max_gain": 4.0},
}

# Only post-wake commands use these settings.
COMMAND_VAD_THRESHOLD = 0.35
COMMAND_MIN_SPEECH_SECONDS = 0.10
COMMAND_SILENCE_SECONDS = 0.80
COMMAND_PREROLL_SECONDS = 0.64
COMMAND_MAX_GAIN = 8.0


class LocalSTT:
    def __init__(self, *, wake_profile="standard"):
        if wake_profile not in WAKE_PROFILES:
            raise ValueError("wake_profile cần là standard hoặc sensitive.")
        settings = WAKE_PROFILES[wake_profile]
        self.wake_max_gain = settings["max_gain"]
        self.wake_preroll_samples = round(settings["preroll"] * RATE)
        import sherpa_onnx
        started = time.monotonic()
        verify_models()
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(MODEL_DIR / "encoder.int8.onnx"),
            decoder=str(MODEL_DIR / "decoder.onnx"),
            joiner=str(MODEL_DIR / "joiner.int8.onnx"),
            tokens=str(MODEL_DIR / "tokens.txt"),
            num_threads=1, sample_rate=RATE, provider="cpu",
            decoding_method="greedy_search", debug=False,
        )
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(MODEL_DIR / "silero_vad.onnx")
        config.silero_vad.window_size = 512
        config.silero_vad.threshold = settings["threshold"]
        config.silero_vad.min_speech_duration = settings["min_speech"]
        config.silero_vad.min_silence_duration = 0.6
        config.silero_vad.max_speech_duration = 6.0
        config.sample_rate = RATE
        config.num_threads = 1
        config.provider = "cpu"
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=8)
        self.wake_vad = self.vad
        self.command_vad = None
        self.command_mode = False
        self.preroll_samples = self.wake_preroll_samples
        self.pending = bytearray()
        self.history = bytearray()
        self.total_samples = 0
        self.startup_seconds = time.monotonic() - started

    def set_command_mode(self, enabled):
        if enabled == self.command_mode:
            return
        if enabled and self.command_vad is None:
            values = (COMMAND_VAD_THRESHOLD, COMMAND_MIN_SPEECH_SECONDS,
                      COMMAND_SILENCE_SECONDS, COMMAND_PREROLL_SECONDS, COMMAND_MAX_GAIN)
            if (not all(math.isfinite(value) for value in values)
                    or not 0 < COMMAND_VAD_THRESHOLD < 1
                    or not 0 < COMMAND_MIN_SPEECH_SECONDS <= 1
                    or not 0 < COMMAND_SILENCE_SECONDS <= 1
                    or not 0 <= COMMAND_PREROLL_SECONDS <= 0.8
                    or not 1 <= COMMAND_MAX_GAIN <= 8):
                raise ValueError("Thông số VAD lệnh không hợp lệ; xem COMMAND_* trong local_stt.py.")
            import sherpa_onnx
            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = str(MODEL_DIR / "silero_vad.onnx")
            config.silero_vad.window_size = 512
            config.silero_vad.threshold = COMMAND_VAD_THRESHOLD
            config.silero_vad.min_speech_duration = COMMAND_MIN_SPEECH_SECONDS
            config.silero_vad.min_silence_duration = COMMAND_SILENCE_SECONDS
            config.silero_vad.max_speech_duration = 6.0
            config.sample_rate = RATE
            config.num_threads = 1
            config.provider = "cpu"
            self.command_vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=8)
        self.reset()
        self.command_mode = enabled
        self.vad = self.command_vad if enabled else self.wake_vad
        self.preroll_samples = (round(COMMAND_PREROLL_SECONDS * RATE) if enabled
                               else self.wake_preroll_samples)
        self.vad.reset()

    def _recognition_audio(self, samples, target_peak):
        max_gain = COMMAND_MAX_GAIN if self.command_mode else self.wake_max_gain
        if max_gain == 1:
            return samples
        peak = float(np.max(np.abs(samples)))
        if peak == 0:
            return samples
        # Keep original PCM for clipping checks/history. Amplify only the copy
        # supplied to VAD/ASR, with bounded gain and no added saturation.
        gain = min(max_gain, max(1.0, target_peak / peak))
        return samples * gain

    def _segments(self):
        result = []
        while not self.vad.empty():
            # Native front references become invalid on pop/reset.
            front = self.vad.front
            start = max(0, int(front.start) - self.preroll_samples)
            end = min(self.total_samples, int(front.start) + len(front.samples) + 1600)
            history_start = self.total_samples - len(self.history) // 2
            if start < history_start or end <= start:
                raise ValueError("Không còn đủ audio liên tục cho đoạn STT.")
            pcm = bytes(self.history[(start - history_start) * 2:(end - history_start) * 2])
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            self.vad.pop()
            if len(samples) > 8 * RATE:
                raise ValueError("VAD vượt giới hạn đoạn audio 8 giây.")
            if len(samples):
                result.append(samples)
        return result

    def feed(self, pcm):
        if not pcm or len(pcm) % 2 or len(pcm) > 640:
            raise ValueError("Cần frame PCM mono 16-bit dài tối đa 20 ms.")
        self.pending.extend(pcm)
        self.total_samples += len(pcm) // 2
        self.history.extend(pcm)
        del self.history[:max(0, len(self.history) - 8 * RATE * 2)]
        while len(self.pending) >= 1024:
            block = bytes(self.pending[:1024])
            del self.pending[:1024]
            samples = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768
            self.vad.accept_waveform(self._recognition_audio(samples, target_peak=0.9))
        return self._segments()

    def flush(self):
        # Only offline WAV EOF uses flush. Live stop discards an unfinished call.
        if self.pending:
            block = bytes(self.pending).ljust(1024, b"\0")
            self.pending.clear()
            samples = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768
            self.vad.accept_waveform(self._recognition_audio(samples, target_peak=0.9))
        self.vad.flush()
        return self._segments()

    def reset(self):
        self.pending.clear()
        self.history.clear()
        self.total_samples = 0
        self.vad.reset()

    def transcribe(self, samples):
        if (samples.ndim != 1 or not 0 < len(samples) <= 8 * RATE
                or not np.isfinite(samples).all()):
            raise ValueError("Đoạn STT cần audio hữu hạn, mono, tối đa 8 giây.")
        stream = self.recognizer.create_stream()
        stream.accept_waveform(RATE, self._recognition_audio(samples, target_peak=0.5))
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()
