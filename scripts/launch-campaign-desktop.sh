#!/usr/bin/env bash
set -euo pipefail

host="${DND_M5_HOST:-m5}"
repo="${DND_M5_REPO:-/Users/agencyastro/Desktop/Agents/repos/claude-dnd-skill/main}"
[[ "$host" =~ ^[A-Za-z0-9._-]+$ ]] || { echo 'Invalid M5 host.' >&2; exit 2; }
[[ "$repo" == /* && "$repo" != *$'\n'* ]] || { echo 'DND_M5_REPO must be an absolute path.' >&2; exit 2; }

remote() {
  local command_string='' part
  for part in "$@"; do printf -v part '%q' "$part"; command_string+="$part "; done
  ssh "$host" "$command_string"
}

printf '\nD&D Campaign — M5\n\n1. Qwen 35B MLX (fully local)\n2. Codex GPT Terra (medium)\n3. Claude Code\n\n'
read -r -p 'Model [1-3]: ' model_choice
case "$model_choice" in
  1) provider=qwen ;;
  2) provider=codex ;;
  3) provider=claude ;;
  *) echo 'Choose 1, 2 or 3.' >&2; exit 2 ;;
esac

printf '\n1. New campaign\n2. Resume a campaign\n\n'
read -r -p 'Campaign [1-2]: ' campaign_choice
case "$campaign_choice" in
  1) campaign='' ;;
  2)
    printf '\nSaved campaigns:\n'
    remote bash "$repo/scripts/launch-campaign-m5.sh" --list
    read -r -p 'Campaign name: ' campaign
    [[ "$campaign" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$campaign" != '.' && "$campaign" != '..' ]] || { echo 'Invalid campaign name.' >&2; exit 2; }
    remote bash "$repo/scripts/launch-campaign-m5.sh" --exists "$campaign" || { echo 'Campaign not found on M5.' >&2; exit 2; }
    ;;
  *) echo 'Choose 1 or 2.' >&2; exit 2 ;;
esac

if [[ "${1:-}" == --check ]]; then
  remote bash "$repo/scripts/launch-campaign-m5.sh" --check "$provider"
  exit
fi
[[ $# -eq 0 ]] || { echo 'Usage: launch-campaign-desktop.sh [--check]' >&2; exit 2; }
if [[ "$provider" == claude ]]; then
  remote bash "$repo/scripts/launch-campaign-m5.sh" --claude-auth
fi

echo 'Checking M5 services...'
remote bash "$repo/scripts/launch-campaign-m5.sh" --start "$provider"

if ! curl --silent --fail --max-time 2 http://127.0.0.1:5001/ping >/dev/null; then
  if lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null 2>&1; then
    echo 'Laptop port 5001 is used by another service. Close it before launching.' >&2
    exit 1
  fi
  ssh -fN -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L 127.0.0.1:5001:127.0.0.1:5001 "$host"
fi
curl --silent --fail --max-time 3 http://127.0.0.1:5001/ping >/dev/null || { echo 'Display tunnel is not ready.' >&2; exit 1; }

if [[ "$(uname)" == Darwin ]]; then
  osascript <<'APPLESCRIPT'
set targetURL to "http://127.0.0.1:5001/"
tell application "Google Chrome"
  set foundTab to false
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t starts with "http://127.0.0.1:5001" then
        set active tab index of w to index of t
        set index of w to 1
        set foundTab to true
        exit repeat
      end if
    end repeat
    if foundTab then exit repeat
  end repeat
  if not foundTab then
    if (count of windows) is 0 then make new window
    tell front window to make new tab with properties {URL:targetURL}
  end if
  activate
end tell
APPLESCRIPT
fi

if [[ -z "$campaign" ]]; then
  prompt='Read the D&D skill and begin the new campaign procedure. Use the local browser display. Ask me for the initial character, dice mode, ruleset, and campaign choices, then save the new campaign before narrating its first scene.'
else
  prompt="Read the D&D skill and load the existing campaign '$campaign'. Reconstruct its current state from saved files, then continue from where we stopped."
fi

args=(bash "$repo/scripts/play-local.sh" "$provider")
[[ -z "$campaign" ]] || args+=(--campaign "$campaign")
case "$provider" in
  codex) args+=(-m gpt-6.1-terra -c 'model_reasoning_effort="medium"' "$prompt") ;;
  qwen) args+=(-i "$prompt") ;;
  claude) args+=("$prompt") ;;
esac
printf -v quoted_repo '%q' "$repo"
remote_command="export PATH=/opt/homebrew/bin:\$HOME/.local/bin:\$PATH; cd $quoted_repo && "
for arg in "${args[@]}"; do printf -v quoted_arg '%q' "$arg"; remote_command+="$quoted_arg "; done
printf '\nOpening %s on the M5. Keep this window open while you play.\n\n' "$provider"
exec ssh -t "$host" "$remote_command"
