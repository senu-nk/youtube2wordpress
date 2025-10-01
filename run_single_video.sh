#!/usr/bin/env bash
# Run the workflow for a single YouTube video: download, upload, and create posts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
PRIMARY_ENV_FILE="$REPO_ROOT/.env"
FALLBACK_ENV_FILE="$REPO_ROOT/wp.env"
if [[ -f "$PRIMARY_ENV_FILE" ]]; then
    ENV_FILE="$PRIMARY_ENV_FILE"
elif [[ -f "$FALLBACK_ENV_FILE" ]]; then
    ENV_FILE="$FALLBACK_ENV_FILE"
else
    ENV_FILE="$PRIMARY_ENV_FILE"
fi
DATA_DIR="$REPO_ROOT/data"
SKIP_DOWNLOAD=false

usage() {
    cat <<USAGE
Usage: ${0##*/} [--skip-download]

Options:
  --skip-download  Skip downloading the video and reuse existing assets.
  -h, --help       Show this help message and exit.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-download)
            SKIP_DOWNLOAD=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

require_file() {
    local path="$1"
    local message="$2"
    if [[ ! -e "$path" ]]; then
        echo "$message" >&2
        exit 1
    fi
}

main() {
    cd "$REPO_ROOT"

    require_file "$VENV_DIR/bin/activate" "Virtual environment not found at $VENV_DIR. Run setup.sh first."
    require_file "$ENV_FILE" "Missing environment file at $ENV_FILE."

    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"

    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a

    mkdir -p "$DATA_DIR"

    read -rp "Enter YouTube video URL: " VIDEO_URL
    if [[ -z "${VIDEO_URL// }" ]]; then
        echo "Video URL cannot be empty." >&2
        exit 1
    fi

    read -rp "Enter playlist/category name: " PLAYLIST_NAME
    if [[ -z "${PLAYLIST_NAME// }" ]]; then
        echo "Playlist/category name cannot be empty." >&2
        exit 1
    fi

    SANITIZED_NAME=$(python3 - "$PLAYLIST_NAME" <<'PY'
import sys
from download_playlist import sanitize_path_segment
print(sanitize_path_segment(sys.argv[1]), end="")
PY
)

    if [[ -z "${SANITIZED_NAME// }" ]]; then
        echo "Failed to derive a directory name from the supplied playlist/category." >&2
        exit 1
    fi

    TARGET_DIR="$DATA_DIR/$SANITIZED_NAME"

    if [[ "$SKIP_DOWNLOAD" == false ]]; then
        success=false
        for attempt in 1 2 3; do
            echo "[download] Attempt $attempt/3"
            if python3 "$REPO_ROOT/download_single_video.py" "$VIDEO_URL" "$PLAYLIST_NAME"; then
                success=true
                break
            fi
            echo "Download attempt $attempt failed." >&2
            sleep 1
        done
        if [[ "$success" == false ]]; then
            echo "Failed to download the requested video after 3 attempts." >&2
            exit 1
        fi
    else
        echo "Skipping download step (existing data will be used)."
    fi

    if [[ ! -d "$TARGET_DIR" ]]; then
        echo "Expected data directory not found at $TARGET_DIR." >&2
        exit 1
    fi

    pushd "$TARGET_DIR" >/dev/null

    shopt -s nullglob
    files=(./*)
    if [[ ${#files[@]} -eq 0 ]]; then
        echo "No files found to upload in $TARGET_DIR." >&2
        exit 1
    fi

    python3 "$REPO_ROOT/upload_to_r2.py" "$TARGET_DIR"
    shopt -u nullglob

    popd >/dev/null

    python3 "$REPO_ROOT/create_posts.py" --env-file "$ENV_FILE" --category "$SANITIZED_NAME"
}

main "$@"
