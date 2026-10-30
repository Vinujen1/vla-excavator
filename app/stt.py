"""Local speech-to-text with faster-whisper (CTranslate2, CPU int8 on the Orin)."""
import os
import threading
import time

from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("WHISPER_MODEL", "base.en")

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    with _lock:
        if _model is None:
            # The PyPI ctranslate2 wheel for aarch64 is CPU-only; int8 keeps it fast.
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        return _model


def transcribe(wav_path):
    model = _get_model()
    started = time.time()
    segments, info = model.transcribe(str(wav_path), language="en", beam_size=1, vad_filter=False)
    segments = list(segments)
    text = " ".join(s.text.strip() for s in segments).strip()
    avg_logprob = min((s.avg_logprob for s in segments), default=None)
    no_speech = max((s.no_speech_prob for s in segments), default=None)
    return {
        "text": text,
        # Recognition-quality hints: the operator confirms low-confidence transcripts before use.
        "min_avg_logprob": round(avg_logprob, 3) if avg_logprob is not None else None,
        "max_no_speech_prob": round(no_speech, 3) if no_speech is not None else None,
        "low_confidence": bool(text) and ((avg_logprob is not None and avg_logprob < -1.0)
                                          or (no_speech is not None and no_speech > 0.6)),
        "model": MODEL_NAME,
        "language": info.language,
        "audio_duration_s": round(info.duration, 2),
        "processing_s": round(time.time() - started, 2),
        "transcribed_at_unix": time.time(),
    }
