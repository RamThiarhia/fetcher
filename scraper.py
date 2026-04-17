"""
BlastTV M3U Scraper — v6
Directly intercepts /api/v4/event/{id}?includePlaybackDetails=URL response.
Credentials via environment variables BLAST_EMAIL / BLAST_PASSWORD.
"""

import asyncio
import re
import sys
import os
import json
import logging
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
LOGIN_URL      = "https://app.blasttv.ph/login"
BASE_URL       = "https://app.blasttv.ph/live/"
API_BASE       = "https://app.blasttv.ph/api/v4/event/"
OUTPUT_FILE    = "fetch.m3u"
TIMEOUT_MS     = 60_000
PAGE_WAIT_MS   = 25_000
LOGIN_WAIT_MS  = 8_000
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO")
SCREENSHOT_DIR = Path("screenshots")
NETLOG_FILE    = Path("network_log.json")

DEFAULT_CHANNELS = ["300024"]

HLS_PATTERNS = [
    r"\.m3u8", r"\.m3u", r"/hls/", r"/stream/",
    r"/live/stream", r"/playlist", r"manifest",
    r"/index\.m", r"videoplayback", r"chunklist",
    r"token=", r"sig=",
]
HLS_REGEX = re.compile("|".join(HLS_PATTERNS), re.IGNORECASE)

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


# ── Login ─────────────────────────────────────────────────────────────────────
async def do_login(context) -> bool:
    email    = os.getenv("BLAST_EMAIL", "").strip()
    password = os.getenv("BLAST_PASSWORD", "").strip()

    if not email or not password:
        log.error("No credentials set. Add BLAST_EMAIL / BLAST_PASSWORD as GitHub Actions secrets.")
        return False

    page = await context.new_page()
    try:
        log.info("Logging in as %s ...", email)
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=TIMEOUT_MS)
        await page.wait_for_timeout(5000)

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        await page.screenshot(path=str(SCREENSHOT_DIR / "login_A_before.png"))

        email_selectors = [
            'input[type="email"]', 'input[name="email"]',
            'input[placeholder*="email" i]', 'input[id*="email" i]',
            'input[autocomplete="email"]',
        ]
        pass_selectors = [
            'input[type="password"]', 'input[name="password"]',
            'input[placeholder*="password" i]', 'input[id*="password" i]',
        ]
        submit_selectors = [
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Login")', 'button:has-text("Sign in")',
            'button:has-text("Log in")', 'button:has-text("Continue")',
        ]

        email_field = None
        for sel in email_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                email_field = sel
                break
            except Exception:
                continue

        pass_field = None
        for sel in pass_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                pass_field = sel
                break
            except Exception:
                continue

        if not email_field or not pass_field:
            log.error("Could not find login form fields.")
            await page.screenshot(path=str(SCREENSHOT_DIR / "login_B_fields_not_found.png"))
            return False

        await page.fill(email_field, email)
        await page.wait_for_timeout(500)
        await page.fill(pass_field, password)
        await page.wait_for_timeout(500)

        submit_btn = None
        for sel in submit_selectors:
            try:
                await page.wait_for_selector(sel, timeout=2000)
                submit_btn = sel
                break
            except Exception:
                continue

        if submit_btn:
            await page.click(submit_btn)
        else:
            await page.keyboard.press("Enter")

        await page.wait_for_timeout(LOGIN_WAIT_MS)
        await page.screenshot(path=str(SCREENSHOT_DIR / "login_C_after.png"))

        current_url = page.url
        log.info("  Post-login URL: %s", current_url)

        if "login" in current_url.lower():
            log.error("Still on login page — credentials may be wrong.")
            return False

        log.info("  ✅ Login successful!")
        return True

    except Exception as exc:
        log.error("Login failed with exception: %s", exc)
        return False
    finally:
        await page.close()


