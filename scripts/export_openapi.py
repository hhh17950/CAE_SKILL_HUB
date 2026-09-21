"""Run from the repository root: python -m scripts.export_openapi."""

import argparse
import json
from pathlib import Path

from core.config import Settings
from main import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if checked-in schema is stale")
    args = parser.parse_args()
    schema = create_app(Settings(_env_file=None)).openapi()
    rendered = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target = Path(__file__).resolve().parents[1] / "docs/contracts/openapi.json"
    if args.check:
        if not target.exists() or target.read_text() != rendered:
            raise SystemExit("OpenAPI is stale; run python -m scripts.export_openapi")
        print("OpenAPI matches implementation")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered)
    print(target)


if __name__ == "__main__":
    main()
