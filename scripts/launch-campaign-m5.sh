#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
data_root="${DND_CAMPAIGN_ROOT:-$HOME/.claude/dnd}"

ready() { curl --silent --fail --max-time 3 "$1" >/dev/null; }
display_status() {
  local health
  health="$(curl --silent --fail --max-time 3 http://127.0.0.1:5001/health)" || return 1
  python3 -c '
import json, os, sys
try:
    data = json.load(sys.stdin)
    if data.get("alive") is not True or not all(k in data for k in ("campaign", "tail_buffer", "tail_path")):
        sys.exit(1)
    actual = os.path.realpath(data["tail_path"])
    expected = os.path.realpath(os.path.join(sys.argv[1], "campaigns"))
    sys.exit(0 if os.path.commonpath((actual, expected)) == expected else 2)
except (ValueError, TypeError, OSError, KeyError):
    sys.exit(1)
' "$data_root" <<< "$health"
}

case "${1:-}" in
  --list)
    if [[ -d "$data_root/campaigns" ]]; then
      for state in "$data_root"/campaigns/*/state.md; do
        [[ -f "$state" ]] || continue
        name="$(basename "$(dirname "$state")")"
        [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && printf '%s\n' "$name"
      done
    fi
    ;;
  --exists)
    name="${2:-}"
    [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$name" != '.' && "$name" != '..' ]] || exit 2
    [[ -f "$data_root/campaigns/$name/state.md" ]]
    ;;
  --claude-auth)
    command -v claude >/dev/null || { echo 'Claude Code is not installed on the M5.' >&2; exit 1; }
    claude auth status | grep -Eq '"loggedIn"[[:space:]]*:[[:space:]]*true' || { echo 'Claude Code is signed out on the M5. Sign in there or choose Qwen/Codex.' >&2; exit 1; }
    ;;
  --check|--start)
    mode="$1"
    provider="${2:-}"
    [[ "$provider" == qwen || "$provider" == codex || "$provider" == claude ]] || { echo 'Unknown model choice.' >&2; exit 2; }
    display_code=0
    display_status || display_code=$?
    if [[ "$display_code" == 2 ]]; then
      if [[ "$mode" == --check ]]; then
        echo 'Display: attached to another campaign data root'
      else
        echo 'Display is attached to another campaign data root. Stop that display before launching this campaign.' >&2
        exit 1
      fi
    elif [[ "$display_code" != 0 ]]; then
      if [[ "$mode" == --check ]]; then
        echo 'Display: stopped'
      else
        if lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null 2>&1; then
          echo 'Display port 5001 is occupied by another service.' >&2; exit 1
        fi
        DND_NO_OPEN=1 bash "$repo_root/skills/dnd/display/start-display.sh" >/dev/null
      fi
    elif [[ "$mode" == --check ]]; then echo 'Display: ready'; fi
    if ! ready http://127.0.0.1:8880/v1/audio/voices; then
      if [[ "$mode" == --check ]]; then
        echo 'Kokoro: stopped'
      else
        command -v docker >/dev/null || { echo 'Docker is needed for Kokoro.' >&2; exit 1; }
        docker start kokoro-tts >/dev/null || { echo 'Kokoro container is missing or could not start.' >&2; exit 1; }
      fi
    elif [[ "$mode" == --check ]]; then echo 'Kokoro: ready'; fi
    if [[ "$provider" == qwen ]] && ! ready http://127.0.0.1:11434/api/version; then
      if [[ "$mode" == --check ]]; then
        echo 'Ollama: stopped'
      else
        command -v ollama >/dev/null || { echo 'Ollama is not installed.' >&2; exit 1; }
        mkdir -p "$data_root"
        nohup ollama serve > "$data_root/ollama-launch.log" 2>&1 < /dev/null &
      fi
    elif [[ "$provider" == qwen && "$mode" == --check ]]; then echo 'Ollama: ready'; fi
    if [[ "$mode" == --start ]]; then
      command -v "$provider" >/dev/null || { echo "$provider CLI is not installed." >&2; exit 1; }
      for attempt in {1..20}; do
        display_status && ready http://127.0.0.1:8880/v1/audio/voices && break
        sleep 1
      done
      display_status || { echo 'Display did not become ready with this campaign data root.' >&2; exit 1; }
      ready http://127.0.0.1:8880/v1/audio/voices || { echo 'Kokoro did not become ready.' >&2; exit 1; }
      if [[ "$provider" == qwen ]]; then
        for attempt in {1..20}; do
          ready http://127.0.0.1:11434/api/version && break
          sleep 1
        done
        ready http://127.0.0.1:11434/api/version || { echo 'Ollama did not become ready.' >&2; exit 1; }
        ollama list | grep -Fq 'qwen3.6:35b-mlx' || { echo 'Qwen 35B MLX is not installed in Ollama.' >&2; exit 1; }
      fi
      if [[ -x "$repo_root/scripts/start-campaign-extras.sh" ]]; then
        "$repo_root/scripts/start-campaign-extras.sh"
      fi
    fi
    if [[ "$mode" == --start ]]; then
      printf 'Display: ready\nKokoro: ready\n'
      [[ "$provider" != qwen ]] || printf 'Ollama/Qwen: ready\n'
    fi
    ;;
  *) echo 'Usage: launch-campaign-m5.sh {--list|--exists|--claude-auth|--check|--start}' >&2; exit 2 ;;
esac
