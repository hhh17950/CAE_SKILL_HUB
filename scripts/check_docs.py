"""Check local documentation links, JSON requests and the published route table."""

import json
import re
from pathlib import Path
from urllib.parse import unquote

from app.config import Settings
from app.main import create_app
from app.schemas.analysis import AnalysisRequest


def main():
    root = Path(__file__).resolve().parents[1]
    files = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    errors = []
    examples = 0
    for file in files:
        body = file.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
            target = target.strip("<>")
            if "://" in target or target.startswith("#"):
                continue
            relative = unquote(target.split("#", 1)[0])
            if not (file.parent / relative).exists():
                errors.append(f"{file.relative_to(root)}: missing link {relative}")
        for raw in re.findall(r"```json\s*\n(.*?)\n```", body, flags=re.S):
            try:
                example = json.loads(raw)
                if isinstance(example, dict) and "workflow_id" in example and "input" in example:
                    AnalysisRequest.model_validate(example)
                    examples += 1
            except (ValueError, TypeError) as exc:
                errors.append(f"{file.relative_to(root)}: invalid JSON example: {exc}")
    schema = create_app(Settings(_env_file=None, enable_legacy_api=False, provider="mock")).openapi()
    contract = (root / "docs/对接指南.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"\| .*?`(GET|POST|PUT|DELETE|PATCH) (/[^` ]+)`", contract))
    published = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH"}
    }
    if documented - published:
        for method, path in sorted(documented - published):
            errors.append(f"documented route is not implemented: {method} {path}")
    if published - documented:
        for method, path in sorted(published - documented):
            errors.append(f"published route is absent from integration guide: {method} {path}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"Documentation OK: {len(files)} Markdown files, {examples} typed request example(s), route table matches"
    )


if __name__ == "__main__":
    main()
