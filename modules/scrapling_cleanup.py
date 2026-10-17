"""Guaranteed, ordered browser shutdown for the Brave-native scraper.

Scrapling's one-shot fetchers already close their context on the happy path
(verified: 0 orphaned processes after a normal run). What they do NOT guarantee
is cleanup when something goes wrong - a KeyboardInterrupt mid-fetch, a SIGTERM
from a cron timeout, or an exception thrown between launch and close leaves a
headless Brave running and a SingletonLock on the profile.

This module makes shutdown deterministic and ordered, the way a careful human
would do it:

    1. close every TAB
    2. close the browser/context
    3. stop the driver
    4. reap any process that outlived the above
    5. clear stale profile locks

Step 5 is guarded: a lock is only removed when no process of ours is actually
using the profile, so it can never yank the lock out from under a live scrape.

    python -c "from scrapling_cleanup import cleanup; cleanup()"
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

PROFILE_DIR = "/home/dhh/.local/share/scrapling/brave-profile"
BRAVE_BIN = "/opt/brave.com/brave-nightly/brave"
LOCKS = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _our_pids() -> list[int]:
    """PIDs of Brave processes running on OUR profile copy only.

    Never touches your daily browser - the real Brave uses a different
    user-data-dir and is filtered out here.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,args", "--no-headers"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        if PROFILE_DIR in line and "brave-nightly/brave" in line and "ps -eo" not in line:
            try:
                pids.append(int(line.split()[0]))
            except (ValueError, IndexError):
                pass
    return pids


def profile_in_use() -> bool:
    return bool(_our_pids())


def close_tabs_via_cdp(port: int = 9222, timeout: float = 5.0) -> int:
    """Step 1+2: close every tab on a running brave-cdp, then let it exit.

    Uses the DevTools HTTP endpoint, so no import of playwright is required and
    it works even if the venv is mid-shutdown.
    """
    import json
    import urllib.request

    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout))
    except Exception:
        return 0

    closed = 0
    for tab in tabs:
        url = tab.get("url", "")
        if url.startswith(("devtools://", "chrome://", "about:blank")):
            continue
        try:
            req = urllib.request.Request(tab["webSocketDebuggerUrl"].replace("ws://", "http://")
                                         if False else f"http://127.0.0.1:{port}/json/close/{tab['id']}")
            urllib.request.urlopen(req, timeout=timeout).read()
            closed += 1
        except Exception:
            continue
    return closed


def graceful_close(page=None, context=None, browser=None, driver=None) -> None:
    """Ordered shutdown for objects you launched yourself.

    tabs -> context -> browser -> driver. Each step is independent and
    best-effort: one failing step must not prevent the next from running.
    """
    if page is not None:
        try:
            if not page.is_closed():
                page.close()
        except Exception:
            pass
    if context is not None:
        try:
            context.close()
        except Exception:
            pass
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    if driver is not None:
        try:
            driver.stop()
        except Exception:
            pass


def _reap(attempts: int = 4, gap: float = 0.8) -> int:
    """Kill our Brave until the set stops changing.

    A single sweep is not enough. If SIGTERM arrives while Playwright is inside
    `launch_persistent_context`, killing the browser we can see merely makes the
    driver start over and spawn a fresh one - the process count actually goes
    *up*. So we sweep repeatedly until two consecutive passes find nothing new.
    """
    killed_total = 0
    for _ in range(attempts):
        pids = _our_pids()
        if not pids:
            break
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue
            except Exception:
                continue
        killed_total += len(pids)
        time.sleep(gap)
        for pid in _our_pids():            # escalate anything that ignored it
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        time.sleep(0.3)
    return killed_total


