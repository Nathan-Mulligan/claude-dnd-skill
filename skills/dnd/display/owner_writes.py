"""owner_writes.py — writes a sandboxed DM can't make, made by the display on its request (#138).

A DM started by the portal can write only its campaign folder and the runtime dir. These steps write
elsewhere, so when the sandbox refuses them the DM's script leaves a request (helper_jobs.request_write)
and the display, which runs outside the sandbox, carries it out:

  roster        copy the campaign's character sheets to the global roster, <data root>/characters/
  names         add or retire one name in the name registry, <data root>/.name_registry.json
  supplemental  fetch descriptions of the campaign's non-SRD spells and features into skills/dnd/data/

The DM can write any request, so each one is checked strictly: it must name the campaign the display
is showing, sheets are read only from that campaign's characters/ folder (never through a link), and
names are short single lines. Run by hand, `roster` copies the sheets itself:

    python3 owner_writes.py roster --campaign <name>
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import write_file  # noqa: E402  (also puts scripts/ on sys.path)
import sidebar_sync  # noqa: E402
from paths import _root, campaigns_dir, characters_dir  # noqa: E402

_CAMPAIGN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHEET = re.compile(r"[^./\\\x00-\x1f][^/\\\x00-\x1f]{0,99}\.md")
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,100}")
SHEET_MAX_BYTES = 512 * 1024
MAX_SHEETS = 20


def _fields(payload: dict, required: set, optional: set = frozenset()) -> None:
    if not isinstance(payload, dict) or not required <= set(payload) <= required | optional:
        raise ValueError("unexpected request fields")


def campaign_folder(name, active) -> str:
    """The folder of campaign `name`, which must be the one the display is showing."""
    if not isinstance(name, str) or not _CAMPAIGN.fullmatch(name) or name in {".", ".."}:
        raise ValueError("invalid campaign name")
    if name != active:
        raise ValueError(f"{name} is not the campaign the display is showing")
    folder = os.path.join(str(campaigns_dir()), name)
    if os.path.islink(folder) or not os.path.isdir(folder):
        raise ValueError(f"{name} has no campaign folder")
    return folder


def sheets(folder: str) -> "list[tuple[str, str]]":
    """(file name, text) of each character sheet in `folder`/characters, never read through a link."""
    characters = os.path.join(folder, "characters")
    if os.path.islink(characters) or not os.path.isdir(characters):
        return []
    found = []
    for name in sorted(os.listdir(characters)):
        if not _SHEET.fullmatch(name) or name.endswith(".inventory.md"):
            continue
        text = sidebar_sync.read_state(os.path.join(characters, name), limit=SHEET_MAX_BYTES)
        if text is not None:
            found.append((name, text))
        if len(found) == MAX_SHEETS:
            break
    return found


def mirror_roster(campaign, active) -> str:
    """Copy the campaign's sheets to the global roster, replacing each sheet's earlier copy."""
    copied = sheets(campaign_folder(campaign, active))
    for name, text in copied:
        write_file(os.path.join(str(characters_dir()), name), text, root=str(_root()))
    return f"copied {len(copied)} sheet(s) of {campaign} to the roster"


def _name(value, field: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value.strip()):
        raise ValueError(f"invalid {field}")
    return value.strip()


def record_name(payload: dict, active) -> str:
    """Add or retire one name in the registry for the campaign the display is showing."""
    import name_registry
    op = payload.get("op") if isinstance(payload, dict) else None
    if op == "add":
        _fields(payload, {"op", "name", "type", "campaign", "session"})
        if payload["type"] not in ("npc", "pc"):
            raise ValueError("invalid type")
        session = payload["session"]
        if type(session) is not int or not 0 <= session <= 100000:
            raise ValueError("invalid session")
        campaign_folder(payload["campaign"], active)
        name = _name(payload["name"], "name")
        name_registry.add(name, payload["type"], payload["campaign"], session, ask_display=False)
        return f"added {name}"
    if op == "retire":
        _fields(payload, {"op", "name", "campaign"}, {"replaced_by"})
        campaign_folder(payload["campaign"], active)
        name = _name(payload["name"], "name")
        replaced_by = payload.get("replaced_by")
        replaced_by = None if replaced_by is None else _name(replaced_by, "replacement name")
        name_registry.retire(name, payload["campaign"], replaced_by, ask_display=False)
        return f"retired {name}"
    raise ValueError("unknown registry operation")


def fetch_supplemental(payload: dict, active) -> str:
    """Fetch descriptions for the non-SRD spells and features on the campaign's sheets."""
    import build_supplemental
    _fields(payload, {"campaign", "ruleset"})
    if payload["ruleset"] not in ("2014", "2024"):
        raise ValueError("invalid ruleset")
    folder = campaign_folder(payload["campaign"], active)
    paths = [os.path.join(folder, "characters", name) for name, _ in sheets(folder)]
    added = build_supplemental.fetch_missing(payload["ruleset"], paths)
    return f"added {added} supplemental entries for {payload['campaign']}"


def writers() -> dict:
    """The display's handlers, loaded when it starts (helper_jobs.py)."""
    import build_supplemental  # noqa: F401 — loaded now, like the helpers
    import name_registry  # noqa: F401
    return {"roster": lambda payload, active: mirror_roster(_campaign_of(payload), active),
            "names": record_name, "supplemental": fetch_supplemental}


def _campaign_of(payload) -> str:
    _fields(payload, {"campaign"})
    return payload["campaign"]


def request(kind: str, payload: dict) -> bool:
    """Ask the display to make this write. Never raises; says on stderr what happened."""
    try:
        import helper_jobs
        helper_jobs.request_write(kind, payload)
    except (OSError, ValueError) as error:
        print(f"owner_writes: could not ask the display ({type(error).__name__}); not recorded", file=sys.stderr)
        return False
    print(f"owner_writes: this DM can't write there, so the display will ({kind})", file=sys.stderr)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy a campaign's character sheets to the global roster.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    roster = sub.add_parser("roster", help="copy the campaign's sheets to <data root>/characters/")
    roster.add_argument("--campaign", required=True)
    args = parser.parse_args()
    try:
        print(mirror_roster(args.campaign, args.campaign))
    except PermissionError:
        return 0 if request("roster", {"campaign": args.campaign}) else 1
    except (OSError, ValueError) as error:
        print(f"owner_writes: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
