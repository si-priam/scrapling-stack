"""scrapling_sync - keep the scraper's logins in step with your daily Brave.

The scraper runs on a share of your real Brave profile: a btrfs reflink clone.
A clone is a point in time. Sign into a new site in your daily Brave and, until
something refreshes the share, the scraper cannot see it - which is the one
barrier that made the sharing feel worse than a copy.

This module is that refresh, and it is designed to be run constantly without
being noticed. It copies only the credential databases, merges them, and
refuses to do anything at all while a scraper browser is live on the profile.

    from scrapling_sync import sync_credentials
    sync_credentials()
    # {"status": "synced", "cookies_added": 12, "cookies_total": 468, ...}

Design notes worth keeping:

* Cookies merge, they are never deleted. The cookies table has a real UNIQUE
  index (host, name, path, ...), so INSERT OR REPLACE is exact: new cookies
  land, changed cookies are updated, and anything the scraper earned on its own
  - a cf_clearance it solved that your daily browser never had - stays put.
  Deleting it on every pass would put the bot walls straight back.

* Logins are re-mirrored per site. the logins table has no UNIQUE index, so
  INSERT OR REPLACE would duplicate the table on every pass. Each site that has
  saved logins in the daily profile is instead replaced wholesale, which is
  idempotent no matter how often it runs.

* The source is copied out before it is read. Reading the live file directly
  returns "database is locked" - Brave holds Login Data open. Copying the file
  together with its -journal and opening the copy read-write lets SQLite roll a
  hot journal forward, so the snapshot is consistent and never blocked.

* Writing a cookie database under a running browser is how you corrupt it, so
  the guard on profile_in_use() is the one that must never be relaxed.

* Nothing here ever writes into your daily profile. A hard check enforces it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

__all__ = ["sync_credentials", "profile_in_use", "state", "DAILY", "SCRAPER"]

SCRAPLING_HOME = Path(__file__).resolve().parent

DAILY = Path(
    os.environ.get("BRAVE_DAILY_PROFILE")
    or Path.home() / ".config/BraveSoftware/Brave-Browser-Nightly"
).expanduser()
SCRAPER = Path(
    os.environ.get("BRAVE_PROFILE_DIR")
    or Path.home() / ".local/share/scrapling/brave-profile"
).expanduser()
STATE = Path(
    os.environ.get("SCRAPLING_SYNC_STATE") or SCRAPLING_HOME / ".sync-state.json"
).expanduser()

# Only the files a login can actually ride in, and only the ones the keyring
# encrypts. Volume, history, tabs and caches are deliberately not our problem.
CREDENTIALS = (("Cookies", "cookies"), ("Login Data", "logins"))
SIDECARS = ("-journal", "-wal", "-shm")


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #

def _process_on_profile() -> bool:
    """Is any Brave actually running against our profile directory?"""
    marker = f"user-data-dir={SCRAPER}"
    try:
        out = subprocess.run(
            ["ps", "-eo", "args", "--no-headers"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return False
    return any(marker in line for line in out.splitlines() if "brave" in line)


def profile_in_use() -> bool:
    """True while a scraper browser holds the profile.

    Conservative on purpose: if either check says busy, we treat it as busy.
    A false "idle" here means writing into a live cookie jar.
    """
    try:
        from scrapling_cleanup import profile_in_use as _shared_check
        if _shared_check():
            return True
    except Exception:
        pass
    return _process_on_profile()


def _refuse_if_daily(target: Path) -> None:
    """Never write into the daily profile - this tool is one-way, daily -> scraper."""
    try:
        target.resolve().relative_to(DAILY.resolve())
    except ValueError:
        return
    except Exception:
        return
    raise RuntimeError(f"refusing to write inside the daily profile: {target}")


# --------------------------------------------------------------------------- #
# SQLite plumbing
# --------------------------------------------------------------------------- #

def _open(path: Path, readonly: bool = False) -> sqlite3.Connection:
    # as_uri() percent-encodes the path, which matters: profile names such as
    # "Login Data" carry a space, and a raw space in a file: URI is a trap.
    if readonly:
        con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=20)
    else:
        con = sqlite3.connect(str(path), timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    return con


def _columns(con: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA {schema}.table_info({table})")]


def _count(con: sqlite3.Connection, table: str) -> int:
    try:
        return int(con.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0])
    except sqlite3.Error:
        return 0


def _sites(con: sqlite3.Connection) -> int:
    try:
        return int(
            con.execute("SELECT count(DISTINCT signon_realm) FROM main.logins").fetchone()[0]
        )
    except sqlite3.Error:
        return 0


def _snapshot(src: Path, workdir: Path) -> Path:
    """Copy a live database out, sidecars included, so it can be read at all.

    Brave keeps Login Data open, so the live file answers "database is locked".
    The copy carries the -journal across too, which lets SQLite roll a hot
    journal forward and hand back a consistent database rather than a torn one.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / src.name
    shutil.copy2(src, dst)
    for suffix in SIDECARS:
        side = Path(str(src) + suffix)
        try:
            if side.exists() and side.stat().st_size:
                shutil.copy2(side, Path(str(dst) + suffix))
        except OSError:
            pass
    # Opening read-write is what triggers journal recovery; a bare read would not.
    con = _open(dst)
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        con.close()
    return dst


