#!/usr/bin/env python3
"""Render fixed Vietnamese acknowledgements using the installed eSpeak NG C API."""
import ctypes as C
import ctypes.util
from pathlib import Path
import wave

ROOT = Path(__file__).resolve().parents[1]


def main():
    library = ctypes.util.find_library("espeak-ng")
    if not library:
        raise RuntimeError("Thiếu libespeak-ng1/espeak-ng-data; có thể dùng WAV tự thu thay thế.")
    lib = C.CDLL(library)
    callback_type = C.CFUNCTYPE(C.c_int, C.POINTER(C.c_short), C.c_int, C.c_void_p)
    lib.espeak_Initialize.argtypes = [C.c_int, C.c_int, C.c_char_p, C.c_int]
    lib.espeak_Initialize.restype = C.c_int
    lib.espeak_SetSynthCallback.argtypes = [callback_type]
    lib.espeak_SetSynthCallback.restype = None
    lib.espeak_SetVoiceByName.argtypes = [C.c_char_p]
    lib.espeak_SetVoiceByName.restype = C.c_int
    lib.espeak_SetParameter.argtypes = [C.c_int, C.c_int, C.c_int]
    lib.espeak_SetParameter.restype = C.c_int
    lib.espeak_Synth.argtypes = [C.c_void_p, C.c_size_t, C.c_uint, C.c_int,
                                C.c_uint, C.c_uint, C.c_void_p, C.c_void_p]
    lib.espeak_Synth.restype = C.c_int
    lib.espeak_Terminate.argtypes = []
    lib.espeak_Terminate.restype = C.c_int
    chunks = []

    @callback_type
    def collect(samples, count, _events):
        if samples and count > 0:
            chunks.append(C.string_at(samples, count * 2))
        return 0

    rate = lib.espeak_Initialize(2, 0, None, 0)  # AUDIO_OUTPUT_SYNCHRONOUS: no audio device
    if rate <= 0:
        raise RuntimeError("Không khởi động được eSpeak NG.")
    try:
        lib.espeak_SetSynthCallback(collect)
        if lib.espeak_SetVoiceByName(b"vi") != 0:
            raise RuntimeError("Không có voice tiếng Việt (vi).")
        if lib.espeak_SetParameter(1, 145, 0) != 0:
            raise RuntimeError("Không đặt được tốc độ nói.")
        for filename, phrase in [("em_nghe.wav", "em nghe"), ("em_day.wav", "em đây")]:
            path = ROOT / "assets" / filename
            if path.exists():
                print(f"Giữ file đã có: {path}")
                continue
            chunks.clear()
            text = C.create_string_buffer(phrase.encode("utf-8"))
            if lib.espeak_Synth(text, len(text), 0, 1, 0, 1, None, None) != 0:
                raise RuntimeError("Không tạo được phản hồi tiếng Việt.")
            pcm = b"".join(chunks)
            if len(pcm) < rate // 5:
                raise RuntimeError("Audio phản hồi rỗng/quá ngắn.")
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                output.writeframes(pcm)
            print(f"PASS {path.name}: ‘{phrase}’, {len(pcm) / (rate * 2):.2f}s, {rate} Hz")
    finally:
        lib.espeak_Terminate()


if __name__ == "__main__":
    main()
