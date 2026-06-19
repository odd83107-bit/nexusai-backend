# NexusAI Shopping Agent

FastAPI server that controls a Playwright-based Amazon browsing agent.

## Install Python dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

## Install Playwright browser

In filtered networks such as NetFree, the regular Playwright browser download may be blocked. Recommended options:

1. Try the official installer first:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

1. If the browser download is blocked, install Chrome/Edge manually through an approved NetFree route and update `agent.py` to launch with an installed browser channel.

## Run server

```powershell
.\.venv\Scripts\uvicorn.exe server:app --reload --host 127.0.0.1 --port 8000
```

## API examples

Health check:

```powershell
curl.exe http://127.0.0.1:8000/health
```

Search Amazon:

```powershell
curl.exe -X POST http://127.0.0.1:8000/amazon/search -H "Content-Type: application/json" -d '{"query":"wireless mouse","limit":3}'
```

## Deploy to the cloud

The project includes a `Dockerfile` based on the official Playwright Python image so Chromium is pre-installed. The server listens on the port provided by the `PORT` environment variable (defaults to `8000`).

### Option 1: Render

1. Go to [render.com](https://render.com) and sign up.
2. Create a new **Web Service**.
3. Connect your GitHub repository containing this project.
4. Select **Docker** runtime.
5. Render reads `render.yaml` automatically, or use the following settings:
   - **Build Command:** (leave empty for Docker)
   - **Start Command:** `python -m uvicorn server:app --host 0.0.0.0 --port 8000`
   - **Health Check Path:** `/health`
6. Deploy. Render will give you a stable HTTPS URL.

### Option 2: Railway

1. Go to [railway.app](https://railway.app) and sign up.
2. Create a new project from your GitHub repo.
3. Railway detects the `Dockerfile` automatically.
4. Set the environment variable `PORT=8000` (Railway usually injects it automatically).
5. Deploy. Railway provides a stable HTTPS URL.

### Important: update Lovable frontend URL

After deployment, replace the local tunnel URL in your frontend code with the new cloud URL:

```text
https://<your-cloud-app-url>/amazon/search
https://<your-cloud-app-url>/amazon/details
```

### Notes

- The container uses ephemeral disk, so `search_cache.json` is reset on each restart.
- The Playwright browser is included in the Docker image.
