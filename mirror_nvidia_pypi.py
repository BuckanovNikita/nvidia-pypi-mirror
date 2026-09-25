#!/usr/bin/env python3
r"""Mirror NVIDIA's HTML package index (Python 3.12+, no runtime dependencies).

Set NVIDIA_REPO to the base URL serving package files and INDEX_URL to the base
URL serving these HTML pages. Both bases retain the original URL path beneath
them. The server must serve project/index.html when pip requests project/.

Full mirror:
    NVIDIA_REPO=https://files.example/nvidia INDEX_URL=https://index.example/simple \
        python3 mirror_nvidia_pypi.py --output nvidia-index

Small test mirror (three packages):
    python3 mirror_nvidia_pypi.py --output nvidia-index-small --packages \
        nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12

Only HTML is downloaded. The output directory must not already exist. It is
published locally only after every requested page has downloaded successfully.
"""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

SOURCE_URL = "https://pypi.nvidia.com/"


def is_html_url(url: str) -> bool:
    path = urlsplit(url).path
    return path.endswith("/") or PurePosixPath(path).suffix.lower() in {"", ".html", ".htm"}


def is_source_url(url: str) -> bool:
    parsed = urlsplit(url)
    source = urlsplit(SOURCE_URL)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == source.hostname
        and parsed.port in {source.port, 80, 443}
        and parsed.username is None
    )


def rewrite_html(
    html: str, page_url: str, nvidia_repo: str, index_url: str
) -> tuple[str, set[str]]:
    """Rewrite source-host anchors, returning HTML and linked source pages."""
    pages: set[str] = set()
    changes: list[tuple[int, int, str]] = []
    offsets = [0]
    # HTMLParser's positions count LF only, unlike str.splitlines().
    offsets.extend(match.end() for match in re.finditer("\n", html))

    class Rewriter(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "base":
                raise ValueError("HTML <base> is unsupported; refusing ambiguous URLs")
            if tag != "a":
                return
            href = dict(attrs).get("href")
            if not href or href.startswith("#"):
                return
            absolute = urljoin(page_url, href)
            if not is_source_url(absolute):
                return
            parsed = urlsplit(absolute)
            if is_html_url(absolute):
                if parsed.query:
                    raise ValueError(f"Cannot mirror query-dependent HTML: {absolute}")
                pages.add(urldefrag(absolute)[0])
                base = index_url
            else:
                base = nvidia_repo
            target = base.rstrip("/") + "/" + parsed.path.lstrip("/")
            target_parts = urlsplit(target)
            target = urlunsplit(target_parts._replace(query=parsed.query, fragment=parsed.fragment))
            attributes = "".join(
                f' {name}="{escape(target if name == "href" else value, quote=True)}"'
                if value is not None
                else f" {name}"
                for name, value in attrs
            )
            raw = self.get_starttag_text()
            assert raw is not None
            closing = "/>" if raw.endswith("/>") else ">"
            line, column = self.getpos()
            start = offsets[line - 1] + column
            changes.append((start, start + len(raw), f"<{tag}{attributes}{closing}"))

    parser = Rewriter(convert_charrefs=False)
    parser.feed(html)
    parser.close()
    # Splice only changed opening tags; preserve comments, text, and other markup.
    chunks: list[str] = []
    position = 0
    for start, end, replacement in changes:
        chunks.extend((html[position:start], replacement))
        position = end
    chunks.append(html[position:])
    return "".join(chunks), pages


def validate_base_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{name} must be an absolute HTTP(S) base URL without query or fragment")
    _ = parsed.port  # Validate malformed port numbers before downloading anything.
    return value.rstrip("/") + "/"


def fetch_html(url: str, timeout: float, retries: int) -> tuple[str, str]:
    """Fetch HTML, retrying transient failures without downloading linked files."""
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.pypi.simple.v1+html, text/html",
            "User-Agent": "nvidia-pypi-mirror/0.1",
        },
    )
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {
                    "text/html",
                    "application/xhtml+xml",
                    "application/vnd.pypi.simple.v1+html",
                }:
                    raise ValueError(f"Expected HTML at {url}, got {content_type}")
                final_url = str(response.geturl())
                if not is_source_url(final_url):
                    raise ValueError(f"Index page redirected outside the source host: {url}")
                body: bytes = response.read()
                return body.decode(response.headers.get_content_charset() or "utf-8"), final_url
        except HTTPError as error:
            error.close()
            if attempt == retries or (error.code not in {408, 429} and error.code < 500):
                raise
        except (URLError, OSError, HTTPException):
            if attempt == retries:
                raise
        time.sleep(min(2**attempt, 8))
    raise AssertionError("unreachable")


