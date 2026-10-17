# The prompt for the new machine

Brave Nightly has to be there first:

```bash
yay -S brave-nightly-bin
```

Then copy everything inside the block below into the agent on that machine.

---

```text
Set up my Scrapling scraper on this machine the way I run it on my other one.

Get my setup from https://github.com/si-priam/scrapling-stack

Then do this:

1. Open Brave Nightly once, sign in to the sites I scrape, and close it.

2. Run:   ./install.sh --check
   This only looks at the machine and changes nothing. If it says something is
   missing, get that thing and run it again.

3. Run:   ./install.sh
   This does the whole setup by itself: the right Python, the scraper, the
   small tools I use, and a background job that keeps my new logins flowing to
   the scraper without me doing anything. It takes a few minutes.

It will not download a Chromium, and it will not touch the Brave I use every
day. It is safe to run twice.

When it finishes, prove it works and tell me the truth:

- start the scraper's browser and check my logins really load
- fetch a page, close the browser, and make sure nothing is left running
- run these two:
      ~/venvs/scrapling/bin/selftest
      ~/venvs/scrapling/bin/verify-stealth
- then tell me, in a short table, what worked, what didn't, and why

If something fails, stop and tell me what failed. Do not invent a different way.
```
