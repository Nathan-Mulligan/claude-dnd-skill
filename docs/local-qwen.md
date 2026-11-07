# Local Qwen campaign option

The existing Claude plugin remains the default. To choose a DM from this checkout:

```bash
scripts/play-local.sh claude
scripts/play-local.sh qwen
# For an existing campaign, select it before play:
scripts/play-local.sh qwen --campaign <name>
```

In the Qwen session, ask to load your campaign by name (for example, `Load my D&D campaign <name>`), or invoke `/skills dnd-local`. The `--campaign` option checks that `state.md` exists, sets the active campaign for display sends, and gives Qwen its absolute path. It works with `claude` too. Both models use the same campaign data root (`~/.claude/dnd` unless `DND_CAMPAIGN_ROOT` is set). End and save the current session before switching models; do not run two DMs against one campaign simultaneously.

The Qwen option uses the locally installed `qwen3.6:35b-mlx` model through Ollama and Qwen Code. For spoken narration, start the existing `kokoro-tts` Docker container and the display, then turn on **Auto Narrate** in the browser. The display's TTS route uses Kokoro locally. Image generation is not connected. A disposable Qwen trial exercised dice, narration, campaign saves and audio generation, but the model still needs a longer live campaign trial before it can be considered equivalent to the Claude path.
