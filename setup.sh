#!/usr/bin/env bash
# Bootstrap the local environment for youtube2wordpress.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ_FILE="$REPO_ROOT/requirements.txt"
VENV_DIR="$REPO_ROOT/.venv"

SYSTEM_PACKAGES=(python3 python3-venv python3-pip ffmpeg)

have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

install_with_apt() {
    sudo apt-get update
    sudo apt-get install -y "${SYSTEM_PACKAGES[@]}"
}

install_with_brew() {
    brew update
    brew install "${SYSTEM_PACKAGES[@]}"
}

ensure_system_packages() {
    echo "Installing system packages (python3, venv, pip, ffmpeg) if needed..."
    if have_cmd apt-get; then
        install_with_apt
    elif have_cmd brew; then
        install_with_brew
    else
        echo "Warning: Unsupported package manager. Please install: ${SYSTEM_PACKAGES[*]}" >&2
    fi
}

create_virtualenv() {
    if [[ -d "$VENV_DIR" ]]; then
        echo "Virtual environment already exists at $VENV_DIR"
    else
        echo "Creating virtual environment in $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    fi
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
    python -m pip install --upgrade pip
}

install_requirements() {
    if [[ ! -f "$REQ_FILE" ]]; then
        echo "Requirements file not found at $REQ_FILE" >&2
        exit 1
    fi
    echo "Installing Python dependencies from $REQ_FILE"
    python -m pip install -r "$REQ_FILE"
}

main() {
    ensure_system_packages
    create_virtualenv
    install_requirements
    echo "Setup complete. Activate the virtualenv with: source $VENV_DIR/bin/activate"
}

main "$@"
