import asyncio
import os
import re
import shutil
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

app = FastAPI(title="ClipForge YouTube API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://gaprailangarah-oss.github.io", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "1200"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(500 * 1024 * 1024)))
HOURLY_LIMIT = int(os.getenv("HOURLY_DOWNLOAD_LIMIT", "3"))
requests_by_ip = defaultdict(list)
jobs = {}
request_lock = asyncio.Lock()
active_job = None


class YouTubeRequest(BaseModel):
    url: HttpUrl


def video_id_from_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        raise HTTPException(400, "Gunakan link YouTube yang valid.")
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path.startswith("/watch"):
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        parts = parsed.path.strip("/").split("/")
        video_id = parts[1] if len(parts) > 1 and parts[0] in {"shorts", "live", "embed"} else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise HTTPException(400, "Link YouTube tidak dikenali. Gunakan satu video, bukan playlist.")
    return video_id


async def enforce_limit(request: Request):
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    now = time.time()
    async with request_lock:
        recent = [stamp for stamp in requests_by_ip[client_ip] if now - stamp < 3600]
        if len(recent) >= HOURLY_LIMIT:
            raise HTTPException(429, f"Batas gratis: maksimal {HOURLY_LIMIT} video per jam dari jaringan ini.")
        recent.append(now)
        requests_by_ip[client_ip] = recent


def run_download(job_id: str, video_id: str, temp_dir: str):
    output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")

    def progress_hook(data):
        job = jobs.get(job_id)
        if not job:
            return
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            job["progress"] = min(95, int(downloaded * 95 / total)) if total else 8
            job["message"] = "Mengunduh video dari YouTube…"
        elif data.get("status") == "finished":
            job["progress"] = 96
            job["message"] = "Menyiapkan file video…"

    options = {
        "format": "best[ext=mp4][height<=720][protocol^=https]/best[ext=mp4][protocol^=https]/best[protocol^=https]",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 1,
        "fragment_retries": 1,
        "concurrent_fragment_downloads": 4,
        "max_filesize": MAX_FILE_BYTES,
        "restrictfilenames": True,
        "progress_hooks": [progress_hook],
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        duration = int(info.get("duration") or 0)
        if duration and duration > MAX_DURATION_SECONDS:
            raise ValueError(f"Video terlalu panjang. Batas server gratis {MAX_DURATION_SECONDS // 60} menit.")
        size_hint = info.get("filesize") or info.get("filesize_approx") or 0
        if size_hint > MAX_FILE_BYTES:
            raise ValueError("Ukuran video melebihi batas 500 MB.")
        job = jobs.get(job_id)
        if job:
            job["message"] = "Video ditemukan. Mengunduh…"
            job["progress"] = 5
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    candidates = [p for p in Path(temp_dir).iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not candidates:
        raise ValueError("File video tidak berhasil dibuat oleh server.")
    file_path = max(candidates, key=lambda p: p.stat().st_size)
    size = file_path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("Ukuran video melebihi batas 500 MB.")
    return file_path, size


async def process_job(job_id: str, video_id: str):
    global active_job
    temp_dir = tempfile.mkdtemp(prefix="clipforge-")
    jobs[job_id].update({"status": "processing", "progress": 2, "message": "Memeriksa video YouTube…", "temp_dir": temp_dir})
    try:
        file_path, size = await asyncio.to_thread(run_download, job_id, video_id, temp_dir)
        jobs[job_id].update({
            "status": "complete", "progress": 100, "message": "Video siap diproses di komputer.",
            "file_path": str(file_path), "filename": f"clipforge-{video_id}{file_path.suffix}", "size": size,
        })
    except Exception as exc:
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(exc)[:300]})
        shutil.rmtree(temp_dir, ignore_errors=True)
    finally:
        active_job = None



MAX_CAPTION_AUDIO_BYTES = int(os.getenv("MAX_CAPTION_AUDIO_BYTES", str(80 * 1024 * 1024)))

