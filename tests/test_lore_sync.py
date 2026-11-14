"""Background faction/quest notes: secrets stay out, notes survive DM pushes."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills/dnd/display"))
spec = importlib.util.spec_from_file_location("lore_sync", REPO / "skills/dnd/display/lore_sync.py")
lore_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lore_sync)

STATE = """# Campaign: Test
## Current Situation
- Party in town.
## World State
- **Faction states:** Road Wardens keep the toll road.
## Active Quests
- Find a return cargo.
## Open Threads & Rumours
- The toll doubled; nobody knows why.
## Next Hooks
- SECRET-HOOK: a courier carries the auditor's notice.
## Faction Moves
- SECRET-MOVE: the Wardens sent an auditor.
## Clocks
- SECRET-CLOCK: bridge closes on the 21st.
## Recent Events
- Sold the flour.
## Campaign Arc
SECRET-ARC
"""


def test_public_text_keeps_party_sections_and_drops_dm_secrets():
    text = lore_sync.public_text(STATE)
    for kept in ("Find a return cargo", "toll doubled", "Sold the flour"):
        assert kept in text
    assert "SECRET" not in text
    assert "Road Wardens keep" not in text  # World State can hold hidden faction activity
    assert "Party in town" not in text


def test_prompt_never_contains_dm_sections():
    prompt = lore_sync.build_prompt(lore_sync.public_text(STATE),
                                    [{"name": "Road Wardens", "standing": "Neutral"}],
                                    [{"name": "Find cargo", "status": "active"}])
    assert "SECRET" not in prompt
    assert "Road Wardens (Neutral)" in prompt and "Find cargo (active)" in prompt


def test_fingerprint_ignores_details_but_tracks_status_and_notes():
    f = [{"name": "A", "standing": "Neutral"}]
    base = lore_sync.fingerprint("x", f, [])
    assert lore_sync.fingerprint("x", [{"name": "A", "standing": "Neutral", "detail": "d"}], []) == base
    assert lore_sync.fingerprint("x", [{"name": "A", "standing": "Hostile"}], []) != base
    assert lore_sync.fingerprint("y", f, []) != base


def test_apply_cached_fills_missing_detail_but_dm_detail_wins():
    cache = {"campaign": "Test", "factions": {"A": "saved"}, "quests": {"Q": "q-saved"}}
    payload = {"factions": [{"name": "A", "standing": "Neutral"}, {"name": "B", "standing": "Neutral"}],
               "quests": [{"name": "Q", "status": "active", "detail": "from DM"}]}
    lore_sync.apply_cached(payload, "Test", cache)
    assert payload["factions"][0]["detail"] == "saved"
    assert "detail" not in payload["factions"][1]
    assert payload["quests"][0]["detail"] == "from DM"


def test_clean_notes_drops_unknown_names_blanks_and_trims():
    result = [{"name": "A", "detail": "  ok  text "}, {"name": "Ghost", "detail": "invented"},
              {"name": "B", "detail": ""}, {"name": "C", "detail": "x" * 999}]
    notes = lore_sync.clean_notes(result, ["A", "B", "C"])
    assert notes["A"] == "ok text"
    assert "Ghost" not in notes and "B" not in notes
    assert len(notes["C"]) == lore_sync.DETAIL_MAX


def test_clean_notes_accepts_name_with_copied_status():
    notes = lore_sync.clean_notes([{"name": "Copper Guild (Neutral)", "detail": "Ferries."},
                                   {"name": "Other (x)", "detail": "Dropped."}], ["Copper Guild", "Other (x)y"])
    assert notes == {"Copper Guild": "Ferries."}


def test_kick_is_off_when_disabled(monkeypatch):
    called = []
    monkeypatch.setenv("DND_LORE_SYNC", "0")
    monkeypatch.setattr(lore_sync.subprocess, "Popen", lambda *a, **k: called.append(a))
    lore_sync.kick()
    assert called == []


def test_apply_cached_needs_matching_known_campaign():
    cache = {"campaign": "A", "factions": {"Guild": "A's secret-ish note"}}
    for campaign in (None, "B"):
        payload = {"factions": [{"name": "Guild", "standing": "Neutral"}]}
        lore_sync.apply_cached(payload, campaign, cache)
        assert "detail" not in payload["factions"][0]


class Live:
    """Fake display + model around a temp runtime dir."""

    def __init__(self, monkeypatch, tmp_path, stats=None):
        camp = tmp_path / "campaigns" / "Test"
        camp.mkdir(parents=True)
        (camp / "state.md").write_text(STATE, encoding="utf-8")
        self.tmp, self.posts, self.calls = tmp_path, [], []
        self.campaign, self.fail_post, self.on_model, self.old_server = "Test", False, None, False
        monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
        self.write_stats(stats or {"factions": [{"name": "Road Wardens", "standing": "Neutral"}], "quests": []})
        monkeypatch.setattr(lore_sync, "_request", self.request)
        monkeypatch.setattr(lore_sync, "ask_model", self.model)
        self.choice = ("fake", "fake-model")
        monkeypatch.setattr(lore_sync, "choose_model", lambda: self.choice)
        monkeypatch.setattr(lore_sync.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start a process"))
        monkeypatch.setattr(lore_sync.time, "sleep", lambda s: None)

    def write_stats(self, stats):
        (self.tmp / "stats.json").write_text(json.dumps(stats) if isinstance(stats, dict) else stats, encoding="utf-8")

    def request(self, path, data=None, timeout=5):
        if path == "/health":
            return {"campaign": self.campaign, "data_root": str(self.tmp)}
        if self.fail_post:
            raise OSError("display down")
        if path == "/stats/lore" and self.old_server:
            raise lore_sync.urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        self.posts.append((path, json.loads(data)))
        return {}

    def model(self, prompt, choice=None):
        assert choice == self.choice
        self.calls.append(prompt)
        if self.on_model:
            self.on_model()
        return {"factions": [{"name": "Road Wardens", "detail": "Keep the toll road."}], "quests": []}


def test_run_skips_model_when_nothing_changed(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    assert lore_sync.run(settle_s=0) == "updated"
    path, body = live.posts[-1]
    assert path == "/stats/lore"  # notes only, never whole lists
    assert body["factions"] == {"Road Wardens": "Keep the toll road."} and body["campaign"] == "Test"
    assert lore_sync.run(settle_s=0) == "unchanged"
    assert len(live.calls) == 1 and "SECRET" not in live.calls[0]


def test_unreadable_stats_never_publishes_empty_lists(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.on_model = lambda: live.write_stats('{"factions": [')  # display mid-write
    lore_sync.run(settle_s=0)
    assert all(path == "/stats/lore" for path, _ in live.posts)  # never a list-replacing post


def test_failed_post_is_retried_next_time_without_new_model_call(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.fail_post = True
    try:
        lore_sync.run(settle_s=0)
    except OSError:
        pass
    assert lore_sync.load_cache("Test")["published"] is False
    live.fail_post = False
    assert lore_sync.run(settle_s=0) == "updated"
    assert len(live.calls) == 1  # reused the saved notes
    assert lore_sync.load_cache("Test")["published"] is True
    assert lore_sync.run(settle_s=0) == "unchanged"


def test_notes_are_saved_before_publishing(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    seen = []
    real = live.request

    def request(path, data=None, timeout=5):
        if path == "/stats/lore":  # a DM push landing now must already refill the new notes
            seen.append(lore_sync.load_cache("Test").get("factions"))
        return real(path, data, timeout)
    monkeypatch.setattr(lore_sync, "_request", request)
    lore_sync.run(settle_s=0)
    assert seen == [{"Road Wardens": "Keep the toll road."}]


def test_every_earlier_helper_note_is_sent_as_previous(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    lore_sync.run(settle_s=0)
    live.write_stats({"factions": [{"name": "Road Wardens", "standing": "Hostile"}], "quests": []})
    lore_sync.run(settle_s=0)
    _, body = live.posts[-1]
    assert body["previous"]["factions"]["Road Wardens"] == ["Keep the toll road."]


def test_campaign_switch_during_generation_blocks_publish(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.on_model = lambda: setattr(live, "campaign", "Other")
    assert lore_sync.run(settle_s=0) == "campaign-changed"
    assert live.posts == []


def test_push_while_busy_is_replayed(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)

    def change_during_model():
        if len(live.calls) == 1:
            live.write_stats({"factions": [{"name": "Road Wardens", "standing": "Hostile"}], "quests": []})
            assert lore_sync.run(settle_s=0) == "busy"  # second kick while the first holds the lock
    live.on_model = change_during_model
    assert lore_sync.run(settle_s=0) == "updated,updated"
    assert len(live.calls) == 2 and "Road Wardens (Hostile)" in live.calls[1]


def test_old_display_never_gets_whole_lists_reposted(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.old_server = True
    assert lore_sync.run(settle_s=0) == "display-too-old"
    assert live.posts == []


def test_pending_work_at_pass_limit_is_rescheduled(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    kicks = []
    monkeypatch.setattr(lore_sync, "kick", lambda: kicks.append(1))

    def push_every_pass():
        n = len(live.calls)
        live.write_stats({"factions": [{"name": "Road Wardens", "standing": "Hostile" if n % 2 else "Neutral"}], "quests": []})
        (tmp_path / "lore_sync.pending").touch()
    live.on_model = push_every_pass
    lore_sync.run(settle_s=0, max_passes=2)
    assert kicks == [1]


def test_helper_off_skips_model_and_keeps_cache(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.choice = None
    assert lore_sync.run(settle_s=0) == "off"
    assert live.calls == [] and live.posts == []
    assert not (tmp_path / "lore_notes.json").exists()  # turning it back on still runs


# ── helper choice and providers ──────────────────────────────────────────────

@pytest.fixture
def helper_env(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    monkeypatch.delenv("DND_LORE_SYNC", raising=False)
    monkeypatch.delenv("DND_LORE_MODEL", raising=False)
    return tmp_path


def test_settings_default_save_and_read(helper_env):
    assert lore_sync.saved_helper() == "luna"
    assert lore_sync.choose_model() == ("codex", "gpt-6-luna")
    for key, expected in (("terra", ("codex", "gpt-6.1-terra")), ("haiku", ("claude", "haiku")),
                          ("qwen", ("ollama", "qwen3.6:35b-mlx")), ("off", None)):
        lore_sync.save_helper(key)
        assert json.loads((helper_env / "helper-settings.json").read_text(encoding="utf-8")) == {"helper": key}
        assert lore_sync.choose_model() == expected
    with pytest.raises(ValueError):
        lore_sync.save_helper("gpt-9")


@pytest.mark.parametrize("content", ["not json", "[]", '{"helper": "nonsense"}', '{"helper": 3}'])
def test_unreadable_settings_fall_back_to_default(helper_env, content):
    (helper_env / "helper-settings.json").write_text(content, encoding="utf-8")
    assert lore_sync.saved_helper() == "luna"


def test_env_overrides_win_over_saved_choice(helper_env, monkeypatch):
    lore_sync.save_helper("haiku")
    monkeypatch.setenv("DND_LORE_MODEL", "qwen")
    assert lore_sync.choose_model() == ("ollama", "qwen3.6:35b-mlx")
    monkeypatch.setenv("DND_LORE_MODEL", "gpt-6-astra")  # a raw Codex model name, as before
    assert lore_sync.choose_model() == ("codex", "gpt-6-astra")
    lore_sync.save_helper("off")
    assert lore_sync.choose_model() == ("codex", "gpt-6-astra")
    monkeypatch.setenv("DND_LORE_MODEL", "off")
    assert lore_sync.choose_model() is None
    monkeypatch.setenv("DND_LORE_MODEL", "terra")
    monkeypatch.setenv("DND_LORE_SYNC", "0")  # always wins
    assert lore_sync.choose_model() is None


def test_kick_respects_saved_off(helper_env, monkeypatch):
    called = []
    monkeypatch.setattr(lore_sync.subprocess, "Popen", lambda *a, **k: called.append(a))
    lore_sync.save_helper("off")
    lore_sync.kick()
    assert called == []
    lore_sync.save_helper("qwen")
    lore_sync.kick()
    assert len(called) == 1


GOOD = {"factions": [{"name": "Guild", "detail": "Traders."}], "quests": []}


@pytest.mark.parametrize("provider", ["codex", "claude", "ollama"])
def test_ask_model_routes_to_chosen_provider(monkeypatch, provider):
    seen = []
    for name in ("codex", "claude", "ollama"):
        monkeypatch.setitem(lore_sync._PROVIDERS, name,
                            lambda prompt, model, name=name: seen.append((name, model)) or GOOD)
    assert lore_sync.ask_model("p", (provider, "m")) == GOOD
    assert seen == [(provider, "m")]


@pytest.mark.parametrize("reply", [
    None, [], "text", {"factions": []}, {"factions": [], "quests": [], "extra": 1},
    {"factions": {}, "quests": []}, {"factions": ["Guild"], "quests": []},
    {"factions": [{"name": "Guild"}], "quests": []},
    {"factions": [{"name": "Guild", "detail": 5}], "quests": []},
    {"factions": [{"name": "Guild", "detail": "x", "secret": "y"}], "quests": []},
])
def test_malformed_reply_is_a_failed_run(monkeypatch, reply):
    monkeypatch.setitem(lore_sync._PROVIDERS, "fake", lambda prompt, model: reply)
    with pytest.raises(ValueError):
        lore_sync.ask_model("p", ("fake", "m"))


def test_malformed_reply_publishes_nothing(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    monkeypatch.setattr(lore_sync, "ask_model", lambda prompt, choice=None: lore_sync.validate({"factions": "x"}))
    with pytest.raises(ValueError):
        lore_sync.run(settle_s=0)
    assert live.posts == [] and not (tmp_path / "lore_notes.json").exists()


def test_claude_call_has_no_tools_and_no_api_key(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return lore_sync.subprocess.CompletedProcess(cmd, 0, json.dumps({"is_error": False, "structured_output": GOOD}), "")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(lore_sync.subprocess, "run", fake_run)
    assert lore_sync.ask_model("the prompt", ("claude", "haiku")) == GOOD
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "ANTHROPIC_API_KEY" not in seen["env"] and seen["input"] == "the prompt"


def test_claude_error_reply_fails(monkeypatch):
    monkeypatch.setattr(lore_sync.subprocess, "run", lambda cmd, **kw: lore_sync.subprocess.CompletedProcess(
        cmd, 0, json.dumps({"is_error": True, "result": "Not logged in"}), ""))
    with pytest.raises(ValueError):
        lore_sync.ask_model("p", ("claude", "haiku"))


def test_ollama_call_sends_schema_and_parses_content(monkeypatch):
    import io
    seen = {}

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, body=json.loads(req.data), timeout=timeout)
        return io.BytesIO(json.dumps({"message": {"content": json.dumps(GOOD)}}).encode())

    monkeypatch.setattr(lore_sync.urllib.request, "urlopen", fake_urlopen)
    assert lore_sync.ask_model("p", ("ollama", "qwen3.6:35b-mlx")) == GOOD
    assert seen["url"] == "http://127.0.0.1:11434/api/chat" and seen["timeout"] == lore_sync.MODEL_TIMEOUT_S
    assert seen["body"]["format"] == lore_sync._SCHEMA and seen["body"]["stream"] is False
    assert seen["body"]["model"] == "qwen3.6:35b-mlx"


def test_ollama_non_json_content_fails(monkeypatch):
    import io
    monkeypatch.setattr(lore_sync.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(b'{"message": {"content": "Sure! Here are notes"}}'))
    with pytest.raises(ValueError):
        lore_sync.ask_model("p", ("ollama", "qwen3.6:35b-mlx"))


# ── display endpoint ─────────────────────────────────────────────────────────

DISPLAY = REPO / "skills/dnd/display"


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    monkeypatch.syspath_prepend(str(DISPLAY))
    spec = importlib.util.spec_from_file_location("_lore_display_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    monkeypatch.setattr(module, "_broadcast", lambda payload: None)
    monkeypatch.setattr(module, "_token_ok", lambda: True)
    monkeypatch.setattr(module, "_active_campaign_name", lambda: "Test")
    module._stats_owner = "Test"
    module._current_stats["factions"] = [
        {"name": "Guild", "standing": "Hostile"},                       # DM changed it meanwhile
        {"name": "Wardens", "standing": "Neutral", "detail": "old helper note"},
        {"name": "Court", "standing": "Allied", "detail": "written by the DM"},
    ]
    module._current_stats["quests"] = [{"name": "New quest", "status": "active"}]
    return module, module.app.test_client()


def test_lore_endpoint_sets_notes_without_replacing_lists(display):
    module, client = display
    r = client.post("/stats/lore", json={
        "campaign": "Test",
        "factions": {"Guild": "Guild note", "Court": "helper tries to overwrite"},
        "quests": {},
        "previous": {"factions": {"Wardens": "old helper note"}},
    })
    assert r.status_code == 204
    f = {x["name"]: x for x in module._current_stats["factions"]}
    assert f["Guild"] == {"name": "Guild", "standing": "Hostile", "detail": "Guild note"}
    assert "detail" not in f["Wardens"]            # stale helper note removed
    assert f["Court"]["detail"] == "written by the DM"  # DM note untouched
    assert module._current_stats["quests"] == [{"name": "New quest", "status": "active"}]


def test_lore_endpoint_refuses_other_campaign(display):
    module, client = display
    r = client.post("/stats/lore", json={"campaign": "Other", "factions": {"Guild": "x"}})
    assert r.status_code == 409
    assert "detail" not in module._current_stats["factions"][0]


def test_lore_endpoint_refuses_when_stats_belong_to_another_campaign(display):
    module, client = display
    module._stats_owner = "Other"  # switch in progress: stats already swapped
    r = client.post("/stats/lore", json={"campaign": "Test", "factions": {"Guild": "x"}})
    assert r.status_code == 409
    assert "detail" not in module._current_stats["factions"][0]


def test_cache_keeps_each_campaign_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    lore_sync.save_cache({"campaign": "A", "fingerprint": "a", "factions": {"Guild": "A note"}, "quests": {}})
    lore_sync.save_cache({"campaign": "B", "fingerprint": "b", "factions": {"Guild": "B note"}, "quests": {}})
    assert lore_sync.load_cache("A")["factions"] == {"Guild": "A note"}
    assert lore_sync.load_cache("B")["factions"] == {"Guild": "B note"}
    assert lore_sync.load_cache(None) == {}


def test_old_single_campaign_cache_still_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    (tmp_path / "lore_notes.json").write_text(json.dumps(
        {"campaign": "Enderal", "fingerprint": "f", "factions": {"Road Wardens": "n"}, "quests": {}}), encoding="utf-8")
    assert lore_sync.load_cache("Enderal")["factions"] == {"Road Wardens": "n"}


def test_lore_endpoint_accepts_a_list_of_earlier_helper_notes(display):
    module, client = display
    r = client.post("/stats/lore", json={"campaign": "Test", "factions": {"Wardens": "new"},
                                         "previous": {"factions": {"Wardens": ["older", "old helper note"]}}})
    assert r.status_code == 204
    f = {x["name"]: x for x in module._current_stats["factions"]}
    assert f["Wardens"]["detail"] == "new"


# ── codex binary and failure log ─────────────────────────────────────────────

LUNA_REJECTED = ('ERROR: {"type":"error","status":400,"error":{"message":'
                 '"The \'gpt-6-luna\' model is not supported when using Codex with a ChatGPT account."}}')


def fake_cli(returncode=0, stderr="", reply=GOOD, seen=None):
    """Behaves like subprocess.run: stderr reaches the caller only when captured."""
    def run(cmd, **kw):
        if seen is not None:
            seen.update(cmd=cmd, **kw)
        if "-o" in cmd and reply is not None:
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(reply), encoding="utf-8")
        captured = kw.get("capture_output") or kw.get("stderr") == lore_sync.subprocess.PIPE
        err = (stderr if kw.get("text") else stderr.encode("utf-8")) if captured else None
        out = ("" if kw.get("text") else b"") if captured else None
        if kw.get("check") and returncode:
            raise lore_sync.subprocess.CalledProcessError(returncode, cmd, out, err)
        return lore_sync.subprocess.CompletedProcess(cmd, returncode, out, err)
    return run


def home_with_codex(monkeypatch, tmp_path, executable=True):
    local = tmp_path / ".local" / "bin" / "codex"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n", encoding="utf-8")
    local.chmod(0o755 if executable else 0o644)
    monkeypatch.setattr(lore_sync.Path, "home", lambda: tmp_path)
    return local


def test_codex_uses_the_local_bin_copy_when_present(monkeypatch, tmp_path):
    local = home_with_codex(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(lore_sync.subprocess, "run", fake_cli(seen=seen))
    assert lore_sync.ask_model("p", ("codex", "gpt-6-luna")) == GOOD
    assert seen["cmd"][0] == str(local)


def test_codex_falls_back_to_path_without_a_local_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync.Path, "home", lambda: tmp_path)
    seen = {}
    monkeypatch.setattr(lore_sync.subprocess, "run", fake_cli(seen=seen))
    assert lore_sync.ask_model("p", ("codex", "gpt-6-luna")) == GOOD
    assert seen["cmd"][0] == "codex"


@pytest.mark.skipif(sys.platform == "win32", reason="no execute bit on Windows")
def test_codex_ignores_a_local_copy_that_cannot_run(monkeypatch, tmp_path):
    home_with_codex(monkeypatch, tmp_path, executable=False)
    seen = {}
    monkeypatch.setattr(lore_sync.subprocess, "run", fake_cli(seen=seen))
    lore_sync.ask_model("p", ("codex", "gpt-6-luna"))
    assert seen["cmd"][0] == "codex"


def test_failed_codex_call_logs_the_reason_not_the_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync.Path, "home", lambda: tmp_path)
    prompt = "SECRET-PROMPT line one\nSECRET-PROMPT line two"
    stderr = (f"OpenAI Codex v0.140.0-alpha.19\n--------\nmodel: gpt-6-luna\n--------\nuser\n{prompt}\n"
              f"warning: Model metadata for `gpt-6-luna` not found.\n{LUNA_REJECTED}\n{LUNA_REJECTED}\n")
    monkeypatch.setattr(lore_sync.subprocess, "run", fake_cli(returncode=1, stderr=stderr, reply=None))
    with pytest.raises(Exception) as caught:
        lore_sync.ask_model(prompt, ("codex", "gpt-6-luna"))
    reason = lore_sync.failure_reason(caught.value)
    assert "is not supported when using Codex" in reason and "exited 1" in reason
    assert "SECRET-PROMPT" not in reason and "--output-schema" not in reason
    assert reason.count("is not supported") == 1 and len(reason) <= 400


def test_codex_prompt_lines_never_reach_the_log_even_at_the_end(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync.Path, "home", lambda: tmp_path)
    prompt = "SECRET-PROMPT notes\nSECRET-PROMPT last line"
    monkeypatch.setattr(lore_sync.subprocess, "run",
                        fake_cli(returncode=1, stderr=f"user\n{prompt}\n", reply=None))
    with pytest.raises(Exception) as caught:
        lore_sync.ask_model(prompt, ("codex", "gpt-6-luna"))
    reason = lore_sync.failure_reason(caught.value)
    assert "SECRET-PROMPT" not in reason and "exited 1" in reason


def test_failed_claude_call_log_line_is_short(monkeypatch):
    monkeypatch.setattr(lore_sync.subprocess, "run", fake_cli(returncode=1, stderr="Not logged in\n"))
    with pytest.raises(Exception) as caught:
        lore_sync.ask_model("SECRET-PROMPT", ("claude", "haiku"))
    reason = lore_sync.failure_reason(caught.value)
    assert "Not logged in" in reason and "--system-prompt" not in reason and "SECRET-PROMPT" not in reason


def test_long_warning_never_crowds_out_the_error(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync.Path, "home", lambda: tmp_path)
    warning = "warning: Skill descriptions were shortened to fit the context budget. " * 3
    monkeypatch.setattr(lore_sync.subprocess, "run",
                        fake_cli(returncode=1, stderr=f"{warning}\n{LUNA_REJECTED}\n", reply=None))
    with pytest.raises(Exception) as caught:
        lore_sync.ask_model("p", ("codex", "gpt-6-luna"))
    reason = lore_sync.failure_reason(caught.value)
    assert reason.endswith(LUNA_REJECTED) and len(reason) <= 400


def test_timeout_log_line_is_short():
    e = lore_sync.subprocess.TimeoutExpired(["codex", "exec", "SECRET-PROMPT " * 500], 180)
    reason = lore_sync.failure_reason(e)
    assert "180" in reason and "SECRET-PROMPT" not in reason and len(reason) <= 400


def test_other_failures_are_cut_short():
    reason = lore_sync.failure_reason(ValueError("x" * 5000))
    assert reason.startswith("ValueError: ") and len(reason) <= 400
