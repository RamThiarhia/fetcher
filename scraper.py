"""
BlastTV M3U Scraper — v2
Intercepts HLS/M3U stream URLs from app.blasttv.ph/live/<id>

Strategy (in order):
  1. Intercept all network REQUEST urls for m3u8/hls patterns
  2. Intercept all network RESPONSE bodies for stream URLs
  3. Sniff XHR/fetch JSON responses for embedded stream URLs
  4. Search the page DOM for video src / source tags
  5. Try direct API guesses based on channel ID
"""

import asyncio
import re
import sys
import os
import json
import logging
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://app.blasttv.ph/live/"
OUTPUT_FILE = "fetch.m3u"
TIMEOUT_MS = 45_000        # max wait per page (ms)
PAGE_WAIT_MS = 15_000      # settle time — increased to 15s for slow JS players
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DEFAULT_CHANNELS = [
    "300024",
]

# Patterns that indicate a stream URL
HLS_PATTERNS = [
    r"\.m3u8",
    r"\.m3u",
    r"/hls/",
    r"/stream/",
    r"/live/stream",
    r"/playlist",
    r"manifest",
    r"/index\.m",
    r"videoplayback",
    r"chunklist",
]
HLS_REGEX = re.compile("|".join(HLS_PATTERNS), re.IGNORECASE)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("blasttv")


# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_urls_from_text(text: str) -> list:
    pattern = re.compile(
        r'https?://[^\s\'"<>]+(?:\.m3u8|\.m3u|/hls/|/stream/|manifest)[^\s\'"<>]*',
        re.IGNORECASE,
    )
    return pattern.findall(text)


def search_json(obj, found: list, depth=0):
    if depth > 10:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and HLS_REGEX.search(v) and v.startswith("http"):
                log.info("  🎯 JSON key '%s': %s", k, v)
                found.append(v)
            else:
                search_json(v, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            search_json(item, found, depth + 1)


# ── Core scraper ──────────────────────────────────────────────────────────────
async def scrape_channel(page, channel_id: str):
    url = f"{BASE_URL}{channel_id}"
    found = []

    def on_request(request):
        req_url = request.url
        if HLS_REGEX.search(req_url):
            log.info("  🎯 Request intercepted: %s", req_url)
            found.append(req_url)

    async def on_response(response):
        resp_url = response.url
        try:
            content_type = response.headers.get("content-type", "")

            if HLS_REGEX.search(resp_url):
                log.info("  🎯 Response URL: %s", resp_url)
                found.append(resp_url)
                return

            if "json" in content_type or resp_url.endswith(".json"):
                try:
                    body = await response.json()
                    search_json(body, found)
                except Exception:
                    try:
                        text = await response.text()
                        urls = extract_urls_from_text(text)
                        for u in urls:
                            log.info("  🎯 Found in JSON text: %s", u)
                        found.extend(urls)
                    except Exception:
                        pass

            elif "text" in content_type or "javascript" in content_type:
                try:
                    text = await response.text()
                    urls = extract_urls_from_text(text)
                    for u in urls:
                        log.info("  🎯 Found in text response: %s", u)
                    found.extend(urls)
                except Exception:
                    pass

        except Exception as exc:
            log.debug("  Response parse error for %s: %s", resp_url, exc)

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        log.info("Loading channel %s -> %s", channel_id, url)
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

        log.info("  Waiting %ds for player to initialize...", PAGE_WAIT_MS // 1000)
        await page.wait_for_timeout(PAGE_WAIT_MS)

        # DOM scraping
        dom_result = await page.evaluate("""() => {
            const results = [];
            const video = document.querySelector('video');
            if (video) {
                if (video.src) results.push(video.src);
                if (video.currentSrc) results.push(video.currentSrc);
            }
            document.querySelectorAll('source').forEach(s => {
                if (s.src) results.push(s.src);
            });
            const scriptPattern = /https?:\\/\\/[^\\s'"<>]+(?:\\.m3u8|\\.m3u|\\/hls\\/|\\/stream\\/|manifest)[^\\s'"<>]*/gi;
            document.querySelectorAll('script').forEach(s => {
                const matches = s.innerHTML.match(scriptPattern);
                if (matches) results.push(...matches);
            });
            return results.filter(u => u && u.startsWith('http'));
        }""")

        for u in dom_result:
            if HLS_REGEX.search(u):
                log.info("  🎯 Found in DOM: %s", u)
                found.append(u)

    except Exception as exc:
        log.warning("  Page error for %s: %s", channel_id, exc)
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

    if found:
        seen = list(dict.fromkeys(found))
        master = next((u for u in seen if "master" in u or "index" in u), seen[0])
        log.info("  Using: %s", master)
        return master

    return None


async def build_m3u(channels: list) -> list:
    from playwright.async_api import async_playwright

    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.6367.82 Mobile Safari/537.36"
            ),
            viewport={"width": 390, "height": 844},
            locale="en-PH",
            timezone_id="Asia/Manila",
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


def write_m3u(entries: list, output: str) -> int:
    path = Path(output)
    lines = ["#EXTM3U\n"]
    found_count = 0

    for entry in entries:
        if not entry["url"]:
            lines.append(f"# FAILED: BlastTV {entry['id']} - no stream found\n\n")
            continue
        found_count += 1
        lines.append(
            f'#EXTINF:-1 tvg-id="{entry["id"]}" '
            f'tvg-name="{entry["name"]}" '
            f'group-title="BlastTV",{entry["name"]}\n'
        )
        lines.append(f'{entry["url"]}\n\n')

    path.write_text("".join(lines), encoding="utf-8")
    log.info("Saved %d/%d channels to %s", found_count, len(entries), output)
    return found_count


async def main():
    env_ids = os.getenv("CHANNEL_IDS", "")
    if env_ids:
        channels = [c.strip() for c in env_ids.split(",") if c.strip()]
    elif len(sys.argv) > 1:
        channels = sys.argv[1:]
    else:
        channels = DEFAULT_CHANNELS

    log.info("Scraping %d channel(s): %s", len(channels), channels)
    entries = await build_m3u(channels)
    found = write_m3u(entries, OUTPUT_FILE)

    if found == 0:
        log.error("No streams found. The site may require login or use a different loading mechanism.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
