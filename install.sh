#!/usr/bin/env bash
# install.sh - bring up the Brave-native Scrapling stack on this machine.
#
# Safe to re-run. Every step is idempotent: it checks first, does only what is
# missing, and never overwrites a working piece.
#
#   ./install.sh              # full install, then verify
#   ./install.sh --no-verify  # install only, skip the checks
#   ./install.sh --check      # preflight only, change nothing
#
# What it builds:
#   ~/venvs/scrapling                     Python stack (scrapling 0.5, pinned)
#   ~/.local/share/scrapling              the modules + the scraper's profile
#   ~/.config/systemd/user/scrapling-*    two-minute login sync, as a user timer
#   ~/.config/opencode/opencode.jsonc     the Scrapling MCP server entry
#
# Nothing outside your home directory is touched. No sudo. No Chromium.

set -euo pipefail

SOURCE_HOME="/home/dhh"
BRAVE_BIN="/opt/brave.com/brave-nightly/brave"
# Scrapling 0.5, pinned so two machines bring up the same thing. 0.5 ships two
# fetching engines -- plain HTTP, and the stealth browser; the old Playwright
# "dynamic" engine was removed upstream, which is why the hidden-browser tier in
# modules/scrapling_brave.py runs the stealth engine. It runs on the system's own
# Python, and every dependency has a wheel, so nothing is built from source.
SCRAPLING_PIN="scrapling[all]==0.5"
PINNED_VERSION="${SCRAPLING_PIN##*==}"
VENV="$HOME/venvs/scrapling"
SHARE="$HOME/.local/share/scrapling"
PROFILE="$SHARE/brave-profile"
DAILY_PROFILE="$HOME/.config/BraveSoftware/Brave-Browser-Nightly"
OPENCODE_CFG="$HOME/.config/opencode/opencode.jsonc"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY=1

for arg in "$@"; do
  case "$arg" in
    --no-verify) VERIFY=0 ;;
    --check) CHECK_ONLY=1 ;;
    --help|-h) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

