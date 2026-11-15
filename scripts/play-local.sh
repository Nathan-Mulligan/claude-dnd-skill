#!/usr/bin/env bash
set -euo pipefail
# tmux names the server the DM runs on in these; the DM must not find it (#161).
unset TMUX TMUX_PANE

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

data_root="${DND_CAMPAIGN_ROOT:-$HOME/.claude/dnd}"
# The display's virtualenv lives outside the data root, where a sandboxed DM cannot change it.
# The one inside the data root is the old place.
for venv in "${DND_VENV:-}" "$HOME/.local/share/dnd/venv" "$data_root/.venv"; do
  if [[ -n "$venv" && -x "$venv/bin/python3" ]]; then export PATH="$venv/bin:$PATH"; break; fi
done

backend="${1:-}"
shift || true

workdir="$repo_root"
while [[ "${1:-}" == "--campaign" || "${1:-}" == "--workdir" ]]; do
  if [[ "$1" == "--workdir" ]]; then
    workdir="${2:-}"
    [[ -d "$workdir" ]] || { echo "Working folder not found: $workdir" >&2; exit 2; }
    shift 2
    continue
  fi
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
done

# The display serves only the campaign the owner chose (#139). The portal records its own choice;
# started by hand, this launcher records the campaign it was given, or that none was.
if [[ "${DND_PORTAL_LAUNCH:-}" != 1 ]]; then
  python3 skills/dnd/scripts/paths.py choose-campaign ${DND_ACTIVE_CAMPAIGN:+"$DND_ACTIVE_CAMPAIGN"}
fi

case "$backend" in
  codex)
    if ! command -v codex >/dev/null; then
      echo 'Codex CLI is not installed.' >&2
      exit 1
    fi
    cd "$workdir"
    exec codex "$@"
    ;;
  claude)
    cd "$workdir"
    exec claude --plugin-dir "$repo_root" "$@"
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
    echo 'Usage: scripts/play-local.sh {codex|claude|qwen} [--campaign NAME] [--workdir DIR] [CLI arguments]' >&2
    exit 2
    ;;
esac