def _replace(src_snapshot: Path, dst: Path) -> None:
    """Write a fresh, self-consistent copy of the snapshot over `dst`."""
    _refuse_if_daily(dst)
    tmp = dst.with_name(dst.name + ".sync-new")
    for junk in (tmp, Path(str(tmp) + "-journal"), Path(str(tmp) + "-wal"),
                 Path(str(tmp) + "-shm")):
        try:
            junk.unlink()
        except OSError:
            pass
    con = _open(src_snapshot, readonly=True)
    try:
        con.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        con.close()
    os.replace(tmp, dst)
    # A replaced database must not inherit a stale journal or WAL: SQLite would
    # replay the old one onto the new file and corrupt it.
    for suffix in SIDECARS:
        try:
            Path(str(dst) + suffix).unlink()
        except OSError:
            pass


def _merge(src_snapshot: Path, dst: Path, table: str, mirror: bool) -> dict:
    """Merge one credential database. Returns what happened, in numbers."""
    out = {"profile": dst.parent.name, "file": dst.name, "mode": "merge",
           "added": 0, "total": 0, "sites": 0}

    if mirror or not dst.exists():
        _replace(src_snapshot, dst)
        out["mode"] = "replaced"
        con = _open(dst, readonly=True)
        try:
            out["total"] = _count(con, table)
            out["sites"] = _sites(con)
        finally:
            con.close()
        out["added"] = out["total"]
        return out

    con = _open(dst)
    try:
        # A plain path, not a file: URI. This connection was not opened with
        # URI handling enabled, so SQLite would read "file:/..." as a literal
        # filename and fail to find it.
        con.execute("ATTACH DATABASE ? AS daily", (str(src_snapshot),))
        if _columns(con, table) != _columns(con, table, "daily"):
            # A Brave upgrade changed the table under us. Take the snapshot
            # whole rather than guessing column by column.
            con.execute("DETACH DATABASE daily")
            con.close()
            _replace(src_snapshot, dst)
            out["mode"] = "replaced (schema changed)"
            con = _open(dst, readonly=True)
            try:
                out["total"] = _count(con, table)
                out["sites"] = _sites(con)
            finally:
                con.close()
            out["added"] = out["total"]
            return out

        out["total"] = _count(con, table)          # count before
        if table == "logins":
            con.execute("DELETE FROM main.logins WHERE signon_realm IN "
                        "(SELECT DISTINCT signon_realm FROM daily.logins)")
            con.execute("INSERT INTO main.logins SELECT * FROM daily.logins")
        else:
            con.execute(f"INSERT OR REPLACE INTO main.{table} SELECT * FROM daily.{table}")
        con.commit()

        out["added"] = max(0, _count(con, table) - out["total"])
        out["total"] = _count(con, table)
        out["sites"] = _sites(con)
    finally:
        try:
            con.execute("DETACH DATABASE daily")
        except Exception:
            pass
        try:
            con.close()
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

def _profile_names() -> list[str]:
    """Profiles that exist on both sides - those are the ones worth syncing."""
    if not DAILY.is_dir():
        return []
    names = []
    for entry in sorted(DAILY.iterdir()):
        if not entry.is_dir() or not (entry / "Preferences").exists():
            continue
        if (SCRAPER / entry.name).is_dir():
            names.append(entry.name)
    return names


def _adopt_new_profiles() -> list[str]:
    """Profiles created in your daily Brave since the share was made.

    A brand-new profile folder does not exist on the scraper side yet, so
    nothing would sync into it. Add it there first as a copy-on-write clone -
    which costs almost no disk and is never a plain copy - and from then on it
    takes part in the sync like Default does.
    """
    adopted: list[str] = []
    if not DAILY.is_dir() or not SCRAPER.is_dir():
        return adopted
    for entry in sorted(DAILY.iterdir()):
        if not entry.is_dir() or not (entry / "Preferences").exists():
            continue
        target = SCRAPER / entry.name
        if target.exists():
            continue
        cloned = subprocess.run(
            ["cp", "-a", "--reflink=always", str(entry), str(target)],
            capture_output=True,
        )
        if cloned.returncode != 0:
            try:
                shutil.copytree(entry, target, dirs_exist_ok=True)
            except Exception:                        # never fatal to the sync
                continue
        for lock in target.glob("Singleton*"):
            try:
                lock.unlink()
            except OSError:
                pass
        adopted.append(entry.name)
    return adopted


