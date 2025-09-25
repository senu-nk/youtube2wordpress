#!/usr/bin/env bash
# Orchestrate the youtube2wordpress workflow: download, upload, create posts.
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
  --skip-download  Skip running download_playlist.py and reuse existing data.
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

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command '$1' not found in PATH." >&2
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

    if [[ "$SKIP_DOWNLOAD" == false ]]; then
        read -rp "Enter YouTube playlist URL: " PLAYLIST_URL
        if [[ -z "${PLAYLIST_URL// }" ]]; then
            echo "Playlist URL cannot be empty." >&2
            exit 1
        fi

        for attempt in 1 2 3; do
            echo "[download] Attempt $attempt/3"
            python3 "$REPO_ROOT/download_playlist.py" "$PLAYLIST_URL"
        done
    else
        echo "Skipping download step (existing data will be used)."
    fi

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

    python3 "$REPO_ROOT/upload_to_r2.py" "$TARGET_DIR"
    shopt -u nullglob

    popd >/dev/null

    python3 "$REPO_ROOT/create_posts.py" --env-file "$ENV_FILE"
}

main "$@"