log()  { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  warn %s\n' "$*" >&2; }
die()  { printf '  FAIL %s\n' "$*" >&2; exit 1; }

# Is the owner's daily Brave actually running? Check the process itself rather
# than any command line that merely mentions the profile path -- a shell, or an
# agent part-way through a command, can match that string and must never be
# mistaken for the browser.
daily_brave_running() {
  local p exe
  for p in $(pgrep -f "$DAILY_PROFILE" 2>/dev/null || true); do
    exe="$(readlink -f "/proc/$p/exe" 2>/dev/null || true)"
    case "$exe" in
      "$BRAVE_BIN"|*/brave-nightly/*) return 0 ;;
    esac
  done
  return 1
}

# --------------------------------------------------------------------------
# 1. Preflight. Stop rather than improvise.
# --------------------------------------------------------------------------
step "Preflight"

[[ -d "$HOME" ]] || die "no home directory"

# Use the Python this machine already has -- the system's own -- so the stack
# needs no interpreter of its own, and nothing here names a version that can go
# stale when the distribution moves forward. Every dependency ships a wheel, so
# nothing is compiled.
#
# The 3.10 in the check below is Scrapling's own minimum, not a preference: it is
# there only to stop with a clear sentence on an ancient Python. Nothing is
# installed either way -- a missing or too-old Python is a problem to report, not
# to solve by installing things.
HAVE_UV=0
if command -v uv >/dev/null 2>&1; then
  HAVE_UV=1
  ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
fi

PY=""
for candidate in /usr/bin/python3 \
                 "$(command -v python3 2>/dev/null || true)" \
                 "$( { command -v uv >/dev/null 2>&1 && uv python find 3 2>/dev/null; } || true )"; do
  [[ -n "$candidate" && -x "$candidate" ]] || continue
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
[[ -n "$PY" ]] \
  || die "the system Python is missing or older than 3.10 (Scrapling's minimum). Install your distribution's Python, then re-run."
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ok "Python $PYVER found: $(readlink -f "$PY")"

[[ -x "$BRAVE_BIN" ]] \
  || die "Brave Nightly is missing at $BRAVE_BIN. Install it first (yay -S brave-nightly-bin) and re-run."
ok "Brave Nightly present"

if pgrep -f '[g]nome-keyring-daemon' >/dev/null 2>&1; then
  ok "gnome-keyring is running"
else
  warn "gnome-keyring-daemon is not running in this session."
  warn "Cookies will come back empty until it is. Start a fresh graphical session."
fi

command -v systemctl >/dev/null 2>&1 \
  || die "systemd --user is unavailable; the login sync cannot be installed."
ok "systemd user session available"

if [[ "$HOME" != "$SOURCE_HOME" ]]; then
  warn "home is $HOME but the bundled files were written for $SOURCE_HOME; rewriting paths"
fi

if [[ -d "$DAILY_PROFILE" ]]; then
  ok "daily Brave profile found ($(du -sh "$DAILY_PROFILE" 2>/dev/null | cut -f1))"
else
  warn "no daily Brave profile yet at $DAILY_PROFILE"
  warn "Launch Brave Nightly once, sign in to your sites, close it, then re-run."
fi

if [[ "${CHECK_ONLY:-0}" == "1" ]]; then
  printf '\npreflight complete - nothing was changed\n'
  exit 0
fi

# --------------------------------------------------------------------------
# 2. The Python environment.
# --------------------------------------------------------------------------
step "Python environment"

# uv first: it is faster and resolves the interpreter before creating the
# environment. pip is the fallback and builds exactly the same environment --
# uv is a preference here, not a requirement.
#
# The system Python deliberately: it is the one the distribution keeps current,
# so no version number here goes stale, and every dependency ships a wheel, so
# there is no source build and no toolchain.
#
# Nothing is installed system-wide either way. Everything lives under $HOME.
install_stack() {
  if [[ "$HAVE_UV" == "1" ]]; then
    uv pip install --python "$VENV/bin/python" "$SCRAPLING_PIN"
  else
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$VENV/bin/python" -m pip install "$SCRAPLING_PIN"
  fi
}

# The environment has to be built on the interpreter chosen above. One left on a
# Python this machine no longer has -- after the distribution upgrades it, say --
# is rebuilt here, so that upgrade needs no manual cleanup.
VENV_PY="$(readlink -f "$VENV/bin/python" 2>/dev/null || true)"
WANT_PY="$(readlink -f "$PY")"

if [[ -x "$VENV/bin/python" && "$VENV_PY" == "$WANT_PY" ]]; then
  ok "environment exists on Python $PYVER ($VENV_PY)"
  # Compare against the pin rather than just "does it import": an environment
  # left on an older Scrapling imports fine and then fails at the first scrape.
  HAVE="$("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("scrapling"))' 2>/dev/null || true)"
  if [[ "$HAVE" == "$PINNED_VERSION" ]]; then
    ok "scrapling $HAVE already installed; leaving it alone"
  else
    if [[ -n "$HAVE" ]]; then
      log "scrapling $HAVE is installed but $PINNED_VERSION is pinned; changing it"
    else
      log "installing $SCRAPLING_PIN"
    fi
    install_stack
  fi
else
  if [[ -e "$VENV" ]]; then
    log "rebuilding $VENV on Python $PYVER (it was built on a different interpreter)"
  else
    log "creating $VENV on Python $PYVER"
  fi
  if [[ "$HAVE_UV" == "1" ]]; then
    uv venv "$VENV" --python "$PY" --clear
  else
    "$PY" -m venv --clear "$VENV"
  fi
  log "installing $SCRAPLING_PIN"
  install_stack
fi

"$VENV/bin/python" - <<'PY' || die "the Python stack did not import cleanly"
import importlib.metadata as md
import scrapling, playwright, patchright
for name in ("scrapling", "playwright", "patchright"):
    print("  ok   %s %s" % (name, md.version(name)))
PY

# --------------------------------------------------------------------------
# 3. Put the files where the paths expect them.
# --------------------------------------------------------------------------
step "Placing the files"

mkdir -p "$SHARE" "$HOME/.config/systemd/user" "$(dirname "$OPENCODE_CFG")"

# The upstream files hardcode /home/dhh. Rewrite only if this home differs.
rewrite() {
  local f="$1"
  if [[ "$HOME" != "$SOURCE_HOME" ]] && grep -q "$SOURCE_HOME" "$f" 2>/dev/null; then
    sed -i "s|$SOURCE_HOME|$HOME|g" "$f"
  fi
}

for f in "$HERE"/modules/scrapling_*.py; do
  install -m 0644 "$f" "$SHARE/$(basename "$f")"
  rewrite "$SHARE/$(basename "$f")"
done
install -m 0644 "$HERE/modules/DIAGNOSTIC.md" "$SHARE/DIAGNOSTIC.md"
ok "modules in $SHARE"

# The modules use Scrapling's own API, so import them now. A Scrapling release
# that changed that API is then caught here with a plain error, instead of at
# the first scrape.
( cd "$SHARE" && "$VENV/bin/python" -c \
  'import scrapling_brave, scrapling_batch, scrapling_cleanup, scrapling_diag, scrapling_sync' ) \
  || die "the scraper's own code did not load against scrapling $PINNED_VERSION (see the error above)"
ok "modules load against scrapling $PINNED_VERSION"

for f in "$HERE"/bin/*; do
  install -m 0755 "$f" "$VENV/bin/$(basename "$f")"
  rewrite "$VENV/bin/$(basename "$f")"
done
ok "commands in $VENV/bin"

# policy.json is a generated file; seed it only when absent.
[[ -f "$SHARE/policy.json" ]] || install -m 0644 "$HERE/modules/policy.example.json" "$SHARE/policy.json"

# --------------------------------------------------------------------------
# 4. Reclaim the duplicate Node runtime (btrfs only).
# --------------------------------------------------------------------------
step "Reclaiming duplicate runtime"

dedupe_node() {
  local P="$VENV/lib/python$PYVER/site-packages"
  local a="$P/playwright/driver/node" b="$P/patchright/driver/node"
  [[ -f "$a" && -f "$b" ]] || { warn "driver binaries not found; skipping"; return 0; }
  if [[ "$(sha256sum "$a" | cut -d' ' -f1)" != "$(sha256sum "$b" | cut -d' ' -f1)" ]]; then
    warn "the two driver binaries differ; leaving both copies alone"
    return 0
  fi
  if cp --reflink=always --remove-destination "$a" "$b" 2>/dev/null; then
    ok "one Node runtime instead of two (about 127 MB reclaimed)"
  else
    warn "reflink unavailable on this filesystem - leaving both copies in place"
  fi
}
dedupe_node

# --------------------------------------------------------------------------
# 5. The scraper's profile: a share of the real one, never a copy.
# --------------------------------------------------------------------------
step "Scraper profile"

if [[ -d "$PROFILE" && -f "$PROFILE/Default/Cookies" ]]; then
  ok "scraper profile already present"
else
  # The share is taken once. It must not be taken from a profile that is being
  # written to, so the running-Brave check belongs here rather than above: a
  # later re-run -- after the system's Python moves, say -- needs no Brave at all.
  if daily_brave_running; then
    die "your daily Brave is running. Close it, then re-run (the profile must not be mid-write)."
  fi
  [[ -d "$DAILY_PROFILE" ]] || die "no daily Brave profile to share yet. Launch Brave Nightly once and re-run."
  mkdir -p "$SHARE"
  rm -rf "$PROFILE"
  if cp -a --reflink=always "$DAILY_PROFILE" "$PROFILE" 2>/dev/null; then
    ok "profile shared with your daily Brave (copy-on-write clone: no extra disk)"
  else
    warn "this filesystem has no reflink support - falling back to a real copy"
    rsync -a --delete \
      --exclude 'Cache' --exclude 'Code Cache' --exclude 'GPUCache' \
      --exclude 'Crashpad' --exclude 'ShaderCache' \
      "$DAILY_PROFILE/" "$PROFILE/"
    ok "profile copied (about 250 MB of real disk)"
  fi
  rm -f "$PROFILE"/Singleton*
fi

# --------------------------------------------------------------------------
# 6. The login sync, as a user timer.
# --------------------------------------------------------------------------
step "Automatic login sync"

for unit in scrapling-sync.service scrapling-sync.timer; do
  install -m 0644 "$HERE/systemd/$unit" "$HOME/.config/systemd/user/$unit"
done
rewrite "$HOME/.config/systemd/user/scrapling-sync.service"
systemctl --user daemon-reload
systemctl --user enable --now scrapling-sync.timer >/dev/null 2>&1 || warn "could not enable the timer"
ok "sync runs every two minutes, and at the start of any scrape"

# --------------------------------------------------------------------------
# 7. The OpenCode entry.
# --------------------------------------------------------------------------
step "OpenCode"

"$VENV/bin/python" - "$OPENCODE_CFG" "$VENV" "$BRAVE_BIN" <<'PY'
import json, pathlib, re, sys
cfg_path, venv, brave = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(cfg_path)
if p.exists():
    raw = p.read_text()
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        # tolerate a jsonc file with comments/trailing commas
        stripped = re.sub(r'(?m)^\s*//.*$', '', raw)
        stripped = re.sub(r',(\s*[}\]])', r'\1', stripped)
        cfg = json.loads(stripped)
else:
    cfg = {}
cfg.setdefault("mcp", {})
cfg["mcp"].setdefault("servers", {})
cfg["mcp"]["servers"]["scrapling"] = {
    "type": "local",
    "command": [f"{venv}/bin/scrapling-mcp", "--executable-path", brave],
}
p.write_text(json.dumps(cfg, indent=2) + "\n")
print("  ok   scrapling server registered in", cfg_path)
PY

# --------------------------------------------------------------------------
# 8. Prove it works.
# --------------------------------------------------------------------------
if [[ "$VERIFY" == "1" ]]; then
  step "Verification"
  # The self-test compares the scraper's browser against the owner's running one
  # and checks that it is never touched, so it cannot run while Brave is closed.
  # Say that plainly rather than reporting a failure that is not one.
  if daily_brave_running; then
    log "self-test"
    "$VENV/bin/selftest" || warn "selftest reported a problem (see above)"
  else
    log "self-test skipped: it needs your daily Brave open"
    printf '       Open Brave Nightly, then run: %s/bin/selftest\n' "$VENV"
  fi
  log "stealth check"
  "$VENV/bin/verify-stealth" | tail -20 || warn "verify-stealth reported a problem (see above)"
  step "Done"
  printf '  Compare the results above with modules/DIAGNOSTIC.md from the other machine.\n'
  printf '  Check on demand with: %s/bin/scrapling-sync --status\n' "$VENV"
else
  step "Done (verification skipped)"
fi
