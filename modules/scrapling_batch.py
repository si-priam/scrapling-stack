"""Batch scraping with honest partial-failure reporting and a persistent ledger.

The problem this solves: if you scrape 10 URLs and only 7 work, the failure is
usually silent. You get 7 results and no idea whether the other 3 were blocked,
timed out, 404'd, or returned a page your selectors didn't match. Next session
you rediscover all of it from scratch.

This module gives you three things:

1. **Never-silent results** - `scrape_many` returns one `Outcome` per input URL,
   always, including the ones that failed.
2. **A failure taxonomy** - each failure is classified into a bucket that implies
   a specific action, so the fix is targeted instead of guessed at.
3. **A persistent ledger** - `~/.local/share/scrapling/ledger.json` remembers what
   tier each host needed and how it failed, so the *next* session starts at the
   right tier immediately and knows which failures are new vs. recurring.

    from scrapling_batch import scrape_many

    report = scrape_many(urls)
    print(report.summary())
    for o in report.failures():
        print(o.url, o.failure_class, o.remedy)

Failure classes
---------------
WALL        Anti-bot wall served.      Remedy: raise the tier (start higher next time).
RATE_LIMIT  429 / throttled.           Remedy: slow down, add delay, back off.
HTTP_ERROR  4xx/5xx that is not a wall. Remedy: check the URL is live; may be dead forever.
TIMEOUT     Did not answer in time.    Remedy: raise timeout; retry later.
CONN        DNS/TCP/TLS failure.        Remedy: network or the host is down.
EMPTY       200 but nothing extractable. Remedy: site renders lazily -> use tier 2/3.
SELECTOR    Content arrived but no data matched. Remedy: your CSS selectors, not the site.
OK          Success.

SELECTOR is the one worth understanding: the fetch worked perfectly, your
extraction just didn't match. That is a *your-code* bug, and no amount of
escalating tiers will ever fix it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

from scrapling_brave import smart_traced, is_blocked, _page_text

LEDGER = Path("/home/dhh/.local/share/scrapling/ledger.json")

# Failure classes and what the operator should actually do about each.
REMEDIES = {
    "WALL":        "anti-bot wall - start at a higher tier next time (ledger now remembers)",
    "RATE_LIMIT":  "throttled - add delay between requests, or scrape fewer per minute",
    "HTTP_ERROR":  "server rejected the request - verify the URL is still live",
    "TIMEOUT":     "no answer in time - raise timeout or retry later",
    "CONN":        "could not connect - DNS/TCP/TLS failure, host may be down",
    "EMPTY":       "200 but no text - renders lazily, force tier 2/3",
    "SELECTOR":    "page fetched fine, your CSS selectors matched nothing - fix your code",
    "KNOWN_DEAD":  "blacklisted by the ledger - skipped without retrying",
    "OK":          "",
}

RETRYABLE = {"TIMEOUT", "CONN", "RATE_LIMIT", "WALL"}


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


@dataclass
class Outcome:
    """Exactly one of these exists per input URL. None are ever dropped."""
    url: str
    host: str
    ok: bool
    tier: int = 0                 # tier that produced the usable response
    status: Optional[int] = None
    failure_class: str = "OK"
    secs: float = 0.0
    text_len: int = 0
    trace: list = field(default_factory=list)
    error: str = ""
    started_tier: int = 1

    @property
    def remedy(self) -> str:
        return REMEDIES.get(self.failure_class, "")

    @property
    def retryable(self) -> bool:
        return self.failure_class in RETRYABLE


@dataclass
class Report:
    outcomes: list[Outcome]

    def __len__(self) -> int:
        return len(self.outcomes)

    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.ok]

    def results(self) -> list[tuple[Outcome, Any]]:
        """(outcome, response) for the successes only."""
        return [(o, r) for o, r in self.outcomes if o.ok and getattr(o, "_page", None) is not None]

    @property
    def coverage(self) -> str:
        ok = len(self.outcomes) - len(self.failures())
        return f"{ok}/{len(self.outcomes)}"

    def summary(self) -> str:
        ok = [o for o in self.outcomes if o.ok]
        bad = self.failures()
        by_class: dict[str, list[str]] = {}
        for o in bad:
            by_class.setdefault(o.failure_class, []).append(o.url)
        lines = [f"COVERAGE {self.coverage}   ({len(ok)} ok, {len(bad)} failed)"]
        if not bad:
            lines.append("All URLs succeeded.")
            return "\n".join(lines)
        lines.append("")
        for cls, urls in sorted(by_class.items()):
            lines.append(f"  {cls} ({len(urls)}) - {REMEDIES.get(cls,'')}")
            for u in urls:
                lines.append(f"      {u}")
        return "\n".join(lines)

    def to_json(self, path: Path | str) -> Path:
        path = Path(path)
        data = [asdict(o) for o in self.outcomes]
        path.write_text(json.dumps(data, indent=2))
        return path


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #

def classify(page, trace, error: str = "") -> str:
    """Decide the failure class for one URL from its evidence."""
    if error:
        e = error.lower()
        if "timeout" in e or "timed out" in e:
            return "TIMEOUT"
        if "name or service not known" in e or "temporary failure in name resolution" in e:
            return "CONN"
        if "ssl" in e or "certificate" in e or "econnrefused" in e or "connection" in e:
            return "CONN"
        return "CONN"

    if page is None:
        # every tier was tried and none worked - report the deepest reason
        for t in reversed(trace):
            if t.get("outcome") == "error":
                return "TIMEOUT" if "timeout" in t.get("error", "").lower() else "CONN"
        return "WALL"

    status = getattr(page, "status", None)
    if status == 429:
        return "RATE_LIMIT"
    if is_blocked(page):
        return "WALL"
    if isinstance(status, int) and 400 <= status < 600:
        return "HTTP_ERROR"
    return "OK"


# --------------------------------------------------------------------------- #
# Ledger - the memory that survives between sessions
# --------------------------------------------------------------------------- #

def load_ledger(path: Path = LEDGER) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"version": 1, "hosts": {}, "totals": {}}


def save_ledger(data: dict, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def start_tier_for(url: str, ledger: dict) -> int:
    """The tier this host has historically needed. 1 if unknown."""
    h = host_of(url)
    rec = ledger.get("hosts", {}).get(h)
    if not rec:
        return 1
    return int(rec.get("min_tier", 1))


KNOWN_DEAD = {"KNOWN_DEAD"}


def record(outcomes: Iterable[Outcome], path: Path = LEDGER) -> dict:
    """Fold this run's outcomes into the ledger and return it."""
    data = load_ledger(path)
    hosts = data.setdefault("hosts", {})
    totals = data.setdefault("totals", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for o in outcomes:
        # a blacklisted skip is not new evidence, so it does not move counters
        if o.failure_class in KNOWN_DEAD:
            continue
        totals[o.failure_class] = totals.get(o.failure_class, 0) + 1
        h = o.host or host_of(o.url)
        if not h:
            continue
        rec = hosts.setdefault(h, {"runs": 0, "ok": 0, "fail": 0, "min_tier": 1,
                                   "last_class": None, "last_seen": None,
                                   "classes": {}})
        rec["runs"] += 1
        rec["last_seen"] = now
        rec["last_class"] = o.failure_class
        if o.ok:
            rec["ok"] += 1
            # the lowest tier that ever worked for this host is the floor
            rec["min_tier"] = min(int(rec.get("min_tier", 3)), o.tier or 3) if rec["ok"] > 1 else o.tier
        else:
            rec["fail"] += 1
            cls = o.failure_class
            rec["classes"][cls] = rec["classes"].get(cls, 0) + 1

    save_ledger(data, path)
    return data


def learned_notes(ledger: dict, threshold: int = 2) -> list[str]:
    """Human-readable advice derived from accumulated evidence."""
    out = []
    for h, rec in sorted(ledger.get("hosts", {}).items()):
        if rec.get("min_tier", 1) > 1 and rec.get("ok", 0) >= 1:
            out.append(f"{h} needs tier {rec['min_tier']} - now starting there automatically")
        for cls, n in rec.get("classes", {}).items():
            if n >= threshold and cls != "OK":
                out.append(f"{h}: {cls} x{n} - {REMEDIES.get(cls,'')}")
    return out


# --------------------------------------------------------------------------- #
# The batch runner
# --------------------------------------------------------------------------- #

def scrape_one(url: str, profile: str = "Default",
               extract: Optional[Callable[[Any], Any]] = None,
               use_ledger: bool = True, policy: Optional[dict] = None, **kw) -> Outcome:
    """Scrape a single URL, always returning a classified Outcome."""
    if policy is None and use_ledger:
        try:
            from scrapling_diag import load_policy
            policy = load_policy()
        except Exception:
            policy = {"blocked_domains": [], "tier_floor": {}}

    # Self-improvement: a host the curator has blacklisted is skipped instantly
    # instead of re-proving it dead on every run.
    if policy:
        from scrapling_diag import is_blocklisted
        dead = is_blocklisted(url, policy)
        if dead:
            return Outcome(url=url, host=dead, ok=False, tier=0, status=None,
                           failure_class="KNOWN_DEAD", secs=0.0, text_len=0,
                           trace=[{"tier": 0, "name": "policy", "outcome": "skipped"}],
                           error="host blacklisted by the failure ledger")

    ledger = load_ledger() if use_ledger else {"hosts": {}}
    start = start_tier_for(url, ledger)

    # tier floor learned from previous runs
    if policy:
        h = host_of(url)
        start = max(start, int(policy.get("tier_floor", {}).get(h, 1)))

    t0 = time.monotonic()
    page, tier, trace = smart_traced(url, profile=profile, start_tier=start, **kw)
    secs = round(time.monotonic() - t0, 2)

    error = ""
    if page is None:
        for t in reversed(trace):
            if t.get("outcome") == "error":
                error = f"{t.get('error')}: {t.get('detail','')}"
                break

    cls = classify(page, trace, error)

    # Fetch succeeded but extraction found nothing -> that's a SELECTOR bug,
    # not a wall. Escalating tiers will never fix it.
    if page is not None and cls == "OK" and extract is not None:
        try:
            if not extract(page):
                cls = "SELECTOR"
        except Exception as exc:
            cls = "SELECTOR"
            error = f"extract raised {type(exc).__name__}"

    text_len = len(_page_text(page)) if page is not None else 0
    o = Outcome(url=url, host=host_of(url), ok=(cls == "OK"), tier=tier,
                status=getattr(page, "status", None), failure_class=cls,
                secs=secs, text_len=text_len, trace=trace, error=error,
                started_tier=start)
    if o.ok:
        o._page = page
    return o


def scrape_many(urls: Iterable[str], profile: str = "Default",
                extract: Optional[Callable[[Any], Any]] = None,
                persist: bool = True, quiet: bool = False, **kw) -> Report:
    """Scrape every URL, returning one Outcome each - successes AND failures.

    Never returns a silently short list. Check `report.coverage`.
    """
    outcomes = []
    for i, u in enumerate(urls, 1):
        if not quiet:
            print(f"  [{i}] {u}", flush=True)
        o = scrape_one(u, profile=profile, extract=extract, **kw)
        outcomes.append(o)
        if not quiet:
            mark = "ok " if o.ok else "FAIL"
            print(f"      {mark} {o.failure_class:10s} tier={o.tier} "
                  f"status={o.status} {o.secs}s", flush=True)
    report = Report(outcomes)
    if persist:
        record(outcomes)
    return report
