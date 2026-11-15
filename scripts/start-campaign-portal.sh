#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
port="${DND_PORTAL_PORT:-5002}"
[[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1024 && port <= 65535 )) || { echo 'Invalid DND_PORTAL_PORT.' >&2; exit 2; }
url="http://127.0.0.1:$port/health"
data_root="${DND_CAMPAIGN_ROOT:-$HOME/.claude/dnd}"

if [[ "${DND_PORTAL_BOOT_SERVICES:-1}" == 1 ]]; then
  bash "$repo_root/scripts/launch-campaign-m5.sh" --start qwen
fi

portal_ready() {
  local body
  body="$(curl --silent --fail --max-time 2 "$url")" || return 1
  python3 -c 'import json,os,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("service") == "dnd-campaign-portal" and d.get("ready") is True and os.path.realpath(d.get("data_root", "")) == os.path.realpath(sys.argv[1]) else 1)' "$data_root" <<< "$body"
}

if portal_ready; then
  if [[ "${1:-}" != --restart && "${1:-}" != --foreground ]]; then
    printf 'Campaign portal ready: http://127.0.0.1:%s/\n' "$port"
    exit 0
  fi
  # A fresh portal opens Safari with a new sign-in link. A running DM keeps running.
  if curl --silent --fail --max-time 2 "$url" | grep -q '"busy":true'; then
    echo 'The portal is saving or changing the DM. Try again when that has finished.' >&2
    exit 1
  fi
  pid="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1)"
  if [[ -z "$pid" ]] || ! ps -o command= -p "$pid" | grep -q 'scripts/campaign_portal.py'; then
    echo "Could not find the campaign portal on port $port." >&2
    exit 1
  fi
  kill "$pid"
  for attempt in {1..20}; do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.5
  done
fi
if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $port belongs to another service; the campaign portal was not started." >&2
  exit 1
fi

mkdir -p "$data_root/.runtime"
foreground=0
[[ "${1:-}" == --foreground ]] && foreground=1
python_bin="$(command -v python3)"
# First virtualenv found wins; the ones inside a data root are the old locations.
for venv in "${DND_VENV:-}" "$HOME/.local/share/dnd/venv" "$data_root/.venv" "$HOME/.claude/dnd/.venv"; do
  if [[ -n "$venv" && -x "$venv/bin/python3" ]]; then python_bin="$venv/bin/python3"; break; fi
done
"$python_bin" -c 'import flask' >/dev/null 2>&1 || { echo 'Flask is needed for the campaign portal.' >&2; exit 1; }

if (( foreground )); then
  # Here the portal prints its sign-in link to this terminal; Ctrl-C stops it.
  exec "$python_bin" -u "$repo_root/scripts/campaign_portal.py"
fi
# The DM can write the runtime dir: replace any file or link there, and never open an existing one.
log="$data_root/.runtime/portal.log"
rm -f "$log"
(set -C; exec nohup "$python_bin" -u "$repo_root/scripts/campaign_portal.py" > "$log" 2>&1 < /dev/null) &
for attempt in {1..20}; do
  if portal_ready; then
    sleep 1
    if grep -q 'No sign-in link was handed out' "$log"; then
      grep 'No sign-in link was handed out' "$log" >&2
      exit 1
    fi
    printf 'Campaign portal ready: http://127.0.0.1:%s/\n' "$port"
    exit 0
  fi
  sleep 0.5
done
echo "Campaign portal did not start. Check $data_root/.runtime/portal.log" >&2
exit 1
