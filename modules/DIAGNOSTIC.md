# Site diagnostic — what works, what's blocked, and why

Measured 2026-10-12 on this machine. Re-run anytime:

```bash
~/venvs/scrapling/bin/diagnose-sites            # tier 1 sweep
~/venvs/scrapling/bin/diagnose-sites --deep     # + escalate failures to tier 2/3
```

## Tier 1 (plain HTTP) — 9 of 17 usable

| Site | Status | Verdict |
|---|---|---|
| example.com | 200 | ✅ |
| quotes.toscrape.com | 200 | ✅ |
| en.wikipedia.org | 200 | ✅ 13 KB of text |
| bing.com/search | 200 | ✅ 1.2 KB |
| startpage.com | 200 | ✅ |
| duckduckgo.com | 200 | ✅ |
| lite.duckduckgo.com | 200 | ✅ |
| apiascholars.org | 200 | ✅ |
| caafoundation.org | 200 | ✅ |
| scholarshipinfocenter.com | 200 | ✅ |
| pathwaystoscience.org | 200 | ✅ |
| scholarships.com | 200 | ✅ |
| cdnjs.com | 200 | ✅ |
| **mojeek.com** | **403** | ❌ hard-blocks curl_cffi |
| **search.brave.com** | **429** | ❌ rate-limits plain HTTP |
| **html.duckduckgo.com/html/** | **202** | ❌ **dead endpoint** — see below |
| **nopecha.com/demo/cloudflare** | **403** | ❌ Cloudflare (expected) |

## The three that fail at every tier

### 1. `html.duckduckgo.com/html/` — dead, stop using it
Returns `202` with a page whose entire content is:

> "Unfortunately, bots use DuckDuckGo too. Please complete the following
> challenge to confirm this search was made by a human."

Tried at tier 1, tier 2 (headless), and tier 3 (headed stealth). All three get
the same challenge. **This endpoint is deprecated** — it is not a stealth
problem and more resources will not fix it.

**Use instead:** `lite.duckduckgo.com/lite/?q=` (works at tier 1, 200, fast),
or `duckduckgo.com/?q=` / `bing.com/search?q=` / `startpage.com`.

### 2. `mojeek.com` — hard 403 to non-browser clients
403 at every tier including headed. Mojeek specifically blocks the curl_cffi
fingerprint class. Treat as unusable unless they change it.

### 3. `nopecha.com/demo/cloudflare` — blocked at tier 1/2, **passes at tier 3**
- headless → 403, blocked
- **headed stealth → PASSES**

This is the intended behaviour and the reason tier 3 exists.

## Sites needing tier 2 (headless) or tier 3 (headed)

| Site | Best tier | Note |
|---|---|---|
| duckduckgo.com (main) | tier 2 | tier 1 gives a JS shell, no results |
| startpage.com | **tier 3** | tier 2 gives 114 chars; headed gives 2,560 |
| apiascholars.org, caafoundation.org | tier 1 has markup but **tier 2 needed for full text** | JS-rendered |

## Detector accuracy: 13/13

`is_blocked()` now gets all 13 regression cases right — 9 true negatives
(no false alarms on real content) and 4 true positives.

Three bugs found and fixed during this diagnostic:

1. **`body::text` selector** returned empty on JS-heavy pages. Real fix: query
   `h1/h2/h3/p/li::text` individually. `.css('p::text')` returns Selector
   objects — you must call `.getall()` and `str()` them.
2. **Rate limits were missed.** `search.brave.com` returns `429` with a 68 KB
   body, so the old "blocked only if body < 5 KB" rule said *not blocked*. Now
   any `401/403/429/503` counts as blocked regardless of body size.
3. **Block pages counted as content.** A Cloudflare interstitial carries its own
   prose ("This website uses a security service..."), which passed the text-length
   test. `content_ok()` now consults `is_blocked()` first.

Also added: anti-bot phrases matched against *visible text* (not markup), so a
page that merely links to a challenge script isn't falsely flagged, plus a
near-empty-body rule that catches stub responses like DDG's `202`.

## Known limitation

`is_blocked()` reports whether a **wall** was served. It does not tell you
whether the extraction was *complete* — a page can be unblocked yet render its
content lazily. If a site returns 200 and looks clean but you get no data,
suspect lazy rendering, not a bot wall.
