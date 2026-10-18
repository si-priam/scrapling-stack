# Scrapling stack — set it up on this machine

This document is written for two readers: an **AI agent** that will do the work,
and a **person** who wants to understand what is happening. Read all of it
before you touch anything, then work through it in order.

**In one sentence:** this sets up a web scraper that drives the Brave browser
already on this machine, using the logins already in that browser, without
downloading a second browser and without disturbing the Brave you use daily.

---

## QUICK START

If you only read one part, read this:

```bash
# 1. Read the instructions first — the commands below change the machine
git clone https://github.com/si-priam/scrapling-stack
cd scrapling-stack

# 2. Look. This changes nothing and reports what is missing.
./install.sh --check

# 3. Install. Close Brave if it asks you to, then run it again.
./install.sh

# 4. Prove it. Note: open Brave before this step — the self-test expects it.
~/venvs/scrapling/bin/selftest
~/venvs/scrapling/bin/verify-stealth
```

Then report the results (§8).

---

## 1. What you are building

Three things:

1. **Scrapling** — the scraper. It fetches web pages and extracts content.
2. **A small set of tools** — commands for checking it, running batches,
   cleaning up, and keeping logins current (§10).
3. **A share of the Brave profile** — so the scraper is signed in wherever you
   are signed in, and looks like your normal browser (§11).

Scrapling works up a three-step ladder and uses the cheapest step that works:

| Step | What it opens | When it is used |
|---|---|---|
| 1 | nothing — a plain web request that impersonates Chrome | simple pages; the fastest by far |
| 2 | the stealth browser, hidden (no window) | pages that need JavaScript to fill in |
| 3 | the stealth browser, visible, solving bot walls | sites that put up a bot wall |

If step 1 gets the page, no browser is ever opened. That is the normal case.

**What "stealth" means.** It does not mean hiding who the owner is. A site can
tell a browser driven by software from a real one by a handful of tells: a
`HeadlessChrome` label, a software-drawn (SwiftShader) WebGL renderer, missing
browser hints, automation hooks left in the page. Scrapling's browser engine is
**Patchright**, a patched Playwright driver that removes those tells, and here it
drives the machine's own Brave with the owner's own profile — so the site sees an
ordinary visit from the owner's browser, carrying the owner's logins.

Steps 2 and 3 are that same engine, headless and headed. Scrapling 0.5 ships only
two engines in total — plain HTTP, and this browser. The plain-Playwright engine
older versions had was removed upstream, which is why both browser steps use the
stealth one.

---

## 2. What is already on this machine

These are expected to be in place already, because **you are not here to install
anything** (see §3). Confirm them if you like; `./install.sh --check` verifies
the ones that matter and reports what it finds.

| Requirement | Notes |
|---|---|
| Omarchy / Arch Linux on x86_64, with a graphical desktop session | the scraper's visible mode needs a real display |
| **Python 3.15** | system-wide, or on `PATH`; `uv` may provide it instead |
| **Brave Nightly** at `/opt/brave.com/brave-nightly/brave` | the only browser used |
| Brave Nightly **opened at least once** and signed in | sign-ins do not travel between machines; they must exist on this machine |
| `gnome-keyring` running | this is what decrypts the saved logins |
| `git` and internet access | to fetch this setup and the Python packages |
| Node.js | **not needed.** See §10. Install or ignore it as you wish. |

What `--check` actually tests: the home directory, Python 3.15 (or `uv`), Brave
Nightly, whether the keyring is running, whether a systemd user session exists,
and whether a daily Brave profile exists. It does **not** test Node.js, `git`,
your CPU, or the network.

---

## 3. Hard rules

These matter more than speed. Breaking one of them can damage the owner's daily
browser profile, which is the one thing here that cannot be rebuilt.

1. **Do not install anything, and do not run any package command.** No `sudo`,
   no `pacman`, no `yay`, no `pip install` outside the installer. If a message
   suggests installing something, **stop and report it** — it is a question for
   the owner, not a task for you. (Creating folders inside the home directory is
   fine.)
