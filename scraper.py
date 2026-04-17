"""
BlastTV M3U Scraper
Intercepts HLS/M3U stream URLs from app.blasttv.ph/live/<id> pages.
"""

import asyncio
import re
import sys
import os
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Request

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://app.blasttv.ph/live/"
OUTPUT_FILE = "fetch.m3u"
TIMEOUT_MS = 30_000          # max wait per channel (ms)
PAGE_WAIT_MS = 8_000         # extra settle time after load
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Channel IDs to scrape – override via env var CHANNEL_IDS (comma-separated)
DEFAULT_CHANNELS = [
    "300024",
]

# M3U URL patterns to capture
HLS_PATTERNS = [
    r"\.m3u8",
    r"\.m3u",
    r"/hls/",
    r"/live/stream",
    r"/playlist",
    r"manifest",
]
HLS_REGEX = re.compile("|".join(HLS_PATTERNS), re.IGNORECASE)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("blasttv")


# ── Core scraper ──────────────────────────────────────────────────────────────
async def scrape_channel(page, channel_id: str) -> str | None:
    """Load a channel page and intercept the HLS stream URL."""
    url = f"{BASE_URL}{channel_id}"
    found: list[str] = []

    def on_request(request: Request):
        req_url = request.url
        if HLS_REGEX.search(req_url):
            log.info("  🎯 Intercepted: %s", req_url)
            found.append(req_url)

    page.on("request", on_request)

    try:
        log.info("Loading channel %s → %s", channel_id, url)
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        # Wait for JS player to boot and fire stream requests
        await page.wait_for_timeout(PAGE_WAIT_MS)
    except Exception as exc:
        log.warning("  ⚠️  Page load error for %s: %s", channel_id, exc)
    finally:
        page.remove_listener("request", on_request)

    if found:
        # Prefer master/index playlists over segment URLs
        master = next((u for u in found if "index" in u or "master" in u), found[0])
        return master

    # Fallback: try scraping <source> or video src from DOM
    try:
        src = await page.evaluate("""() => {
            const v = document.querySelector('video');
            if (v && v.src) return v.src;
            const s = document.querySelector('source');
            if (s && s.src) return s.src;
            return null;
        }""")
        if src and HLS_REGEX.search(src):
            log.info("  🎯 Found in DOM: %s", src)
            return src
    except Exception:
        pass

    log.warning("  ❌ No stream URL found for channel %s", channel_id)
    return None


async def build_m3u(channels: list[str]) -> list[dict]:
    """Scrape all channels and return a list of {id, name, url} dicts."""
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )

        for channel_id in channels:
            page = await context.new_page()
            stream_url = await scrape_channel(page, channel_id)
            await page.close()

            results.append({
                "id": channel_id,
                "name": f"BlastTV {channel_id}",
                "url": stream_url,
            })

        await browser.close()

    return results


def write_m3u(entries: list[dict], output: str):
    """Write results to an M3U playlist file."""
    path = Path(output)
    lines = ["#EXTM3U\n"]

    found_count = 0
    for entry in entries:
        if not entry["url"]:
            lines.append(f"# FAILED: BlastTV {entry['id']} — no stream found\n")
            continue
        found_count += 1
        lines.append(
            f'#EXTINF:-1 tvg-id="{entry["id"]}" '
            f'tvg-name="{entry["name"]}" '
            f'group-title="BlastTV",{entry["name"]}\n'
        )
        lines.append(f'{entry["url"]}\n')
        lines.append("\n")

    path.write_text("".join(lines), encoding="utf-8")
    log.info("✅ Saved %d/%d channels to %s", found_count, len(entries), output)
    return found_count


# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    # Support channel list from env or CLI args
    env_ids = os.getenv("CHANNEL_IDS", "")
    if env_ids:
        channels = [c.strip() for c in env_ids.split(",") if c.strip()]
    elif len(sys.argv) > 1:
        channels = sys.argv[1:]
    else:
        channels = DEFAULT_CHANNELS

    log.info("🚀 Scraping %d channel(s): %s", len(channels), channels)
    entries = await build_m3u(channels)
    found = write_m3u(entries, OUTPUT_FILE)

    # Exit non-zero if nothing was found (makes GH Actions fail visibly)
    if found == 0:
        log.error("No streams found — check selectors or site changes.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
