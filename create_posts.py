#!/usr/bin/env python3
"""Create WordPress posts from playlist metadata files."""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Iterable

import requests
from requests.auth import HTTPBasicAuth


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create WordPress posts from local playlist metadata.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root directory containing category subdirectories (default: data)",
    )
    parser.add_argument(
        "--metadata-file",
        default="playlist_metadata.json",
        help="Metadata filename expected inside each category directory (default: playlist_metadata.json)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the env file with WordPress credentials (default: .env; falls back to wp.env if missing)",
    )
    parser.add_argument(
        "--uploads-path",
        default="wp-content/uploads/2025/youtube2wordpress",
        help="Uploads subpath used to build media URLs for the shortcode",
    )
    parser.add_argument(
        "--status",
        default="draft",
        choices=("draft", "publish", "pending", "future", "private"),
        help="Publication status for created posts (default: draft)",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="Limit processing to specific category directory names (repeatable)",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=15,
        help="Skip value for the dharma_player shortcode (default: 15)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the posts that would be created without calling WordPress",
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
        raise ValueError(f"Metadata in {metadata_path} must be a list of objects")

    entries: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        title = item.get("title")
        description = item.get("description")
        if not video_id or not title:
            continue
        entries.append(
            {
                "id": str(video_id),
                "title": str(title),
                "description": str(description or ""),
            }
        )
    return entries


def ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def build_media_base(site: str, uploads_path: str) -> str:
    site_base = ensure_trailing_slash(site.rstrip("/"))
    uploads = uploads_path.strip("/")
    return site_base + uploads + "/"


def build_shortcode(media_base: str, video_id: str, title: str, skip: int) -> str:
    safe_title = html.escape(title, quote=True)
    audio_url = f"{media_base}{video_id}.mp3"
    image_url = f"{media_base}{video_id}.jpg"
    return (
        f"[dharma_player audio=\"{audio_url}\" image=\"{image_url}\" "
        f"title=\"{safe_title}\" skip=\"{skip}\"]"
    )


def render_description_block(description: str) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in description.splitlines():
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))

    if not paragraphs:
        return "<!-- wp:paragraph -->\n<p></p>\n<!-- /wp:paragraph -->"

    rendered = []
    for paragraph in paragraphs:
        rendered.append("<!-- wp:paragraph -->")
        rendered.append(f"<p>{html.escape(paragraph)}</p>")
        rendered.append("<!-- /wp:paragraph -->")
    return "\n".join(rendered)


def build_post_content(media_base: str, entry: dict[str, str], skip: int) -> str:
    shortcode_block = [
        "<!-- wp:shortcode -->",
        build_shortcode(media_base, entry["id"], entry["title"], skip),
        "<!-- /wp:shortcode -->",
        "",
    ]
    description_block = render_description_block(entry["description"])
    return "\n".join(shortcode_block) + "\n" + description_block


def find_metadata_directories(root: Path, metadata_filename: str, allowed_categories: set[str] | None) -> list[tuple[str, Path]]:
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Data root not found: {root}")

    discovered: list[tuple[str, Path]] = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        category_name = candidate.name
        if allowed_categories and category_name not in allowed_categories:
            continue
        metadata_path = candidate / metadata_filename
        if metadata_path.exists():
            discovered.append((category_name, metadata_path))
        else:
            print(f"Skipping {candidate}: missing {metadata_filename}")
    return discovered


def ensure_category(session: requests.Session, site: str, category_name: str) -> int:
    categories_endpoint = site.rstrip("/") + "/wp-json/wp/v2/categories"
    params = {
        "search": category_name,
        "per_page": 100,
    }
    response = session.get(categories_endpoint, params=params, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to list categories for '{category_name}': {response.status_code} {response.text.strip()}"
        )
    for item in response.json():
        if item.get("name", "").lower() == category_name.lower():
            return int(item["id"])

    payload = {"name": category_name}
    create_response = session.post(categories_endpoint, json=payload, timeout=30)
    if create_response.status_code >= 400:
        raise RuntimeError(
            f"Failed to create category '{category_name}': {create_response.status_code} {create_response.text.strip()}"
        )
    created = create_response.json()
    category_id = created.get("id")
    if not isinstance(category_id, int):
        raise RuntimeError(f"Unexpected response creating category '{category_name}': {created}")
    print(f"Created category '{category_name}' as ID {category_id}")
    return category_id