2. **Never run `scrapling install`.** That single command downloads a private
   Chromium. Brave is the only browser used here.
3. **Never download a Chromium or a Playwright browser.** If you see a browser
   download start, stop immediately.
4. **Never write to `~/.config/BraveSoftware/Brave-Browser-Nightly`** — not
   into it, not copied over it, not moved, not deleted. Reading from it is
   allowed and expected. Writing to it never is.
5. **Never close, kill, or signal the Brave the owner is using.** Only ever
   touch browser processes that belong to the scraper's own profile.
6. **Never delete** `~/venvs/scrapling`, `~/.local/share/scrapling`, or the
   `scrapling-sync` files under `~/.config/systemd/user/`.
7. **Never run `scrapling-sync --mirror` or `scrapling-sync --force`** unless
   the owner explicitly asks. They can throw away logins the scraper earned for
   itself, and bot walls would come back.
8. **Nothing system-wide.** Everything lives inside the owner's home directory.
9. **Report truthfully.** Do not write "passed" for anything you did not watch
   pass. If a step failed, name the step and quote its message.

---

## 4. The steps, in detail

### Step 1 — get the code

```bash
git clone https://github.com/si-priam/scrapling-stack
cd scrapling-stack
```

Read `README.md` and `install.sh` before running anything. `install.sh` is the
whole installation; there is no hidden step elsewhere.

- If `git` is missing, or the clone fails, **stop and report it**. Do not
  download the repository another way.
- If the folder already exists, use it rather than cloning again.

### Step 2 — make sure Brave has the logins

Open Brave Nightly normally, sign in to the sites you want the scraper to reach,
then **close it**.

- Skipping this does not break the install; it only means the scraper will not
  be signed in anywhere, so sites that need a login will turn it away.
- Sign-ins are **not** copied from another machine. They have to exist on this
  one, because logins are encrypted with a key held by this machine's keyring.

### Step 3 — look before you touch

```bash
./install.sh --check
```

This changes nothing at all. It reports each item from §2 that it checks.

If something is reported missing, **stop and report it**. Do not install it
yourself, and do not work around it with a substitute.

### Step 4 — install

```bash
./install.sh
```

This does the whole job in about a few minutes, most of it fetching Python
packages. It is safe to run a second time if it stops partway.

**The one expected interruption:** if Brave is open, the installer **stops with
an error** telling you to close it. That is deliberate — a browser is always
writing to its profile, and capturing it mid-write can produce a damaged copy.
Close Brave and run the command again.

### Step 5 — prove it works

Follow §7 exactly, then report using §8.

---

## 5. What the installer does, step by step

So you can follow along and know which part failed if something does.

| # | Stage | What happens |
|---|---|---|
| 1 | **Preflight** | checks every requirement in §2 that matters. Changes nothing. Stops with one plain sentence if something is missing. |
| 2 | **Python environment** | creates `~/venvs/scrapling` using the Python 3.15 already on the machine, then installs Scrapling pinned to `0.5`. Uses `uv` when it is present, otherwise the system's own `python3.15`; both build the same environment. Nothing system-wide. |
| 3 | **Placing the files** | the scraper code goes to `~/.local/share/scrapling/`, the commands to `~/venvs/scrapling/bin/`, plus the measured results file and a small policy file. |
| 4 | **Reclaiming space** | Playwright and Patchright each ship an identical 127 MB copy of Node.js. On a btrfs disk they are made to share one copy. On other filesystems they are left as they are. |
| 5 | **Profile share** | the scraper gets a **share** of the Brave profile, not a copy — on btrfs. On other filesystems it falls back to a real copy (about 250 MB) and says so. |
| 6 | **Login sync** | a background job, running as you, wakes every two minutes and carries new sign-ins from the daily Brave to the scraper. It does nothing when nothing changed. |
| 7 | **OpenCode** | adds one entry to `~/.config/opencode/opencode.jsonc` so the Scrapling tools appear in OpenCode. Your other settings are kept; note that comments and exact formatting in that file are not preserved. |
| 8 | **Verification** | runs `selftest` and `verify-stealth`, and prints the results. |

