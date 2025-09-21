#!/usr/bin/env python3
"""Download a YouTube playlist into audio tracks, thumbnails, and metadata."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
from urllib import error, request

try:
    import yt_dlp  # type: ignore
    from yt_dlp.utils import DownloadError  # type: ignore
except ImportError as err:
    print("yt_dlp is required to run this script. Install it with 'pip install yt-dlp'.", file=sys.stderr)
    raise

THUMBNAIL_CANDIDATES = (
    "maxresdefault.jpg",
    "sddefault.jpg",
    "hqdefault.jpg",
    "mqdefault.jpg",
    "default.jpg",
)
INVALID_PATH_CHARS = set('<>:"/\\|?*')


@dataclass
class VideoMetadata:
    """Metadata captured for each playlist entry."""

    video_id: str
    title: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.video_id,
            "title": self.title,
            "description": self.description,
        }


def load_existing_metadata(metadata_path: Path) -> dict[str, VideoMetadata]:
    """Return existing metadata from disk keyed by video ID."""

    if not metadata_path.exists():
        return {}

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        print(f"Could not parse existing metadata file {metadata_path}: {err}", file=sys.stderr)
        return {}

    collected: dict[str, VideoMetadata] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            if not video_id:
                continue
            title = item.get("title") or ""
            description = item.get("description") or ""
            collected[video_id] = VideoMetadata(video_id=video_id, title=title, description=description)
    return collected


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download an entire YouTube playlist to audio, thumbnails, and metadata.")
    parser.add_argument("playlist", help="YouTube playlist URL or ID to download")
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("data"),
        help="Base directory where the playlist folder should be created (default: ./data)",
    )
    parser.add_argument(
        "--metadata-file",
        default="playlist_metadata.json",
        help="Filename for the playlist metadata JSON (default: playlist_metadata.json)",
    )
    parser.add_argument(
        "--cookies-file",
        type=Path,
        help="Path to cookies file exported from a browser for authenticated YouTube access",
    )
    return parser.parse_args(argv)


def sanitize_path_segment(name: str, fallback: str = "playlist") -> str:
    sanitized = "".join("_" if ch in INVALID_PATH_CHARS else ch for ch in name).strip()
    sanitized = re.sub(r"\s+", " ", sanitized)
    sanitized = sanitized.rstrip(".")  # avoid trailing dots on Windows
    sanitized = sanitized or fallback
    return sanitized


def ensure_output_dir(base_dir: Path, playlist_title: str, playlist_id: str) -> Path:
    target_name = sanitize_path_segment(playlist_title, fallback=f"playlist_{playlist_id}")
    destination = base_dir / target_name
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def download_thumbnail(video_id: str, destination: Path) -> None:
    base_url = f"https://i.ytimg.com/vi/{video_id}/"
    for candidate in THUMBNAIL_CANDIDATES:
        url = base_url + candidate
        try:
            with request.urlopen(url) as response:  # noqa: S310 - URL built from trusted template
                if response.status != 200:
                    continue
                data = response.read()
                destination.write_bytes(data)
                return
        except error.HTTPError as http_err:
            if http_err.code == 404:
                continue
            raise
    raise RuntimeError(f"Failed to fetch a thumbnail for video {video_id}")


def extract_playlist_info(playlist: str, cookie_file: Path | None) -> dict:
    """Fetch playlist metadata without downloading media."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "ignoreerrors": True,
    }
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(playlist, download=False)
    if not info:
        raise RuntimeError("Could not retrieve playlist information.")
    if "entries" not in info or not info["entries"]:
        raise RuntimeError("Playlist contains no downloadable entries.")
    return info


def download_audio(video_url: str, target_dir: Path, cookie_file: Path | None) -> None:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
        "keepvideo": False,
        "overwrites": False,
    }
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([video_url])


