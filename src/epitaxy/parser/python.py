"""Python AST parser → module + function + parameter nodes + depends-on edges.

PR1 scope: module + function nodes + depends-on edges (per SCHEMA §2.1, §2.2, §3).
PR2 scope: POR docstring frontmatter (parser/por.py) — `node.por` populated
when docstring opens with `---…---`.
PR4 scope: opt-in parameter extraction (`epi sync --parameters`) via
two signals per SCHEMA §2.5:

  (a) `# epitaxy:param` comment on the assignment line — strict
      physical-line recognition (Codex round-1 Med-9).
  (b) Candidate parameter ID present in the `decides_claimed` set
      collected from ADR `decides:` frontmatter by parser/markdown.py
      (the SCHEMA §2.5 OR clause).

Both signals present → composite provenance "ast+comment+adr-frontmatter"
(SCHEMA §2.5 amended in PR4 commit 1).

This applies broadly: `rank = 128` (ML hyperparameter) and
`sample_temperature_K = 77  # epitaxy:param` (physical constraint from
applied science) are equally first-class. Epitaxy preserves tuned-value
intent across ML AND domain-constrained values — instrument settings,
chemical thresholds, mathematical bounds, validation requirements,
physical constants. See [[feedback_epitaxy_product_framing]] for the
broader product framing.

Function-call resolution heuristics — explicit boundaries locked by tests:

  Supported (emits `depends-on` edge):
    - Same-module direct call: `foo()` where `def foo` is in the same module
    - Imported-name direct call: `from x.y import bar; bar()` (x.y intra-repo)
    - Same-class method call: `self.method()` inside a class body

  Not supported in PR1 (no edge emitted, no warning):
    - Aliased imports: `import x as y; y.foo()`
    - Module-qualified calls: `import x; x.foo()`
    - Dynamic dispatch / `getattr(...)()`
    - Calls to third-party / stdlib / unknown-type locals

Call attribution is scoped to a function's own body — nested `def` / `async def`
/ `lambda` bodies are NOT walked, so calls inside an inner function never
pollute the outer function's edge list (Codex review High Med-3).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import re

from ..store.models import Edge, FunctionNode, ModuleNode, Node, ParameterNode
from .por import PORParseError, parse_docstring as parse_por_docstring
from .refs import BodyRecord


_EPITAXY_PARAM_RE = re.compile(r"#\s*epitaxy:param(?:\s|$)")
"""Tight regex per Codex round-1 Low-10: rejects `# epitaxy:params` (typo)
and `# epitaxy:param-extra` (suffix). Only exact marker followed by
whitespace or end-of-line counts.
"""


@dataclass(frozen=True)
class ParseError:
    """One file that couldn't be AST-parsed; surfaced by `parse_repo`.

    Per CLI.md §7 exit codes, presence of any ParseError → `epi sync` exits 3
    after still writing a partial index with the successfully-parsed nodes.
    """

    path: Path
    reason: str


def module_id(rel_path: str) -> str:
    return f"module:{rel_path}"


def function_id(rel_path: str, qualname: str) -> str:
    return f"function:{rel_path}::{qualname}"


def _module_dotted_name(rel_path: str, package_roots: list[str]) -> str:
    """Convert a repo-relative path to a Python dotted name.

    `package_roots` are path prefixes (e.g. `["src/"]`) that get stripped
    BEFORE conversion — matches PEP-conventional src-layout, where
    `src/foo/bar.py` is imported as `foo.bar` (not `src.foo.bar`) after
    `pip install -e .`. Without stripping, a real-world src-layout repo's
    `from foo.bar import baz` imports would never resolve to a parsed node
    (Codex review High-2).

    `__init__.py` collapses: `src/foo/__init__.py` → `foo`.
    """
    p = rel_path.replace("\\", "/")
    for root in package_roots:
        root_norm = root.rstrip("/") + "/"
        if p.startswith(root_norm):
            p = p[len(root_norm):]
            break
    if p.endswith("/__init__.py"):
        p = p[: -len("/__init__.py")]
    elif p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _first_paragraph(docstring: str | None) -> str | None:
    if not docstring:
        return None
    return docstring.strip().split("\n\n", 1)[0].strip()


def _render_signature(func_def: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(func_def, ast.AsyncFunctionDef) else "def"
    args_str = ast.unparse(func_def.args)
    returns_str = f" -> {ast.unparse(func_def.returns)}" if func_def.returns else ""
    return f"{prefix} {func_def.name}({args_str}){returns_str}"


FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


def _walk_functions(tree: ast.Module) -> list[tuple[str, FuncDef, int]]:
    """Top-level functions + class methods (one level deep).

    Nested functions (`def` inside another `def`) are NOT included — they are
    not addressable through Epitaxy IDs in PR1, so a node for them would have
    nowhere to be referenced from.
    """
    results: list[tuple[str, FuncDef, int]] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            results.append((stmt.name, stmt, stmt.lineno))
        elif isinstance(stmt, ast.ClassDef):
            for cls_stmt in stmt.body:
                if isinstance(cls_stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    results.append((f"{stmt.name}.{cls_stmt.name}", cls_stmt, cls_stmt.lineno))
    return results


def _calls_in_function_body(fdef: FuncDef):
    """Yield Call nodes reachable from `fdef.body` WITHOUT descending into nested
    function / lambda bodies.

    Without this guard, `def outer(): def inner(): helper()` falsely attributes
    `outer -> helper` because `ast.walk` recurses into `inner`'s body (Codex
    review Medium-3).
    """

    def visit(node):
        if isinstance(node, ast.Call):
            yield node
        # Skip nested function / lambda bodies — they are addressed (or not)
        # independently by `_walk_functions`. ClassDef inside a function is
        # rare but the same logic applies: methods belong to the inner class.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return
        for child in ast.iter_child_nodes(node):
            yield from visit(child)

    for stmt in fdef.body:
        yield from visit(stmt)


def _resolve_call(
    call_node: ast.Call,
    *,
    current_qualname: str,
    same_module_qualnames: set[str],
    imported_names: set[str],
) -> str | None:
    """Resolve a Call target per PR1 heuristics.

    Returns:
        - `"<qualname>"` for same-module target
        - `"imported:<name>"` for imported target (caller maps name → rel_path)
        - None for unsupported / unresolvable cases
    """
    func = call_node.func

    if isinstance(func, ast.Name):
        name = func.id
        if name in same_module_qualnames:
            return name
        if name in imported_names:
            return f"imported:{name}"
        return None

    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        # self.method() inside a class method
        if func.value.id == "self" and "." in current_qualname:
            class_name = current_qualname.split(".", 1)[0]
            method_qn = f"{class_name}.{func.attr}"
            if method_qn in same_module_qualnames:
                return method_qn
            return None
        # Module-qualified `mod.foo()` or instance-method on typed local — PR1 skips
        return None

    # Other shapes (Subscript, Call-returning-callable, etc.) — unsupported
    return None


def parameter_id(rel_path: str, scope: str, name: str) -> str:
    """Canonical ParameterNode ID per SCHEMA §2.5: `param:<path>::<scope>::<name>`.

    `scope` is the function qualname (e.g. `"Ranker.fit"`) for function-body
    assignments, or the literal sentinel `"<module>"` for module-level
    assignments. The angle-bracket sentinel cannot collide with a real
    Python identifier.
    """
    return f"param:{rel_path}::{scope}::{name}"


_AssignNode = ast.Assign | ast.AnnAssign


def _single_name_target(assign: _AssignNode) -> str | None:
    """Return the assigned name when the LHS is a single bare Name, else None.

    Skips: tuple-LHS (`a, b = 1, 2`), AugAssign (handled by isinstance check
    upstream — AugAssign is excluded from the candidate set), Subscript-LHS
    (`d['k'] = v`), Starred (`*rest = ...`), Attribute (`self.x = v`),
    multi-target (`a = b = 1`).
    """
    if isinstance(assign, ast.AnnAssign):
        return assign.target.id if isinstance(assign.target, ast.Name) else None
    # ast.Assign — possibly multi-target. Require single target that is Name.
    if len(assign.targets) != 1:
        return None
    target = assign.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _render_value(source_text: str, value_node: ast.expr) -> str:
    """Verbatim source-text RHS via `ast.get_source_segment`, fallback to
    `ast.unparse` for synthetic nodes (Codex round-1 Med-5).

    MCP §3 contract says `current_value` is shown to LLM consumers as-is.
    `ast.unparse(value_node)` normalizes literals (`1e-3` → `0.001`), which
    misleads. `ast.get_source_segment` preserves source-as-written.

    Caller is responsible for filtering AnnAssign-without-value before
    reaching here — at the call site `value_node` is always a real
    `ast.expr` subclass.
    """
    segment = ast.get_source_segment(source_text, value_node)
    if segment is not None:
        return segment
    return ast.unparse(value_node)


def _iter_assignments_in_body(
    body: list[ast.stmt],
) -> "Iterator[_AssignNode]":
    """Yield ast.Assign / ast.AnnAssign with value-not-None nodes directly
    inside `body`. Does NOT recurse into nested function / class bodies.
    """
    for stmt in body:
        if isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:  # `x: int = 1` (skip bare `x: int`)
                yield stmt
        elif isinstance(stmt, ast.Assign):
            yield stmt


def _extract_parameters(
    source_text: str,
    tree: ast.Module,
    rel_path: str,
    *,
    decides_claimed: set[str],
) -> list[ParameterNode]:
    """Extract ParameterNode list per SCHEMA §2.5 OR clause.

    Two signals per SCHEMA §2.5; a candidate assignment becomes a
    ParameterNode if EITHER applies:

    (a) Source line `source_lines[assign.lineno - 1]` matches the
        `# epitaxy:param` marker (strict physical-line per Codex
        round-1 Med-9; continuation-line markers are NOT recognized).
    (b) Candidate parameter ID is in `decides_claimed` (the union of
        validated `decides:` entries across all ADRs from
        parser/markdown.py).

    Composite case (both signals): provenance is
    `"ast+comment+adr-frontmatter"` per SCHEMA §2.5 (amended in PR4 C1
    to bless the composite vocab).

    Scope is `"<module>"` for module-level assignments OR the function
    qualname (`"foo"` / `"Cls.method"`) for function/method bodies.
    Class-body assignments BETWEEN methods (rare; ambiguous semantics)
    are skipped — v0 emits only the high-signal cases.

    Domain scope: `# epitaxy:param` marks both ML hyperparameters
    (`rank = 128`) and domain-constrained values (`sample_temperature_K
    = 77`, `chamber_pressure_Torr = 1e-6`, `validation_threshold = 0.95`).
    Epitaxy preserves intent across both audiences — see
    [[feedback_epitaxy_product_framing]].
    """
    source_lines = source_text.split("\n")
    parameters: list[ParameterNode] = []

    def _consider(
        assign: _AssignNode,
        scope: str,
        module_node_id: str,
    ) -> None:
        name = _single_name_target(assign)
        if name is None:
            return  # tuple-LHS / Subscript-LHS / Starred / Attribute etc.
        # Skip annotation-only `x: int` (no value)
        if isinstance(assign, ast.AnnAssign) and assign.value is None:
            return
        value_node = assign.value
        if value_node is None:
            return

        line_no = assign.lineno
        if line_no < 1 or line_no > len(source_lines):
            return  # defensive
        source_line = source_lines[line_no - 1]

        has_comment = bool(_EPITAXY_PARAM_RE.search(source_line))
        candidate_id = parameter_id(rel_path, scope, name)
        has_adr_claim = candidate_id in decides_claimed

        if not (has_comment or has_adr_claim):
            return

        if has_comment and has_adr_claim:
            provenance = "ast+comment+adr-frontmatter"
        elif has_comment:
            provenance = "ast+comment"
        else:
            provenance = "adr-frontmatter"

        parameters.append(
            ParameterNode(
                id=candidate_id,
                module=module_node_id,
                scope=scope,
                name=name,
                value=_render_value(source_text, value_node),
                line=line_no,
                provenance=provenance,
            )
        )

    mod_id_value = module_id(rel_path)

    # Module-level assignments
    for assign in _iter_assignments_in_body(tree.body):
        _consider(assign, scope="<module>", module_node_id=mod_id_value)

    # Function-body + method-body assignments (one level deep — same scope
    # boundary as _walk_functions; nested-function assignments are skipped
    # because nested functions aren't addressable through Epitaxy IDs).
    for qn, fdef, _line in _walk_functions(tree):
        for assign in _iter_assignments_in_body(fdef.body):
            _consider(assign, scope=qn, module_node_id=mod_id_value)

    return parameters


def parse_repo(
    repo_root: Path,
    py_files: list[Path],
    *,
    package_roots: list[str] | None = None,
    extracted_at: datetime | None = None,
    parameters_enabled: bool = False,
    decides_claimed: set[str] | None = None,
) -> tuple[list[Node], list[Edge], list[ParseError], list[BodyRecord]]:
    """Parse a set of Python files into intent-graph nodes + edges + parse errors.

    `py_files` are absolute paths under `repo_root`. Glob expansion and exclude
    filtering are the caller's responsibility (CLI.md §5 `roots` / `excludes`).

    `package_roots` are path prefixes (e.g. `["src/"]`) stripped before computing
    dotted module names — required to honor PEP-conventional src-layout (see
    `_module_dotted_name`). Defaults to no stripping if `None`.

    Files that fail to AST-parse are skipped and surfaced in the returned
    `list[ParseError]` so the CLI can exit 3 per CLI.md §7 (Codex review
    Medium-4).

    PR2: returns a 4th element — `list[BodyRecord]` accumulating
    post-frontmatter docstring bodies for the references-edge final pass
    (parser/refs.py). One record per module/function with a non-empty
    docstring body.
    """
    extracted_at = extracted_at or datetime.now(timezone.utc)
    roots = list(package_roots or [])

    # Pass 1: parse every file, build dotted_name → rel_path map for import resolution
    # PR4: stores source text alongside the AST so `_extract_parameters` can
    # call `ast.get_source_segment` for verbatim value rendering.
    parsed: list[tuple[str, str, ast.Module, str]] = []
    dotted_to_rel: dict[str, str] = {}
    errors: list[ParseError] = []

    for abs_path in py_files:
        rel_path = str(abs_path.relative_to(repo_root)).replace("\\", "/")
        try:
            source = abs_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(abs_path))
        except SyntaxError as e:
            errors.append(ParseError(path=abs_path, reason=f"SyntaxError: {e}"))
            continue
        except UnicodeDecodeError as e:
            errors.append(ParseError(path=abs_path, reason=f"UnicodeDecodeError: {e}"))
            continue
        dotted = _module_dotted_name(rel_path, roots)
        parsed.append((rel_path, dotted, tree, source))
        dotted_to_rel[dotted] = rel_path

    nodes: list[Node] = []
    edges: list[Edge] = []
    body_records: list[BodyRecord] = []
    seen_edges: set[tuple[str, str, str]] = set()
    all_function_ids: set[str] = set()
    per_module: list[dict] = []
    claimed_set: set[str] = decides_claimed if decides_claimed is not None else set()

    # Pass 2: emit ModuleNode + FunctionNode for every parsed file; record imports;
    # extract parameters when parameters_enabled (PR4).
    for rel_path, dotted, tree, source in parsed:
        abs_path_for_errors = repo_root / rel_path  # for ParseError attribution
        mod_id = module_id(rel_path)

        # POR docstring frontmatter for the module
        mod_raw_doc = ast.get_docstring(tree)
        mod_doc_body: str | None = None  # post-frontmatter body for refs pass
        try:
            mod_por_result = parse_por_docstring(mod_raw_doc)
            mod_por = mod_por_result.por
            mod_doc = _first_paragraph(mod_por_result.body)
            mod_doc_body = mod_por_result.body
        except PORParseError as e:
            errors.append(
                ParseError(
                    path=abs_path_for_errors,
                    reason=f"module docstring POR: {e.reason}",
                )
            )
            mod_por = None
            mod_doc = _first_paragraph(mod_raw_doc)
            mod_doc_body = mod_raw_doc

        nodes.append(
            ModuleNode(
                id=mod_id,
                path=rel_path,
                doc=mod_doc,
                por=mod_por,
                provenance="ast",
                extracted_at=extracted_at,
            )
        )

        if mod_doc_body and mod_doc_body.strip():
            # body_start_line is approximate: module docstring lives at top
            # of file; the body proper begins on line 2+ depending on
            # frontmatter length. Best-effort attribution; line precision
            # within a docstring is PR3+ territory.
            body_records.append(
                BodyRecord(
                    body_text=mod_doc_body,
                    source_node_id=mod_id,
                    source_path=rel_path,
                    body_start_line=1,
                    source_kind="docstring",
                )
            )

        functions: dict[str, FuncDef] = {}
        for qn, fdef, line in _walk_functions(tree):
            fid = function_id(rel_path, qn)

            fn_raw_doc = ast.get_docstring(fdef)
            fn_doc_body: str | None = None
            try:
                fn_por_result = parse_por_docstring(fn_raw_doc)
                fn_por = fn_por_result.por
                fn_doc = _first_paragraph(fn_por_result.body)
                fn_doc_body = fn_por_result.body
            except PORParseError as e:
                errors.append(
                    ParseError(
                        path=abs_path_for_errors,
                        reason=f"{qn} docstring POR: {e.reason}",
                    )
                )
                fn_por = None
                fn_doc = _first_paragraph(fn_raw_doc)
                fn_doc_body = fn_raw_doc

            nodes.append(
                FunctionNode(
                    id=fid,
                    module=mod_id,
                    name=fdef.name,
                    qualname=qn,
                    signature=_render_signature(fdef),
                    line=line,
                    doc=fn_doc,
                    por=fn_por,
                    provenance="ast",
                )
            )
            functions[qn] = fdef
            all_function_ids.add(fid)

            if fn_doc_body and fn_doc_body.strip():
                body_records.append(
                    BodyRecord(
                        body_text=fn_doc_body,
                        source_node_id=fid,
                        source_path=rel_path,
                        body_start_line=line,
                        source_kind="docstring",
                    )
                )

        imports: dict[str, str] = {}
        for stmt in tree.body:
            if not isinstance(stmt, ast.ImportFrom):
                continue
            if stmt.level != 0:
                base_parts = dotted.split(".")
                base = base_parts[: max(0, len(base_parts) - stmt.level)]
                target_dotted = ".".join(base + ([stmt.module] if stmt.module else []))
            else:
                target_dotted = stmt.module or ""
            target_rel = dotted_to_rel.get(target_dotted)
            if target_rel is None:
                continue
            edge_key = (mod_id, module_id(target_rel), "depends-on")
            if edge_key not in seen_edges:
                edges.append(
                    Edge.model_validate(
                        {
                            "from": mod_id,
                            "to": module_id(target_rel),
                            "type": "depends-on",
                            "source": "import",
                            "line": stmt.lineno,
                            "provenance": "ast",
                        }
                    )
                )
                seen_edges.add(edge_key)
            for alias in stmt.names:
                if alias.asname:
                    continue  # alias unsupported in PR1
                imports[alias.name] = target_rel

        per_module.append({"rel_path": rel_path, "functions": functions, "imports": imports})

        # PR4: extract parameters when --parameters is on. Runs inside Pass 2
        # so the parameter nodes interleave with their parent module/function
        # nodes in the output list (matches PR2/PR3 emission patterns).
        if parameters_enabled:
            nodes.extend(
                _extract_parameters(
                    source, tree, rel_path, decides_claimed=claimed_set
                )
            )

    # Pass 3: resolve function calls now that all nodes exist
    same_qns_by_path: dict[str, set[str]] = {
        st["rel_path"]: set(st["functions"].keys()) for st in per_module
    }

    for st in per_module:
        rel_path: str = st["rel_path"]
        functions: dict[str, FuncDef] = st["functions"]
        imports: dict[str, str] = st["imports"]
        same_qns = same_qns_by_path[rel_path]

        for qn, fdef in functions.items():
            from_id = function_id(rel_path, qn)
            for child in _calls_in_function_body(fdef):
                resolved = _resolve_call(
                    child,
                    current_qualname=qn,
                    same_module_qualnames=same_qns,
                    imported_names=set(imports.keys()),
                )
                if resolved is None:
                    continue
                if resolved.startswith("imported:"):
                    name = resolved[len("imported:") :]
                    target_rel = imports[name]
                    target_id = function_id(target_rel, name)
                else:
                    target_id = function_id(rel_path, resolved)
                if target_id not in all_function_ids:
                    continue
                edge_key = (from_id, target_id, "depends-on")
                if edge_key in seen_edges:
                    continue
                edges.append(
                    Edge.model_validate(
                        {
                            "from": from_id,
                            "to": target_id,
                            "type": "depends-on",
                            "source": "call",
                            "line": child.lineno,
                            "provenance": "ast",
                        }
                    )
                )
                seen_edges.add(edge_key)

    return nodes, edges, errors, body_records
