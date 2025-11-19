#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: .env file not found at $ENV_FILE" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

: "${DEPLOY_HOST:?Need to set DEPLOY_HOST in .env}"
: "${DEPLOY_USER:?Need to set DEPLOY_USER in .env}"
: "${DEPLOY_REPO_DIR:?Need to set DEPLOY_REPO_DIR in .env}"

echo "Deploying to $DEPLOY_HOST as $DEPLOY_USER (repo: $DEPLOY_REPO_DIR)"

ssh "${DEPLOY_USER}@${DEPLOY_HOST}" <<EOF
set -euo pipefail
cd "$DEPLOY_REPO_DIR"
git pull
EOF