def select_projects(html: str, packages: list[str]) -> str:
    """Build a root index containing only requested, normalized project names."""
    wanted = {re.sub(r"[-_.]+", "-", name).lower() for name in packages}
    found: dict[str, str] = {}

    class Projects(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            href = dict(attrs).get("href")
            if tag != "a" or not href:
                return
            url = urljoin(SOURCE_URL, href)
            if not is_source_url(url) or not is_html_url(url):
                return
            name = re.sub(r"[-_.]+", "-", urlsplit(url).path.strip("/")).lower()
            if name in wanted:
                found[name] = url

    parser = Projects()
    parser.feed(html)
    parser.close()
    missing = wanted - found.keys()
    if missing:
        raise ValueError("Packages missing from source index: " + ", ".join(sorted(missing)))
    links = "\n".join(
        f'<a href="{escape(url, quote=True)}">{escape(name)}</a><br>'
        for name, url in sorted(found.items())
    )
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="UTF-8">'
        "<title>Package Index</title></head><body>\n" + links + "\n</body></html>\n"
    )


def local_html_path(url: str) -> Path:
    path = unquote(urlsplit(url).path).lstrip("/")
    if "\\" in path or "\x00" in path or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError(f"Unsafe HTML path in {url}")
    if not path or path.endswith("/") or not PurePosixPath(path).suffix:
        path = path.rstrip("/") + "/index.html" if path else "index.html"
    return Path(path)


def mirror_index(
    output: Path,
    nvidia_repo: str,
    index_url: str,
    *,
    packages: list[str] | None = None,
    workers: int = 8,
    timeout: float = 30,
    retries: int = 3,
) -> int:
    """Download all reachable index HTML, or only the selected project pages."""
    nvidia_repo = validate_base_url(nvidia_repo, "NVIDIA_REPO")
    index_url = validate_base_url(index_url, "INDEX_URL")
    if workers < 1 or timeout <= 0 or retries < 0:
        raise ValueError("workers and timeout must be positive; retries must be nonnegative")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")
    root_html, root_url = fetch_html(SOURCE_URL, timeout, retries)
    if packages:
        root_html = select_projects(root_html, packages)
    root_html, pending = rewrite_html(root_html, root_url, nvidia_repo, index_url)
    # In small mode, navigation links must not expand the requested package set.
    allowed = pending.copy() if packages else None
    seen = {SOURCE_URL, root_url}
    paths = {Path("index.html")}
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as directory:
        staging = Path(directory)
        (staging / "index.html").write_text(root_html, encoding="utf-8")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            while pending - seen:
                batch = sorted(pending - seen)
                seen.update(batch)
                pending = set()
                futures = {executor.submit(fetch_html, url, timeout, retries): url for url in batch}
                for future in as_completed(futures):
                    url = futures[future]
                    html, final_url = future.result()
                    html, linked = rewrite_html(html, final_url, nvidia_repo, index_url)
                    relative = local_html_path(url)
                    if relative in paths:
                        raise ValueError(f"Multiple source URLs map to {relative}")
                    paths.add(relative)
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(html, encoding="utf-8")
                    pending.update(linked if allowed is None else linked & allowed)
                    print(f"Saved {relative}", file=sys.stderr)
        # Rename on the same filesystem so failures never expose a partial mirror.
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Output appeared during download: {output}")
        staging.rename(output)
    return len(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output", type=Path, default=Path("nvidia-index"), help="New output directory"
    )
    parser.add_argument(
        "--packages", nargs="+", help="Only these projects (omit to mirror all projects)"
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retries after a transient failure")
    args = parser.parse_args(argv)
    try:
        repo = validate_base_url(os.environ.get("NVIDIA_REPO", ""), "NVIDIA_REPO")
        index = validate_base_url(os.environ.get("INDEX_URL", ""), "INDEX_URL")
        if args.workers < 1 or args.timeout <= 0 or args.retries < 0:
            raise ValueError("workers and timeout must be positive; retries must be nonnegative")
    except ValueError as error:
        parser.error(str(error))
    try:
        count = mirror_index(
            args.output,
            repo,
            index,
            packages=args.packages,
            workers=args.workers,
            timeout=args.timeout,
            retries=args.retries,
        )
    except (OSError, URLError, HTTPException, UnicodeError, ValueError) as error:
        print(f"Mirror failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Mirror interrupted.", file=sys.stderr)
        return 130
    print(f"Saved {count} HTML pages to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
