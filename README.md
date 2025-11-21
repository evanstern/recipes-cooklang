# recipes-cooklang
This repository collects Cooklang-formatted recipes organized by meal category and tooling to keep
the tag index synchronized and to suggest normalized tags for new recipes.

## Repository structure
```
= README and helper docs
config/                      # shared pantry/aisle configuration
tags-index.md                # generated global tag index
Welcome.md

bread/                       # loaf and baked-good recipes
desserts/                    # sweeter recipes
entrees/                     # main-course recipes, often with images
salad/                       # salads and dressings
sides/                       # side dishes
soup/                        # soups and stews

scripts/                     # utility scripts described below
requirements.txt             # Python dependencies for the scripts
workspace.code-workspace     # VS Code workspace definition
```

Every `.cook` file contains Cooklang front matter that includes `tags:` (a comma-separated
list or YAML list of tags) and the recipe body. The `config/` directory stores shared data
used elsewhere, and `tags-index.md` mirrors the repository-wide tag usage.

## Included scripts
- `scripts/generate_tag_index.py`: Walks every `.cook` file, extracts the `tags` front matter,
  counts occurrences, and rewrites `tags-index.md` sorted by popularity. Run it after editing
  recipes to keep the index current.
- `scripts/suggest_tags.py`: Sends a single recipe to OpenAI (uses `gpt-4o-mini`) and prints
  normalized tag recommendations (or overwrites the recipe’s `tags:` line with `--write`). It
  reads `.env` if present, requires `OPENAI_API_KEY`, and relies on `tags-index.md` to show
  popular tags in the prompt. Example:
  ```
  python scripts/suggest_tags.py entrees/pasta-pomodoro.cook
  OPENAI_API_KEY=... python scripts/suggest_tags.py --json-only --write soup/lentil-soup.cook
  ```
- `scripts/download_cook_image.sh`: Downloads the `image:` metadata URL from a single `.cook`
  recipe, saves it beside the source recipe as `<recipe-name>.jpg`, converts as needed via
  `magick`, and optionally strips the metadata with `--update`. Run with `--json-only` for
  machine-readable output.
- `scripts/deploy.sh`: Simple SSH deploy to a host/dir defined in `.env` (`DEPLOY_HOST`,
  `DEPLOY_USER`, `DEPLOY_REPO_DIR`). It sources `.env` and runs `git pull` on the remote.

## Setup & dependency installation
1. **Install Python 3.11+** if needed (macOS Homebrew example):
   ```sh
   brew install python
   python3 --version
   ```
2. **Create and activate a virtual environment** inside the repo:
   ```sh
   cd /Users/evanstern/projects/evanstern/recipes-cooklang
   python3 -m venv .venv
   source .venv/bin/activate
   ```
   On fish: `source .venv/bin/activate.fish`. On Windows (PowerShell): `.venv\\Scripts\\Activate.ps1`.
3. **Upgrade pip and install dependencies**:
   ```sh
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. **Set up secrets/environment** as needed:
   - `scripts/suggest_tags.py` requires `OPENAI_API_KEY`.
   - `scripts/deploy.sh` needs `.env` with `DEPLOY_HOST`, `DEPLOY_USER`, and `DEPLOY_REPO_DIR`.

## Running the scripts
- Regenerate the tag index after recipe edits: `python scripts/generate_tag_index.py`
- Suggest tags for a recipe: see the `scripts/suggest_tags.py` examples above.
- Download/update recipe images: `./scripts/download_cook_image.sh [--json-only] [--update] path/to/recipe.cook`
- Run the deploy helper with its `.env` configured: `scripts/deploy.sh`.

When done working, `deactivate` to leave the virtual environment.