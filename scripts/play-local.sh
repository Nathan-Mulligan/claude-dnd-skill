#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

case "${1:-}" in
  claude)
    shift
    exec claude "$@"
    ;;
  qwen)
    shift
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
    exec qwen --auth-type openai \
      --openai-base-url http://127.0.0.1:11434/v1 \
      --model qwen3.6:35b-mlx "$@"
    ;;
  *)
    echo 'Usage: scripts/play-local.sh {claude|qwen} [CLI arguments]' >&2
    exit 2
    ;;
esac
