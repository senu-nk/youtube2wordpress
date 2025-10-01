#!/usr/bin/env python3
"""Download a single YouTube video into the youtube2wordpress data layout."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

try:
    from yt_dlp.utils import DownloadError  # type: ignore
    import yt_dlp  # type: ignore
except ImportError as err:  # pragma: no cover - surfaced to CLI
    print("yt_dlp is required to run this script. Install it with 'pip install yt-dlp'.", file=sys.stderr)
    raise

from download_playlist import (  # type: ignore
    collect_metadata_and_assets,
    load_existing_metadata,
    sanitize_path_segment,
    write_metadata_file,
)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a single YouTube video to audio, thumbnail, and metadata.")
    parser.add_argument("video_url", help="Full YouTube video URL or ID to download")
    parser.add_argument("playlist_name", help="Category/playlist name used to store the downloaded assets")
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("data"),
        help="Base directory where the category folder should be created (default: ./data)",
    )
    parser.add_argument(
        "--metadata-file",
        default="playlist_metadata.json",
        help="Filename for the metadata JSON (default: playlist_metadata.json)",
    )
    parser.add_argument(
        "--cookies-file",
        type=Path,
        help="Path to cookies file exported from a browser for authenticated YouTube access",
    )
    return parser.parse_args(argv)


def fetch_video_info(video_url: str, cookie_file: Path | None) -> dict:
    opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    if not info:
        raise RuntimeError("Could not retrieve video information.")
    return info


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])

    if args.cookies_file and not args.cookies_file.exists():
        print(f"Cookies file not found: {args.cookies_file}", file=sys.stderr)
        sys.exit(1)

    name = args.playlist_name.strip()
    if not name:
        print("Playlist name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    category_name = sanitize_path_segment(name)
    if not category_name:
        print("Playlist name resolved to an empty directory name.", file=sys.stderr)
        sys.exit(1)

    base_dir = args.output_dir.resolve()
    target_dir = base_dir / category_name
    target_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = target_dir / args.metadata_file
    existing_metadata = load_existing_metadata(metadata_path)

    try:
        video_info = fetch_video_info(args.video_url, args.cookies_file)
    except DownloadError as err:
        print(f"Failed to download metadata for {args.video_url}: {err}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - surface helpful context to CLI
        print(f"Failed to retrieve video details: {exc}", file=sys.stderr)
        sys.exit(1)

    if "webpage_url" not in video_info or not video_info.get("webpage_url"):
        video_info["webpage_url"] = args.video_url

    metadata_entries = collect_metadata_and_assets(
        [video_info],
        target_dir,
        existing_metadata,
        args.cookies_file,
    )

    if not metadata_entries:
        print("No metadata collected for the provided video. Nothing to do.")
        return

    write_metadata_file(metadata_entries, metadata_path)
    print(f"Saved assets and metadata to {target_dir}")


if __name__ == "__main__":
    main()
