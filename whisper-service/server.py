"""Local Whisper service for the Content Digest workflow.

The n8n workflow calls this when a YouTube video has no captions. It downloads
the audio track with yt-dlp, transcribes it with OpenAI Whisper on this
machine (GPU if available) and returns the text.

    POST /transcribe   {"videoId": "dQw4w9WgXcQ", "languages": ["en", "hu"]}
                       header  X-Whisper-Token: <WHISPER_TOKEN>
                       →  {"videoId", "text", "language", "durationSec", "model"}
    GET  /health       →  {"ok": true, "model": "small", "device": "cuda"}

Settings (environment variables):

    WHISPER_TOKEN        required; the n8n credential sends the same value
    WHISPER_HOST         default 0.0.0.0 (the n8n container must reach it)
    WHISPER_PORT         default 8790
    WHISPER_MODEL        default small (medium needs more than 4 GB VRAM)
    WHISPER_MAX_MINUTES  default 180; longer videos are refused

Only one transcription runs at a time: a second request waits for the first,
because two Whisper models would not fit on a small GPU.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = os.environ.get("WHISPER_TOKEN", "")
HOST = os.environ.get("WHISPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("WHISPER_PORT", "8790"))
MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
MAX_SECONDS = int(os.environ.get("WHISPER_MAX_MINUTES", "180")) * 60

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}$")
LIVE_STATES = {"is_live", "is_upcoming", "post_live"}

_model = None
_lock = threading.Lock()


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def ensure_ffmpeg() -> None:
    """A WinGet-installed ffmpeg is not always on PATH; add it if found."""
    if shutil.which("ffmpeg"):
        return
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        for exe in packages.glob("*FFmpeg*/**/bin/ffmpeg.exe"):
            os.environ["PATH"] = str(exe.parent) + os.pathsep + os.environ["PATH"]
            return
    raise SystemExit("ffmpeg was not found. Install it (winget install Gyan.FFmpeg) and retry.")


def device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def load_model():
    global _model
    if _model is None:
        import whisper

        log(f"Loading Whisper model '{MODEL_NAME}' on {device()} ...")
        _model = whisper.load_model(MODEL_NAME)
    return _model


def audio_format(languages: list[str]) -> str:
    """Prefer the audio track in the expected language.

    YouTube adds auto-dubbed tracks to many videos, and plain `worstaudio` may
    pick one of those (an English video once came back as Arabic). The `^=`
    prefix match is needed because tracks are tagged with regions (en-US).
    """
    preferred = [f"worstaudio[language^={lang}]" for lang in languages]
    return "/".join(preferred + ["worstaudio", "bestaudio", "best"])


def transcribe(video_id: str, languages: list[str]) -> dict:
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        options = {
            "format": audio_format(languages),
            "outtmpl": os.path.join(tmpdir, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "64"}
            ],
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False) or {}
                if info.get("is_live") or info.get("live_status") in LIVE_STATES:
                    raise ServiceError(409, "the video is a live stream or has not finished yet")
                duration = int(info.get("duration") or 0)
                if duration > MAX_SECONDS:
                    raise ServiceError(413, f"the video is {duration // 60} minutes long, "
                                            f"the limit is {MAX_SECONDS // 60}")
                ydl.download([url])
        except ServiceError:
            raise
        except Exception as exc:  # yt-dlp raises many types; a 403 usually means yt-dlp is outdated
            raise ServiceError(502, f"audio download failed: {exc}") from exc

        audio = next((p for p in Path(tmpdir).iterdir() if p.is_file()), None)
        if audio is None:
            raise ServiceError(502, "the downloaded audio file is missing")

        started = time.monotonic()
        result = load_model().transcribe(str(audio), verbose=None)
        elapsed = time.monotonic() - started

    text = (result.get("text") or "").strip()
    if not text:
        raise ServiceError(422, "Whisper returned an empty transcript")
    language = result.get("language", "unknown")
    log(f"{video_id}: {duration // 60} min of audio, language {language}, "
        f"transcribed in {elapsed:.0f} s")
    return {
        "videoId": video_id,
        "text": text,
        "language": language,
        "durationSec": duration,
        "model": MODEL_NAME,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "WhisperService/1.0"

    def log_message(self, fmt: str, *args) -> None:  # silence the default access log
        pass

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True, "model": MODEL_NAME, "device": device(),
                                 "busy": _lock.locked()})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/transcribe":
            self.send_json(404, {"error": "not found"})
            return
        given = self.headers.get("X-Whisper-Token", "")
        if not hmac.compare_digest(given.encode(), TOKEN.encode()):
            self.send_json(401, {"error": "missing or wrong X-Whisper-Token header"})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "the body must be JSON"})
            return

        video_id = str(request.get("videoId") or "")
        if not VIDEO_ID.match(video_id):
            self.send_json(400, {"error": "videoId must be an 11-character YouTube video ID"})
            return
        languages = [str(lang).lower() for lang in request.get("languages") or []]
        languages = [lang for lang in languages if LANGUAGE.match(lang)][:5]

        log(f"{video_id}: request received"
            + (" (waiting for the previous one)" if _lock.locked() else ""))
        try:
            with _lock:
                self.send_json(200, transcribe(video_id, languages))
        except ServiceError as exc:
            log(f"{video_id}: {exc}")
            self.send_json(exc.status, {"videoId": video_id, "error": str(exc)})
        except Exception as exc:  # keep the server alive whatever Whisper throws
            log(f"{video_id}: unexpected error: {exc!r}")
            self.send_json(500, {"videoId": video_id, "error": f"transcription failed: {exc}"})


def main() -> None:
    if len(TOKEN) < 16:
        raise SystemExit("Set WHISPER_TOKEN to a random value of at least 16 characters "
                         "(the n8n credential sends the same value).")
    ensure_ffmpeg()
    load_model()  # fail at start-up, not on the first video
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"Whisper service listening on http://{HOST}:{PORT} (model {MODEL_NAME}, {device()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