# ── Core scraper ──────────────────────────────────────────────────────────────
async def scrape_channel(page, channel_id: str, pass_num: int = 1):
    url          = f"{BASE_URL}{channel_id}"
    found        = []
    api_result   = {}
    all_requests = []

    def on_request(request):
        req_url = request.url
        all_requests.append({"type": "request", "url": req_url,
                              "resource": request.resource_type})
        if HLS_REGEX.search(req_url):
            log.info("  🎯 Request intercepted: %s", req_url)
            found.append(req_url)

    async def on_response(response):
        resp_url     = response.url
        status       = response.status
        content_type = response.headers.get("content-type", "")
        all_requests.append({"type": "response", "url": resp_url,
                              "status": status, "ct": content_type})
        try:
            # ── Directly capture the event API response ──────────────────
            if f"/api/v4/event/{channel_id}" in resp_url:
                try:
                    body = await response.json()
                    log.info("  📡 Event API response (%d): %s", status, json.dumps(body)[:800])
                    api_result["raw"] = body
                    search_json(body, found)
                except Exception:
                    try:
                        text = await response.text()
                        log.info("  📡 Event API raw text (%d): %s", status, text[:800])
                        api_result["text"] = text
                        urls = extract_urls_from_text(text)
                        found.extend(urls)
                    except Exception:
                        pass
                return

            if HLS_REGEX.search(resp_url):
                log.info("  🎯 Response URL (%d): %s", status, resp_url)
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
                        found.extend(urls)
                    except Exception:
                        pass

            elif any(t in content_type for t in ("text", "javascript", "xml")):
                try:
                    text = await response.text()
                    urls = extract_urls_from_text(text)
                    for u in urls:
                        log.info("  🎯 Found in text/js: %s", u)
                    found.extend(urls)
                except Exception:
                    pass

        except Exception as exc:
            log.debug("  Response parse error for %s: %s", resp_url, exc)

    page.on("request",  on_request)
    page.on("response", on_response)

    try:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        log.info("Pass %d — Loading channel %s → %s", pass_num, channel_id, url)
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

        ss1 = SCREENSHOT_DIR / f"{channel_id}_pass{pass_num}_A_domloaded.png"
        await page.screenshot(path=str(ss1))
        log.info("  📸 %s", ss1)

        title = await page.title()
        log.info("  Page title: %r", title)

        log.info("  Waiting %ds for player...", PAGE_WAIT_MS // 1000)
        await page.wait_for_timeout(PAGE_WAIT_MS)

        ss2 = SCREENSHOT_DIR / f"{channel_id}_pass{pass_num}_B_after_wait.png"
        await page.screenshot(path=str(ss2))
        log.info("  📸 %s", ss2)

        # DOM / player extraction
        player_urls = await page.evaluate("""() => {
            const urls = [];
            for (const key of Object.keys(window)) {
                try {
                    const obj = window[key];
                    if (obj && obj.url && typeof obj.url === 'string' &&
                        obj.url.includes('m3u')) urls.push(obj.url);
                    if (obj && obj._hls && obj._hls.url) urls.push(obj._hls.url);
                    if (obj && obj.hls  && obj.hls.url)  urls.push(obj.hls.url);
                } catch(e) {}
            }
            document.querySelectorAll('video').forEach(v => {
                if (v.src)        urls.push(v.src);
                if (v.currentSrc) urls.push(v.currentSrc);
            });
            document.querySelectorAll('source').forEach(s => { if (s.src) urls.push(s.src); });
            const rx = /https?:\\/\\/[^\\s'"<>]+(?:\\.m3u8|\\.m3u|\\/hls\\/|\\/stream\\/|manifest)[^\\s'"<>]*/gi;
            document.querySelectorAll('script').forEach(s => {
                (s.innerHTML.match(rx) || []).forEach(u => urls.push(u));
            });
            ['__NUXT__','__NEXT_DATA__','__INITIAL_STATE__','__APP_STATE__'].forEach(k => {
                try { (JSON.stringify(window[k]||{}).match(rx)||[]).forEach(u=>urls.push(u)); } catch(e){}
            });
            return [...new Set(urls.filter(u => u && u.startsWith('http')))];
        }""")

        for u in player_urls:
            if HLS_REGEX.search(u):
                log.info("  🎯 Found via player/DOM: %s", u)
                found.append(u)

        # Save full API response for debugging
        if api_result:
            api_dump = Path(f"api_response_{channel_id}.json")
            api_dump.write_text(json.dumps(api_result, indent=2), encoding="utf-8")
            log.info("  💾 API response saved → %s", api_dump)

    except Exception as exc:
        log.warning("  Page error for %s: %s", channel_id, exc)
    finally:
        page.remove_listener("request",  on_request)
        page.remove_listener("response", on_response)

    NETLOG_FILE.write_text(
        json.dumps(all_requests, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("  📋 Network log (%d entries) → %s", len(all_requests), NETLOG_FILE)

    if not found:
        return None

    seen   = list(dict.fromkeys(found))
    master = next((u for u in seen if "master" in u or "index" in u), seen[0])
    log.info("  ✅ Using: %s", master)
    return master


# ── Build M3U ─────────────────────────────────────────────────────────────────
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
                "--allow-running-insecure-content",
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

        logged_in = await do_login(context)
        if not logged_in:
            log.warning("Proceeding without login — streams may not load.")

        for channel_id in channels:
            page = await context.new_page()
            stream_url = await scrape_channel(page, channel_id, pass_num=1)

            if not stream_url:
                log.info("  Retrying %s with extended wait...", channel_id)
                await page.close()
                page = await context.new_page()
                stream_url = await scrape_channel(page, channel_id, pass_num=2)

            await page.close()
            results.append({
                "id":   channel_id,
                "name": f"BlastTV {channel_id}",
                "url":  stream_url,
            })

        await browser.close()

    return results


def write_m3u(entries: list, output: str) -> int:
    path = Path(output)
    lines = ["#EXTM3U\n"]
    found_count = 0

    for entry in entries:
        if not entry["url"]:
            log.warning("  ❌ No stream for channel %s", entry["id"])
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
    found   = write_m3u(entries, OUTPUT_FILE)

    if NETLOG_FILE.exists():
        log.info("\n── Network summary ──────────────────────────────────────")
        netlog = json.loads(NETLOG_FILE.read_text())
        media  = [r for r in netlog if r["type"] == "request"
                  and r.get("resource") in ("media", "xhr", "fetch", "document")]
        for r in media[:40]:
            log.info("  [%s] %s", r.get("resource", "?"), r["url"][:120])

    if found == 0:
        log.error(
            "\nNo streams found. Tips:\n"
            "  1. Check screenshots/ for what the player looks like\n"
            "  2. Check api_response_300024.json — this shows exactly what the API returned\n"
            "  3. The stream may be geo-blocked (requires Philippine IP)\n"
            "  4. Check network_log.json for more clues"
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
