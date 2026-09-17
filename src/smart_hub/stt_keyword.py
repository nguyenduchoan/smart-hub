"""Final-transcript keyword matching, independent of audio/model dependencies."""
from datetime import datetime
import math
import re
import unicodedata

from .events import WakeEvent

DEFAULT_ALIASES = {"maika ơi": ("mai ca ơi", "mai ka ơi")}

def words(text):
    # Preserve Vietnamese accents: 'ơi', 'ôi' and 'oi' are different words.
    return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold()))


class KeywordTrigger:
    def __init__(self, phrase, emit, aliases=(), cooldown_seconds=2.0):
        self.phrase = phrase
        alternatives = DEFAULT_ALIASES.get(" ".join(words(phrase)), ())
        self.phrases = [words(value) for value in (phrase, *alternatives, *aliases)]
        if any(not value for value in self.phrases):
            raise ValueError("Cụm kích hoạt không được rỗng.")
        if not math.isfinite(cooldown_seconds) or cooldown_seconds <= 0:
            raise ValueError("Cooldown cần số dương hữu hạn.")
        self.emit = emit
        self.cooldown = cooldown_seconds
        self.last_segment = -1
        self.last_time = -math.inf
        self.last_event = -math.inf
        self.events = 0

    def accept(self, text, segment_id, audio_seconds):
        if not math.isfinite(audio_seconds) or audio_seconds < 0:
            raise ValueError("Timestamp audio không hợp lệ.")
        # Final results only. Never act twice on a duplicate or stale result.
        if segment_id <= self.last_segment:
            return False
        if audio_seconds < self.last_time:
            raise ValueError("Timestamp audio đi lùi.")
        self.last_segment, self.last_time = segment_id, audio_seconds
        tokens = words(text)
        matched = any(tokens[i:i + len(phrase)] == phrase
                      for phrase in self.phrases
                      for i in range(len(tokens) - len(phrase) + 1))
        if not matched or audio_seconds - self.last_event < self.cooldown:
            return False
        self.last_event = audio_seconds
        self.events += 1
        # 1.0 denotes a lexical match, not model confidence or acoustic score.
        self.emit(WakeEvent(self.phrase, 1.0, audio_seconds,
                            datetime.now().astimezone()))
        return True
