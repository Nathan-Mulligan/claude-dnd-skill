import contextlib
import importlib.util
import io
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "skills" / "dnd" / "scripts" / "dice.py"
SPEC = importlib.util.spec_from_file_location("dnd_dice", SOURCE)
dice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dice)


def test_generic_d20_label_does_not_claim_attack_outcome(monkeypatch):
    monkeypatch.setattr(dice, "roll_dice", lambda count, sides: [20])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert dice.run("d20+3", force_local=True) == 23
    assert "NATURAL 20" in output.getvalue()
    assert "CRITICAL HIT" not in output.getvalue()


def test_physical_d20_label_does_not_claim_attack_outcome():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        dice._print_physical({"rolls": [1], "kept": [1], "total": 4}, 1, 20, 3, None, None, False, False, False)
    assert "NATURAL 1" in output.getvalue()
    assert "FUMBLE" not in output.getvalue()
