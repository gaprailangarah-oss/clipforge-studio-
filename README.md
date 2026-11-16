# ClipForge Studio

Browser-based video editor with local Whisper transcription, local word-timed Whisper captions, automatic highlight selection, and MP4 export.

## YouTube link processing (free, limited)

The static GitHub Pages app needs a small API service to retrieve a video file from a YouTube URL. A Render Blueprint is included at `render.yaml`.

### Deploy the API

1. Sign in to Render and choose **New → Blueprint**.
2. Connect this GitHub repository and select the `main` branch.
3. Render detects `render.yaml`; review the service named `clipforge-studio-api` and deploy it on the free plan.
4. The expected API address is `https://clipforge-studio-api.onrender.com`. If Render assigns a different hostname, change `API_BASE_DEFAULT` near the top of the module script in `index.html` to that URL and commit the change.
5. Open the GitHub Pages site again. The first request may be slow while the free Render service wakes up.

### Current limits

- One video at a time; soft limit of 3 downloads per source IP per hour.
- Maximum video duration: 20 minutes; maximum file size: 500 MB; server selects a fast progressive stream up to 720p where available.
- Render free services can sleep, have limited CPU/RAM/bandwidth, and may be slower or unavailable during cold starts.
- YouTube extraction can fail when YouTube changes its delivery requirements or blocks server requests. The app keeps local-file upload as a fallback.
- After the video is downloaded into the browser, Whisper, caption timing, emoji decoration, clip selection, and export run on the user's computer. WebGPU is used when supported. The clip ranker favors complete thoughts, short opening hooks, narrative signals, payoff, and distinct non-overlapping moments.
- No automatic publishing to YouTube and no watermark.

### Local AI captions (free)

Auto Caption runs Whisper Base locally in the browser. The editor requests word-level Whisper timestamps using the timestamp-enabled ONNX export, then groups adjacent words into readable caption phrases. No audio is uploaded to a paid transcription or voice-isolation provider. Word alignment uses Whisper's DTW-based estimate and can still drift around pauses, overlapping speakers, music, or noisy audio; review captions before exporting. If word alignment is unavailable, the editor falls back to segment timestamps and clearly reports that manual checking is needed.

## Responsible use

Only process videos you own or have permission to download and edit. Respect copyright, privacy, and YouTube's Terms of Service. The service does not bypass sign-in, age restrictions, DRM, or other access controls. Do not submit private or restricted videos.

## Local API development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

API health: `http://127.0.0.1:8000/api/health`


Hook and clip scores are editorial estimates based on transcript patterns and timing heuristics. They do not predict virality or guarantee views. Always preview the five recommendations and adjust cuts manually where needed.
