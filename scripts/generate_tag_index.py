#!/usr/bin/env python3
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COOK_FILES = sorted(ROOT.glob("**/*.cook"))


def extract_front_matter(text):
    lines = text.splitlines()
    front = []
    in_front = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if not in_front:
                in_front = True
                continue
            break
        if in_front:
            front.append(line)
    return front


def extract_tags_from_front_matter(front):
    tags = []
    collecting_list = False
    for line in front:
        stripped = line.lstrip()
        if collecting_list:
            if stripped.startswith("-"):
                tags.append(stripped.lstrip("-").strip())
                continue
            collecting_list = False
        if stripped.lower().startswith("tags:"):
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                tags.extend([item.strip() for item in remainder.split(",") if item.strip()])
            else:
                collecting_list = True
    return tags


def build_tag_index():
    counter = Counter()
    for cook_file in COOK_FILES:
        text = cook_file.read_text(encoding="utf-8")
        front = extract_front_matter(text)
        tags = extract_tags_from_front_matter(front)
        counter.update(tags)
    return counter


def render_index(counter):
    header = [
        "# Tag Index",
        "",
        f"Generated on {datetime.utcnow().isoformat()}Z by `scripts/generate_tag_index.py`",
        "",
        "| Tag | Count |",
        "| --- | ----- |",
    ]
    rows = [f"| {tag} | {count} |" for tag, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
    return "\n".join(header + rows) + "\n"


def main():
    counter = build_tag_index()
    if not counter:
        raise SystemExit("No tags found across .cook files")
    index = render_index(counter)
    (ROOT / "tags-index.md").write_text(index, encoding="utf-8")


if __name__ == "__main__":
    main()

