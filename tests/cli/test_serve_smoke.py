"""Pillar-3 `epi serve` smoke tests — render unit + threaded HTTP request."""

from __future__ import annotations

import shutil
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

import re

from epitaxy.cli.app import app
from epitaxy.serve.app import _anchor_for, build_handler, render_index
from epitaxy.store import read_index


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"
runner = CliRunner()


@pytest.fixture
def synced_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output
    return dest / ".epitaxy" / "index.json"


# --------------------------------------------------------------------------- #
# render_index unit tests                                                     #
# --------------------------------------------------------------------------- #


def test_render_includes_all_module_paths(synced_index: Path) -> None:
    html = render_index(read_index(synced_index))
    assert "src/sample/data.py" in html
    assert "src/sample/model.py" in html
    assert "src/sample/boundary.py" in html


def test_render_includes_function_signatures(synced_index: Path) -> None:
    html = render_index(read_index(synced_index))
    assert "def load(" in html
    assert "def fit(self)" in html


def test_anchor_for_only_contains_attribute_safe_chars():
    """Locks the contract: anchors always match `n-[0-9a-f]+` (Codex Medium-2)."""
    pattern = re.compile(r"^n-[0-9a-f]+$")
    for node_id in [
        "module:src/x.py",
        "function:src/x.py::Cls.method",
        'module:src/weird"path.py',  # quote — would have broken attribute before
        "module:src/<script>.py",  # angle brackets
        "module:src/&amp;.py",  # ampersand
    ]:
        anchor = _anchor_for(node_id)
        assert pattern.match(anchor), f"anchor {anchor!r} not attr-safe for input {node_id!r}"


def test_render_escapes_html_in_docstrings(tmp_path: Path) -> None:
    """A docstring containing `<script>` must not break out into raw HTML."""
    repo = tmp_path / "evil_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "evil.py").write_text(
        '"""<script>alert(1)</script>"""\n\ndef f(): pass\n'
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(repo)
    try:
        result = runner.invoke(app, ["sync", "--quiet"])
        assert result.exit_code == 0
        html = render_index(read_index(repo / ".epitaxy" / "index.json"))
        # The docstring's malicious <script>alert(1)</script> must stay escaped;
        # the legitimate <script> tag wrapping the auto-open JS island added in
        # PR3 C5 is fine. Check for the specific malicious payload, not a bare
        # <script> substring.
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    finally:
        monkeypatch.undo()


# --------------------------------------------------------------------------- #
# Threaded HTTP smoke test                                                    #
# --------------------------------------------------------------------------- #


def test_http_handler_returns_200_with_module_list(synced_index: Path) -> None:
    handler_cls = build_handler(synced_index)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)  # port 0 = OS-assigned
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "Epitaxy index" in body
        assert "src/sample/data.py" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_http_handler_returns_404_for_unknown_path(synced_index: Path) -> None:
    handler_cls = build_handler(synced_index)
    httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=2)
            pytest.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# CLI fail-fast: missing index                                                #
# --------------------------------------------------------------------------- #


def test_serve_missing_index_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["serve", "--no-open"])
    assert result.exit_code == 2
    assert "Run `epi sync`" in result.output
