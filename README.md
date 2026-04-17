# BlastTV M3U Scraper

Automatically scrapes HLS/M3U stream URLs from **app.blasttv.ph** and saves them to `fetch.m3u`. Runs on GitHub Actions every 6 hours (or on-demand).

---

## 📁 Project Structure

```
blasttv-scraper/
├── .github/
│   └── workflows/
│       └── scrape.yml      # GitHub Actions workflow
├── scraper.py              # Main Python scraper
├── requirements.txt        # Python dependencies
├── fetch.m3u               # Output playlist (auto-generated)
└── .gitignore
```

---

## 🚀 Quick Setup Guide

### Step 1 — Create a GitHub Repository

1. Go to [github.com/new](https://github.com/new)
2. Name it e.g. `blasttv-scraper`
3. Set it to **Public** or **Private** (your choice)
4. Click **Create repository**

### Step 2 — Upload the Files

Option A — via GitHub web UI:
1. Click **Add file → Upload files**
2. Upload all files keeping the folder structure intact

Option B — via Git CLI:
```bash
git clone https://github.com/YOUR_USERNAME/blasttv-scraper.git
cd blasttv-scraper
# copy all project files here
git add .
git commit -m "init: BlastTV M3U scraper"
git push
```

### Step 3 — Enable Workflow Permissions

1. Go to your repo → **Settings → Actions → General**
2. Under **Workflow permissions**, select **Read and write permissions**
3. Click **Save**

This allows the workflow to push `fetch.m3u` back to the repo automatically.

### Step 4 — Run It!

**Manual trigger:**
1. Go to **Actions** tab in your repo
2. Click **Scrape BlastTV M3U**
3. Click **Run workflow**
4. Optionally enter channel IDs (e.g. `300024,300025,300026`)
5. Click the green **Run workflow** button

**Automatic:** Runs every 6 hours via cron automatically.

---

## ⚙️ Configuration

### Change Which Channels to Scrape

Edit `scraper.py`, find the `DEFAULT_CHANNELS` list:

```python
DEFAULT_CHANNELS = [
    "300024",
    "300025",   # add more IDs here
    "300026",
]
```

Or pass them at runtime without editing code:
```bash
# CLI
python scraper.py 300024 300025 300026

# Environment variable
CHANNEL_IDS="300024,300025,300026" python scraper.py
```

Or via the **workflow_dispatch** input when triggering manually on GitHub.

### Change Run Frequency

Edit `.github/workflows/scrape.yml`, find the `cron` line:

```yaml
- cron: "0 */6 * * *"   # every 6 hours
- cron: "0 */1 * * *"   # every hour
- cron: "0 0 * * *"     # once a day at midnight UTC
```

---

## 📺 Using the M3U Playlist

After the workflow runs, `fetch.m3u` will appear in your repo.

**Raw URL format:**
```
https://raw.githubusercontent.com/YOUR_USERNAME/blasttv-scraper/main/fetch.m3u
```

Load this URL in:
- **VLC** → Media → Open Network Stream
- **Kodi** → IPTV Simple Client plugin
- **TiviMate**, **OTT Navigator**, or any IPTV player

---

## 🛠 Local Development

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run scraper locally
python scraper.py

# Run with specific channels
python scraper.py 300024 300025
```

---

## ❓ Troubleshooting

| Problem | Solution |
|---|---|
| Workflow fails with "Permission denied" | Enable **Read and write permissions** in repo Settings → Actions |
| `fetch.m3u` is empty / has no URLs | The site may have changed its player. Open an issue or try increasing `PAGE_WAIT_MS` in `scraper.py` |
| Playwright install fails | Make sure `requirements.txt` is present and `playwright install chromium --with-deps` ran |
| Want more channels | Add IDs to `DEFAULT_CHANNELS` in `scraper.py` |

---

## ⚠️ Disclaimer

This tool is for personal/educational use only. Respect the terms of service of the site being scraped.
