# Local Qwen campaign option

The existing Claude plugin remains the default. To choose a DM from this checkout:

```bash
scripts/play-local.sh claude
scripts/play-local.sh qwen
```

In the Qwen session, ask to load your campaign by name (for example, `Load my D&D campaign <name>`), or invoke `/skills dnd-local`. Both models use the same campaign data root (`~/.claude/dnd` unless `DND_CAMPAIGN_ROOT` is set). End and save the current session before switching models; do not run two DMs against one campaign simultaneously.

The Qwen option uses the locally installed `qwen3.6:35b-mlx` model through Ollama and Qwen Code. The display's existing TTS setting handles Kokoro. Image generation is not connected. Qwen-specific gameplay still needs a live campaign trial before it can be considered equivalent to the Claude path.