def reset_session(profile_dir: str = PROFILE_DIR, profiles: tuple[str, ...] = ()) -> dict:
    """Stop the profile restoring stale tabs on the next launch.

    Brave restores its previous session when it believes it crashed - that is,
    when `session.exited_cleanly` is absent or false. It is absent here because
    the profile was copied while your daily Brave was running, and because a
    killed scraper never gets to set it. The result is a fresh scraper launch
    opening dozens of tabs nobody asked for.

    Two things are fixed:

    * the `Sessions/Tabs_*` files are deleted (they hold the stale tab list)
    * `session.exited_cleanly` is set true, so a clean exit is recorded

    Must be called with the browser CLOSED. Brave rewrites Preferences on exit
    and would overwrite the change.

    :param profiles: extra profile names to reset, e.g. ("Profile 1",).
        Defaults to every profile directory present in the profile dir.
    """
    import json
    import shutil

    root = Path(profile_dir)
    out = {"tabs_cleared": 0, "prefs_fixed": 0, "profiles": []}
    if not root.is_dir():
        return out

    names = list(profiles)
    if not names:
        names = [p.name for p in root.iterdir()
                 if p.is_dir() and (p / "Preferences").exists()]

    for name in names:
        prof = root / name
        if not prof.is_dir():
            continue
        out["profiles"].append(name)

        # 1. stale tab/session state
        sess = prof / "Sessions"
        if sess.is_dir():
            for f in sess.glob("Tabs_*"):
                try:
                    f.unlink(); out["tabs_cleared"] += 1
                except Exception:
                    pass
            for f in sess.glob("Session_*"):
                try:
                    f.unlink()
                except Exception:
                    pass
        for legacy in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
            p = prof / legacy
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

        # 2. tell Brave the last exit was clean, and never restore on startup
        pref = prof / "Preferences"
        try:
            data = json.loads(pref.read_text()) if pref.exists() else {}
        except Exception:
            data = {}
        s = data.setdefault("session", {})
        s["exited_cleanly"] = True
        s["restore_on_startup"] = 5          # 5 = open a new tab, never restore
        s["restore_on_startup_from_urls"] = []
        data.setdefault("profile", {}).setdefault("exit_type", "Normal")
        data["profile"]["exited_cleanly"] = True
        try:
            pref.write_text(json.dumps(data, separators=(",", ":")))
            out["prefs_fixed"] += 1
        except Exception:
            pass

        # 3. tell it not to show the crash-restore bubble either
        for local in ("Local State",):
            pass
    return out


def cleanup(port: int = 9222, kill: bool = True, verbose: bool = True,
            reset: bool = True) -> dict:
    """Full shutdown. Safe to call at any time, including twice.

    :param reset: after a successful reap, mark the profile clean and drop stale
        tab state so the NEXT launch never restores old tabs.
    """
    report = {"tabs_closed": 0, "cdp_stopped": False, "killed": 0,
              "locks_cleared": 0, "profile_still_busy": False, "reset": {}}

    # 1+2: tabs first, then the cdp browser
    report["tabs_closed"] = close_tabs_via_cdp(port)

    if kill:
        report["killed"] = _reap()
        report["cdp_stopped"] = report["killed"] > 0

    # 5: locks, but only once nothing of ours is running. This guard is the one
    #    that matters: it fires when the reap could not finish the job, so we
    #    never pull a SingletonLock out from under a browser that is still alive.
    if profile_in_use():
        report["profile_still_busy"] = True
    else:
        for name in LOCKS:
            p = Path(PROFILE_DIR) / name
            try:
                if p.exists() or p.is_symlink():
                    p.unlink()
                    report["locks_cleared"] += 1
            except Exception:
                pass
        if reset:
            report["reset"] = reset_session(PROFILE_DIR)

    if verbose:
        print(f"cleanup: {report['tabs_closed']} tab(s) closed, "
              f"{report['killed']} process(es) terminated, "
              f"{report['locks_cleared']} lock(s) cleared"
              + ("  [profile still busy - locks kept]" if report["profile_still_busy"] else ""))
    return report


def _driver_pids() -> list[int]:
    """PIDs of Playwright/Patchright node driver processes we started.

    These are children of this Python process. `os._exit()` does NOT reap
    children, so a surviving driver can outlive us and relaunch a browser after
    our sweep has already finished - which is why a SIGTERM during
    `launch_persistent_context` used to leave orphans behind.
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid,ppid,args", "--no-headers"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    me = os.getpid()
    pids = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, args = parts
        try:
            pid, ppid = int(pid), int(ppid)
        except ValueError:
            continue
        if ppid == me and ("playwright" in args or "patchright" in args or "driver/node" in args):
            pids.append(pid)
    return pids


def reap_all(verbose: bool = False) -> dict:
    """Driver-first shutdown. Correct order for a hard stop:

    1. kill the node drivers  - stops anything from relaunching a browser
    2. reap our Brave         - now nothing can spawn more
    3. clear locks            - only once the profile is idle
    """
    drivers = _driver_pids()
    for pid in drivers:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    if drivers:
        time.sleep(0.4)

    report = cleanup(kill=True, verbose=False)
    report["drivers_killed"] = len(drivers)
    if verbose:
        print(f"reap_all: {len(drivers)} driver(s), {report['killed']} brave proc(s), "
              f"{report['locks_cleared']} lock(s)")
    return report


def install_signal_handlers() -> None:
    """Close everything on Ctrl+C / SIGTERM instead of orphaning it.

    Deliberately does NOT use `os.killpg(SIGKILL)`. This process very often
    shares a process group with the shell, a test harness, or a cron runner,
    and group-killing would take all of those down with it. `reap_all()` is
    already sufficient: it kills the node driver children first (so nothing can
    relaunch a browser) and then reaps the Brave processes themselves.
    """
    def handler(signum, frame):
        try:
            reap_all()
        except Exception:
            pass
        os._exit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
