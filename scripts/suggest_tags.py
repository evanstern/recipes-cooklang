#!/usr/bin/env python3
"""
Suggest tags for a single Cooklang recipe using OpenAI (gpt-4o-mini).

Requires OPENAI_API_KEY to be set in the environment.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent.parent
TAGS_INDEX = ROOT / "tags-index.md"

PROMPT_TEMPLATE = """You are a recipe tag curator. Assign normalized, reusable tags
for the recipe below (course, cuisine, main ingredient focus, and high-level traits).

Prefer the popular tags listed below, but add new tags when they will likely describe
other recipes too. Normalize each tag by lowercasing it, keeping only letters/spaces,
and preferring general terms (e.g., break “thai vegetable curry” into “thai”,
“vegetable”, “curry”). Ignore the existing front-matter tags when picking your list.

Popular tags: {tag_index_section}

Recipe:
```
{recipe}
```

Return JSON: `{{"recommended_tags": [...]}}`.
"""
openai_module: Optional[Any] = None


def summarize_tag_index(text: str, limit: int = 8) -> str:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("| "):
            continue
        parts = [col.strip() for col in line.split("|") if col.strip()]
        if len(parts) < 2 or parts[0].lower() == "tag":
            continue
        rows.append((parts[0], parts[1]))
    return ", ".join(f"{tag}({count})" for tag, count in rows[:limit]) or "none"


def format_tag_line(tags: list[str]) -> str:
    joined = ", ".join(tags)
    return f"tags: {joined}"


def update_cook_tags(path: Path, tags: list[str]):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SystemExit(f"{path} is missing front matter.")
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise SystemExit(f"{path} front matter not closed with '---'.")

    front = lines[1:end_idx]
    new_front = []
    replaced = False
    tag_line = format_tag_line(tags)
    for line in front:
        stripped = line.lstrip()
        if stripped.lower().startswith("tags:"):
            new_front.append(tag_line)
            replaced = True
        else:
            new_front.append(line)
    if not replaced:
        new_front.append(tag_line)

    updated = ["---"] + new_front + ["---"] + lines[end_idx + 1 :]
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def build_prompt(recipe_text: str, tag_index: str | None) -> str:
    """Return the prompt that will be sent to the LLM."""
    section = summarize_tag_index(tag_index) if tag_index else "none"
    return PROMPT_TEMPLATE.format(recipe=recipe_text, tag_index_section=section)


def get_openai_module() -> Any:
    global openai_module
    if openai_module is not None:
        return openai_module
    try:
        openai_module = importlib.import_module("openai")
    except ModuleNotFoundError:
        raise SystemExit("Install the openai package to run this script.")
    return openai_module


def query_openai(prompt: str) -> str:
    """Call OpenAI chat completion using gpt-4o-mini and return the response text."""
    openai = get_openai_module()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY to call OpenAI.")

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant that classifies recipes into normalized tag sets.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=600,
    )
    return response.choices[0].message.content


def load_recipe(path: Path) -> str:
    """Read the .cook file content that will be analyzed."""
    return path.read_text(encoding="utf-8")


def load_dotenv():
    """Load key/value pairs from a .env file into the process environment."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_llm_output(content: str):
    """Try to parse the LLM response as JSON."""
    trimmed = content.strip()
    if trimmed.startswith("```json"):
        trimmed = trimmed.split("\n", 1)[-1]
    if trimmed.endswith("```"):
        trimmed = trimmed[: -3].rstrip()
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        raise SystemExit("LLM response was not valid JSON:\n" + content)


def read_tag_index() -> str | None:
    if TAGS_INDEX.exists():
        return TAGS_INDEX.read_text(encoding="utf-8").strip()
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Suggest normalized recipe tags for one Cooklang file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/suggest_tags.py soup/lentil-soup.cook
  OPENAI_API_KEY=... python scripts/suggest_tags.py entrees/pasta-pomodoro.cook
  python scripts/suggest_tags.py --json-only entrees/best-veggie-burger.cook
""",
    )
    parser.add_argument("cook_file", type=Path, help="Path to a .cook file to analyze.")
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Suppress prompt output and emit just the JSON returned by the LLM.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Overwrite the recipe's front-matter tags line with the suggested tags.",
    )
    args = parser.parse_args()

    load_dotenv()

    recipe = load_recipe(args.cook_file)
    tag_index = read_tag_index()
    prompt = build_prompt(recipe, tag_index)
    content = query_openai(prompt)
    result = parse_llm_output(content)

    recommended = result.get("recommended_tags")
    if not isinstance(recommended, list):
        raise SystemExit("LLM did not return a 'recommended_tags' list.")

    if args.write:
        update_cook_tags(args.cook_file, recommended)

    if args.json_only:
        print(json.dumps(result, ensure_ascii=False))
        return

    print("Prompt being sent to the LLM:\n")
    print(prompt)
    print("\n---\n")

    recommended = result.get("recommended_tags")
    if not isinstance(recommended, list):
        raise SystemExit("LLM did not return a 'recommended_tags' list.")

    print("Recommended tags:")
    for tag in recommended:
        print(f"- {tag}")


if __name__ == "__main__":
    main()

