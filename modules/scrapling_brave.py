"""Scrapling preset: drive your existing Brave with your existing logins.

No Chromium download. No sudo. No second browser. No re-login.

    from scrapling_brave import smart, fetch, dynamic, stealth

    smart("https://example.com")            # auto-escalates; fast when it can
    fetch("https://example.com")            # plain HTTP only
    dynamic("https://js-heavy.com")         # Brave, headless
    stealth("https://cloudflare-wall.com")  # Brave, headed, solves bot walls

The engines (Scrapling 0.5)
---------------------------
0.5 ships two: ``Fetcher`` -- plain HTTP with curl_cffi TLS impersonation -- and
the stealth browser, ``StealthyFetcher``/``StealthySession``, which is Patchright
underneath. The old vanilla-Playwright "dynamic" engine was removed upstream, so
``dynamic()`` and ``stealth()`` are the same engine, headless and headed
respectively; the two names are kept because the tiers mean different things.
``brave()`` points either one at the Brave already on this machine, with your
logins, so no browser is ever downloaded.

Why these settings exist
------------------------
* `executable_path` overrides Scrapling's hardcoded ``channel="chromium"``, so
  Playwright and Patchright never look for a bundled browser. Nothing downloads.
* `user_data_dir` switches Scrapling to ``launch_persistent_context``, which is
  what loads your cookies/logins from the copied profile.
* ``--password-store=gnome-libsecret`` is REQUIRED. Your cookies are encrypted
  with a key held by the GNOME keyring daemon, which is keyed to the *user*,
  not the profile path -- so the copy still decrypts. Drop this flag and the
  cookie jar silently comes back empty.
* Patchright sanitizes Chromium launch switches and removes the ``Runtime.enable``
  CDP tell at LAUNCH time. Attaching over CDP (``cdp_url``) skips all of that, so
  stealth scraping must let Patchright launch the browser itself. Use ``brave-cdp``
  for interactive browsing, not for stealth.

Headless fingerprint reality (measured on this machine)
------------------------------------------------------
Headless Brave reports ``HeadlessChrome/155`` in its UA, ``SwiftShader`` for
WebGL (software rasteriser), and no ``navigator.userAgentData`` at all. Scrapling
additionally stamps a *hardcoded* ``Chrome/153.0.0.0`` UA whenever
``headless=True``, which contradicts Brave's real 155. Headed mode on your
Wayland session corrects all four automatically, which is why ``stealth()`` is
headed by default.

Bulk / large-project usage
--------------------------
    with session(headless=False, solve_cloudflare=True) as s:
        for url in urls:
            page = s.fetch(url)        # one browser, cookies persist, ~3s/page

    # concurrent
    await async_smart(urls, concurrency=5)

    # full crawls with pause/resume checkpoints
    from scrapling.spiders import Spider
    MySpider(crawldir="./crawl_data").start()

    result.items.to_json("out.json")    # or to_jsonl / to_csv / to_xml
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Iterable, Optional

# Scrapling 0.5 has no vanilla-Playwright engine: `DynamicFetcher` was removed
# upstream, and the only browser engine it ships is the Patchright-based stealth
# one. Tier 2 below therefore runs that engine headless, and tier 3 runs it headed.
from scrapling.fetchers import (
    Fetcher,
    StealthyFetcher,
    AsyncStealthySession,
)
from scrapling.engines._browsers._stealth import StealthySession

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BRAVE_BIN = "/opt/brave.com/brave-nightly/brave"
PROFILE_DIR = "/home/dhh/.local/share/scrapling/brave-profile"

# Brave Nightly's true identity. Scrapling would otherwise stamp its own
# hardcoded Chrome/153 UA whenever headless=True, contradicting Brave's real 155
# in navigator.userAgentData. Passing this explicitly wins because Scrapling
# checks `if config.useragent:` *before* the headless branch.
BRAVE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)

# Launch flags. --password-store=gnome-libsecret is what makes the copied
# profile's cookies decrypt.
FLAGS = [
    "--password-store=gnome-libsecret",
    "--no-first-run",
    "--no-default-browser-check",
]

log = logging.getLogger("scrapling_brave")

# --------------------------------------------------------------------------- #
# Cloudflare / bot-wall detection
# --------------------------------------------------------------------------- #
# Scrapling's solve_cloudflare does NOT raise on failure. _stealth.py calls
# `self._cloudflare_solver(page)` and discards the return value, so a blocked
# page comes back as a normal Response with no exception. Detection therefore
# has to inspect the returned content.
#
# We reuse Scrapling's own detector first (catches the cType markers and the
# Turnstile script tag), then fall back to string sentinels it does not cover.
EXTRA_WALL_MARKERS = (
    "Just a moment...",
    "Checking your browser",
    "Attention Required! | Cloudflare",
    "cf_chl_opt",
    "/cdn-cgi/challenge-platform/",
    "cf-browser-verification",
    "challenges.cloudflare.com/turnstile",
    "Enable JavaScript and cookies to continue",
    "DDoS protection by Cloudflare",
)

WALL_STATUSES = {401, 403, 429, 503}

# Phrases that only ever appear on an anti-bot interstitial, never in real
# article text. Checked against visible text only, so a site merely *linking* to
# Cloudflare's challenge script in markup is not falsely flagged.
WALL_PHRASES = (
    "This website uses a security service to protect against malicious bots",
    "Please enable cookies to continue",
    "Enable JavaScript and cookies to continue",
    "Checking your browser before accessing",
    "Attention Required! | Cloudflare",
    "DDoS protection by Cloudflare",
    "confirm this search was made by a human",
    "bots use DuckDuckGo too",
    "Ray ID:",
)


def _page_html(page: Any) -> str:
    for attr in ("html_content", "body", "text"):
        val = getattr(page, attr, None)
        if isinstance(val, str) and val:
            return val
    try:
        return str(page)
    except Exception:  # pragma: no cover - defensive
        return ""


def _page_text(page: Any) -> str:
    """Visible text only. `::text` nodes exclude markup, so a page that merely
    references a challenge script URL is not mistaken for a challenge."""
    try:
        parts: list[str] = []
        for sel in ("h1::text", "h2::text", "h3::text", "p::text", "li::text",
                    "td::text", "span::text", "div::text"):
            try:
                parts += [str(v) for v in page.css(sel).getall()]
            except Exception:
                continue
        return " ".join(x.strip() for x in parts if x and x.strip())
    except Exception:  # pragma: no cover - defensive
        return ""


def is_blocked(page: Any) -> bool:
    """True if the response is a bot-wall rather than real content.

    Checks four independent signals because Cloudflare serves walls with 200,
    403, 429 and 503 alike, and a real 403 page is indistinguishable from a
    challenge page by status alone.
    """
    status = getattr(page, "status", None)
    html = _page_html(page)

    # 1. Scrapling's own detector (turnstile cType markers / script tag)
    try:
        if StealthySession._detect_cloudflare(html):
            return True
    except Exception:  # pragma: no cover - detector is best-effort
        pass

    # 2. Sentinels in raw markup
    if any(marker in html for marker in EXTRA_WALL_MARKERS):
        return True

    # 3. Sentinels in visible text (catches blocks whose markup is clean)
    text = _page_text(page)
    if text and any(phrase in text for phrase in WALL_PHRASES):
        return True

    # 4. A wall status code. A rate-limit or access-denied response is a block
    #    for scraping purposes no matter how large its body is - Brave Search
    #    returns 429 with a 68 KB page, so a body-size test was wrong.
    if status in WALL_STATUSES:
        return True

    # 5. Near-empty body. A 2xx with essentially no text is an anti-bot stub:
    #    DuckDuckGo's html/ endpoint answers 200/202 with a page whose only
    #    content is a noreply address (see DIAGNOSTIC.md - endpoint is dead).
    text_len = len(text)
    if text_len < 400 and len(html) < 20000 and status in (200, 202, 204):
        return True

    return False


# --------------------------------------------------------------------------- #
# Launcher options
# --------------------------------------------------------------------------- #

def brave(profile: str = "Default", headless: bool = True) -> dict:
    """Options for launching Brave on the profile copy, with your logins.

    Logins added in your daily Brave since the share was made are pulled in
    first, so a site you signed into this morning works this afternoon.
    """
    _sync_best_effort()
    return {
        "executable_path": BRAVE_BIN,
        "user_data_dir": PROFILE_DIR,
        "extra_flags": FLAGS + [f"--profile-directory={profile}"],
        "headless": headless,
    }


def _sync_best_effort() -> None:
    """Refresh our cookies/logins from the daily Brave before a launch.

    Best effort by design: the profile share is a point in time, and a stale
    one still scrapes. A problem here must never be the reason a scrape fails,
    and the sync itself does nothing while a scraper browser is already
    running.
    """
    try:
        from scrapling_sync import sync_credentials
        sync_credentials(quiet=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Tier 1 - plain HTTP
# --------------------------------------------------------------------------- #

def fetch(url: str, **kw):
    """Tier 1: plain HTTP via curl_cffi TLS impersonation. No browser, ~0.3s."""
    kw.setdefault("stealthy_headers", True)
    return Fetcher.get(url, **kw)


# --------------------------------------------------------------------------- #
# Tier 2 - headless browser
# --------------------------------------------------------------------------- #

def dynamic(url: str, profile: str = "Default", headless: bool = True, **kw):
    """Tier 2: the stealth browser + your Brave, with no window. For JS-rendered
    sites.

    Headless is still detectable (HeadlessChrome token, SwiftShader WebGL, no
    Client Hints), so this tier is for sites without a WAF. For WAF-protected
    sites use `stealth` or `smart`.
    """
    kw.setdefault("useragent", BRAVE_UA)
    kw.setdefault("solve_cloudflare", False)
    # Same engine as tier 3, run headless: 0.5 has no separate hidden-browser
    # engine. Wall-solving is left to tier 3.
    return StealthyFetcher.fetch(url, **brave(profile, headless), **kw)


# --------------------------------------------------------------------------- #
# Tier 3 - headed stealth
# --------------------------------------------------------------------------- #

def stealth(url: str, profile: str = "Default", headless: bool = False,
            solve_cloudflare: bool = True, **kw):
    """Tier 3: Patchright + your Brave, headed. For Cloudflare / anti-bot walls.

    Headed by default because on this machine headless gives away three signals
    at once: a `HeadlessChrome` UA token, SwiftShader for WebGL, and no
    Client Hints. Headed also disables Scrapling's hardcoded Chrome/153 UA
    override, so Brave reports its true identity. Patchright launches the
    browser itself (via `executable_path`) so its launch-switch patches stay
    active -- attaching over CDP would bypass them.
    """
    return StealthyFetcher.fetch(
        url, **brave(profile, headless), solve_cloudflare=solve_cloudflare, **kw
    )


def session(profile: str = "Default", headless: bool = False,
            solve_cloudflare: bool = True, max_pages: int = 1, **kw):
    """Long-lived stealth session. One browser, cookies persist, ~3s/page.

    Much faster than one-shot `stealth()` for bulk work, and the `cf_clearance`
    cookie carries between pages instead of being re-solved each time.

        with session(headless=False, solve_cloudflare=True) as s:
            for url in urls:
                page = s.fetch(url)

    Only one session class exists in 0.5: StealthySession (Patchright). The
    vanilla-Playwright DynamicSession was removed upstream along with its engine,
    so ``solve_cloudflare`` no longer changes which class is used.
    """
    opts = brave(profile, headless)
    return StealthySession(**opts, max_pages=max_pages, **kw)


# --------------------------------------------------------------------------- #
# The cascade
# --------------------------------------------------------------------------- #

def _headed(fn):
    """Run a headed fetch.

    A workspace guard was tried here and removed: on this Hyprland 0.56.2
    (Lua config) build `hyprctl` reads work but every write returns "ok" without
    taking effect, so the guard silently did nothing. See scrapling_workspace.py.
    """
    return fn()


def smart_traced(url: str, profile: str = "Default", start_tier: int = 1, **_kw):
    """Fetch `url`, escalating only as far as necessary.

    Returns ``(response, tier, trace)``. ``tier`` is the tier that produced the
    usable response (0 if none did). ``trace`` records every tier attempted and
    why it was rejected, so a caller can report partial failure honestly
    instead of silently returning whatever came back.

    :param start_tier: skip tiers below this. The batch runner uses the failure
        ledger to skip tiers a host has already proven it needs.
    """
    trace: list[dict] = []
    tiers = [
        (1, "HTTP", lambda: fetch(url, **_kw)),
        (2, "headless", lambda: dynamic(url, profile=profile, **_kw)),
        # Tier 3 opens a real window, so it is wrapped in the workspace guard:
        # the window lands on the scraper workspace and your view is handed
        # straight back.
        (3, "headed-stealth", lambda: _headed(lambda: stealth(url, profile=profile, **_kw))),
    ]

    for n, name, run in tiers:
        if n < start_tier:
            trace.append({"tier": n, "name": name, "outcome": "skipped"})
            continue
        t0 = time.monotonic()
        try:
            r = run()
        except Exception as exc:
            trace.append({"tier": n, "name": name, "outcome": "error",
                          "error": type(exc).__name__, "detail": str(exc)[:200],
                          "secs": round(time.monotonic() - t0, 2)})
            log.info("tier %d (%s) failed (%s), escalating", n, name, type(exc).__name__)
            continue

        blocked = is_blocked(r)
        trace.append({"tier": n, "name": name,
                      "outcome": "blocked" if blocked else "ok",
                      "status": getattr(r, "status", None),
                      "secs": round(time.monotonic() - t0, 2)})
        if not blocked:
            return r, n, trace
        log.info("tier %d (%s) hit a wall, escalating", n, name)

    return None, 0, trace


def smart(url: str, profile: str = "Default", **_kw):
    """Fetch `url`, escalating only as far as necessary.

    Tier 1 plain HTTP (~0.3s, invisible) -> Tier 2 headless Brave (~3s)
    -> Tier 3 headed stealth Brave (~10s, window appears).

    Returns the first response that is not a bot-wall. If every tier fails the
    last response is returned anyway -- use `smart_traced` if you need to know.
    """
    start = _kw.pop("start_tier", 1)
    last = None
    for n in (1, 2, 3):
        r, tier, _ = smart_traced(url, profile=profile, start_tier=max(start, n), **_kw)
        if r is not None:
            return r
        last = last or r
    return last


async def async_smart(urls: Iterable[str], profile: str = "Default",
                      concurrency: int = 5, **_kw) -> list:
    """Concurrent `smart` across many URLs for bulk work."""
    sem = asyncio.Semaphore(concurrency)

    async def one(u: str):
        async with sem:
            return await asyncio.to_thread(smart, u, profile=profile, **_kw)

    return await asyncio.gather(*(one(u) for u in urls))
