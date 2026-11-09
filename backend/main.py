import asyncio
import ipaddress
import os
import re
import shutil
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="ClipForge YouTube API", version="1.0.0")

ALLOWED_ORIGINS = [
    "https://gaprailangarah-oss.github.io",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "1200"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(500 * 1024 * 1024)))
HOURLY_LIMIT = int(os.getenv("HOURLY_DOWNLOAD_LIMIT", "3"))
requests_by_ip = defaultdict(list)
request_lock = asyncio.Lock()
download_semaphore = asyncio.Semaphore(1)


class YouTubeRequest(BaseModel):
    url: HttpUrl


def video_id_from_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com"}
    if host not in allowed:
        raise HTTPException(400, "Gunakan link YouTube yang valid.")
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        match = re.match(r"^/(?:watch|shorts|live|embed)/?", parsed.path)
        if match:
            if parsed.path.startswith("/watch"):
                from urllib.parse import parse_qs
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            else:
                video_id = parsed.path.strip("/").split("/")[-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise HTTPException(400, "Link YouTube tidak dikenali. Pastikan link menuju satu video, bukan playlist.")
    return video_id


async def enforce_limit(request: Request):
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    now = time.time()
    async with request_lock:
        recent = [stamp for stamp in requests_by_ip[client_ip] if now - stamp < 3600]
        if len(recent) >= HOURLY_LIMIT:
            raise HTTPException(429, f"Batas gratis tercapai: maksimal {HOURLY_LIMIT} video per jam dari alamat jaringan ini.")
        recent.append(now)
        requests_by_ip[client_ip] = recent


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "ClipForge YouTube API", "max_duration_seconds": MAX_DURATION_SECONDS,
            "max_file_bytes": MAX_FILE_BYTES, "hourly_download_limit": HOURLY_LIMIT}


@app.post("/api/youtube/download")
async def download_youtube(payload: YouTubeRequest, request: Request):
    video_id = video_id_from_url(str(payload.url))
    await enforce_limit(request)
    if download_semaphore.locked():
        raise HTTPException(429, "Server sedang memproses video lain. Coba lagi sebentar.")
    async with download_semaphore:
        temp_dir = tempfile.mkdtemp(prefix="clipforge-")
        try:
            output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")
            options = {
                "format": "best[ext=mp4][height<=1080]/best[height<=1080]/best",
                "outtmpl": output_template,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 20,
                "retries": 1,
                "fragment_retries": 1,
                "concurrent_fragment_downloads": 4,
                "max_filesize": MAX_FILE_BYTES,
                "restrictfilenames": True,
            }
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, f"https://www.youtube.com/watch?v={video_id}", download=False)
                    duration = int(info.get("duration") or 0)
                    if duration and duration > MAX_DURATION_SECONDS:
                        raise HTTPException(413, f"Video terlalu panjang. Batas server gratis saat ini {MAX_DURATION_SECONDS // 60} menit.")
                    if (info.get("filesize") or info.get("filesize_approx") or 0) > MAX_FILE_BYTES:
                        raise HTTPException(413, "Ukuran video melebihi batas 500 MB.")
                    await asyncio.to_thread(ydl.download, [f"https://www.youtube.com/watch?v={video_id}"])
            except HTTPException:
                raise
            except Exception as exc:
                detail = str(exc).replace("\\n", " ")[:260]
                raise HTTPException(502, f"YouTube tidak dapat diambil saat ini. Coba video publik lain atau unggah file sendiri. Detail: {detail}")
            candidates = [p for p in Path(temp_dir).iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
            if not candidates:
                raise HTTPException(502, "File video tidak berhasil dibuat oleh server.")
            file_path = max(candidates, key=lambda p: p.stat().st_size)
            size = file_path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise HTTPException(413, "Ukuran video melebihi batas 500 MB.")
            return FileResponse(
                str(file_path),
                media_type="video/mp4" if file_path.suffix.lower() == ".mp4" else "application/octet-stream",
                filename=f"clipforge-{video_id}{file_path.suffix}",
                headers={"X-ClipForge-Video-Id": video_id, "X-ClipForge-File-Size": str(size)},
                background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
