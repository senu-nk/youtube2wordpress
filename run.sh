#!/usr/bin/env bash
# Orchestrate the youtube2wordpress workflow: download, upload, create posts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
ENV_FILE="$REPO_ROOT/wp.env"
DATA_DIR="$REPO_ROOT/data"
REMOTE_PATH="u2961-etg8zonmhuba@ssh.kaalaawatharanaa2.sg-host.com:/home/u2961-etg8zonmhuba/www/kaalaawatharanaa2.sg-host.com/public_html/wp-content/uploads/2025/youtube2wordpress"

require_file() {
    local path="$1"
    local message="$2"
    if [[ ! -e "$path" ]]; then
        echo "$message" >&2
        exit 1
    fi
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command '$1' not found in PATH." >&2
        exit 1
    fi
}

main() {
    cd "$REPO_ROOT"

    require_file "$VENV_DIR/bin/activate" "Virtual environment not found at $VENV_DIR. Run setup.sh first."
    require_file "$ENV_FILE" "Missing WordPress credentials file at $ENV_FILE."
    require_command scp

    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"

    read -rp "Enter YouTube playlist URL: " PLAYLIST_URL
    if [[ -z "${PLAYLIST_URL// }" ]]; then
        echo "Playlist URL cannot be empty." >&2
        exit 1
    fi

    mkdir -p "$DATA_DIR"

    for attempt in 1 2 3; do
        echo "[download] Attempt $attempt/3"
        python3 "$REPO_ROOT/download_playlist.py" "$PLAYLIST_URL"
    done

    if [[ ! -d "$DATA_DIR" ]]; then
        echo "Data directory not found at $DATA_DIR." >&2
        exit 1
    fi

    mapfile -t CATEGORY_DIRS < <(find "$DATA_DIR" -mindepth 1 -maxdepth 1 -type d -print | sort)
    if [[ ${#CATEGORY_DIRS[@]} -ne 1 ]]; then
        echo "Expected exactly one playlist directory inside $DATA_DIR but found ${#CATEGORY_DIRS[@]}:" >&2
        for dir in "${CATEGORY_DIRS[@]}"; do
            echo " - $dir" >&2
        done
        exit 1
    fi

    TARGET_DIR="${CATEGORY_DIRS[0]}"
    pushd "$TARGET_DIR" >/dev/null

    shopt -s nullglob
    files=(./*)
    if [[ ${#files[@]} -eq 0 ]]; then
        echo "No files found to upload in $TARGET_DIR." >&2
        exit 1
    fi

    scp -P 18765 -r "${files[@]}" "$REMOTE_PATH"
    shopt -u nullglob

    popd >/dev/null

    python3 "$REPO_ROOT/create_posts.py"
}

main "$@"
