#!/usr/bin/env python3
"""
Normalize ingredients by adding synonyms to `config/aisle.conf` and categorizing new items.

Reads ingredients from a Cooklang recipe.
Checks if they exist in `aisle.conf`.
If not, uses OpenAI to find if they are synonyms of existing ingredients or new items.
If new, asks OpenAI to categorize them into aisles (e.g. [produce], [dairy]).
Updates `aisle.conf` by appending synonyms or inserting new items into appropriate categories.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
AISLE_CONF = ROOT / "config" / "aisle.conf"
ENV_FILE = ROOT / ".env"

PROMPT_TEMPLATE = """Analyze the following ingredients and normalize them against the known list and categories.

Existing Ingredients (from aisle.conf):
{known_list}

Existing Categories:
{categories}

New Ingredients (found in recipe, not in aisle.conf):
{new_ingredients}

Task:
1. Identify synonyms: If a New Ingredient is a variation of an Existing Ingredient (e.g., "Garlic Cloves" -> "Garlic"), map it.
2. Identify new items: If it has no match, list it as a new item and assign it to an appropriate Category (use existing ones or suggest a standard new one like [produce], [dairy], [pantry], [spices], [meat], [frozen], [bakery]).

Return ONLY JSON:
{{
  "synonyms": {{ "Existing Ingredient": ["New Ingredient Variation"] }},
  "new_items": {{ "Category Name": ["Truly New Ingredient 1", "Truly New Ingredient 2"] }}
}}
"""

openai_module: Optional[Any] = None


def load_dotenv():
    """Load key/value pairs from a .env file into the process environment."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_openai_module() -> Any:
    global openai_module
    if openai_module is not None:
        return openai_module
    try:
        openai_module = importlib.import_module("openai")
    except ModuleNotFoundError:
        raise SystemExit("Install the openai package to run this script.")
    return openai_module


def parse_aisle_conf() -> tuple[dict[str, int], list[str], dict[str, int]]:
    """
    Parses aisle.conf.
    Returns:
        - mapping: {ingredient_name: line_number} (0-indexed)
        - lines: list of all lines in the file
        - categories: {category_name: line_number_of_header}
    """
    if not AISLE_CONF.exists():
        return {}, [], {}
    
    lines = AISLE_CONF.read_text(encoding="utf-8").splitlines()
    mapping = {}
    categories = {}
    
    current_category = None
    
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
            
        if stripped.startswith("[") and stripped.endswith("]"):
            current_category = stripped[1:-1].lower()
            categories[current_category] = idx
            continue
            
        # Split by |
        parts = stripped.split("|")
        for part in parts:
            name = part.strip()
            if name:
                mapping[name] = idx
                
    return mapping, lines, categories


def extract_ingredients(text: str) -> set[str]:
    # Matches @name{...}
    matches_braces = re.findall(r"@([^@#\n]+?)\{", text)
    return set(matches_braces)


def query_openai(known: list[str], categories: list[str], unknown: list[str]) -> dict:
    openai = get_openai_module()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY to call OpenAI.")

    client = openai.OpenAI(api_key=api_key)
    
    known_str = "\n".join(sorted(known)) if known else "(None)"
    categories_str = ", ".join(sorted(categories)) if categories else "(None)"
    unknown_str = "\n".join(sorted(unknown))
    
    prompt = PROMPT_TEMPLATE.format(known_list=known_str, categories=categories_str, new_ingredients=unknown_str)
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant for organizing grocery lists."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    
    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise SystemExit(f"Invalid JSON from LLM: {content}")


