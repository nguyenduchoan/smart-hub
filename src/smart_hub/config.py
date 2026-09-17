from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import unicodedata

from .turn import TurnSettings

ROOT = Path(__file__).resolve().parents[2]


def normalized(text):
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


@dataclass(frozen=True)
class Config:
    wake_word: str = "Maika ơi"
    engine: str = "neural"
    model_path: Path = ROOT / "models/maika_oi_neural.npz"
    backbone_path: Path = ROOT / "models/efficientwordnet-int8.onnx"
    negative_path: Path = ROOT / "models/maika_oi_negative.npz"
    device: str = "pipewire"
    feedback_enabled: bool = True
    feedback_path: Path = ROOT / "assets/em_nghe_vieneu.wav"
    playback_device: str = "pipewire"
    threshold: float = 0.8
    cooldown_seconds: float = 2.0
    min_rms: float = 180
    silence_seconds: float = 0.4
    min_speech_seconds: float = 0.35
    max_speech_seconds: float = 2.5
    turn: TurnSettings = field(default_factory=TurnSettings)

    def __post_init__(self):
        if not isinstance(self.turn, TurnSettings):
            raise ValueError("turn cần là cấu hình lượt hội thoại hợp lệ.")
        if self.engine not in ("neural", "dtw"):
            raise ValueError("engine chỉ hỗ trợ neural hoặc dtw.")
        if not isinstance(self.feedback_enabled, bool):
            raise ValueError("feedback_enabled phải là true/false.")
        if not isinstance(self.playback_device, str) or not self.playback_device.strip():
            raise ValueError("playback_device không được rỗng.")
        if not isinstance(self.wake_word, str) or not self.wake_word.strip():
            raise ValueError("wake_word không được rỗng.")
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError("device không được rỗng.")
        if self.device == "null":
            raise ValueError("ALSA null không phải microphone; dùng --mock để mô phỏng.")
        for name in ["threshold", "cooldown_seconds", "min_rms", "silence_seconds",
                     "min_speech_seconds", "max_speech_seconds"]:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} phải là số hữu hạn.")
            if value <= 0:
                raise ValueError(f"{name} nằm ngoài khoảng hợp lệ.")
        if not 0 < self.threshold <= 1:
            raise ValueError("threshold phải thuộc (0, 1].")
        if not 0.1 <= self.min_speech_seconds < self.max_speech_seconds <= 5:
            raise ValueError("Cần 0.1 <= min_speech_seconds < max_speech_seconds <= 5.")
        if not 0.1 <= self.silence_seconds <= 2 or self.min_rms > 10000:
            raise ValueError("silence_seconds hoặc min_rms nằm ngoài khoảng hợp lệ.")


def load_config(path=ROOT / "config.json"):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Cấu hình phải là JSON object.")
    if "turn" in data:
        if not isinstance(data["turn"], dict):
            raise ValueError("turn phải là JSON object.")
        data["turn"] = TurnSettings(**data["turn"])
    for name in ["model_path", "backbone_path", "negative_path", "feedback_path"]:
        if name in data:
            data[name] = (path.parent / data[name]).resolve()
    return Config(**data)