def collect_metadata_and_assets(
    entries: List[dict | None],
    target_dir: Path,
    existing_metadata: dict[str, VideoMetadata],
    cookie_file: Path | None,
) -> List[VideoMetadata]:
    collected: List[VideoMetadata] = []
    for position, entry in enumerate(entries, start=1):
        if entry is None:
            print(f"Skipping entry at position {position}: no data returned", file=sys.stderr)
            continue

        video_url = entry.get("webpage_url") or entry.get("url")
        if not video_url:
            print(f"Skipping entry at position {position}: missing URL", file=sys.stderr)
            continue

        video_id = entry.get("id")
        if not video_id:
            print(f"Skipping entry at position {position}: missing video ID", file=sys.stderr)
            continue

        audio_path = target_dir / f"{video_id}.mp3"
        thumbnail_path = target_dir / f"{video_id}.jpg"
        existing_entry = existing_metadata.get(video_id)
        if existing_entry:
            if not audio_path.exists() or not thumbnail_path.exists():
                print(
                    f"Skipping {video_id}: metadata present but media files missing",
                    file=sys.stderr,
                )
            else:
                print(f"Skipping {video_id}: already present in metadata")
            collected.append(existing_entry)
            continue

        ydl_opts = {"quiet": True}
        if cookie_file:
            ydl_opts["cookiefile"] = str(cookie_file)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except DownloadError as err:
            print(f"Skipping {video_url}: {err}", file=sys.stderr)
            continue

        audio_path = target_dir / f"{video_id}.mp3"
        if audio_path.exists():
            print(f"Audio already exists for {video_id}, skipping download")
        else:
            print(f"Downloading audio for {video_id}")
            try:
                download_audio(video_url, target_dir, cookie_file)
            except DownloadError as err:
                print(f"Failed to download audio for {video_id}: {err}", file=sys.stderr)
                continue
            except RuntimeError as err:
                print(f"Failed to download audio for {video_id}: {err}", file=sys.stderr)
                continue

        thumbnail_path = target_dir / f"{video_id}.jpg"
        if thumbnail_path.exists():
            print(f"Thumbnail already exists for {video_id}, skipping download")
        else:
            print(f"Downloading thumbnail for {video_id}")
            try:
                download_thumbnail(video_id, thumbnail_path)
            except RuntimeError as err:
                print(f"Failed to download thumbnail for {video_id}: {err}", file=sys.stderr)
                thumbnail_path.unlink(missing_ok=True)
                continue

        title = info.get("title") or ""
        description = info.get("description") or ""
        metadata_entry = VideoMetadata(video_id=video_id, title=title, description=description)
        collected.append(metadata_entry)
        existing_metadata[video_id] = metadata_entry
    return collected


def write_metadata_file(metadata: List[VideoMetadata], destination: Path) -> None:
    payload = [item.to_dict() for item in metadata]
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])

    if args.cookies_file and not args.cookies_file.exists():
        print(f"Cookies file not found: {args.cookies_file}", file=sys.stderr)
        sys.exit(1)

    try:
        playlist_info = extract_playlist_info(args.playlist, args.cookies_file)
    except Exception as exc:  # noqa: BLE001 - surface helpful failure to CLI
        print(f"Failed to get playlist details: {exc}", file=sys.stderr)
        sys.exit(1)

    playlist_title = playlist_info.get("title") or ""
    playlist_id = playlist_info.get("id") or "playlist"

    target_dir = ensure_output_dir(args.output_dir.resolve(), playlist_title, playlist_id)

    print(f"Saving playlist to {target_dir}")

    metadata_path = target_dir / args.metadata_file
    existing_metadata = load_existing_metadata(metadata_path)

    metadata = collect_metadata_and_assets(
        playlist_info["entries"],
        target_dir,
        existing_metadata,
        args.cookies_file,
    )
    write_metadata_file(metadata, metadata_path)
    print(f"Metadata written to {metadata_path}")


if __name__ == "__main__":
    main()
