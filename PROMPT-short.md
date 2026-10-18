# The prompt for the new machine

Everything the scraper needs should already be installed there: the system's
**Python**, **Node.js**, and **Brave Nightly**. Nothing else needs preparing — the
installer uses `uv` when it is there and plain `pip` when it is not.

Copy everything inside the block below into the agent on that machine.

---

```text
Set up my Scrapling scraper on this machine, the way I run it on my other one.

Everything it needs is already installed here: the system Python, Node.js and
Brave Nightly. Do not install anything. Check what is there first, then do the
setup.

Get my setup from https://github.com/si-priam/scrapling-stack

1. Open Brave Nightly once, sign in to the sites I scrape, and close it.

2. Run:   ./install.sh --check
   This only looks at the machine and changes nothing. If something is missing,
   tell me. Do not install it yourself.

3. Run:   ./install.sh
   It sets Scrapling up for me.

What I want when it is done:
- Scrapling runs from the system Python, in its own local environment.
- Everything stays inside my home folder. No sudo, nothing system-wide.
- It works with my Brave Nightly profile, so it keeps my logins and my normal
  browser fingerprint.
- It never downloads a Chromium, and it never touches the Brave I use daily.

When it finishes, prove it works and tell me the truth:

- start the scraper's browser and check my logins really load
- fetch a page, close the browser, and make sure nothing is left running
- run these two:
      ~/venvs/scrapling/bin/selftest
      ~/venvs/scrapling/bin/verify-stealth
- then tell me, in a short table, what worked, what didn't, and why

If something fails, stop and tell me what failed. Do not invent a different way.
```
