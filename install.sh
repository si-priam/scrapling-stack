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
#   ~/venvs/scrapling                     Python stack (scrapling 0.4.15, pinned)
#   ~/.local/share/scrapling              the modules + the scraper's profile
#   ~/.config/systemd/user/scrapling-*    two-minute login sync, as a user timer
#   ~/.config/opencode/opencode.jsonc     the Scrapling MCP server entry
#
# Nothing outside your home directory is touched. No sudo. No Chromium.

set -euo pipefail

SOURCE_HOME="/home/dhh"
BRAVE_BIN="/opt/brave.com/brave-nightly/brave"
SCRAPLING_PIN="scrapling[all]==0.4.15"
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

# --------------------------------------------------------------------------
# 1. Preflight. Stop rather than improvise.
# --------------------------------------------------------------------------
step "Preflight"

[[ -d "$HOME" ]] || die "no home directory"

command -v uv >/dev/null 2>&1 \
  || die "uv is missing. Install it first (Omarchy: mise use -g uv@latest) and re-run."
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

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
# 2. Put the files where the paths expect them.
# --------------------------------------------------------------------------
step "Placing the files"

mkdir -p "$SHARE" "$VENV/bin" "$HOME/.config/systemd/user" "$(dirname "$OPENCODE_CFG")"

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

for f in "$HERE"/bin/*; do
  install -m 0755 "$f" "$VENV/bin/$(basename "$f")"
  rewrite "$VENV/bin/$(basename "$f")"
done
ok "commands in $VENV/bin"

# policy.json is a generated file; seed it only when absent.
[[ -f "$SHARE/policy.json" ]] || install -m 0644 "$HERE/modules/policy.example.json" "$SHARE/policy.json"

# --------------------------------------------------------------------------
# 3. Python 3.13 + the pinned stack.
# --------------------------------------------------------------------------
step "Python stack"

# 3.13 deliberately: curl_cffi 0.16.3 publishes no cp314 wheel, so the system's
# 3.14 would try to build it from source and pull in a Rust toolchain.
# Nothing is installed system-wide: uv keeps any interpreter it fetches inside
# its own private store, and reuses one that is already on the machine.
if existing_py="$(uv python find 3.13 2>/dev/null)"; then
  ok "Python 3.13 is already on this machine - nothing fetched"
  log "   $existing_py"
else
  log "fetching a private Python 3.13 into uv's own store (your system Python is not touched)"
  uv python install 3.13 >/dev/null
fi

if [[ -x "$VENV/bin/python" ]]; then
  ok "environment exists ($("$VENV/bin/python" --version 2>&1))"
  if "$VENV/bin/python" -c 'import scrapling' 2>/dev/null; then
    ok "scrapling already installed; leaving it alone"
  else
    log "installing $SCRAPLING_PIN"
    uv pip install --python "$VENV/bin/python" "$SCRAPLING_PIN"
  fi
else
  log "creating $VENV on Python 3.13"
  uv venv "$VENV" --python 3.13
  log "installing $SCRAPLING_PIN"
  uv pip install --python "$VENV/bin/python" "$SCRAPLING_PIN"
fi

"$VENV/bin/python" - <<'PY' || die "the Python stack did not import cleanly"
import importlib.metadata as md
import scrapling, playwright, patchright
for name in ("scrapling", "playwright", "patchright"):
    print("  ok   %s %s" % (name, md.version(name)))
PY

# --------------------------------------------------------------------------
# 4. Reclaim the duplicate Node runtime (btrfs only).
# --------------------------------------------------------------------------
step "Reclaiming duplicate runtime"

dedupe_node() {
  local P="$VENV/lib/python3.13/site-packages"
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

if pgrep -f "$DAILY_PROFILE" >/dev/null 2>&1; then
  die "your daily Brave is running. Close it, then re-run (the profile must not be mid-write)."
fi

if [[ -d "$PROFILE" && -f "$PROFILE/Default/Cookies" ]]; then
  ok "scraper profile already present"
else
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
  log "self-test"
  "$VENV/bin/selftest" || warn "selftest reported a problem (see above)"
  log "stealth check"
  "$VENV/bin/verify-stealth" | tail -20 || warn "verify-stealth reported a problem (see above)"
  step "Done"
  printf '  Compare the results above with modules/DIAGNOSTIC.md from the other machine.\n'
  printf '  Check on demand with: %s/bin/scrapling-sync --status\n' "$VENV"
else
  step "Done (verification skipped)"
fi
