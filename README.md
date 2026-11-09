# ClipForge Studio

Browser-based video editor with local Whisper transcription, optional cloud voice isolation + Scribe v2 captions, automatic highlight selection, and MP4 export.

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
- After the video is downloaded into the browser, Whisper, caption timing, emoji decoration, clip selection, and export run on the user's computer. WebGPU is used when supported.
- No automatic publishing to YouTube and no watermark.

### Cloud captions: voice isolation + Scribe v2

The editor includes an optional **AI Bersihkan Suara + Caption** button. It sends a mono 16 kHz WAV extracted in the browser to the backend, then:
1. Calls ElevenLabs Audio Isolation to reduce background noise and isolate speech.
2. Sends the isolated audio to ElevenLabs Scribe v2 for Indonesian/English transcription, word-level timestamps, and speaker labels.
3. Converts word timings into short subtitle phrases for the Shorts-style renderer.

### Enable the paid cloud feature

1. Create an ElevenLabs account and generate an API key from its developer dashboard.
2. Deploy the latest repository changes to Render (the Blueprint adds ELEVENLABS_API_KEY as a secret environment variable).
3. In Render → your clipforge-studio-api service → Environment, add ELEVENLABS_API_KEY with your key as its value, then save/redeploy.
4. Open the GitHub Pages editor, reload, choose a video, and click **AI Bersihkan Suara + Caption**.

**Important:** Never put the API key in index.html, GitHub, or this chat. Keep it only in Render Environment. ElevenLabs charges for API usage according to the account's plan and usage; check its current pricing before processing long videos. Audio is sent to ElevenLabs for processing. Very noisy audio, overlapping voices, music mixed over speech, and distant microphones can still cause errors; voice isolation improves the odds but cannot guarantee perfect recovery. The existing local Whisper button remains available as a fallback.

Official API references:
- [ElevenLabs Audio Isolation](https://elevenlabs.io/docs/api-reference/audio-isolation/convert)
- [ElevenLabs Speech to Text / Scribe v2](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)

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
