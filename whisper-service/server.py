"""Local video service for the Content Digest workflow: transcripts and frames.

The n8n workflow calls it for two things:

* /transcribe: when a YouTube video has no captions. It downloads the audio
  track with yt-dlp, transcribes it with OpenAI Whisper on this machine (GPU if
  available) and returns the text.
* /frames: for every investor video (YouTube, or a video attached to an X
  post). It downloads the picture track, cuts frames at scene changes with
  ffmpeg and returns them as base64 JPEGs. The people this workflow follows
  draw their price levels on charts while saying "at this level", so the
  transcript alone often misses the actual numbers.

    POST /transcribe   {"videoId": "dQw4w9WgXcQ", "languages": ["en", "hu"]}
                       →  {"videoId", "text", "language", "durationSec", "model"}
    POST /frames       {"videoId": "dQw4w9WgXcQ", "maxFrames": 10}
                       or {"url": "https://x.com/<user>/status/<id>", "maxFrames": 8}
                       →  {"url", "durationSec", "frames": [{"second", "at", "mediaType", "data"}]}
    POST /claude       a Messages API request body (model, system, one user message)
                       →  a Messages API-shaped reply: {"content": [{"type": "text", "text"}], "usage"}
    GET  /health       →  {"ok": true, "model": "small", "device": "cuda"}

All POST endpoints need the header  X-Whisper-Token: <WHISPER_TOKEN>.

/claude is optional. It writes the report with the Claude Code CLI (`claude -p`)
on this machine, so it runs on a Claude subscription instead of an API key.
The workflow uses it when `claudeUrl` is set in its Settings node. The CLI
runs without tools, MCP servers, skills or settings, in an empty folder, and
ANTHROPIC_API_KEY is removed from its environment so the subscription is used.

Settings (environment variables):

    WHISPER_TOKEN        required; the n8n credential sends the same value
    WHISPER_HOST         default 0.0.0.0 (the n8n container must reach it)
    WHISPER_PORT         default 8790
    WHISPER_MODEL        default small (medium needs more than 4 GB VRAM)
    WHISPER_MAX_MINUTES  default 180; longer videos are refused (both endpoints)
    CLAUDE_BIN           path to the claude executable; found on PATH by default

Only one transcription runs at a time: a second request waits for the first,
because two Whisper models would not fit on a small GPU. Frame extraction and
Claude calls have their own queues, so they do not wait for a transcription.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TOKEN = os.environ.get("WHISPER_TOKEN", "")
HOST = os.environ.get("WHISPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("WHISPER_PORT", "8790"))
MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
MAX_SECONDS = int(os.environ.get("WHISPER_MAX_MINUTES", "180")) * 60

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}$")
LIVE_STATES = {"is_live", "is_upcoming", "post_live"}

# /frames only downloads from these sites, so the service cannot be used to
# fetch arbitrary URLs.
FRAME_HOSTS = ("youtube.com", "youtu.be", "x.com", "twitter.com")
MAX_FRAMES = 12
FRAME_HEIGHT = 720
# Scene changes collected before picking; a safety cap for videos full of cuts.
MAX_SCENE_FRAMES = 300

# /claude: a report request carries base64 frames, so it can be a few MB.
MAX_CLAUDE_BODY = 50_000_000
MAX_IMAGE_BYTES = 10_000_000
CLAUDE_TIMEOUT = 900
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.\-\[\]]{1,80}$")

_model = None
_lock = threading.Lock()
_frames_lock = threading.Lock()
_claude_lock = threading.Lock()


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


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------


def frame_source(request: dict) -> str:
    """The video URL to cut frames from: a YouTube ID or an allowed URL."""
    video_id = str(request.get("videoId") or "")
    if video_id:
        if not VIDEO_ID.match(video_id):
            raise ServiceError(400, "videoId must be an 11-character YouTube video ID")
        return f"https://www.youtube.com/watch?v={video_id}"

    url = str(request.get("url") or "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = any(host == h or host.endswith("." + h) for h in FRAME_HOSTS)
    if parsed.scheme != "https" or not allowed:
        raise ServiceError(400, "send a videoId, or an https URL on " + ", ".join(FRAME_HOSTS))
    return url


def mmss(second: float) -> str:
    total = int(second)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def extract_frames(url: str, max_frames: int) -> dict:
    import yt_dlp

    is_x = urlparse(url).hostname.endswith(("x.com", "twitter.com"))
    # Picture only: frames don't need the sound, and merging two tracks is the
    # step where YouTube downloads fail with 403 most often. X serves combined
    # mp4 files, so there we take the mp4 as it is.
    video_format = (
        f"best[height<={FRAME_HEIGHT}][ext=mp4]/best[ext=mp4]/best" if is_x
        else f"bestvideo[height<={FRAME_HEIGHT}]/best[height<={FRAME_HEIGHT}]/best"
    )
    # Short X clips cut less often; the same thresholds as the Python original.
    threshold = 0.30 if is_x else 0.35

    with tempfile.TemporaryDirectory() as tmpdir:
        options = {
            "format": video_format,
            "outtmpl": os.path.join(tmpdir, "video.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
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
            raise ServiceError(502, f"video download failed: {exc}") from exc

        video = next((p for p in Path(tmpdir).iterdir() if p.is_file()), None)
        if video is None:
            raise ServiceError(502, "the downloaded video file is missing")

        out_dir = Path(tmpdir) / "frames"
        out_dir.mkdir()
        started = time.monotonic()
        frames = (scene_frames(video, out_dir, max_frames, threshold)
                  or uniform_frames(video, out_dir, max_frames, duration))
        elapsed = time.monotonic() - started

        result = [
            {
                "second": int(second),
                "at": mmss(second),
                "mediaType": "image/jpeg",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
            for second, path in frames
        ]

    log(f"{url}: {len(result)} frames from {duration // 60} min of video in {elapsed:.0f} s")
    return {"url": url, "durationSec": duration, "frames": result}


def scene_frames(video: Path, out_dir: Path, max_frames: int,
                 threshold: float) -> list[tuple[float, Path]]:
    """Frames at scene changes, spread evenly over the whole video.

    The process is not cut short with a frame limit: cuts cluster at the start
    of a video, so every frame would come from the first minute. All scene
    changes are collected first, then picked evenly in time.
    """
    raw_dir = out_dir / "raw"
    raw_dir.mkdir()
    command = [
        "ffmpeg", "-hide_banner", "-i", str(video),
        # showinfo prints pts_time for every frame let through: that is the
        # second of the video the frame comes from.
        "-vf", f"select='gt(scene,{threshold})',scale=-2:{FRAME_HEIGHT},showinfo",
        "-vsync", "vfr", "-frames:v", str(MAX_SCENE_FRAMES), "-q:v", "3",
        str(raw_dir / "raw_%04d.jpg"),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                errors="replace", timeout=1800)
    except (subprocess.TimeoutExpired, OSError):
        return []

    seconds = [float(s) for s in re.findall(r"pts_time:([0-9.]+)", result.stderr)]
    candidates = sorted(raw_dir.glob("raw_*.jpg"))
    # Without a usable timestamp for every frame the pairing would be off.
    if not candidates or len(seconds) < len(candidates):
        return []
    return [(seconds[i], candidates[i]) for i in spread(len(candidates), max_frames)]


def spread(total: int, count: int) -> list[int]:
    """`count` indexes out of `total`, evenly spaced."""
    if total <= count:
        return list(range(total))
    step = (total - 1) / (count - 1) if count > 1 else 0
    return [round(i * step) for i in range(count)]


def uniform_frames(video: Path, out_dir: Path, max_frames: int,
                   duration: int) -> list[tuple[float, Path]]:
    """Fallback for videos without real cuts, e.g. one chart shown throughout."""
    if duration <= 0:
        return []
    step = duration / (max_frames + 1)
    frames = []
    for index in range(1, max_frames + 1):
        second = step * index
        path = out_dir / f"uniform_{index:03d}.jpg"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(second),
            "-i", str(video), "-vf", f"scale=-2:{FRAME_HEIGHT}",
            "-frames:v", "1", "-q:v", "3", "-y", str(path),
        ]
        try:
            subprocess.run(command, capture_output=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if path.exists():
            frames.append((second, path))
    return frames


# --------------------------------------------------------------------------
# Claude via the CLI (subscription)
# --------------------------------------------------------------------------


def claude_binary() -> str:
    """The claude executable itself, never the npm .cmd wrapper.

    Through a .cmd file, cmd.exe would parse the arguments again, and an empty
    argument (--tools "") would be lost.
    """
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or ""
    if found.lower().endswith((".cmd", ".bat")):
        exe = (Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code"
               / "bin" / "claude.exe")
        found = str(exe) if exe.exists() else ""
    if not found:
        raise ServiceError(503, "the claude CLI was not found; set CLAUDE_BIN")
    return found


def inline_image(block: dict) -> dict:
    """URL images (X post charts) are downloaded and passed on as base64."""
    source = block.get("source") or {}
    if source.get("type") != "url":
        return block
    url = str(source.get("url") or "")
    if urlparse(url).scheme != "https":
        raise ServiceError(400, "image URLs must be https")
    try:
        with urlopen(Request(url, headers={"User-Agent": "content-digest"}), timeout=60) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
            media_type = resp.headers.get_content_type()
    except Exception as exc:
        raise ServiceError(502, f"image download failed: {url}: {exc}") from exc
    if len(data) > MAX_IMAGE_BYTES or not media_type.startswith("image/"):
        raise ServiceError(502, f"not a usable image: {url}")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.b64encode(data).decode("ascii")}}


def run_claude(request: dict) -> dict:
    model = str(request.get("model") or "")
    if not MODEL_ID.match(model):
        raise ServiceError(400, "model must be a Claude model ID")
    messages = request.get("messages")
    if not (isinstance(messages, list) and len(messages) == 1
            and isinstance(messages[0], dict) and messages[0].get("role") == "user"):
        raise ServiceError(400, "exactly one user message is supported")
    content = messages[0].get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise ServiceError(400, "the message content must be a string or a list")
    content = [inline_image(b) if isinstance(b, dict) and b.get("type") == "image" else b
               for b in content]
    system = request.get("system") or ""
    if not isinstance(system, str):
        raise ServiceError(400, "system must be a string")

    # Without the key in its environment the CLI uses the logged-in subscription.
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    with tempfile.TemporaryDirectory() as tmpdir:
        # An empty working folder: no CLAUDE.md or project settings get loaded.
        Path(tmpdir, "system.md").write_text(system, encoding="utf-8")
        line = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
        command = [
            claude_binary(), "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", model,
            "--tools", "",
            "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
            "--system-prompt-file", "system.md",
            "--no-session-persistence",
        ]
        started = time.monotonic()
        try:
            proc = subprocess.run(command, input=line + "\n", capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", cwd=tmpdir, env=env,
                                  timeout=CLAUDE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise ServiceError(504, f"claude did not finish in {CLAUDE_TIMEOUT} s") from exc
        except OSError as exc:
            raise ServiceError(503, f"claude could not be started: {exc}") from exc
        elapsed = time.monotonic() - started

    result = None
    for raw in proc.stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    if result is None or result.get("is_error") or not result.get("result"):
        detail = (result or {}).get("result") or proc.stderr.strip()[-500:] or "no result"
        raise ServiceError(502, f"claude failed (exit {proc.returncode}): {detail}")

    usage = result.get("usage") or {}
    log(f"claude {model}: {usage.get('output_tokens', 0)} output tokens in {elapsed:.0f} s")
    return {
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": result["result"]}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
        # What the same call would cost on the API; on a subscription nothing is billed.
        "costUsd": result.get("total_cost_usd"),
        "durationSec": round(elapsed),
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


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
                                 "busy": _lock.locked(), "framesBusy": _frames_lock.locked(),
                                 "claudeBusy": _claude_lock.locked()})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in ("/transcribe", "/frames", "/claude"):
            self.send_json(404, {"error": "not found"})
            return
        given = self.headers.get("X-Whisper-Token", "")
        if not hmac.compare_digest(given.encode(), TOKEN.encode()):
            self.send_json(401, {"error": "missing or wrong X-Whisper-Token header"})
            return
        limit = MAX_CLAUDE_BODY if self.path == "/claude" else 10_000
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                self.send_json(413, {"error": f"the body is larger than {limit} bytes"})
                return
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "the body must be JSON"})
            return
        if not isinstance(request, dict):
            self.send_json(400, {"error": "the body must be a JSON object"})
            return

        if self.path == "/frames":
            self.handle_frames(request)
        elif self.path == "/claude":
            self.handle_claude(request)
        else:
            self.handle_transcribe(request)

    def handle_claude(self, request: dict) -> None:
        log("claude: request received"
            + (" (waiting for the previous one)" if _claude_lock.locked() else ""))
        try:
            with _claude_lock:
                self.send_json(200, run_claude(request))
        except ServiceError as exc:
            log(f"claude: {exc}")
            self.send_json(exc.status, {"type": "error", "error": {"message": str(exc)}})
        except Exception as exc:  # keep the server alive whatever happens
            log(f"claude: unexpected error: {exc!r}")
            self.send_json(500, {"type": "error", "error": {"message": f"claude call failed: {exc}"}})

    def handle_frames(self, request: dict) -> None:
        try:
            url = frame_source(request)
            max_frames = int(request.get("maxFrames") or 10)
        except ServiceError as exc:
            self.send_json(exc.status, {"error": str(exc)})
            return
        except (TypeError, ValueError):
            self.send_json(400, {"error": "maxFrames must be a number"})
            return
        max_frames = max(1, min(max_frames, MAX_FRAMES))

        log(f"{url}: frames requested"
            + (" (waiting for the previous one)" if _frames_lock.locked() else ""))
        try:
            with _frames_lock:
                self.send_json(200, extract_frames(url, max_frames))
        except ServiceError as exc:
            log(f"{url}: {exc}")
            self.send_json(exc.status, {"url": url, "error": str(exc)})
        except Exception as exc:  # keep the server alive whatever ffmpeg or yt-dlp throws
            log(f"{url}: unexpected error: {exc!r}")
            self.send_json(500, {"url": url, "error": f"frame extraction failed: {exc}"})

    def handle_transcribe(self, request: dict) -> None:
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
