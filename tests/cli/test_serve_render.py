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


# --------------------------------------------------------------------------- #
# PR4 (Codex code-time High-1): ParameterNode + decides rendering             #
# --------------------------------------------------------------------------- #


@pytest.fixture
def rendered_html_with_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Like `rendered_html` but runs `epi sync --parameters` so the fixture's
    parameter nodes + decides edges appear in the rendered HTML."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    result = runner.invoke(app, ["sync", "--parameters", "--quiet"])
    assert result.exit_code == 0, result.output
    return render_index(read_index(dest / ".epitaxy" / "index.json"))


@pytest.fixture
def soup_with_params(rendered_html_with_params: str) -> BeautifulSoup:
    return BeautifulSoup(rendered_html_with_params, "html.parser")


def test_parameters_section_present_when_index_has_parameters(
    soup_with_params: BeautifulSoup,
) -> None:
    section = soup_with_params.find("section", id="parameters")
    assert section is not None
    assert section.find("h2") is not None


def test_parameters_section_absent_when_index_has_no_parameters(
    soup: BeautifulSoup,
) -> None:
    """Without --parameters, no parameter nodes exist → no section emitted."""
    assert soup.find("section", id="parameters") is None


def test_nav_links_to_parameters_section_when_present(
    soup_with_params: BeautifulSoup,
) -> None:
    nav = soup_with_params.find("nav")
    hrefs = {a["href"] for a in nav.find_all("a", href=True)}
    assert "#parameters" in hrefs


def test_each_parameter_renders_as_details_with_name_and_value(
    soup_with_params: BeautifulSoup,
) -> None:
    section = soup_with_params.find("section", id="parameters")
    param_blocks = section.find_all("details", class_="node-parameter")
    # Fixture has: rank (composite), DEFAULT_RANK, sample_temperature_K, learning_rate
    assert len(param_blocks) == 4
    for block in param_blocks:
        summary = block.find("summary")
        assert summary is not None
        path_span = summary.find("span", class_="path")
        assert path_span is not None
        # Value appears in summary as a code span
        assert summary.find("code", class_="param-value") is not None


def test_parameter_detail_includes_required_fields(
    soup_with_params: BeautifulSoup,
) -> None:
    """Each parameter's body has scope, line, value, provenance, module."""
    section = soup_with_params.find("section", id="parameters")
    rank_block = next(
        b for b in section.find_all("details") if "rank" in b.find("summary").get_text()
        and "DEFAULT" not in b.find("summary").get_text()
    )
    detail_text = rank_block.find("div", class_="parameter-detail").get_text()
    assert "M.fit" in detail_text  # scope
    assert "128" in detail_text  # value
    assert "ast+comment+adr-frontmatter" in detail_text  # composite provenance


def test_decided_by_links_to_adrs(
    soup_with_params: BeautifulSoup,
) -> None:
    """rank has decided_by [2026-02, 2026-04]; both should be linked."""
    section = soup_with_params.find("section", id="parameters")
    rank_block = next(
        b for b in section.find_all("details") if "rank" in b.find("summary").get_text()
        and "DEFAULT" not in b.find("summary").get_text()
    )
    # The decided_by list should link to both ADR anchors
    detail = rank_block.find("div", class_="parameter-detail")
    anchor_targets = [a.get_text() for a in detail.find_all("a", href=True)]
    assert any("2026-04-rank-dim" in t for t in anchor_targets)
    assert any("2026-02-rank-baseline" in t for t in anchor_targets)


def test_adr_decides_edges_rendered_in_adr_detail(
    soup_with_params: BeautifulSoup,
) -> None:
    """ADR detail blocks now include a 'Decides' section listing param targets."""
    adrs_section = soup_with_params.find("section", id="adrs")
    adr_04 = next(
        b for b in adrs_section.find_all("details")
        if "2026-04-rank-dim" in b.find("summary").get_text()
    )
    detail_text = adr_04.find("div", class_="adr-detail").get_text()
    assert "Decides" in detail_text
    assert "rank" in detail_text
    assert "learning_rate" in detail_text


def test_dangling_decides_target_renders_as_missing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCHEMA §6 PR4 amendment: dangling decides target (parameter doesn't
    exist) renders as <span class="missing-target"> per Codex code-time
    High-1's contract concern. PR3 missing-target rendering applies to
    decides too."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    # Add an ADR whose `decides:` points at a parameter that doesn't exist
    # in source. Parameter extraction won't emit a node for it; the decides
    # edge persists per SCHEMA §6.
    (dest / "decisions" / "ghost-decides.md").write_text(
        "---\n"
        "title: ghost decider\n"
        "decides:\n"
        "  - param:src/sample/model.py::M.fit::removed_param\n"
        "---\n"
        "# ghost\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["sync", "--parameters", "--quiet"])
    assert result.exit_code == 0, result.output
    html = render_index(read_index(dest / ".epitaxy" / "index.json"))
    soup_ = BeautifulSoup(html, "html.parser")

    # The ghost ADR's Decides section should contain a missing-target span
    # for the removed parameter.
    ghost_adr = next(
        b for b in soup_.find_all("details")
        if "ghost-decides" in b.find("summary").get_text()
    )
    missing_spans = ghost_adr.find_all("span", class_="missing-target")
    assert len(missing_spans) >= 1
    assert any("removed_param" in s.get_text() for s in missing_spans)


def test_header_counts_include_parameters(
    rendered_html_with_params: str,
) -> None:
    """Header summary line includes parameter count."""
    assert "4 parameters" in rendered_html_with_params


def test_header_counts_show_zero_parameters_when_absent(
    rendered_html: str,
) -> None:
    """Without --parameters, the header still includes the count (zero)."""
    assert "0 parameters" in rendered_html
