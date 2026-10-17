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
| `PROMPT.md` | the full prompt to paste on the new machine: every step, every check, and what to do when something fails |
| `PROMPT-short.md` | a six-line version of the same thing, for when you just want it done |
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

Profiles are found on their own. Every real profile in your daily Brave — the
Default one you use, plus any others — is carried across, and a profile you
create later is added the first time the sync runs after that. Nothing to name,
nothing to configure.

`du` will over-report these folders because shared blocks are counted twice.
`btrfs filesystem du -s <folder>` is the honest number; read the **Exclusive**
column.

## Requirements

Omarchy/Arch on x86_64 with a graphical session, **Brave Nightly**
(`yay -S brave-nightly-bin`, landing at `/opt/brave.com/brave-nightly/brave`),
**Python 3.13**, and a running **gnome-keyring**.

That is the whole list. `install.sh` checks each one and stops with a clear
message if something is missing — it does not install anything for you.
`uv` is used if it happens to be present, but it is not required; the system's
own `python3.13` builds the same environment.

Node.js is not needed either: the browser drivers that come with Scrapling carry
their own copy.
