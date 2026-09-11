"""Rebuild docs.zip from current Markdown and JSON files, without old archive entries."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    files = sorted(
        p
        for p in (root / "docs").rglob("*")
        if p.is_file()
        and p.suffix in {".md", ".json"}
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )
    target = root / "docs.zip"
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, file.relative_to(root))
    print(f"{target}: {len(files)} current documents; previous archive replaced")


if __name__ == "__main__":
    main()
