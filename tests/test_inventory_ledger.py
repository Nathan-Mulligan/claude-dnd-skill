"""inventory.py owns items and coins; the sheet section and the display follow it.

The DM miscounted coins and rewrote inventory prose by hand. These tests hold
the script to the things that went wrong: the coin maths (including change
across denominations), refusing what cannot happen without touching the files,
and reading the two real Enderal sheets into a ledger without inventing items
or keeping trade history as inventory.
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/dnd/scripts"
DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
FIXTURES = Path(__file__).resolve().parent / "fixtures/enderal_sheets"
sys.path.insert(0, str(SCRIPTS))
import inventory as inv  # noqa: E402
import sheet_extract  # noqa: E402


@pytest.fixture
def enderal(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.delenv("DND_RUNTIME_DIR", raising=False)
    root = tmp_path / "campaigns" / "Enderal"
    (root / "characters").mkdir(parents=True)
    (root / "state.md").write_text("# Enderal\n", encoding="utf-8")
    for sheet in FIXTURES.glob("*.md"):
        shutil.copy(sheet, root / "characters" / sheet.name)
    return root


def cli(*args) -> tuple[int, str, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = inv.main([args[0], "--campaign", "Enderal", "--no-push", *args[1:]])
    return code, out.getvalue().strip(), err.getvalue().strip()


def migrated(enderal):
    for who in ("elbane-iii", "nella-reed"):
        assert cli("migrate", "--character", who, "--write")[0] == 0
    return enderal


def ledger(root, stem):
    return json.loads((root / "characters" / f"{stem}.inventory.json").read_text(encoding="utf-8"))


# ── coin maths ───────────────────────────────────────────────────────────────

def test_parse_and_format_coins():
    assert inv.parse_coins("32 gp 8 sp 7 cp") == {"pp": 0, "gp": 32, "ep": 0, "sp": 8, "cp": 7}
    assert inv.parse_coins("26sp")["sp"] == 26
    assert inv.parse_coins("1,000 gp and 5 sp")["gp"] == 1000
    assert inv.format_coins({"pp": 0, "gp": 3, "ep": 0, "sp": 0, "cp": 2}) == "3 gp, 2 cp"
    assert inv.format_coins(inv.empty_purse()) == "0 gp"
    for bad in ("", "banana", "5 gold", "3 gp of salt", "0 gp"):
        with pytest.raises(inv.LedgerError):
            inv.parse_coins(bad)


def purse(**coins):
    return {**inv.empty_purse(), **coins}


def test_exact_coins_are_used_before_breaking_a_larger_coin():
    new, paid, change = inv.pay(purse(gp=2, sp=5), purse(sp=3))
    assert new == purse(gp=2, sp=2) and paid == purse(sp=3) and change == inv.empty_purse()


def test_change_is_made_across_denominations():
    # 32 gp 3 sp 7 cp, pay 43 cp: hands over the copper, the silver and one gold piece.
    new, paid, change = inv.pay(purse(gp=32, sp=3, cp=7), purse(cp=43))
    assert inv.value(new) == inv.value(purse(gp=32, sp=3, cp=7)) - 43
    assert new == purse(gp=31, sp=9, cp=4)
    assert paid == purse(gp=1, cp=3) and change == purse(sp=6)


def test_breaking_gold_for_silver_gives_silver_change():
    new, paid, change = inv.pay(purse(gp=109), purse(sp=3))
    assert new == purse(gp=108, sp=7) and paid == purse(gp=1) and change == purse(sp=7)


def test_platinum_and_electrum_are_spent_but_change_comes_in_common_coin():
    new, _, change = inv.pay(purse(pp=1, ep=1), purse(gp=6))
    assert inv.value(new) == 1050 - 600
    assert new["ep"] == 0 and new["pp"] == 0 and change == purse(gp=4, sp=5)


def test_overspending_is_refused_and_says_by_how_much():
    with pytest.raises(inv.LedgerError, match="short by 91 gp"):
        inv.pay(purse(gp=109), purse(gp=200))


def test_payment_always_conserves_value_and_never_goes_negative():
    rng = random.Random(7)
    for _ in range(2000):
        have = {d: rng.randint(0, 12) for d in inv.DENOMS}
        price = {d: rng.randint(0, 6) if rng.random() < 0.5 else 0 for d in inv.DENOMS}
        if inv.value(price) == 0:
            continue
        if inv.value(price) > inv.value(have):
            with pytest.raises(inv.LedgerError):
                inv.pay(have, price)
            continue
        new, paid, change = inv.pay(have, price)
        assert inv.value(new) == inv.value(have) - inv.value(price)
        assert all(new[d] >= 0 and paid[d] >= 0 and change[d] >= 0 for d in inv.DENOMS)
        assert inv.value(paid) - inv.value(change) == inv.value(price)
        assert not any(paid[d] and change[d] for d in inv.DENOMS)


# ── migration of the two Enderal sheets ─────────────────────────────────────

def test_migration_dry_run_reports_and_writes_nothing(enderal):
    before = (enderal / "characters/elbane-iii.md").read_text(encoding="utf-8")
    code, out, _ = cli("migrate", "--character", "elbane-iii")
    assert code == 0
    assert "purse 32 gp, 8 sp, 7 cp" in out
    assert "Dagger ×2" in out and "Arrow ×20" in out and "Leather armour (equipped)" in out
    assert "Dry run" in out
    assert not (enderal / "characters/elbane-iii.inventory.json").exists()
    assert (enderal / "characters/elbane-iii.md").read_text(encoding="utf-8") == before


def test_migrating_elbane(enderal):
    migrated(enderal)
    data = ledger(enderal, "elbane-iii")
    assert data["coins"] == purse(gp=32, sp=8, cp=7)
    items = {i["name"]: i for i in data["items"]}
    assert {n: (i["qty"], i["category"]) for n, i in items.items()} == {
        "Shortsword": (1, "weapons"), "Dagger": (2, "weapons"), "Shortbow": (1, "weapons"),
        "Arrow": (20, "weapons"), "Quiver": (1, "gear"), "Leather armour": (1, "armour"),
        "Burglar's pack": (1, "gear"), "Thieves' tools": (1, "tools"),
        "Traveller's clothes": (1, "gear"), "Pouch": (2, "gear"), "Navigator's tools": (1, "tools"),
    }
    assert items["Leather armour"]["equipped"] is True
    # Trade history is not inventory: no flour, blankets or salt items.
    assert not any(w in json.dumps(data).lower() for w in ("flour", "blanket", "salt", "hessa"))
    sheet = (enderal / "characters/elbane-iii.md").read_text(encoding="utf-8")
    section = sheet.split("## Equipment & Inventory", 1)[1].split("\n## ", 1)[0]
    assert "flour" not in section and "Generated by inventory.py" in section
    assert "**Currency:** 32 gp 8 sp 7 cp" in section
    notes = sheet.split("## Backstory & Notes", 1)[1]
    assert "flour was sold to Meren for 26 sp" in notes and "after selling the flour" in notes
    assert list((enderal / "backups").glob("elbane-iii.md.before-inventory-ledger-*"))


def test_migrating_nella_shares_the_wagon_once(enderal):
    migrated(enderal)
    data = ledger(enderal, "nella-reed")
    assert data["coins"] == purse(gp=109)
    assert {(i["name"], i["qty"], i["category"], i["equipped"]) for i in data["items"]} == {
        ("Longsword", 1, "weapons", False), ("Javelin", 1, "weapons", False),
        ("Studded leather", 1, "armour", True), ("Shield", 1, "armour", True),
        ("Holy symbol", 1, "gear", False), ("Explorer's pack", 1, "gear", False),
        ("Torch", 10, "consumables", False),
    }
    cargo = json.loads((enderal / "party_inventory.json").read_text(encoding="utf-8"))
    assert [i["name"] for i in cargo["items"]] == ["Wagon", "Mule"]
    assert "3 sp" in cargo["items"][0]["notes"]
    for stem in ("elbane-iii", "nella-reed"):
        text = (enderal / f"characters/{stem}.md").read_text(encoding="utf-8")
        assert "**Shared cargo (party):** Wagon" in text
    nella = (enderal / "characters/nella-reed.md").read_text(encoding="utf-8")
    assert "Starting gold alternatives" in nella.split("## Backstory & Notes", 1)[1]


def test_migrating_twice_is_refused(enderal):
    migrated(enderal)
    code, _, err = cli("migrate", "--character", "elbane-iii", "--write")
    assert code == 2 and "already has a ledger" in err


def test_migration_keeps_lines_it_cannot_read_as_notes():
    parsed = inv.parse_prose_inventory("**Mystery:** a humming thing\n**Weapons:** 3 spears\n")
    assert parsed["unread"] == ["**Mystery:** a humming thing"]
    assert parsed["items"][0]["name"] == "Spear" and parsed["items"][0]["qty"] == 3


# ── commands ─────────────────────────────────────────────────────────────────

def test_spend_buy_sell_and_items_update_ledger_and_sheet(enderal):
    migrated(enderal)
    code, out, _ = cli("spend", "--character", "Elbane III", "43 cp")
    assert code == 0 and "Purse: 32 gp, 4 sp, 4 cp." in out
    code, out, _ = cli("buy", "--character", "Nella", "Hooded lantern", "--price", "5 gp")
    assert code == 0 and "Purse: 104 gp." in out
    code, out, _ = cli("sell", "--character", "elbane-iii", "Pouch", "--qty", "2", "--price", "1 sp")
    assert code == 0 and "sells Pouch ×2 for 1 sp" in out
    assert cli("remove", "--character", "elbane-iii", "arrows", "--qty", "3")[1].endswith("17 left.")
    assert cli("add", "--character", "elbane-iii", "Rope", "--qty", "2", "--notes", "hempen")[0] == 0
    assert cli("equip", "--character", "elbane-iii", "shortsword")[0] == 0
    data = ledger(enderal, "elbane-iii")
    items = {i["name"]: i for i in data["items"]}
    assert "Pouch" not in items and items["Arrow"]["qty"] == 17
    assert items["Rope"] == {"name": "Rope", "qty": 2, "category": "gear", "equipped": False, "notes": "hempen"}
    assert items["Shortsword"]["equipped"] is True
    sheet = (enderal / "characters/elbane-iii.md").read_text(encoding="utf-8")
    assert "**Currency:** 32 gp 5 sp 4 cp" in sheet
    assert "Shortsword (equipped)" in sheet and "Rope ×2 (hempen)" in sheet


@pytest.mark.parametrize("args, message", [
    (("spend", "--character", "nella-reed", "200 gp"), "short by 91 gp"),
    (("buy", "--character", "nella-reed", "Warhorse", "--price", "400 gp"), "cannot afford"),
    (("remove", "--character", "nella-reed", "Torch", "--qty", "11"), "only 10"),
    (("sell", "--character", "nella-reed", "Lute", "--price", "5 gp"), "has no 'Lute'"),
    (("earn", "--character", "nella-reed", "lots"), "cannot read the amount"),
    (("add", "--character", "nella-reed", "Rope", "--category", "snacks"), "unknown category"),
    (("add", "--character", "nella-reed", "Rope", "--qty", "0"), "at least 1"),
    (("show", "--character", "Gorm"), "no character 'Gorm'"),
])
def test_impossible_requests_are_refused_without_writing(enderal, args, message):
    migrated(enderal)
    before = {p.name: p.read_text(encoding="utf-8") for p in enderal.rglob("*") if p.is_file()}
    code, _, err = cli(*args)
    assert code == 2 and message in err
    after = {p.name: p.read_text(encoding="utf-8") for p in enderal.rglob("*") if p.is_file()}
    assert after == before


def test_commands_need_a_ledger_first(enderal):
    code, _, err = cli("earn", "--character", "elbane-iii", "1 gp")
    assert code == 2 and "migrate" in err


def test_cargo_is_shared_and_shows_on_every_sheet(enderal):
    migrated(enderal)
    assert cli("cargo", "add", "Sack of salt", "--qty", "3")[0] == 0
    assert cli("cargo", "remove", "Mule")[1].endswith("0 left.")
    for stem in ("elbane-iii", "nella-reed"):
        text = (enderal / f"characters/{stem}.md").read_text(encoding="utf-8")
        assert "Sack of salt ×3" in text and "Mule" not in text.split("## Equipment & Inventory")[1].split("\n## ")[0]


def test_hand_edited_ledger_with_bad_coins_is_refused(enderal):
    migrated(enderal)
    path = enderal / "characters/elbane-iii.inventory.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["coins"]["gp"] = -3
    path.write_text(json.dumps(data), encoding="utf-8")
    code, _, err = cli("show", "--character", "elbane-iii")
    assert code == 2 and "whole numbers" in err


def test_push_only_targets_the_campaign_the_display_shows(enderal, monkeypatch):
    migrated(enderal)
    sent = []
    sys.path.insert(0, str(DISPLAY))
    import push_stats
    monkeypatch.setattr(push_stats, "_send", lambda url, data, token: sent.append(json.loads(data)))
    monkeypatch.delenv("DND_INVENTORY_PUSH", raising=False)
    runtime = inv.campaign_path("Enderal").parents[1] / ".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / ".campaign").write_text("Other", encoding="utf-8")
    assert inv.push(enderal) is False and sent == []
    (runtime / ".campaign").write_text("Enderal", encoding="utf-8")
    assert inv.push(enderal) is True
    players = {p["name"]: p["sheet"] for p in sent[0]["players"]}
    assert set(players) == {"Elbane III", "Nella Reed"}
    assert players["Nella Reed"]["ledger"]["coins"]["gp"] == 109
    assert [c["name"] for c in players["Nella Reed"]["ledger"]["cargo"]] == ["Wagon", "Mule"]
    assert players["Nella Reed"]["inventory"][0] == "**Currency:** 109 gp"
    monkeypatch.setenv("DND_INVENTORY_PUSH", "0")
    assert inv.push(enderal) is False


# ── features ─────────────────────────────────────────────────────────────────

def test_features_are_named_and_grouped_by_source(enderal):
    text = (enderal / "characters/elbane-iii.md").read_text(encoding="utf-8")
    features = sheet_extract.parse_features(text)
    names = [(f["kind"], f["source"], f["name"]) for f in features]
    assert names == [
        ("class", "Rogue", "Expertise"), ("class", "Rogue", "Sneak Attack"),
        ("class", "Rogue", "Thieves' Cant"), ("class", "Rogue", "Weapon Mastery"),
        ("class", "Rogue", "Proficiencies"),
        ("species", "Human", "Resourceful"), ("species", "Human", "Skillful"),
        ("species", "Human", "Versatile"), ("species", "Human", "Size, speed and languages"),
        ("background", "Merchant", "Lucky"), ("background", "Merchant", "Proficiencies"),
    ]
    by_name = {f["name"]: f for f in features}
    assert by_name["Lucky"]["tag"] == "Origin feat"
    assert by_name["Weapon Mastery"]["text"] == "Shortsword and shortbow (Vex)."
    assert by_name["Expertise"]["text"] == "Investigation and Persuasion; doubled proficiency bonus."
    rogue = next(f for f in features if f["source"] == "Rogue" and f["name"] == "Proficiencies")
    assert "Skills: Insight" in rogue["text"]
    assert by_name["Size, speed and languages"]["text"].startswith("Medium; speed 30 ft")


def test_nella_features_keep_proficiency_clauses_together(enderal):
    text = (enderal / "characters/nella-reed.md").read_text(encoding="utf-8")
    features = sheet_extract.parse_features(text)
    paladin = [f["name"] for f in features if f["source"] == "Paladin"]
    assert paladin == ["Lay on Hands", "Spellcasting", "Weapon Mastery", "Proficiencies", "Notes"]
    prof = next(f for f in features if f["source"] == "Paladin" and f["name"] == "Proficiencies")
    assert "Class skills: Athletics and Insight" in prof["text"]


def test_bold_feature_lines_and_template_headings():
    text = ("# Mira\n- **Race:** Elf | **Class:** Fighter | **Level:** 2 | **Background:** Soldier\n"
            "## Features & Traits\n### Fighter\n- **Second Wind:** Bonus action: regain 1d10+2 HP.\n"
            "### Elf Racial\n- Darkvision: 60 ft.\n### Soldier Background\n-\n## Equipment & Inventory\n")
    features = sheet_extract.parse_features(text)
    assert [(f["kind"], f["source"], f["name"]) for f in features] == [
        ("class", "Fighter", "Second Wind"), ("species", "Elf", "Darkvision")]
    assert features[0]["text"] == "Bonus action: regain 1d10+2 HP."


def test_push_stats_from_sheets_merges_into_the_load_payload(enderal):
    migrated(enderal)
    sys.path.insert(0, str(DISPLAY))
    import push_stats
    payload = {"players": [{"name": "Elbane III", "sheet": {"attacks": [{"name": "Dagger"}]}}]}
    push_stats._merge_sheets(payload, "Enderal")
    sheet = payload["players"][0]["sheet"]
    assert sheet["attacks"] == [{"name": "Dagger"}]
    assert sheet["ledger"]["coins"]["gp"] == 32 and sheet["features"][0]["name"] == "Expertise"
    assert len(payload["players"]) == 1  # a player not in a --json push is not added
    partial = {}
    push_stats._merge_sheets(partial, "Enderal")
    assert {p["name"] for p in partial["players"]} == {"Elbane III", "Nella Reed"}


def test_without_a_ledger_the_display_gets_the_prose_lines(enderal):
    name, data = sheet_extract.sheet_payload(enderal, enderal / "characters/nella-reed.md")
    assert name == "Nella Reed" and "ledger" not in data
    assert data["inventory"][3].startswith("**Currency:** 109 gp")


def test_forced_remigration_reads_the_generated_section_back_unchanged(enderal):
    migrated(enderal)
    cli("equip", "--character", "elbane-iii", "Shortsword")
    before = ledger(enderal, "elbane-iii")
    assert cli("migrate", "--character", "elbane-iii", "--write", "--force")[0] == 0
    after = ledger(enderal, "elbane-iii")
    by_name = lambda items: sorted(items, key=lambda i: i["name"])  # noqa: E731
    assert after["coins"] == before["coins"]
    assert by_name(after["items"]) == by_name(before["items"])


def test_broken_cargo_file_refuses_before_any_coin_moves(enderal):
    migrated(enderal)
    (enderal / "party_inventory.json").write_text("{not json", encoding="utf-8")
    before = (enderal / "characters/elbane-iii.inventory.json").read_text(encoding="utf-8")
    code, _, err = cli("spend", "--character", "elbane-iii", "1 gp")
    assert code == 2 and "party_inventory.json" in err
    assert (enderal / "characters/elbane-iii.inventory.json").read_text(encoding="utf-8") == before


def test_template_labels_carry_to_the_bullets_below_them():
    parsed = inv.parse_prose_inventory(
        "**Weapons:**\n- Net\n- 2 javelins\n\n**Armour:**\n- Leather\n\n**Adventuring Gear:**\n- Bedroll\n"
        "\n**Currency:** 0gp 4sp 0cp\n")
    got = {(i["name"], i["qty"], i["category"], i["equipped"]) for i in parsed["items"]}
    assert got == {("Net", 1, "weapons", False), ("Javelin", 2, "weapons", False),
                   ("Leather", 1, "armour", True), ("Bedroll", 1, "gear", False)}
    assert parsed["coins"]["sp"] == 4 and not parsed["unread"]


def test_renaming_a_pc_moves_the_ledger_with_the_sheet(enderal):
    import os
    import subprocess
    migrated(enderal)
    (enderal / "state.md").write_text("# Enderal\n- **Party:** Elbane III, Nella Reed\n", encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "npc_rename.py"), "--campaign", "Enderal", "--old", "Nella Reed",
         "--new", "Nella Thorn", "--type", "pc", "--yes"],
        capture_output=True, text=True, env={**os.environ}, timeout=60)
    assert out.returncode == 0, out.stderr
    assert not (enderal / "characters/nella-reed.inventory.json").exists()
    assert ledger(enderal, "nella-thorn")["character"] == "Nella Thorn"
    code, text, _ = cli("show", "--character", "Nella Thorn")
    assert code == 0 and "109 gp" in text


def test_tonights_lines_become_cargo_and_a_document_item():
    parsed = inv.parse_prose_inventory(
        "**Shared caravan property:** one additional shortbow confiscated from the surrendered road "
        "bandit on 17 Duskfall; no extra arrows or coins taken.\n"
        "**Business documents:** Dera Voss’s order: two crates of iron barrel hoops, 4 sp cartage.\n")
    assert [(c["name"], c["notes"]) for c in parsed["cargo"]] == [
        ("Shortbow", "confiscated from the surrendered road bandit on 17 Duskfall")]
    assert parsed["notes"] == ["Shared caravan property: No extra arrows or coins taken."]
    doc = parsed["items"][0]
    assert doc["name"] == "Business documents" and doc["category"] == "gear" and "4 sp cartage" in doc["notes"]
    assert not parsed["unread"]


def test_parenthesised_notes_keep_their_and():
    parsed = inv.parse_prose_inventory("**Adventuring Gear:** rope (gift from Ada and Bob), 2 sacks\n")
    assert [(i["name"], i.get("notes")) for i in parsed["items"]] == [
        ("Rope", "gift from Ada and Bob"), ("Sack", None)]


def test_forced_remigration_keeps_armour_taken_off(enderal):
    migrated(enderal)
    cli("unequip", "--character", "nella-reed", "Shield")
    assert cli("migrate", "--character", "nella-reed", "--write", "--force")[0] == 0
    shield = next(i for i in ledger(enderal, "nella-reed")["items"] if i["name"] == "Shield")
    assert shield["equipped"] is False


def test_cargo_change_refused_before_writing_when_a_ledger_is_broken(enderal):
    migrated(enderal)
    (enderal / "characters/nella-reed.inventory.json").write_text("{bad", encoding="utf-8")
    before = (enderal / "party_inventory.json").read_text(encoding="utf-8")
    code, _, err = cli("cargo", "add", "Barrel hoops", "--qty", "2")
    assert code == 2 and "nella-reed.inventory.json" in err
    assert (enderal / "party_inventory.json").read_text(encoding="utf-8") == before


def test_adding_to_a_stack_keeps_the_attributes_given(enderal):
    migrated(enderal)
    assert cli("add", "--character", "elbane-iii", "Dagger", "--equipped", "--weight", "1")[0] == 0
    dagger = next(i for i in ledger(enderal, "elbane-iii")["items"] if i["name"] == "Dagger")
    assert dagger["qty"] == 3 and dagger["equipped"] is True and dagger["weight"] == 1


def test_a_features_heading_is_not_a_feats_heading():
    text = ("# Bo\n- **Class:** Fighter | **Race:** Elf\n## Features & Traits\n### Fighter Features\n"
            "- **Second Wind:** Heal.\n### Feats\n- **Alert:** Initiative.\n")
    kinds = [(f["name"], f["kind"]) for f in sheet_extract.parse_features(text)]
    assert kinds == [("Second Wind", "class"), ("Alert", "feat")]
