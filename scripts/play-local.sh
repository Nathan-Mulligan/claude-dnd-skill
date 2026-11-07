#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

backend="${1:-}"
shift || true

if [[ "${1:-}" == "--campaign" ]]; then
  campaign_name="${2:-}"
  if [[ ! "$campaign_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ || "$campaign_name" == "." || "$campaign_name" == ".." ]]; then
    echo 'Campaign name must use letters, numbers, dots, dashes or underscores.' >&2
    exit 2
  fi
  campaign_path="$(python3 skills/dnd/scripts/paths.py campaign-path "$campaign_name")"
  if [[ ! -f "$campaign_path/state.md" ]]; then
    echo "Campaign not found: $campaign_path/state.md" >&2
    exit 2
  fi
  export DND_ACTIVE_CAMPAIGN="$campaign_name"
  shift 2
fi

case "$backend" in
  claude)
    exec claude "$@"
    ;;
  qwen)
    if ! command -v qwen >/dev/null; then
      echo 'Qwen Code is not installed.' >&2
      exit 1
    fi
    if ! curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/version >/dev/null; then
      echo 'Local Ollama is not running.' >&2
      exit 1
    fi
    # Ollama accepts any local placeholder key. This is not an external API key.
    export OPENAI_API_KEY=ollama
    qwen_args=()
    system_notes=()
    if [[ -n "${DND_CAMPAIGN_ROOT:-}" ]]; then
      system_notes+=("Campaign data isolation: all campaign reads and writes MUST use ${DND_CAMPAIGN_ROOT}/campaigns. Never create or edit campaign files under ~/.claude/dnd while this root is set. Before saving, resolve the absolute path and verify it begins with ${DND_CAMPAIGN_ROOT}/campaigns/.")
    fi
    if [[ -n "${DND_ACTIVE_CAMPAIGN:-}" ]]; then
      campaign_path="${campaign_path:-$(python3 skills/dnd/scripts/paths.py campaign-path "$DND_ACTIVE_CAMPAIGN")}"
      system_notes+=("The active campaign is ${DND_ACTIVE_CAMPAIGN}. Its authoritative directory is ${campaign_path}. The display sender enforces this campaign before every send. Save only under that absolute directory.")
    fi
    if (( ${#system_notes[@]} > 0 )); then
      qwen_args+=(--append-system-prompt "${system_notes[*]}")
    fi
    exec qwen --auth-type openai \
      --openai-base-url http://127.0.0.1:11434/v1 \
      --model qwen3.6:35b-mlx "${qwen_args[@]}" "$@"
    ;;
  *)
    echo 'Usage: scripts/play-local.sh {claude|qwen} [--campaign NAME] [CLI arguments]' >&2
    exit 2
    ;;
esac