def create_post(
    session: requests.Session,
    site: str,
    title: str,
    content: str,
    category_id: int,
    status: str,
) -> dict:
    posts_endpoint = site.rstrip("/") + "/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": content,
        "status": status,
        "categories": [category_id],
    }
    response = session.post(posts_endpoint, json=payload, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to create post '{title}': {response.status_code} {response.text.strip()}"
        )
    return response.json()


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        load_env_file(args.env_file)
    except FileNotFoundError:
        # Backward-compat fallback to wp.env when --env-file not found
        fallback = Path("wp.env")
        if fallback.exists():
            load_env_file(fallback)
        else:
            print(f"Env file not found: {args.env_file}", file=sys.stderr)
            sys.exit(1)

    site = os.getenv("WP_BASE_URL", "").strip()
    username = os.getenv("WP_USERNAME", "").strip()
    app_password = os.getenv("WP_APP_PASSWORD", "").strip()

    if not site or not username or not app_password:
        print(
            "Missing WordPress credentials. Ensure WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD are set.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        metadata_dirs = find_metadata_directories(
            args.data_root,
            args.metadata_file,
            set(args.categories) if args.categories else None,
        )
    except FileNotFoundError as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)

    if not metadata_dirs:
        print("No metadata files found to process.")
        return

    uploads_path = os.getenv("WP_UPLOADS_PATH", "").strip() or args.uploads_path
    media_base_override = os.getenv("MEDIA_BASE_URL", "").strip()
    if media_base_override:
        media_base = ensure_trailing_slash(media_base_override.rstrip("/"))
    else:
        media_base = build_media_base(site, uploads_path)

    with requests.Session() as session:
        session.auth = HTTPBasicAuth(username, app_password)

        category_cache: dict[str, int] = {}

        for category_name, metadata_path in metadata_dirs:
            try:
                entries = load_metadata(metadata_path)
            except (FileNotFoundError, ValueError) as err:
                print(str(err), file=sys.stderr)
                continue

            if not entries:
                print(f"No entries in {metadata_path}")
                continue

            if category_name not in category_cache and not args.dry_run:
                try:
                    category_cache[category_name] = ensure_category(session, site, category_name)
                except RuntimeError as err:
                    print(str(err), file=sys.stderr)
                    continue
            elif category_name not in category_cache:
                category_cache[category_name] = -1  # placeholder for dry-run

            print(f"Processing category '{category_name}' ({len(entries)} posts)")
            for entry in entries:
                content = build_post_content(media_base, entry, args.skip)
                if args.dry_run:
                    audio_name = f"{entry['id']}.mp3"
                    image_name = f"{entry['id']}.jpg"
                    audio_path = metadata_path.parent / audio_name
                    image_path = metadata_path.parent / image_name
                    audio_status = "present" if audio_path.exists() else "missing"
                    image_status = "present" if image_path.exists() else "missing"
                    print(f"- Would create post '{entry['title']}'")
                    print(f"  audio: {audio_name} [{audio_status}]")
                    print(f"  image: {image_name} [{image_status}]")
                    continue

                category_id = category_cache[category_name]
                if category_id < 0:
                    try:
                        category_id = ensure_category(session, site, category_name)
                        category_cache[category_name] = category_id
                    except RuntimeError as err:
                        print(str(err), file=sys.stderr)
                        break

                try:
                    response = create_post(session, site, entry["title"], content, category_id, args.status)
                except RuntimeError as err:
                    print(str(err), file=sys.stderr)
                    continue

                print(
                    f"- Created post '{entry['title']}' as ID {response.get('id', 'unknown')} "
                    f"({response.get('link', 'no link')})"
                )


if __name__ == "__main__":
    main()
