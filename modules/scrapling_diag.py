"""Live diagnostic panel: success / fallback / failure rates and the reasons why.

The RLM connection
------------------
A Recursive Language Model never pulls bulky input into its own context. It
hands the input to a persistent Python process, inspects it there, and lets
sub-workers chew through the verbose parts - keeping only a small, structured
summary in context.

That is exactly what this scraper already does:

| RLM concept                     | Here                                    |
|---------------------------------|-----------------------------------------|
| persistent Python REPL          | the venv + `scrape_batch`               |
| bulky input never in context    | raw HTML stays in the process, not your prompt |
| external knowledge base         | `ledger.json`                           |
| Reflector (extract lessons)     | `--learn`, the LEARNED block below       |
| Curator (update the KB)         | `policy.json` (blocklist + tier floors) |
| `answer["content"]`             | this panel - a few hundred chars        |

The panel is the "answer". Everything else stays in Python, where it is cheap.

Usage
-----
    from scrapling_diag import show
    show(report)                       # after any scrape_many()
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

POLICY = Path("/home/dhh/.local/share/scrapling/policy.json")
LEDGER = Path("/home/dhh/.local/share/scrapling/ledger.json")

# What may blacklist a host.
#
# Only CONN qualifies. A host that does not resolve or refuses TCP is genuinely
# unusable, and retrying it costs ten seconds every single time.
#
# WALL is deliberately NOT in this set. We drive your real, logged-in browser:
# many sites that serve an anti-bot wall to an anonymous client let a signed-in
# session straight through, and some (with your warm profile and cf_clearance)
# are fine. Blacklisting on WALL would throw away working sites - and the cost
# of that mistake is silent, permanent data loss, whereas a retry costs seconds.
# Walls are tracked and surfaced, never acted on.
DEAD_CLASSES = {"CONN"}
DEAD_AFTER = 3


@dataclass
class Metrics:
    total: int = 0
    direct: int = 0      # succeeded at the tier we started at
    rescued: int = 0     # succeeded only after escalating
    lost: int = 0        # never succeeded
    secs: float = 0.0
    reasons: dict = field(default_factory=dict)      # failure_class -> count
    fallback_why: dict = field(default_factory=dict) # why we escalated
    by_host: dict = field(default_factory=dict)

    @property
    def ok(self) -> int:
        return self.direct + self.rescued

    @property
    def success_rate(self) -> float:
        return (self.ok / self.total * 100) if self.total else 0.0

    @property
    def fallback_rate(self) -> float:
        return (self.rescued / self.total * 100) if self.total else 0.0

    @property
    def failure_rate(self) -> float:
        return (self.lost / self.total * 100) if self.total else 0.0


def analyze(report) -> Metrics:
    """Turn a Report into the three numbers, plus the reasons behind them."""
    m = Metrics()
    outcomes = list(report.outcomes)
    m.total = len(outcomes)

    for o in outcomes:
        m.secs += o.secs
        m.by_host[o.host] = m.by_host.get(o.host, 0) + (1 if o.ok else 0)

        if o.ok:
            if o.tier and o.started_tier and o.tier > o.started_tier:
                m.rescued += 1
                # why did we have to escalate? the trace records it
                for t in o.trace:
                    if t.get("outcome") in ("blocked", "error") and t.get("tier"):
                        key = f"tier{t['tier']} {t['name']} -> {t['outcome']}"
                        if t.get("status"):
                            key += f"({t['status']})"
                        m.fallback_why[key] = m.fallback_why.get(key, 0) + 1
                        break
            else:
                m.direct += 1
        else:
            m.lost += 1
            key = f"{o.failure_class}"
            if o.failure_class == "WALL" and o.started_tier > 1:
                key = "WALL (already at max tier)"
            m.reasons[key] = m.reasons.get(key, 0) + 1
    return m


# --------------------------------------------------------------------------- #
# Curator: turn accumulated evidence into an actionable policy
# --------------------------------------------------------------------------- #

def curate(ledger: dict | None = None, policy_path: Path = POLICY) -> dict:
    """Read the ledger, write policy.json. This is the self-improvement step.

    Two rules, both learned from evidence rather than hardcoded:

    * a host that has failed the same way `DEAD_AFTER` times is blacklisted, so
      future runs skip it instantly instead of re-proving it every session
    * a host that needed escalation is pinned to that tier floor
    """
    if ledger is None:
        try:
            ledger = json.loads(LEDGER.read_text())
        except Exception:
            ledger = {"hosts": {}, "totals": {}}

    blocked, floors, gated = set(), {}, {}
    for host, rec in ledger.get("hosts", {}).items():
        cls = rec.get("classes", {})
        # only a genuinely unreachable host gets blacklisted
        if sum(n for c, n in cls.items() if c in DEAD_CLASSES) >= DEAD_AFTER:
            blocked.add(host)
        # walls are surfaced, never blocked - a logged-in session often passes
        walls = cls.get("WALL", 0)
        if walls and host not in blocked:
            gated[host] = walls
        if rec.get("ok", 0) >= 1 and rec.get("min_tier", 1) > 1:
            floors[host] = rec["min_tier"]

    policy = {
        "version": 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "blocked_domains": sorted(blocked),
        "tier_floor": floors,
        "walled_but_tried": sorted(gated),
        "note": ("auto-generated by scrapling_diag.curate() - safe to delete. "
                 "Only unreachable hosts are blocked; anti-bot walls are "
                 "recorded but never skipped, because we scrape with a real "
                 "logged-in browser profile."),
    }
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(policy, indent=2))
    return policy


def load_policy(policy_path: Path = POLICY) -> dict:
    try:
        return json.loads(policy_path.read_text())
    except Exception:
        return {"blocked_domains": [], "tier_floor": {}}


def is_blocklisted(url: str, policy: dict | None = None) -> str | None:
    from urllib.parse import urlparse
    p = policy if policy is not None else load_policy()
    h = (urlparse(url).hostname or "").lower()
    return h if h in p.get("blocked_domains", []) else None


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

BAR = "█" * 40
BAR_ON = "█"
BAR_OFF = "░"


def _bar(pct: float, width: int = 24) -> str:
    filled = int(round(width * pct / 100))
    return BAR_ON * filled + BAR_OFF * (width - filled)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def panel(m: Metrics, policy: dict | None = None, width: int = 68) -> str:
    """The compact diagnostic box. A few hundred characters, never the HTML."""
    policy = policy if policy is not None else load_policy()
    lines: list[str] = []

    head = (f"  {m.total} urls   "
            f"✔{m.direct} direct   "
            f"↻{m.rescued} rescued   "
            f"✖{m.lost} lost   "
            f"{m.secs:.1f}s")
    lines.append(head)
    lines.append(f"  {_bar(m.success_rate)}  {m.success_rate:.0f}% success")
    lines.append(f"     fallback {m.fallback_rate:.0f}%   failure {m.failure_rate:.0f}%")

    if m.fallback_why:
        top = sorted(m.fallback_why.items(), key=lambda kv: -kv[1])[:3]
        lines.append("  why escalated: " + ", ".join(f"{k}×{v}" for k, v in top))
    if m.reasons:
        top = sorted(m.reasons.items(), key=lambda kv: -kv[1])[:3]
        lines.append("  why lost     : " + ", ".join(f"{k}×{v}" for k, v in top))

    bl = policy.get("blocked_domains", [])
    fl = policy.get("tier_floor", {})
    wl = policy.get("walled_but_tried", [])
    if bl:
        lines.append(f"  skipped      : {len(bl)} unreachable host(s) — {_clip(', '.join(bl), 32)}")
    if wl:
        lines.append(f"  walled, kept : {len(wl)} host(s) served a wall but are still tried")
    if fl:
        lines.append(f"  tier floor   : {len(fl)} host(s) pinned higher")

    inner = max(len(x) for x in lines)
    inner = min(max(inner, len(head)), width - 4)
    box = ["┌" + "─" * (inner + 2) + "┐"]
    for ln in lines:
        box.append("│ " + ln.ljust(inner)[:inner] + " │")
    box.append("└" + "─" * (inner + 2) + "┘")
    return "\n".join(box)


def show(report, policy: dict | None = None, bottom: bool = True) -> str:
    """Render the panel. On a TTY it docks to the bottom centre, then restores
    the cursor position so your scrollback is untouched."""
    m = analyze(report)
    text = panel(m, policy)

    if not (bottom and sys.stdout.isatty()):
        print(text)
        return text

    term = shutil.get_terminal_size()
    lines = text.splitlines()
    pad_top = max(0, term.lines - len(lines) - 1)
    col = max(0, (term.columns - max(len(x) for x in lines)) // 2)

    sys.stdout.write(f"\x1b7")                       # save cursor
    sys.stdout.write(f"\x1b[{pad_top + 1};1H")       # move to the dock row
    for ln in lines:
        sys.stdout.write(f"\x1b[{col + 1}G{ln}\n")
    sys.stdout.write("\x1b8")                       # restore cursor
    sys.stdout.flush()
    return text
