#!/usr/bin/env bash
set -euo pipefail

json_only=false
cook_source=""
update_metadata=false
metadata_removed=false

usage() {
  cat <<'EOF'
Usage: download_cook_image.sh [--json-only] [--update] path/to/recipe.cook

Download the image referenced in the .cook metadata and write a JPG that
matches the recipe filename. With --json-only the script suppresses human
output and emits a single JSON object. Adding --update strips the image
metadata once the download succeeds.
EOF
}

log() {
  # simple logging wrapper that can be suppressed for JSON mode
  if [[ "$json_only" == "false" ]]; then
    printf '%s\n' "$*"
  fi
}

remove_image_metadata_tag() {
  local cook="$1"
  python3 - "$cook" <<'PY'
import pathlib, sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
lines = text.splitlines()

in_meta = False
meta_done = False
removed = False
new_lines = []

for line in lines:
    stripped = line.strip()
    if stripped == '---':
        if not in_meta and not meta_done:
            in_meta = True
        elif in_meta and not meta_done:
            meta_done = True
            in_meta = False
        new_lines.append(line)
        continue
    if in_meta and not meta_done:
        if line.lstrip().lower().startswith('image:'):
            removed = True
            continue
    new_lines.append(line)

if removed:
    content = "\n".join(new_lines)
    if text.endswith("\n"):
        content += "\n"
    path.write_text(content)
print("removed" if removed else "none")
PY
}

json_escape() {
  local input="$1"
  input="${input//\\/\\\\}"
  input="${input//$'\n'/\\n}"
  input="${input//$'\r'/\\r}"
  input="${input//\"/\\\"}"
  input="${input//$'\t'/\\t}"
  printf '%s' "$input"
}

json_error() {
  local msg
  msg=$(json_escape "$1")
  printf '{"status":"error","message":"%s"}\n' "$msg"
}

json_success() {
  local converted="$1"
  local downloaded="$2"
  local dest="$3"
  local image_url="$4"
  local cook="$5"
  local metadata_removed="$6"

  local cook_escaped image_escaped downloaded_escaped dest_escaped
  cook_escaped=$(json_escape "$cook")
  image_escaped=$(json_escape "$image_url")
  downloaded_escaped=$(json_escape "$downloaded")
  dest_escaped=$(json_escape "$dest")
  printf '{"status":"success","cook_file":"%s","image_url":"%s","downloaded_file":"%s","destination":"%s","converted":%s,"metadata_removed":%s}\n' \
    "$cook_escaped" "$image_escaped" "$downloaded_escaped" "$dest_escaped" "$converted" "$metadata_removed"
}

error_exit() {
  local msg="$1"
  if [[ "$json_only" == "true" ]]; then
    json_error "$msg"
  else
    printf 'ERROR: %s\n' "$msg" >&2
  fi
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error_exit "required command '$1' is not available"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json-only)
      json_only=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --update)
      update_metadata=true
      shift
      ;;
    *)
      if [[ -z "$cook_source" ]]; then
        cook_source="$1"
      else
        error_exit "unexpected argument: $1"
      fi
      shift
      ;;
  esac
done

if [[ -z "$cook_source" ]]; then
  usage
  exit 0
fi

require_command curl
require_command magick
require_command python3

cook_path="$(realpath "$cook_source")"
if [[ ! -f "$cook_path" ]]; then
  error_exit "cook file '$cook_path' does not exist"
fi

metadata=$(awk '
  BEGIN { in_meta = 0 }
  /^---$/ {
    if (in_meta == 0) { in_meta = 1; next }
    if (in_meta == 1) { exit }
  }
  in_meta == 1 { print }
' "$cook_path")

image_line=$(printf '%s\n' "$metadata" | awk '/^image[[:space:]]*:/ { print substr($0, index($0, ":") + 1); exit }')
image_url=$(printf '%s' "$image_line" | sed -E "s/^[[:space:]\"']+//;s/[[:space:]\"']+$//")

if [[ -z "$image_url" ]]; then
  error_exit "no 'image' metadata found in $cook_path"
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

url_path="${image_url%%\?*}"
base_name=$(basename "$url_path")
if [[ -z "$base_name" || "$base_name" == "/" ]]; then
  base_name="image"
fi
source_file="$tmpdir/$base_name"

log "Downloading $image_url..."
if ! curl -fL -o "$source_file" "$image_url"; then
  error_exit "failed to download $image_url"
fi
log "Downloaded to $source_file"

dest_dir=$(dirname "$cook_path")
dest="$dest_dir/$(basename "${cook_path%.cook}.jpg")"

ext="${base_name##*.}"
lower_ext=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
case "$lower_ext" in
  jpg|jpeg)
    converted=false
    log "Copying JPG to $dest"
    cp -f "$source_file" "$dest"
    ;;
  *)
    converted=true
    if [[ -f "$dest" ]]; then
      rm -f "$dest"
    fi
    log "Converting $source_file to JPG at $dest"
    magick "$source_file" "$dest"
    ;;
esac

if [[ "$update_metadata" == "true" ]]; then
  metadata_result=$(remove_image_metadata_tag "$cook_path")
  metadata_result=${metadata_result//$'\n'/}
  if [[ "$metadata_result" == "removed" ]]; then
    metadata_removed=true
    log "Removed 'image' metadata from $cook_path"
  else
    log "No 'image' metadata tag found to remove from $cook_path"
  fi
fi

if [[ "$json_only" == "true" ]]; then
  json_success "$converted" "$source_file" "$dest" "$image_url" "$cook_path" "$metadata_removed"
else
  log "Image available at: $dest (converted=$converted)"
fi

