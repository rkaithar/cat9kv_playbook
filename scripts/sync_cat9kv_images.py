#!/usr/bin/env python3
"""Sync Cat9kV image files from an HTTP directory listing.

Only files whose basename starts with "cat9kv-" are eligible. By default the
script also limits downloads to OVA and ISO images.
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import http.client
import os
from pathlib import Path
import posixpath
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_EXTENSIONS = (".iso", ".ova")
DEFAULT_PREFIX = "cat9kv-"
DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT = 30
RETRY_EXCEPTIONS = (
    OSError,
    TimeoutError,
    http.client.HTTPException,
    socket.timeout,
    urllib.error.URLError,
)


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download only Cat9kV image files from an HTTP directory listing."
    )
    parser.add_argument("source_url", help="HTTP directory URL, for example http://10.76.90.102/")
    parser.add_argument(
        "--dest",
        default="/srv/cat9kv/images",
        help="Destination directory. Default: /srv/cat9kv/images",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help='Required filename prefix. Default: "cat9kv-"',
    )
    parser.add_argument(
        "--extensions",
        default=",".join(DEFAULT_EXTENSIONS),
        help="Comma-separated allowed extensions. Default: .iso,.ova",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show selected files without downloading.")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove destination ISO/OVA files that do not match the selected Cat9kV file set.",
    )
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="HTTP retry count. Default: 3")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds. Default: 30")
    return parser.parse_args()


def retry_delay(attempt: int) -> int:
    return min(30, 2**attempt)


def fetch_text(url: str, retries: int, timeout: int) -> str:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except RETRY_EXCEPTIONS as exc:
            if attempt >= retries:
                raise
            delay = retry_delay(attempt)
            print(f"retry listing after error: {exc}; sleeping {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def normalize_extensions(raw: str) -> tuple[str, ...]:
    extensions = []
    for item in raw.split(","):
        ext = item.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        extensions.append(ext)
    return tuple(extensions)


def file_name_from_href(href: str) -> str:
    parsed = urllib.parse.urlparse(href)
    return posixpath.basename(urllib.parse.unquote(parsed.path))


def select_cat9kv_files(
    source_url: str,
    prefix: str,
    extensions: tuple[str, ...],
    retries: int,
    timeout: int,
) -> list[tuple[str, str]]:
    parser = LinkParser()
    parser.feed(fetch_text(source_url, retries, timeout))

    selected: dict[str, str] = {}
    for href in parser.links:
        filename = file_name_from_href(href)
        if not filename or filename in {".", ".."}:
            continue
        lower_name = filename.lower()
        if not lower_name.startswith(prefix.lower()):
            continue
        if extensions and not lower_name.endswith(extensions):
            continue
        selected[filename] = urllib.parse.urljoin(source_url, href)

    return sorted(selected.items())


def remote_size(url: str, retries: int, timeout: int) -> int | None:
    request = urllib.request.Request(url, method="HEAD")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
            return int(length) if length and length.isdigit() else None
        except RETRY_EXCEPTIONS as exc:
            if attempt >= retries:
                raise
            delay = retry_delay(attempt)
            print(f"retry HEAD after error: {exc}; sleeping {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def download_file(url: str, destination: Path, retries: int, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected_size = remote_size(url, retries, timeout)
    if destination.exists() and expected_size is not None and destination.stat().st_size == expected_size:
        print(f"skip existing: {destination.name}", flush=True)
        destination.chmod(0o664)
        return

    for attempt in range(retries + 1):
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            print(f"download: {url} -> {destination}", flush=True)
            with urllib.request.urlopen(url, timeout=timeout) as response, temp_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            if expected_size is not None and temp_path.stat().st_size != expected_size:
                raise IOError(
                    f"downloaded size mismatch for {destination.name}: "
                    f"{temp_path.stat().st_size} != {expected_size}"
                )
            temp_path.chmod(0o664)
            temp_path.replace(destination)
            return
        except RETRY_EXCEPTIONS as exc:
            temp_path.unlink(missing_ok=True)
            if attempt >= retries:
                raise
            delay = retry_delay(attempt)
            print(f"retry download after error: {exc}; sleeping {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)
        finally:
            temp_path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(destination: Path, filenames: list[str]) -> None:
    manifest = destination / "SHA256SUMS-cat9kv.txt"
    with manifest.open("w", encoding="utf-8") as handle:
        for filename in filenames:
            path = destination / filename
            handle.write(f"{sha256_file(path)}  {filename}\n")
    manifest.chmod(0o664)
    print(f"wrote: {manifest}", flush=True)


def prune_destination(destination: Path, selected_filenames: set[str], extensions: tuple[str, ...]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in destination.iterdir():
        if not path.is_file():
            continue
        lower_name = path.name.lower()
        if lower_name.endswith(extensions) and path.name not in selected_filenames:
            print(f"remove non-selected image: {path.name}", flush=True)
            path.unlink()
        elif lower_name.startswith("sha256sums") and lower_name.endswith(".txt"):
            print(f"remove stale manifest: {path.name}", flush=True)
            path.unlink()


def main() -> int:
    args = parse_args()
    source_url = args.source_url.rstrip("/") + "/"
    destination = Path(args.dest)
    extensions = normalize_extensions(args.extensions)
    selected = select_cat9kv_files(source_url, args.prefix, extensions, args.retries, args.timeout)

    if not selected:
        print(
            f"No files matched prefix {args.prefix!r} and extensions {extensions!r} at {source_url}",
            file=sys.stderr,
        )
        return 1

    print("selected files:", flush=True)
    for filename, url in selected:
        print(f"  {filename} <- {url}", flush=True)

    if args.dry_run:
        return 0

    if args.prune:
        prune_destination(destination, {filename for filename, _url in selected}, extensions)
    for filename, url in selected:
        download_file(url, destination / filename, args.retries, args.timeout)
    write_manifest(destination, [filename for filename, _url in selected])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