@app.post("/api/captions/clean")
async def clean_voice_captions(audio: UploadFile = File(...), language: str = Form("auto")):
    """Isolate speech from background audio, then return word-timed Scribe captions."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "Cloud Caption belum dikonfigurasi. Tambahkan ELEVENLABS_API_KEY di Render Environment.")
    raw = await audio.read(MAX_CAPTION_AUDIO_BYTES + 1)
    if not raw:
        raise HTTPException(400, "Audio kosong.")
    if len(raw) > MAX_CAPTION_AUDIO_BYTES:
        raise HTTPException(413, "Audio terlalu besar untuk Cloud Caption. Coba video yang lebih pendek.")
    filename = Path(audio.filename or "clipforge.wav").name
    mime = audio.content_type or "audio/wav"
    timeout = httpx.Timeout(300.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            isolated = await client.post(
                "https://api.elevenlabs.io/v1/audio-isolation",
                headers={"xi-api-key": api_key},
                files={"audio": (filename, raw, mime)},
                data={"file_format": "other"},
            )
            if isolated.status_code >= 400:
                detail = isolated.text[:500]
                raise HTTPException(isolated.status_code if isolated.status_code < 500 else 502,
                                    "Audio isolation gagal: " + detail)
            clean_audio = isolated.content
            if not clean_audio:
                raise HTTPException(502, "Layanan pemisahan suara mengembalikan audio kosong.")
            form = {
                "model_id": "scribe_v2",
                "timestamps_granularity": "word",
                "diarize": "true",
                "tag_audio_events": "false",
            }
            if language and language != "auto":
                lang_map = {"indonesian": "ind", "english": "eng"}
                form["language_code"] = lang_map.get(language, language)
            transcript = await client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": api_key},
                files={"file": ("isolated-audio.wav", clean_audio, "audio/wav")},
                data=form,
            )
            if transcript.status_code >= 400:
                detail = transcript.text[:500]
                raise HTTPException(transcript.status_code if transcript.status_code < 500 else 502,
                                    "Transkripsi Scribe gagal: " + detail)
            payload = transcript.json()
        except httpx.TimeoutException:
            raise HTTPException(504, "Pemrosesan audio melewati batas waktu. Coba video yang lebih pendek.")
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Tidak dapat menghubungi layanan AI audio: " + str(exc)[:250])
    words = payload.get("words") or []
    timed = []
    for item in words:
        if item.get("type") != "word":
            continue
        text = (item.get("text") or "").strip()
        start, end = item.get("start"), item.get("end")
        if not text or not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            continue
        timed.append({"text": text, "start": float(start), "end": float(end), "speaker": item.get("speaker_id")})
    if not timed:
        raise HTTPException(422, "AI tidak menemukan ucapan yang jelas pada audio ini.")
    segments = []
    group = []
    for word in timed:
        if group and (len(group) >= 5 or word["start"] - group[-1]["end"] > 0.45):
            segments.append({"start": group[0]["start"], "end": group[-1]["end"],
                             "text": " ".join(w["text"] for w in group), "speaker": group[0]["speaker"]})
            group = []
        group.append(word)
        if word["text"].endswith((".", "?", "!")):
            segments.append({"start": group[0]["start"], "end": group[-1]["end"],
                             "text": " ".join(w["text"] for w in group), "speaker": group[0]["speaker"]})
            group = []
    if group:
        segments.append({"start": group[0]["start"], "end": group[-1]["end"],
                         "text": " ".join(w["text"] for w in group), "speaker": group[0]["speaker"]})
    return {"segments": segments, "text": payload.get("text", ""),
            "language": payload.get("language_code"), "word_count": len(timed),
            "provider": "ElevenLabs Audio Isolation + Scribe v2"}

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "ClipForge YouTube API", "max_duration_seconds": MAX_DURATION_SECONDS,
            "max_file_bytes": MAX_FILE_BYTES, "hourly_download_limit": HOURLY_LIMIT}


@app.post("/api/youtube/jobs", status_code=202)
async def create_youtube_job(payload: YouTubeRequest, request: Request):
    global active_job
    video_id = video_id_from_url(str(payload.url))
    await enforce_limit(request)
    async with request_lock:
        if active_job:
            raise HTTPException(429, "Server gratis sedang memproses video lain. Tunggu sampai selesai.")
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"status": "queued", "progress": 1, "message": "Antrean dibuat.", "created": time.time()}
        active_job = job_id
    asyncio.create_task(process_job(job_id, video_id))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/youtube/jobs/{job_id}")
async def get_youtube_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Tugas tidak ditemukan atau server baru saja dimulai ulang.")
    return {key: job.get(key) for key in ("status", "progress", "message", "filename", "size")}


@app.get("/api/youtube/jobs/{job_id}/file")
async def get_youtube_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job.get("status") != "complete" or not job.get("file_path"):
        raise HTTPException(409, "Video belum siap diunduh.")
    path = Path(job["file_path"])
    return FileResponse(
        str(path),
        media_type="video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream",
        filename=job["filename"],
        headers={"X-ClipForge-File-Size": str(job["size"])},
        background=BackgroundTask(shutil.rmtree, job["temp_dir"], ignore_errors=True),
    )
