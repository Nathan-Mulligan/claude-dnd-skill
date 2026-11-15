"""
test_autosave_checkpoint.py — cadence + flag logic for the autosave Stop hook
(v2.2.0). Covers the pure `decide()` cadence function, Session-Flags parsing,
and the active-campaign marker. No Claude Code harness is exercised — only the
deterministic pieces the hook relies on.

Run from repo root:
    python3 -m unittest tests.test_autosave_checkpoint -v
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SCRIPTS = SKILL / "scripts"


def _import(name, filename):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, str(SCRIPTS / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DecideCadenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ac = _import("autosave_checkpoint", "autosave_checkpoint.py")

    def test_increments_without_blocking(self):
        turns, reason = self.ac.decide({}, 0, every_n=10)
        self.assertEqual(turns, 1)
        self.assertIsNone(reason)

    def test_blocks_and_resets_on_cadence(self):
        turns, reason = self.ac.decide({}, 9, every_n=10)
        self.assertEqual(turns, 0)
        self.assertIsNotNone(reason)
        self.assertIn("checkpoint", reason.lower())

    def test_never_blocks_when_continuation_active(self):
        # stop_hook_active means we're already inside a forced continuation.
        turns, reason = self.ac.decide({"stop_hook_active": True}, 9, every_n=10)
        self.assertEqual(turns, 9)
        self.assertIsNone(reason)

    def test_full_cycle_blocks_once_per_period(self):
        turns, blocks = 0, 0
        for _ in range(30):
            turns, reason = self.ac.decide({}, turns, every_n=10)
            if reason:
                blocks += 1
        self.assertEqual(blocks, 3)

    def test_every_zero_never_blocks(self):
        turns, reason = self.ac.decide({}, 5, every_n=0)
        self.assertEqual(turns, 6)
        self.assertIsNone(reason)


class FlagAndMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ac = _import("autosave_checkpoint", "autosave_checkpoint.py")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Preserve the outer value (pytest may already have set DND_RUNTIME_DIR
        # for isolation); tearDown restores rather than pops — popping would
        # delete the outer isolation and pollute the real data path.
        self._saved_campaign_root = os.environ.get("DND_CAMPAIGN_ROOT")
        self._saved_runtime_dir = os.environ.get("DND_RUNTIME_DIR")
        os.environ["DND_CAMPAIGN_ROOT"] = self.tmp
        os.environ["DND_RUNTIME_DIR"] = os.path.join(self.tmp, ".runtime")
        self.camp_dir = pathlib.Path(self.tmp) / "campaigns" / "test-camp"
        self.camp_dir.mkdir(parents=True)

    def tearDown(self):
        if self._saved_campaign_root is None:
            os.environ.pop("DND_CAMPAIGN_ROOT", None)
        else:
            os.environ["DND_CAMPAIGN_ROOT"] = self._saved_campaign_root
        if self._saved_runtime_dir is None:
            os.environ.pop("DND_RUNTIME_DIR", None)
        else:
            os.environ["DND_RUNTIME_DIR"] = self._saved_runtime_dir

    def _write_state(self, flags_body):
        (self.camp_dir / "state.md").write_text(
            "# Campaign: test-camp\n\n## Session Flags\n" + flags_body + "\n",
            encoding="utf-8",
        )

    def test_autosave_default_on_when_flag_absent(self):
        self._write_state("autorun: off")
        self.assertTrue(self.ac.autosave_enabled("test-camp"))

    def test_autosave_default_on_when_section_absent(self):
        (self.camp_dir / "state.md").write_text("# Campaign: test-camp\n", encoding="utf-8")
        self.assertTrue(self.ac.autosave_enabled("test-camp"))

    def test_autosave_off_respected(self):
        self._write_state("autosave: off")
        self.assertFalse(self.ac.autosave_enabled("test-camp"))

    def test_autosave_on_explicit(self):
        self._write_state("autosave: on")
        self.assertTrue(self.ac.autosave_enabled("test-camp"))

    def test_missing_state_is_disabled(self):
        self.assertFalse(self.ac.autosave_enabled("no-such-camp"))

    def test_active_campaign_marker_roundtrip(self):
        runtime = pathlib.Path(os.environ["DND_RUNTIME_DIR"])
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / self.ac.ACTIVE_MARKER).write_text(
            json.dumps({"name": "test-camp"}), encoding="utf-8"
        )
        self.assertEqual(self.ac.active_campaign(), "test-camp")

    def test_active_campaign_none_when_no_marker(self):
        self.assertIsNone(self.ac.active_campaign())

    def test_snapshot_writes_recovery_file(self):
        self._write_state("autosave: on")
        self.ac.snapshot("test-camp")
        runtime = pathlib.Path(os.environ["DND_RUNTIME_DIR"])
        self.assertTrue((runtime / "test-camp.autocheckpoint.md").exists())

    # The hook runs outside the DM's sandbox, and the DM can write both the campaign and the runtime dir (#162).

    def _outside(self, text="keep me"):
        target = pathlib.Path(self.tmp) / "outside.txt"
        target.write_text(text, encoding="utf-8")
        return target

    @unittest.skipIf(os.name == "nt", "links are POSIX-only")
    def test_snapshot_replaces_a_planted_link_instead_of_writing_through_it(self):
        self._write_state("autosave: on")
        target = self._outside()
        runtime = pathlib.Path(os.environ["DND_RUNTIME_DIR"])
        runtime.mkdir(parents=True, exist_ok=True)
        dest = runtime / "test-camp.autocheckpoint.md"
        dest.symlink_to(target)
        self.ac.snapshot("test-camp")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep me")
        self.assertFalse(dest.is_symlink())
        self.assertIn("autosave: on", dest.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "links are POSIX-only")
    def test_snapshot_never_copies_a_linked_state_md(self):
        secret = self._outside("SECRET-OUTSIDE")
        (self.camp_dir / "state.md").symlink_to(secret)
        self.ac.snapshot("test-camp")
        dest = pathlib.Path(os.environ["DND_RUNTIME_DIR"]) / "test-camp.autocheckpoint.md"
        self.assertFalse(dest.exists() and "SECRET-OUTSIDE" in dest.read_text(encoding="utf-8"))
        self.assertFalse(self.ac.autosave_enabled("test-camp"))

    @unittest.skipIf(os.name == "nt", "links are POSIX-only")
    def test_turn_counter_is_not_written_through_a_link(self):
        target = self._outside()
        counter = self.ac._counter_path("sid", "test-camp")
        counter.parent.mkdir(parents=True, exist_ok=True)
        counter.symlink_to(target)
        self.ac._save_counter(counter, 3, "test-camp")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep me")
        self.assertFalse(counter.is_symlink())
        self.assertEqual(self.ac._load_counter(counter), 3)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs on Windows")
    def test_a_fifo_state_md_never_hangs_the_hook(self):
        import subprocess
        os.mkfifo(self.camp_dir / "state.md")
        done = subprocess.run([sys.executable, str(SCRIPTS / "autosave_checkpoint.py"), "--campaign", "test-camp"],
                              capture_output=True, text=True, encoding="utf-8", timeout=20, env=os.environ.copy())
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
