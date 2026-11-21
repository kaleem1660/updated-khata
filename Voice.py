"""OPTIONAL speech helpers. The app works without them (typed commands)."""
import os
import re
import tempfile
from functools import lru_cache


def stt_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def tts_available():
    try:
        import gtts  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel(os.getenv("VK_WHISPER_MODEL", "small"), device="cpu", compute_type="int8")


def transcribe(audio_bytes, language=None):
    """Speech -> text. language: 'hi', 'ur', 'ta', 'en' or None for auto-detect."""
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        f.write(audio_bytes)
        f.flush()
        segments, _info = _model().transcribe(f.name, language=language, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def speak(text, lang="hi"):
    """Text -> mp3 bytes (gTTS needs internet). Returns None if unavailable."""
    if not tts_available():
        return None
    from io import BytesIO
    from gtts import gTTS
    spoken = re.sub(r"₹\s?([\d,]+)", r"\1 rupaye", text).replace(",", "")
    try:
        buf = BytesIO()
        gTTS(text=spoken, lang=lang).write_to_fp(buf)
        return buf.getvalue()
    except Exception:
        return None
