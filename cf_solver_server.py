#!/usr/bin/env python3
"""
Cloudflare Bypass Solver + Relay Server
=========================================
Two jobs:
  1. /solve  - Solve CF challenges using Chrome (get cf_clearance cookies)
  2. RELAY   - When Burp sends a request with X-CF-Relay header, this server
               adds CF cookies and forwards to the real server, returning the
               real response back to Burp.

Burp gets the REAL 200 response directly — no setResponse() hacks.

Usage:
    pip install -r requirements.txt
    python cf_solver_server.py [--port 8899]
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import urlparse, urlencode

import requests as py_requests
from flask import Flask, jsonify, request, Response, make_response

try:
    import undetected_chromedriver as uc
    USE_UC = True
except ImportError:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    USE_UC = False
    print("[!] undetected-chromedriver not found, falling back to selenium")
    print("[!] pip install undetected-chromedriver for best results")

from selenium.webdriver.common.by import By

# ---------------------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cf-solver")

cookie_cache = {}       # domain -> {name: value}
cookie_cache_lock = threading.Lock()
browser_instance = None
browser_lock = threading.Lock()
HEADLESS_MODE = False
BROWSER_UA = None       # real User-Agent of the solver browser (cf_clearance is bound to it)
FALLBACK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36")


def detect_chrome_major():
    """Return the installed Chrome major version (e.g. 154), or None."""
    version = None
    try:
        if sys.platform.startswith("win"):
            import winreg
            for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Google\Chrome\BLBeacon"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        version = winreg.QueryValueEx(k, "version")[0]
                        break
                except OSError:
                    continue
            if not version:
                for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                             os.environ.get("LOCALAPPDATA")):
                    exe = os.path.join(base or "", "Google", "Chrome", "Application", "chrome.exe")
                    if os.path.exists(exe):
                        out = subprocess.check_output(
                            ["powershell", "-NoProfile", "-Command",
                             "(Get-Item '%s').VersionInfo.ProductVersion" % exe],
                            text=True, timeout=15)
                        version = out.strip()
                        break
        else:
            for cmd in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
                try:
                    out = subprocess.check_output([cmd, "--version"], text=True,
                                                  stderr=subprocess.DEVNULL, timeout=15)
                    version = out
                    break
                except Exception:
                    continue
    except Exception as e:
        log.warning("Chrome version detection failed: %s", e)
    m = re.search(r"(\d+)\.\d+\.\d+\.\d+", version or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
def get_browser():
    global browser_instance
    with browser_lock:
        if browser_instance is not None:
            try:
                _ = browser_instance.title
                return browser_instance
            except Exception:
                browser_instance = None

        log.info("Launching Chrome...")
        if USE_UC:
            options = uc.ChromeOptions()
            if HEADLESS_MODE:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--window-size=1920,1080")
            # Move window off-screen so it doesn't pop up visibly
            options.add_argument("--window-position=-2400,-2400")
            # No custom --user-agent: let Chrome send its real UA so it always
            # matches the installed version (a mismatch triggers CF challenges).
            major = detect_chrome_major()
            log.info("Detected Chrome major version: %s", major or "unknown (auto)")
            browser_instance = uc.Chrome(options=options, version_main=major)
        else:
            options = ChromeOptions()
            if HEADLESS_MODE:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--window-position=-2400,-2400")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            browser_instance = webdriver.Chrome(options=options)
            browser_instance.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            })

        global BROWSER_UA
        try:
            BROWSER_UA = browser_instance.execute_script("return navigator.userAgent")
            log.info("Chrome launched (UA: %s)", BROWSER_UA)
        except Exception:
            log.info("Chrome launched")
        return browser_instance


def close_browser():
    global browser_instance
    with browser_lock:
        if browser_instance:
            try: browser_instance.quit()
            except: pass
            browser_instance = None


# ---------------------------------------------------------------------------
# Challenge Solver
# ---------------------------------------------------------------------------
def solve_challenge(url, existing_cookies=None, timeout=45):
    parsed = urlparse(url)
    domain = parsed.hostname
    driver = get_browser()

    try:
        if existing_cookies:
            try:
                driver.get(f"{parsed.scheme}://{domain}/")
                time.sleep(1)
            except: pass
            for c in existing_cookies:
                try:
                    driver.add_cookie({
                        "name": c.get("name"), "value": c.get("value"),
                        "domain": c.get("domain", domain), "path": "/"
                    })
                except: pass

        log.info("Navigating to: %s", url)
        driver.get(url)
        log.info("Waiting for CF challenge (up to %ds)...", timeout)

        start = time.time()
        solved = False
        while time.time() - start < timeout:
            title = driver.title
            src = driver.page_source[:5000]
            cookies = driver.get_cookies()
            names = [c["name"] for c in cookies]

            has_cf = "cf_clearance" in names
            is_challenge = (
                "Just a moment" in title or
                "Checking your browser" in title or
                "challenge-platform" in src or
                "Performing security verification" in src
            )
            if has_cf and not is_challenge:
                solved = True
                break
            if not is_challenge and "challenge" not in src.lower():
                solved = True
                break
            time.sleep(1.5)

        if not solved:
            cookies = driver.get_cookies()
            if "cf_clearance" in [c["name"] for c in cookies]:
                solved = True

        if not solved:
            return {"status": "timeout", "message": "Challenge not solved in %ds" % timeout}

        all_cookies = driver.get_cookies()
        cookies_dict = {c["name"]: c["value"] for c in all_cookies}

        with cookie_cache_lock:
            cookie_cache[domain] = cookies_dict.copy()

        log.info("SOLVED! %d cookies cached for %s", len(cookies_dict), domain)
        return {
            "status": "solved",
            "cookies_dict": cookies_dict,
            "cf_clearance": cookies_dict.get("cf_clearance", ""),
            "domain": domain,
        }

    except Exception as e:
        log.error("Solve error: %s", e)
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# RELAY HANDLER — the key to getting 200 in Burp
# ---------------------------------------------------------------------------
@app.before_request
def handle_relay():
    """
    If the request has X-CF-Relay header, it's from Burp and needs to be
    relayed to the real server with CF cookies.
    """
    relay_scheme = request.headers.get("X-CF-Relay")
    if not relay_scheme:
        return None  # Not a relay request, let Flask route normally

    # --- This is a relay request from Burp ---
    host_header = request.headers.get("Host", "")
    # Remove port from host if present
    domain = host_header.split(":")[0] if host_header else ""

    if not domain:
        return Response("Missing Host header", status=400)

    # Build target URL
    path = request.path
    qs = request.query_string.decode("utf-8", errors="replace")
    target_url = "%s://%s%s" % (relay_scheme, domain, path)
    if qs:
        target_url += "?" + qs

    log.info("[RELAY] %s %s", request.method, target_url)

    # Get cached cookies
    with cookie_cache_lock:
        cookies = cookie_cache.get(domain, {}).copy()

    # If no cookies, auto-solve first
    if not cookies or "cf_clearance" not in cookies:
        log.info("[RELAY] No cookies for %s — solving first...", domain)
        solve_url = "%s://%s/" % (relay_scheme, domain)
        result = solve_challenge(solve_url, timeout=45)
        if result.get("status") == "solved":
            with cookie_cache_lock:
                cookies = cookie_cache.get(domain, {}).copy()
            log.info("[RELAY] Solved! Now relaying...")
        else:
            log.error("[RELAY] Solve failed: %s", result.get("message"))
            # Return a clear error response
            error_body = json.dumps({
                "error": "CF Bypass: Challenge solve failed",
                "detail": result.get("message", "unknown"),
                "hint": "Try running without --headless"
            })
            return Response(error_body, status=503, content_type="application/json")

    # Forward headers (skip internal/hop-by-hop ones)
    skip = {"x-cf-relay", "host", "content-length", "transfer-encoding",
            "connection", "accept-encoding"}
    fwd_headers = {}
    for key, val in request.headers:
        if key.lower() not in skip:
            fwd_headers[key] = val
    # Ensure proper Accept-Encoding (no gzip so response is readable in Burp)
    fwd_headers["Accept-Encoding"] = "identity"
    # cf_clearance is bound to the solver browser's User-Agent, so always use it
    fwd_headers["User-Agent"] = BROWSER_UA or FALLBACK_UA.format(v=detect_chrome_major() or 152)

    # Make the real request
    try:
        resp = py_requests.request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            cookies=cookies,
            data=request.get_data() or None,
            timeout=30,
            allow_redirects=True,
            verify=True,
        )
    except Exception as e:
        log.error("[RELAY] Request failed: %s", e)
        return Response(
            json.dumps({"error": "Relay request failed", "detail": str(e)}),
            status=502, content_type="application/json"
        )

    log.info("[RELAY] Got HTTP %d (%d bytes) from %s",
             resp.status_code, len(resp.content), domain)

    # If we got another 403 with CF challenge, cookies might have expired
    if resp.status_code == 403 and ("cf_chl_opt" in resp.text[:3000] or
                                     "Just a moment" in resp.text[:3000]):
        log.info("[RELAY] Got CF challenge again — cookies expired, re-solving...")
        # Clear old cookies
        with cookie_cache_lock:
            cookie_cache.pop(domain, None)
        # Re-solve
        solve_url = "%s://%s/" % (relay_scheme, domain)
        result = solve_challenge(solve_url, timeout=45)
        if result.get("status") == "solved":
            with cookie_cache_lock:
                cookies = cookie_cache.get(domain, {}).copy()
            # Retry the request
            try:
                resp = py_requests.request(
                    method=request.method,
                    url=target_url,
                    headers=fwd_headers,
                    cookies=cookies,
                    data=request.get_data() or None,
                    timeout=30,
                    allow_redirects=True,
                    verify=True,
                )
                log.info("[RELAY] Retry got HTTP %d", resp.status_code)
            except Exception as e:
                return Response(
                    json.dumps({"error": "Retry failed", "detail": str(e)}),
                    status=502, content_type="application/json"
                )
        else:
            log.error("[RELAY] Re-solve failed")

    # Build Flask response to send back to Burp
    # This is what Burp will display in the Response tab
    relay_resp = make_response(resp.content, resp.status_code)

    # Copy response headers (skip problematic ones)
    skip_resp = {"transfer-encoding", "content-encoding", "content-length"}
    for name, value in resp.headers.items():
        if name.lower() not in skip_resp:
            relay_resp.headers[name] = value
    relay_resp.headers["Content-Length"] = str(len(resp.content))
    relay_resp.headers["X-CF-Relay"] = "true"

    return relay_resp


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "browser_ready": browser_instance is not None,
        "cached_domains": list(cookie_cache.keys()),
        "uc": USE_UC,
    })


@app.route("/solve", methods=["POST"])
def solve():
    data = request.get_json(force=True)
    url = data.get("url")
    if not url:
        return jsonify({"status": "error", "message": "Missing url"}), 400
    cookies = data.get("cookies", [])
    timeout = data.get("timeout", 45)
    result = solve_challenge(url, cookies, timeout)
    return jsonify(result)


@app.route("/cookies")
def get_cookies():
    domain = request.args.get("domain", "")
    with cookie_cache_lock:
        c = cookie_cache.get(domain, {})
    return jsonify({"domain": domain, "cookies": c})


@app.route("/clear", methods=["POST"])
def clear():
    with cookie_cache_lock:
        cookie_cache.clear()
    return jsonify({"status": "cleared"})


# ---------------------------------------------------------------------------
def main():
    global HEADLESS_MODE
    parser = argparse.ArgumentParser(description="CF Solver + Relay Server")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run Chrome headless (less reliable for CF)")
    parser.add_argument("--no-preload", action="store_true", default=False)
    args = parser.parse_args()
    HEADLESS_MODE = args.headless

    print(r"""
   ____ _____   ____        _
  / ___|  ___| / ___|  ___ | |_   _____ _ __
 | |   | |_    \___ \ / _ \| \ \ / / _ \ '__|
 | |___|  _|    ___) | (_) | |\ V /  __/ |
  \____|_|     |____/ \___/|_| \_/ \___|_|

  CF Solver + Relay Server v3.0
    """)
    log.info("Port: %d | Headless: %s | UC: %s", args.port, HEADLESS_MODE, USE_UC)

    if not args.no_preload:
        try:
            get_browser()
            log.info("Chrome ready!")
        except Exception as e:
            log.error("Chrome failed: %s", e)

    print()
    print("  HOW IT WORKS:")
    print("  Burp request → this server (adds CF cookies) → real server → response → Burp")
    print("  You see the REAL 200 response directly in Burp!")
    print()
    print("  Endpoints:")
    print(f"    RELAY: Any request with X-CF-Relay header → auto-relayed")
    print(f"    POST  http://127.0.0.1:{args.port}/solve   → Solve CF challenge")
    print(f"    GET   http://127.0.0.1:{args.port}/health  → Health check")
    print()

    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    finally:
        close_browser()

if __name__ == "__main__":
    main()
