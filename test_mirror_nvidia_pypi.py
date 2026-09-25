from collections.abc import Iterator
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError

import pytest

import mirror_nvidia_pypi as mirror
from mirror_nvidia_pypi import rewrite_html


def anchors(html: str) -> list[dict[str, str | None]]:
    result: list[dict[str, str | None]] = []

    class Parser(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "a":
                result.append(dict(attrs))

    Parser().feed(html)
    return result


def test_resolves_links_and_preserves_package_metadata() -> None:
    html = """<!DOCTYPE html><html><body>
<!-- preserve this -->
<a href="../other/">Other</a>
<a href="demo-1.whl?x=1&amp;y=2#sha256=abc"
   data-requires-python="&gt;=3.10" data-yanked="bad &amp; old"
   data-core-metadata="sha256=def">demo-1.whl</a>
<a href="https://pypi.nvidia.com/files/demo.tar.gz#sha256=123">sdist</a>
<a href="https://example.com/demo.whl">external</a>
<a href="https://pypi.nvidia.com.evil.test/demo.whl">other host</a>
</body></html>"""
    rewritten, pages = rewrite_html(
        html,
        "https://pypi.nvidia.com/demo/",
        "https://art.example/artifactory/nvidia-files/",
        "https://art.example/artifactory/nvidia-index/",
    )
    links = anchors(rewritten)
    assert links[0]["href"] == "https://art.example/artifactory/nvidia-index/other/"
    assert links[1] == {
        "href": "https://art.example/artifactory/nvidia-files/demo/demo-1.whl?x=1&y=2#sha256=abc",
        "data-requires-python": ">=3.10",
        "data-yanked": "bad & old",
        "data-core-metadata": "sha256=def",
    }
    assert (
        links[2]["href"]
        == "https://art.example/artifactory/nvidia-files/files/demo.tar.gz#sha256=123"
    )
    assert links[3]["href"] == "https://example.com/demo.whl"
    assert links[4]["href"] == "https://pypi.nvidia.com.evil.test/demo.whl"
    assert "<!-- preserve this -->" in rewritten
    assert pages == {"https://pypi.nvidia.com/other/"}


@pytest.fixture
def documents() -> dict[str, str]:
    return {
        "/": '<a href="alpha/">alpha</a><a href="beta/">beta</a><a href="gamma/">gamma</a>',
        "/alpha/": '<a href="../">Home</a><a href="alpha-1.whl#sha256=abc">wheel</a>',
        "/beta/": '<a href="../alpha/">alpha</a><a href="beta-1.tar.gz">sdist</a>',
        "/gamma/": '<a href="gamma-1.whl">wheel</a>',
    }


@pytest.fixture
def failures() -> dict[str, int]:
    return {}


@pytest.fixture
def upstream(
    monkeypatch: pytest.MonkeyPatch, documents: dict[str, str], failures: dict[str, int]
) -> Iterator[list[str]]:
    requested: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested.append(self.path)
            if failures.get(self.path, 0):
                failures[self.path] -= 1
                self.send_error(503)
                return
            if self.path not in documents:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(documents[self.path].encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        monkeypatch.setattr(mirror, "SOURCE_URL", f"http://127.0.0.1:{server.server_port}/")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield requested
        finally:
            server.shutdown()
            thread.join()


def test_full_mirror_crawls_html_once_and_never_downloads_wheels(
    tmp_path: Path, upstream: list[str]
) -> None:
    output = tmp_path / "index"
    count = mirror.mirror_index(
        output, "https://files.example/repo", "https://index.example/simple"
    )
    assert count == 4
    assert sorted(upstream) == ["/", "/alpha/", "/beta/", "/gamma/"]
    assert sorted(str(p.relative_to(output)) for p in output.rglob("*.html")) == [
        "alpha/index.html",
        "beta/index.html",
        "gamma/index.html",
        "index.html",
    ]
    assert anchors((output / "alpha/index.html").read_text())[1]["href"] == (
        "https://files.example/repo/alpha/alpha-1.whl#sha256=abc"
    )


def test_small_mirror_filters_root_and_downloads_only_selected_packages(
    tmp_path: Path, upstream: list[str]
) -> None:
    output = tmp_path / "small"
    count = mirror.mirror_index(
        output, "https://files.example/", "https://index.example/", packages=["ALPHA", "beta"]
    )
    assert count == 3
    assert sorted(upstream) == ["/", "/alpha/", "/beta/"]
    assert [link["href"] for link in anchors((output / "index.html").read_text())] == [
        "https://index.example/alpha/",
        "https://index.example/beta/",
    ]
    assert not (output / "gamma").exists()


def test_unknown_package_does_not_publish_an_incomplete_output(
    tmp_path: Path, upstream: list[str]
) -> None:
    output = tmp_path / "bad"
    with pytest.raises(ValueError, match="missing"):
        mirror.mirror_index(
            output, "https://files.example/", "https://index.example/", packages=["missing"]
        )
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        mirror.mirror_index(tmp_path, "https://files.example/", "https://index.example/")
    assert marker.read_text() == "keep"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "art.example/repo",
        "file:///tmp/index",
        "https://art.example/?q=1",
        "https://art.example/#x",
    ],
)
def test_invalid_environment_fails_before_network(
    value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NVIDIA_REPO", value)
    monkeypatch.setenv("INDEX_URL", "https://index.example/")
    with pytest.raises(SystemExit) as error:
        mirror.main(["--output", str(tmp_path / "out")])
    assert error.value.code == 2
    assert not (tmp_path / "out").exists()


def test_failed_project_leaves_no_partial_output_or_temporary_files(
    tmp_path: Path, upstream: list[str], documents: dict[str, str]
) -> None:
    del documents["/beta/"]
    with pytest.raises(HTTPError):
        mirror.mirror_index(
            tmp_path / "out", "https://files.example/", "https://index.example/", retries=0
        )
    assert list(tmp_path.iterdir()) == []


def test_transient_http_failure_is_retried(
    tmp_path: Path, upstream: list[str], failures: dict[str, int]
) -> None:
    failures["/beta/"] = 1
    count = mirror.mirror_index(
        tmp_path / "out", "https://files.example/", "https://index.example/", retries=1
    )
    assert count == 4
    assert upstream.count("/beta/") == 2
    assert (tmp_path / "out/beta/index.html").exists()


def test_cli_uses_environment_urls(
    tmp_path: Path, upstream: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_REPO", "https://files.example/prefix")
    monkeypatch.setenv("INDEX_URL", "https://index.example/prefix")
    output = tmp_path / "out"
    assert mirror.main(["--output", str(output), "--packages", "alpha", "beta"]) == 0
    assert anchors((output / "index.html").read_text())[0]["href"] == (
        "https://index.example/prefix/alpha/"
    )
    assert anchors((output / "beta/index.html").read_text())[1]["href"] == (
        "https://files.example/prefix/beta/beta-1.tar.gz"
    )


@pytest.mark.parametrize("separator", ["\r", "\r\n", "\f", "\u2028"])
def test_unusual_text_separators_do_not_corrupt_html(separator: str) -> None:
    prefix = f"<p>text{separator}</p>\n"
    html, _ = rewrite_html(
        prefix + '<a href="a.whl">file</a>',
        "https://pypi.nvidia.com/",
        "https://files.example/",
        "https://index.example/",
    )
    assert html == prefix + '<a href="https://files.example/a.whl">file</a>'


@pytest.mark.parametrize("path", ["/%2e%2e/escape/", "/dir/%2e%2e/escape/", "/dir%5cescape/"])
def test_upstream_paths_cannot_escape_output(path: str) -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        mirror.local_html_path("https://pypi.nvidia.com" + path)


def test_directory_created_during_download_is_preserved(
    tmp_path: Path, upstream: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    original_fetch = mirror.fetch_html

    def fetch_then_create(url: str, timeout: float, retries: int) -> tuple[str, str]:
        result = original_fetch(url, timeout, retries)
        if url == mirror.SOURCE_URL:
            output.mkdir()
        return result

    monkeypatch.setattr(mirror, "fetch_html", fetch_then_create)
    with pytest.raises(FileExistsError):
        mirror.mirror_index(output, "https://files.example/", "https://index.example/")
    assert output.is_dir()
    assert list(output.iterdir()) == []
    assert list(tmp_path.iterdir()) == [output]


def test_existing_broken_symlink_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.symlink_to("missing", target_is_directory=True)
    with pytest.raises(FileExistsError):
        mirror.mirror_index(output, "https://files.example/", "https://index.example/")
    assert output.is_symlink()