def _source_mtime() -> float:
    """Newest change across the credential files we watch.

    This is the cheap fast path: if this number has not moved since the last
    pass, there is nothing to do and we do not touch a single database.
    """
    newest = 0.0
    for name in _profile_names():
        for fname, _ in CREDENTIALS:
            try:
                newest = max(newest, (DAILY / name / fname).stat().st_mtime)
            except OSError:
                continue
    return round(newest, 3)


def state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(record: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(record, indent=2) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #

def sync_credentials(force: bool = False, mirror: bool = False,
                     quiet: bool = True) -> dict:
    """Bring the scraper's cookies and saved logins up to date with your daily Brave.

    :param force: sync even if the daily profile has not changed since last time.
    :param mirror: replace each credential database outright instead of merging.
        Exact, but it discards cookies the scraper earned for itself.
    :param quiet: accepted for symmetry with the CLI; nothing prints from here.
    :returns: a report dict; "status" is one of
        synced | unchanged | in-use | no-daily-profile | error.
    """
    report = {
        "status": "synced",
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cookies_added": 0,
        "cookies_total": 0,
        "cookies_by_profile": {},
        "logins_sites": 0,
        "touched": [],
        "errors": [],
        "detail": "",
    }

    if not DAILY.is_dir():
        report["status"] = "no-daily-profile"
        report["detail"] = f"{DAILY} does not exist"
        return report

    if profile_in_use():
        report["status"] = "in-use"
        report["detail"] = "a scraper browser is running; not touching the profile"
        return report

    adopted = _adopt_new_profiles()

    names = _profile_names()
    if not names:
        report["status"] = "no-daily-profile"
        report["detail"] = "no profile directories are present on both sides"
        return report

    newest = _source_mtime()
    previous = state()
    if not force and previous.get("source_mtime") == newest:
        report["status"] = "unchanged"
        report["cookies_total"] = int(previous.get("cookies_total", 0) or 0)
        report["logins_sites"] = int(previous.get("logins_sites", 0) or 0)
        report["detail"] = "nothing changed in your daily Brave since the last check"
        return report

    workdir = Path(tempfile.mkdtemp(prefix="scrapling-sync-"))
    try:
        for name in names:
            for fname, table in CREDENTIALS:
                src = DAILY / name / fname
                if not src.exists():
                    continue
                dst = SCRAPER / name / fname
                try:
                    snap = _snapshot(src, workdir / name)
                    result = _merge(snap, dst, table, mirror)
                except Exception as exc:                    # never fatal
                    report["errors"].append(f"{name}/{fname}: {exc}")
                    continue
                report["touched"].append(f"{name}/{fname}")
                if table == "cookies":
                    report["cookies_added"] += result["added"]
                    report["cookies_by_profile"][name] = result["total"]
                else:
                    report["logins_sites"] = max(report["logins_sites"], result["sites"])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # "total" means the profile the scraper actually works in. Other profiles are
    # reported separately, so a busy second profile cannot inflate Default.
    by_profile = report["cookies_by_profile"]
    report["cookies_total"] = by_profile.get("Default") or max(by_profile.values(), default=0)

    if report["errors"]:
        report["status"] = "error" if not report["touched"] else "synced"
        report["detail"] = "; ".join(report["errors"])
    elif not report["touched"]:
        report["status"] = "unchanged"
        report["detail"] = "no credential files found to sync"
    else:
        report["detail"] = ("refreshed " + ", ".join(report["touched"]))
        if adopted:
            report["detail"] += "; added " + ", ".join(adopted)

    if report["status"] in ("synced", "unchanged"):
        # Only advance the watermark when the pass genuinely completed, so a
        # failed pass is retried on the next tick rather than skipped forever.
        _save_state({
            "last_sync": report["when"],
            "source_mtime": newest,
            "cookies_total": report["cookies_total"],
            "logins_sites": report["logins_sites"],
            "detail": report["detail"],
        })

    return report


if __name__ == "__main__":                    # pragma: no cover
    print(json.dumps(sync_credentials(force=True), indent=2))
