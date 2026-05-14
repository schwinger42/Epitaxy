"""Epitaxy Pillar-3 drill-down — stdlib http.server static HTML.

PR1 ships an ugly-but-functional single-page renderer. Progressive-Enhancement
HTML/CSS rewrite lands in PR3 (ROADMAP §4).
"""

from .app import build_handler, render_index

__all__ = ["build_handler", "render_index"]