def update_aisle_conf(lines: list[str], synonyms: dict[str, list[str]], new_items_by_cat: dict[str, list[str]], mapping: dict[str, int], categories: dict[str, int]) -> list[str]:
    new_lines = list(lines)
    
    # 1. Add synonyms to existing lines
    # We use a map to track updates to avoid index shifting issues if we were inserting,
    # but for synonyms we are modifying in place.
    
    updates_by_line = {}
    for existing, new_syns in synonyms.items():
        if existing not in mapping:
            # Handle hallucinated "existing" items by treating them as new items in "uncategorized" or inferring?
            # Let's put them in [uncategorized] for now if LLM messed up, or just add to new_items_by_cat if possible.
            # But we don't know the category.
            # Let's just log warning and skip, or add to a default category.
            print(f"Warning: '{existing}' not found in aisle.conf. Adding to [uncategorized].")
            if "uncategorized" not in new_items_by_cat:
                new_items_by_cat["uncategorized"] = []
            combined = existing + " | " + " | ".join(new_syns)
            new_items_by_cat["uncategorized"].append(combined)
            continue
            
        line_idx = mapping[existing]
        if line_idx not in updates_by_line:
            updates_by_line[line_idx] = []
        updates_by_line[line_idx].extend(new_syns)
        
    for line_idx, syns in updates_by_line.items():
        current_line = new_lines[line_idx]
        existing_parts = [p.strip() for p in current_line.split("|")]
        to_add = [s for s in syns if s not in existing_parts]
        
        if to_add:
            new_lines[line_idx] = current_line + " | " + " | ".join(to_add)
            print(f"Updated line {line_idx+1}: {new_lines[line_idx]}")

    # 2. Insert new items into categories
    # We need to handle insertions carefully. Inserting changes indices of subsequent lines.
    # Strategy: Build a new list of lines.
    
    # First, let's normalize category names to lowercase for matching
    normalized_cats = {k.lower(): v for k, v in categories.items()}
    
    # We will iterate through the original lines (modified with synonyms) and construct the final list
    # But wait, we need to insert items *after* the category header.
    # And if a category doesn't exist, we append it at the end.
    
    # Let's group new items by existing categories vs new categories
    existing_cat_items = {}
    new_cat_items = {}
    
    for cat, items in new_items_by_cat.items():
        cat_lower = cat.lower()
        # Strip brackets if LLM included them
        if cat_lower.startswith("[") and cat_lower.endswith("]"):
            cat_lower = cat_lower[1:-1]
            
        if cat_lower in normalized_cats:
            existing_cat_items[cat_lower] = items
        else:
            new_cat_items[cat] = items # Keep original casing for new category title
            
    final_lines = []
    
    # Helper to find where a category block ends
    # A block ends at the next category header or EOF
    # But we just need to insert *immediately after* the header is usually fine, 
    # or at the end of the block?
    # Cooklang docs say: "[produce] potatoes ... [dairy] milk ..."
    # So we can insert right after the header.
    
    # We'll iterate through new_lines. If we hit a category header, we append it, 
    # then append any new items for that category.
    
    # We need to know which line corresponds to which category header.
    # The 'categories' dict maps name -> original line index.
    # But 'new_lines' has same length as original 'lines' so far.
    
    # Invert categories mapping to line_idx -> cat_name
    line_to_cat = {v: k for k, v in normalized_cats.items()}
    
    for idx, line in enumerate(new_lines):
        final_lines.append(line)
        
        if idx in line_to_cat:
            cat_name = line_to_cat[idx]
            if cat_name in existing_cat_items:
                items = existing_cat_items[cat_name]
                for item in items:
                    final_lines.append(item)
                    print(f"Added to [{cat_name}]: {item}")
                del existing_cat_items[cat_name] # Mark as done
                
    # 3. Add remaining new categories and items
    if new_cat_items:
        if final_lines and final_lines[-1].strip() != "":
            final_lines.append("")
            
        for cat, items in new_cat_items.items():
            # Format category header
            header = f"[{cat}]" if not cat.startswith("[") else cat
            final_lines.append(header)
            print(f"Created new category: {header}")
            for item in items:
                final_lines.append(item)
                print(f"Added to {header}: {item}")
            final_lines.append("")

    return final_lines


def main():
    parser = argparse.ArgumentParser(description="Update aisle.conf with ingredients from a Cooklang file.")
    parser.add_argument("cook_file", type=Path, help="Path to the .cook file.")
    parser.add_argument("--dry-run", action="store_true", help="Show changes but do not write them.")
    
    args = parser.parse_args()
    
    load_dotenv()
    
    if not args.cook_file.exists():
        raise SystemExit(f"File not found: {args.cook_file}")
        
    content = args.cook_file.read_text(encoding="utf-8")
    recipe_ingredients = extract_ingredients(content)
    
    if not recipe_ingredients:
        print("No ingredients found in recipe.")
        return
        
    mapping, lines, categories = parse_aisle_conf()
    known_ingredients = set(mapping.keys())
    
    unknown_ingredients = [i for i in recipe_ingredients if i not in known_ingredients]
    
    if not unknown_ingredients:
        print("All ingredients are already known in aisle.conf.")
        return
        
    print(f"Found {len(unknown_ingredients)} unknown ingredients. Checking for synonyms and categories...")
    
    result = query_openai(list(known_ingredients), list(categories.keys()), unknown_ingredients)
    
    synonyms = result.get("synonyms", {})
    new_items_by_cat = result.get("new_items", {})
    
    if not synonyms and not new_items_by_cat:
        print("No changes recommended.")
        return
        
    new_lines = update_aisle_conf(lines, synonyms, new_items_by_cat, mapping, categories)
    
    if args.dry_run:
        print("\n[Dry Run] No changes written to aisle.conf.")
        return
        
    AISLE_CONF.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\nUpdated {AISLE_CONF}")


if __name__ == "__main__":
    main()
