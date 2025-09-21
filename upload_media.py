#!/usr/bin/env python3
"""Upload downloaded playlist media (audio + thumbnails) to a WordPress site."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError as err:
    print("The 'requests' library is required. Install it with 'pip install requests'.", file=sys.stderr)
    raise


# Supported file extensions mapped to MIME types.
AUDIO_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}
IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@dataclass
class MediaTarget:
    """Represents a file that should be uploaded along with its metadata."""

    video_id: str
    path: Path
    kind: str  # "audio" or "thumbnail"
    mime_type: str
    title: str
    description: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload playlist audio and thumbnails to a WordPress site.")
    parser.add_argument("source", type=Path, help="Directory containing downloaded media and metadata JSON")
    parser.add_argument(
        "--metadata-file",
        default="playlist_metadata.json",
        help="Metadata filename inside the source directory (default: playlist_metadata.json)",
    )
    parser.add_argument("--site", help="Base URL of the WordPress site (e.g. https://example.com)")
    parser.add_argument("--username", help="WordPress username")
    parser.add_argument("--app-password", help="WordPress application password")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to a .env file containing WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD (default: wp.env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be uploaded without sending them to WordPress",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Ignore items that are missing either audio or thumbnail instead of aborting",
    )
    return parser.parse_args(argv)


def load_env_file(path: Path) -> None:
    """Populate os.environ with key/value pairs from a .env style file."""

    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def load_metadata(metadata_path: Path) -> list[dict[str, str]]:
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"Could not parse {metadata_path}: {err}") from err

    if not isinstance(payload, list):
        raise ValueError("Metadata JSON must contain a list of entries")

    entries: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        if not video_id:
            continue
        entries.append(
            {
                "id": video_id,
                "title": item.get("title", ""),
                "description": item.get("description", ""),
            }
        )
    return entries


def iter_media_targets(source_dir: Path, metadata: list[dict[str, str]], skip_missing: bool) -> Iterator[MediaTarget]:
    for record in metadata:
        video_id = record["id"]
        title = record.get("title", "")
        description = record.get("description", "")

        audio_path = None
        for extension, mime_type in AUDIO_MIME_TYPES.items():
            candidate = source_dir / f"{video_id}{extension}"
            if candidate.exists():
                audio_path = (candidate, mime_type)
                break

        thumbnail_path = None
        for extension, mime_type in IMAGE_MIME_TYPES.items():
            candidate = source_dir / f"{video_id}{extension}"
            if candidate.exists():
                thumbnail_path = (candidate, mime_type)
                break

        if audio_path is None or thumbnail_path is None:
            message = (
                f"Missing files for {video_id}: "
                f"audio={'present' if audio_path else 'missing'}, thumbnail={'present' if thumbnail_path else 'missing'}"
            )
            if skip_missing:
                print(message, file=sys.stderr)
                continue
            raise FileNotFoundError(message)

        audio_file, audio_mime = audio_path
        thumb_file, thumb_mime = thumbnail_path

        yield MediaTarget(video_id=video_id, path=audio_file, kind="audio", mime_type=audio_mime, title=title, description=description)
        yield MediaTarget(video_id=video_id, path=thumb_file, kind="thumbnail", mime_type=thumb_mime, title=title, description=description)


def upload_media_file(site: str, auth: HTTPBasicAuth, target: MediaTarget) -> dict:
    media_endpoint = site.rstrip("/") + "/wp-json/wp/v2/media"
    headers = {
        "Content-Disposition": f'attachment; filename="{target.path.name}"',
        "Content-Type": target.mime_type,
        "Slug": target.path.stem,
    }

    with target.path.open("rb") as handle:
        response = requests.post(
            media_endpoint,
            headers=headers,
            data=handle,
            auth=auth,
            timeout=120,
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to upload {target.path.name} ({target.kind}): {response.status_code} {response.text.strip()}"
        )

    return response.json()


def update_media_metadata(site: str, auth: HTTPBasicAuth, media_id: int, target: MediaTarget) -> None:
    payload = {
        "title": target.title or target.path.stem,
    }
    if target.kind == "thumbnail" and target.description:
        payload["alt_text"] = target.title or target.description[:120]

    # WordPress expects POST to update existing media items.
    media_endpoint = site.rstrip("/") + f"/wp-json/wp/v2/media/{media_id}"

    response = requests.post(media_endpoint, json=payload, auth=auth, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to update metadata for media {media_id}: {response.status_code} {response.text.strip()}"
        )


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)

    env_file = args.env_file
    if env_file is None:
        default_env = Path("wp.env")
        if default_env.exists():
            env_file = default_env
    if env_file:
        try:
            load_env_file(env_file)
            print(f"Loaded credentials from {env_file}")
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    site = args.site or os.getenv("WP_BASE_URL")
    username = args.username or os.getenv("WP_USERNAME")
    app_password = args.app_password or os.getenv("WP_APP_PASSWORD")

    if not site or not username or not app_password:
        print(
            "WordPress credentials are required. Provide --site/--username/--app-password or set "
            "WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD.",
            file=sys.stderr,
        )
        sys.exit(1)

    source_dir = args.source.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        print(f"Source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    metadata_path = Path(args.metadata_file)
    if not metadata_path.is_absolute():
        metadata_path = source_dir / metadata_path

    try:
        metadata = load_metadata(metadata_path)
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if not metadata:
        print(f"No entries found in metadata file {metadata_path}", file=sys.stderr)
        sys.exit(1)

    auth = HTTPBasicAuth(username, app_password)

    targets = list(iter_media_targets(source_dir, metadata, args.skip_missing))

    if not targets:
        print("Nothing to upload. All items were skipped or missing.")
        return

    for target in targets:
        print(f"Preparing {target.kind} for {target.video_id}: {target.path}")

    if args.dry_run:
        print("Dry run complete. No files were uploaded.")
        return

    successes = 0
    for target in targets:
        try:
            response = upload_media_file(site, auth, target)
            media_id = response.get("id")
            if isinstance(media_id, int):
                try:
                    update_media_metadata(site, auth, media_id, target)
                except RuntimeError as meta_err:
                    print(str(meta_err), file=sys.stderr)
            print(
                f"Uploaded {target.kind} for {target.video_id} as attachment ID {response.get('id', 'unknown')}"
            )
            successes += 1
        except Exception as err:  # noqa: BLE001 - surface upload failure to CLI
            print(str(err), file=sys.stderr)

    print(f"Upload complete. {successes} files succeeded, {len(targets) - successes} failed.")


if __name__ == "__main__":
    main()
