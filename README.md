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
| `opencode.snippet.jsonc` | the OpenCode server entry, for reference. `install.sh` writes the same entry itself, with this machine's own paths |

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

On a new machine, open Brave Nightly once and sign in to your sites before that
first install: the scraper's copy of the profile is taken from it, and sign-ins
never travel between machines.

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

## What "stealth" means here

Scrapling works up a three-step ladder and uses the cheapest step that works:

| Step | What it opens | When it is used |
|---|---|---|
| 1 | nothing — a plain request that impersonates Chrome's handshake | ordinary pages; by far the fastest |
| 2 | the stealth browser, with no window | pages that need JavaScript to build themselves |
| 3 | the stealth browser, visible, solving bot walls | sites that answer with a challenge |

**Stealth is not about hiding who you are.** It means the browser does not give
off the usual signs of being driven by software — the tells a site uses to tell
an automated visit from a real one: a `HeadlessChrome` label, a software-drawn
WebGL renderer, missing browser hints, automation hooks left in the page.
Scrapling's browser engine is **Patchright**, a patched build of the Playwright
driver that removes those tells, and here it drives the Brave already on your
machine with your own profile — so what a site sees is your normal browser.

Steps 2 and 3 are the same engine; step 2 just runs it without a window. Step 1
opens no browser at all.

## Requirements

Omarchy/Arch on x86_64 with a graphical session, **Brave Nightly**
(`yay -S brave-nightly-bin`, landing at `/opt/brave.com/brave-nightly/brave`),
the **system's own Python** — the one your distribution ships and keeps current,
3.14 on a current Arch — and a running **gnome-keyring**.

That is the whole list. `install.sh` checks each one and stops with a clear
message if something is missing — it does not install anything for you.

**uv is preferred; pip does the same job.** `uv` creates the environment and
installs the packages when it is present, and the system's own `pip` builds an
identical environment when it is not. Neither needs anything system-wide.

Using the machine's own Python is deliberate: it is the one your distribution
keeps current, so no version number here can go stale when the system moves
forward, and every dependency resolves to a ready-made wheel, so nothing is
compiled.

That Python is not a second one. It is the same system package the rest of your
system's software is built against — 3.14 on a current Arch — taken exactly as
your system has it, patch level included; no patch level is pinned here. The
scraper's own packages live in a private environment of their own, so they
neither depend on nor disturb the system's Python packages.

Node.js is not needed either: the browser drivers that come with Scrapling carry
their own copy.