---

## 6. Nothing here is hard to undo

If the owner ever wants this gone, all of it is inside their home directory:

```bash
rm -rf ~/venvs/scrapling ~/.local/share/scrapling
rm -f  ~/.config/systemd/user/scrapling-sync.{service,timer}
systemctl --user daemon-reload
# then remove the "scrapling" entry from ~/.config/opencode/opencode.jsonc
```

The Brave profile itself is never modified, so the owner's browsing is
unaffected either way.

---

## 7. How to prove it works

Run these and **read the output** rather than trusting it.

### 7.1 The suite

```bash
~/venvs/scrapling/bin/selftest
~/venvs/scrapling/bin/verify-stealth
```

Two things to know before you run them:

- **`selftest` expects the owner's daily Brave to be running.** Open Brave
  before running it. It fails at the first check if Brave is closed.
- **`selftest` opens a visible Brave window and fetches a few real pages.** That
  window is the scraper's own browser, not the owner's. It closes it afterwards.
  Seeing a window appear is expected, not a fault.
- `verify-stealth` on its own runs the offline checks plus one live page fetch.
  Add `--live` for the full fingerprint comparison against the owner's real
  Brave and a live bot-challenge test.

### 7.2 The logins really decrypt

A file sitting on disk is not proof. The cookies must decrypt.

```bash
~/venvs/scrapling/bin/scrapling-sync --status
```

This prints how many cookies the scraper currently sees and how many login
sites they cover. **A count of zero means one of two things:** the keyring is
not running, or the owner never signed in on this machine (Step 2). Check the
keyring first.

### 7.3 A real page comes back

```bash
~/venvs/scrapling/bin/scrape-batch https://example.com
```

It should report success for that URL. If it reports a failure, quote the exact
reason it gives.

### 7.4 Nothing was left behind

```bash
~/venvs/scrapling/bin/brave-cleanup --status   # what is running
~/venvs/scrapling/bin/brave-cleanup            # ordered shutdown
```

Afterwards confirm no browser process is left on the scraper's profile, and
that the owner's daily Brave was never signalled — `selftest` checks that last
point explicitly, so its result counts as your evidence.

### 7.5 The timer is running

```bash
systemctl --user is-active scrapling-sync.timer
```

Expected: `active`.

---

## 8. How to report back

Give the owner a short, plain report in this shape:

```
Result: success / failed at step N

| Check                            | Result |
|----------------------------------|--------|
| Python 3.15 found                |        |
| Brave Nightly found              |        |
| Scrapling installed              |        |
| Login sync timer active          |        |
| Logins decrypt (cookies / sites) |        |
| Page fetched successfully        |        |
| Nothing left running             |        |
| selftest                         |        |
| verify-stealth                   |        |

Anything that did not work, with the exact message it printed:
Time taken:
```

Rules for the report:

- No claim without evidence. If you did not see it pass, do not write "pass".
- If something failed, name the step and quote its message.
- Do not round a partial success up. "Installed, but the logins did not decrypt"
  is the correct answer when that is what happened.

---

## 9. If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `no Python 3.15 on this machine` | Python 3.15 is genuinely absent | stop and report; do not install it |
| `Brave Nightly is missing at ...` | Brave is not where it is expected | stop and report |
| `your daily Brave is running` | Brave is open, so the profile cannot be captured safely | close Brave, run again |
| `gnome-keyring-daemon is not running in this session` | a warning, not a stop: logins cannot be decrypted until it runs | report it; a fresh graphical session usually fixes it |
| `systemd --user is unavailable` | the background sync cannot be installed | stop and report |
| `no daily Brave profile to share yet` | Brave has never been opened on this machine | open Brave once, close it, run again |
| `the Python stack did not import cleanly` | a package failed to install | report the exact error; retry once; if it fails again, stop |
| a package download fails | network or PyPI unreachable | report the exact error; retry once; if it fails again, stop |
| cookie count is 0 | keyring not running, or nobody signed in on this machine | check the keyring first |
| anything else | — | stop and report; never invent a different approach |

