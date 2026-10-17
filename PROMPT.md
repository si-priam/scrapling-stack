# The prompt to paste on the new machine

Copy everything inside the block below, and nothing else, into the agent on the
fresh machine. It fetches the stack, installs it, and proves it works.

Brave Nightly must already be installed there first:

```bash
yay -S brave-nightly-bin
```

Everything else — Python 3.13, Scrapling, the modules, the profile share, the
automatic login sync, the OpenCode entry — the agent handles.

---

```text
Set up the Scrapling scraper stack on this machine. I run the same one on
another machine, and I want this one built the same way.

Read this whole message first, tell me your plan in a few lines, then do it.
Work until the checks pass. If something cannot be done, stop and tell me
exactly what failed. Do not swap in a different approach on your own.

Where everything comes from
- The stack itself (install script and the helper tools):
    https://github.com/si-priam/scrapling-stack
  Clone it. It contains install.sh, which does the whole install.
- Scrapling, the scraper:        https://github.com/D4Vinci/Scrapling
  Installed from PyPI, pinned by install.sh to version 0.4.15.
- Brave Nightly, the browser:    https://brave.com/download-nightly/
  Already installed at /opt/brave.com/brave-nightly/brave.
- uv, the Python installer:      https://github.com/astral-sh/uv
  Omarchy ships it: mise use -g uv@latest
- Python 3.13:                   https://github.com/astral-sh/python-build-standalone
  uv fetches it; do not install a system Python.
- Playwright and Patchright, the browser drivers:
    https://github.com/microsoft/playwright-python
    https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python
  They arrive automatically with Scrapling at version 1.63.0.
- gnome-keyring and libsecret: Arch packages, needed to decrypt the profile's
  cookies. Omarchy already has both; the keyring must be running.

What the end result is
- Scrapling drives my real Brave through a shared copy of my Brave profile, so
  it keeps my logins and my normal browser fingerprint.
- It never downloads its own Chromium. Brave is the only browser.
- It tries plain HTTP first, opens a headless browser only when a page needs
  JavaScript, and goes headed when a site puts up a Cloudflare wall.
- The Scrapling tools show up in OpenCode.
- Any site I log into in my daily Brave reaches the scraper by itself within a
  couple of minutes, forever. I never run a command to keep them in step.

What to do
1. Clone https://github.com/si-priam/scrapling-stack to a folder.

2. Open Brave Nightly once, sign in to whatever sites I want the scraper to be
   able to reach, and close it. This creates the profile that gets shared.

3. Run the preflight, which changes nothing and tells you if anything is
   missing:
       ./install.sh --check
   If it stops, fix what it names and run it again. Do not work around it.

4. Install:
       ./install.sh
   It sets up Python 3.13 and Scrapling 0.4.15, places the tools, shares the
   profile instead of copying it, reclaims the duplicated browser runtime,
   installs the two-minute login sync, and registers the OpenCode entry. It is
   safe to re-run.

Rules that matter
- Python 3.13, never the system's 3.14: curl_cffi has no wheel for 3.14 and
  would try to compile from source.
- Never run "scrapling install" and never download a Chromium.
- Never point anything at ~/.config/BraveSoftware/Brave-Browser-Nightly. The
  scraper uses the share at ~/.local/share/scrapling/brave-profile.
- Never kill the Brave I use day to day.
- Brave has to be launched with --password-store=gnome-libsecret, or the shared
  profile's cookies silently come back empty. The tools already do this; do not
  remove it.

Then prove it, and report honestly
- Launch Brave on the scraper profile and read the cookies from the live
  browser. They must decrypt, not merely exist on disk.
- Fetch a page with it, shut it down, and confirm no browser is left running
  and my daily Brave was untouched.
- Run ~/venvs/scrapling/bin/selftest and
  ~/venvs/scrapling/bin/verify-stealth.
- Tell me in a short table what worked, what did not, and why. Do not tell me
  something passed unless you saw it pass.
```
