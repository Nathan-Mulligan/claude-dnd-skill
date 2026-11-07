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
  printf 'Campaign portal ready: http://127.0.0.1:%s/\n' "$port"
  exit 0
fi
if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $port belongs to another service; the campaign portal was not started." >&2
  exit 1
fi

mkdir -p "$data_root/.runtime"
python_bin="$(command -v python3)"
if [[ -x "$data_root/.venv/bin/python3" ]]; then python_bin="$data_root/.venv/bin/python3"; fi
if [[ ! -x "$data_root/.venv/bin/python3" && -x "$HOME/.claude/dnd/.venv/bin/python3" ]]; then
  python_bin="$HOME/.claude/dnd/.venv/bin/python3"
fi
"$python_bin" -c 'import flask' >/dev/null 2>&1 || { echo 'Flask is needed for the campaign portal.' >&2; exit 1; }

nohup "$python_bin" -u "$repo_root/scripts/campaign_portal.py" > "$data_root/.runtime/portal.log" 2>&1 < /dev/null &
for attempt in {1..20}; do
  portal_ready && { printf 'Campaign portal ready: http://127.0.0.1:%s/\n' "$port"; exit 0; }
  sleep 0.5
done
echo "Campaign portal did not start. Check $data_root/.runtime/portal.log" >&2
exit 1
