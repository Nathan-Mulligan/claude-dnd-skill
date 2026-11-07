#!/usr/bin/env python3
"""Point the display at an already installed local Stable Audio 3 MLX runtime."""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "skills" / "dnd" / "display"))
from runtime_paths import rt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True, help="Executable sa3 wrapper")
    parser.add_argument("--hf-home", type=Path, required=True, help="Local weights cache")
    args = parser.parse_args()
    cli = args.cli.expanduser().resolve()
    hf_home = args.hf_home.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        parser.error(f"not an executable file: {cli}")
    if not hf_home.is_dir():
        parser.error(f"weights cache does not exist: {hf_home}")
    target = Path(rt("music-config.json"))
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"cli": str(cli), "hf_home": str(hf_home)}), encoding="utf-8")
    temporary.replace(target)
    print(f"Configured local music runtime at {target}")


if __name__ == "__main__":
    main()