The one thing never to do: silently substitute a different method. A machine
that is *nearly* right is worse than one that plainly is not finished, because
afterwards nobody can tell the difference.

---

## 10. Reference — the commands you end up with

All of them live in `~/venvs/scrapling/bin/`.

| Command | What it does |
|---|---|
| `selftest` | checks the whole setup, including that the daily Brave is never touched. Opens a visible browser window; expects the daily Brave to be running |
| `verify-stealth` | offline checks plus one live page fetch; `--live` adds the fingerprint comparison against the real Brave and a live bot-challenge test |
| `diagnose-sites` | sweeps real sites and reports which fail at which step, and why; `--deep` escalates failures up the ladder |
| `scrape-batch` | scrapes many URLs, reports honestly which failed and why, and remembers what it learned between runs |
| `brave-cdp` | starts the scraper's browser with a debug port open. Add `--headed` to watch it work and solve a captcha by hand |
| `brave-cleanup` | ordered shutdown: tabs first, then the browser. `--status` shows what is running. Your daily Brave is left alone |
| `scrapling-sync` | keeps the scraper's logins in step with the daily Brave. `--status` shows the last catch-up and the login count |

**About Node.js:** this setup does not use it. The two browser drivers that come
with Scrapling carry their own copy of Node, so a system-wide Node is simply
unused. Installing it is harmless; not installing it changes nothing.

---

## 11. Reference — how the profile sharing works

This is the part people rightly want explained, because at first hearing it
sounds dangerous.

**Two different things are involved:**

- **The app** — the Brave window you browse in.
- **The profile** — the folder holding your logins, cookies and history
  (`Default`, plus any other profiles you have made).

The scraper uses **the profile**. It never touches **the app**.

So:

- It **reads your logins**, which is exactly why it is signed in where you are.
- It **never opens windows in your browser**, and never closes it. When the
  scraper needs a browser, it starts its own separate instance.
- It **never writes to your real profile**. Information flows one way only:
  reads come from yours, writes go to the scraper's own side.
- The two browsers **can run at the same time**. One exception: while the
  scraper's browser is open, the background login sync waits — writing into a
  profile mid-run is how you corrupt one. It catches up as soon as that browser
  closes.
- Profiles are found automatically. **Default** is the one the scraper works in;
  any others are carried along quietly, and a profile you create later joins on
  the next check. Nothing to name, nothing to choose.

**Why sharing is safe.** Both sides point at the same data on disk, and the
moment either side writes, the disk gives that side its own fresh copy of the
changed parts and leaves the original exactly as it was. This is a feature of
the btrfs filesystem, which Omarchy uses. On a non-btrfs disk the share is not
possible, so the installer makes a real copy instead and warns you — it still
works, it just uses about 250 MB more.

**One moment needs Brave closed:** the very first time the share is created,
because a browser is always writing to its profile and capturing it mid-write
could copy a damaged version. The installer enforces this. After that first
time, never again.

---

## 12. Definition of done

It is finished when all of these are true:

- Scrapling is installed for Python 3.15 at `~/venvs/scrapling`, pinned to `0.5`
- The commands in §10 are present and runnable
- The scraper works in a share of the Brave profile, and its logins decrypt
- `scrapling-sync.timer` is `active`, and `scrapling-sync --status` reports cookies and login sites
- The Scrapling entry is present in `~/.config/opencode/opencode.jsonc`
- Every check in §7 has been run, and its real result reported in the §8 format

If any of those is not true, say so plainly. A clear "not finished, and here is
why" is a good outcome. A vague "done" that hides a failure is not.
