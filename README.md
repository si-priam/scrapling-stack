# scrapling-stack

A Brave-native web-scraping setup: Scrapling driving the machine's own Brave
Nightly through a **shared** copy of the Brave profile, so pages are fetched with
a real browser fingerprint and real logins. No Chromium is ever downloaded.

This folder is the whole stack except the Python packages themselves, which are
rebuilt from PyPI in about a minute. It exists so a new machine can be brought
up identically instead of being reverse-engineered from memory.

## What is in here

| Path | What it is |
|---|---|
| `install.sh` | the whole bring-up. Idempotent, safe to re-run. |
| `modules/` | the working code: three-tier cascade, bot-wall detector, batch runner, cleanup, login sync, and `DIAGNOSTIC.md` (the measured pass/fail record) |
| `bin/` | the commands you drive by hand |
| `systemd/` | the two-minute login sync |
| `opencode.snippet.jsonc` | the OpenCode server entry `install.sh` merges for you |

## The commands

| Command | What it does |
|---|---|
| `selftest` | proves the setup works: launches Brave on the scraper profile, checks the debug port, confirms your daily Brave is never touched |
| `verify-stealth` | compares the scraper's browser fingerprint against your real Brave — proves there are no headless tells |
| `diagnose-sites` | sweeps real sites and reports which fail at which tier, and why (`--deep` escalates) |
| `scrape-batch` | scrapes many URLs, says honestly which failed and why, remembers what it learned |
| `brave-cdp` | starts Brave on the scraper profile with a debug port, so you can watch it and solve a captcha yourself |
| `brave-cleanup` | ordered shutdown: tabs first, then the browser, leaving your daily Brave alone |
| `scrapling-sync` | keeps the scraper's logins in step with your daily Brave (`--status` to see it) |

All of them live in `~/venvs/scrapling/bin/`.

## Use it

```bash
./install.sh --check     # preflight only, changes nothing
./install.sh             # install, then verify
```

## The one thing to know

Your daily Brave and the scraper share the same profile blocks on disk. Writes
on the scraper's side never reach your real profile — but that also means
**sign-ins flow in one direction**: log into a new site in your daily Brave and
the sync carries it across within about two minutes, or the moment a scrape
starts. You never run anything.

`du` will over-report these folders because shared blocks are counted twice.
`btrfs filesystem du -s <folder>` is the honest number; read the **Exclusive**
column.

## Requirements

Omarchy/Arch on x86_64 with a graphical session, Brave Nightly
(`yay -S brave-nightly-bin`, landing at `/opt/brave.com/brave-nightly/brave`),
`uv`, and a running `gnome-keyring`. `install.sh` checks all of it and stops
with a clear message if something is missing. Python 3.13 comes from `uv`; you
install nothing.
