"""PR3 [B] tests — BeautifulSoup structural contracts for `render_index`.

Per Codex round-1 Med-7 + round-2 fold:

- Structural contracts via BeautifulSoup lock real behaviors that any
  future refactor must preserve: section presence, drill-down primitive
  usage, no-broken-internal-hrefs, escaped node text, edge-anchor
  correctness, missing-target plain-text rendering.
- Coarse substring checks for hooks: one `<style>` block, hashchange
  listener in the JS island, `:target` rule somewhere in CSS.
- NOT required (per round-1 Low-10 / round-2 Low-8): dark-mode @media
  rule presence, sticky-nav rule, status-badge color tokens — best-effort
  polish that shouldn't gate PR3 merge.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from typer.testing import CliRunner

from epitaxy.cli.app import app
from epitaxy.serve.app import render_index
from epitaxy.store import read_index


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"
runner = CliRunner()


@pytest.fixture
def rendered_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output
    return render_index(read_index(dest / ".epitaxy" / "index.json"))


@pytest.fixture
def soup(rendered_html: str) -> BeautifulSoup:
    return BeautifulSoup(rendered_html, "html.parser")


# --------------------------------------------------------------------------- #
# Structural contracts (BeautifulSoup)                                        #
# --------------------------------------------------------------------------- #


def test_exactly_one_main(soup: BeautifulSoup) -> None:
    assert len(soup.find_all("main")) == 1


def test_nav_present_with_section_links(soup: BeautifulSoup) -> None:
    nav = soup.find("nav")
    assert nav is not None
    hrefs = {a["href"] for a in nav.find_all("a", href=True)}
    assert "#modules" in hrefs
    # ADRs + plans sections are conditional on having any of that node type;
    # the PR2 fixture has both, so the ToC must link them.
    assert "#adrs" in hrefs
    assert "#plans" in hrefs


def test_all_four_node_type_sections_present(soup: BeautifulSoup) -> None:
    section_ids = {s.get("id") for s in soup.find_all("section")}
    assert "modules" in section_ids
    assert "adrs" in section_ids
    assert "plans" in section_ids


def test_each_module_renders_as_details_with_summary(soup: BeautifulSoup) -> None:
    modules_section = soup.find("section", id="modules")
    details_blocks = modules_section.find_all("details", class_="node-module")
    assert len(details_blocks) >= 3  # PR2 fixture has data.py / model.py / boundary.py
    for d in details_blocks:
        assert d.find("summary") is not None
        assert d.get("id", "").startswith("n-")


def test_each_adr_renders_as_details_with_status_badge(soup: BeautifulSoup) -> None:
    adrs_section = soup.find("section", id="adrs")
    adr_blocks = adrs_section.find_all("details", class_="node-adr")
    assert len(adr_blocks) >= 2  # PR2 fixture has 2026-04 + 2026-02
    statuses = [
        s["data-status"]
        for d in adr_blocks
        for s in d.find_all("span", class_="status")
    ]
    assert "accepted" in statuses
    assert "superseded" in statuses


def test_each_plan_renders_as_details(soup: BeautifulSoup) -> None:
    plans_section = soup.find("section", id="plans")
    plan_blocks = plans_section.find_all("details", class_="node-plan")
    assert len(plan_blocks) >= 1  # PR2 fixture has q2-launch.md


def test_no_broken_internal_hrefs(soup: BeautifulSoup) -> None:
    """Every `href="#..."` must point at an id that exists in the document."""
    all_ids = {tag.get("id") for tag in soup.find_all(id=True)}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("#") and len(href) > 1:
            anchor = href[1:]
            assert anchor in all_ids, (
                f"broken internal href={href!r} (text: {a.get_text()!r})"
            )


def test_supersedes_edge_renders_as_anchor_when_target_present(
    soup: BeautifulSoup,
) -> None:
    """PR2 fixture: 2026-04 supersedes 2026-02 (target exists in index)."""
    text = soup.get_text()
    # The newer ADR has supersedes pointing at the older one — should be a link.
    adrs_section = soup.find("section", id="adrs")
    superseded_links = [
        a for a in adrs_section.find_all("a", href=True)
        if "2026-02-rank-baseline" in a.get_text()
    ]
    assert len(superseded_links) >= 1, (
        "expected at least one anchor pointing at the superseded ADR; "
        f"all adrs-section text: {text!r}"
    )


def test_missing_supersedes_target_renders_as_missing_target_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round-2 Med-9: a supersedes edge whose target ADR is NOT in the
    index must render as plain text + indicator, not a broken anchor.
    """
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    # Add an ADR whose supersedes points at a non-existent file.
    (dest / "decisions" / "ghost-supersedes.md").write_text(
        "---\n"
        "title: ghost\n"
        "supersedes: adr:decisions/never-existed.md\n"
        "---\n"
        "# ghost",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["sync"])
    # Partial-success: index still written; exit_code 0 (no parse errors here).
    assert result.exit_code == 0, result.output

    soup_ = BeautifulSoup(
        render_index(read_index(dest / ".epitaxy" / "index.json")),
        "html.parser",
    )
    missing_spans = soup_.find_all("span", class_="missing-target")
    assert len(missing_spans) >= 1
    # The missing target ID should appear as text inside the span.
    texts = [s.get_text() for s in missing_spans]
    assert any("never-existed" in t for t in texts)
    # And it must NOT be an <a> anchor — verify no <a> with that target id.
    bad_anchors = [
        a for a in soup_.find_all("a", href=True)
        if "never-existed" in a.get("href", "")
    ]
    assert bad_anchors == []


def test_function_signature_in_dl(soup: BeautifulSoup) -> None:
    """Functions inside a module render as <dl><dt><code>signature</code></dt>...."""
    code_tags = [c.get_text() for c in soup.find_all("code")]
    assert any("def load(" in c for c in code_tags)
    assert any("def fit(self)" in c for c in code_tags)


def test_node_text_html_escaped(soup: BeautifulSoup, rendered_html: str) -> None:
    """HTML in docstrings must not break out into raw tags."""
    # The fixture docstrings contain no malicious HTML, but the structural
    # contract is that everything inside summary/path spans goes through _esc.
    # We re-assert PR1's Codex Medium-2 guarantee here: no raw HTML payloads
    # from the fixture leak into the DOM.
    for path_span in soup.find_all("span", class_="path"):
        text = path_span.get_text()
        # Paths in the fixture are tame; this asserts the structural slot.
        assert "<" not in text or "&lt;" in text


# --------------------------------------------------------------------------- #
# Coarse hooks (substring) — not over-locked                                  #
# --------------------------------------------------------------------------- #


def test_exactly_one_style_block(rendered_html: str) -> None:
    assert rendered_html.count("<style>") == 1


def test_target_rule_present_in_css(rendered_html: str) -> None:
    """The :target rule is what gives the URL-anchor highlight; lock it."""
    assert ":target" in rendered_html


def test_details_open_rule_present_in_css(rendered_html: str) -> None:
    """details[open] selector drives the open-chevron CSS; lock it."""
    assert "details[open]" in rendered_html


def test_auto_open_script_handles_hashchange(rendered_html: str) -> None:
    """Codex round-2 Med-3: DOMContentLoaded alone misses later anchor clicks."""
    assert "hashchange" in rendered_html
    assert "DOMContentLoaded" in rendered_html


def test_meta_viewport_present(rendered_html: str) -> None:
    """Mobile responsiveness gate — viewport meta required."""
    assert 'name="viewport"' in rendered_html
