"""Python AST parser → module + function nodes + depends-on edges.

PR1 scope (tracer-bullet) per docs/SCHEMA.md §2.1, §2.2, §3.
POR docstring frontmatter recognition deferred to PR2 (`node.por` always None).

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
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from ..store.models import Edge, FunctionNode, ModuleNode, Node


def module_id(rel_path: str) -> str:
    return f"module:{rel_path}"


def function_id(rel_path: str, qualname: str) -> str:
    return f"function:{rel_path}::{qualname}"


def _module_dotted_name(rel_path: str) -> str:
    """Convert `src/foo/bar.py` → `src.foo.bar`; `src/foo/__init__.py` → `src.foo`."""
    p = rel_path.replace("\\", "/")
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
    """Return (qualname, def_node, line) for top-level funcs + class methods.

    Nested functions (inside another function) are NOT included — PR1 boundary.
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


def parse_repo(
    repo_root: Path,
    py_files: list[Path],
    *,
    extracted_at: datetime | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Parse a set of Python files into intent-graph nodes + edges.

    `py_files` are absolute paths under `repo_root`. Glob expansion and exclude
    filtering are the caller's responsibility (CLI.md §5 `roots` / `excludes`).
    Files that fail to AST-parse are skipped silently — caller (CLI) decides
    whether to return exit code 3 for partial success.
    """
    extracted_at = extracted_at or datetime.now(timezone.utc)

    # Pass 1: parse every file, build dotted_name → rel_path map for import resolution
    parsed: list[tuple[str, str, ast.Module]] = []
    dotted_to_rel: dict[str, str] = {}

    for abs_path in py_files:
        rel_path = str(abs_path.relative_to(repo_root)).replace("\\", "/")
        try:
            source = abs_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(abs_path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        dotted = _module_dotted_name(rel_path)
        parsed.append((rel_path, dotted, tree))
        dotted_to_rel[dotted] = rel_path

    nodes: list[Node] = []
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    all_function_ids: set[str] = set()
    per_module: list[dict] = []

    # Pass 2: emit ModuleNode + FunctionNode for every parsed file; record imports
    for rel_path, dotted, tree in parsed:
        mod_id = module_id(rel_path)
        nodes.append(
            ModuleNode(
                id=mod_id,
                path=rel_path,
                doc=_first_paragraph(ast.get_docstring(tree)),
                provenance="ast",
                extracted_at=extracted_at,
            )
        )

        functions: dict[str, FuncDef] = {}
        for qn, fdef, line in _walk_functions(tree):
            fid = function_id(rel_path, qn)
            nodes.append(
                FunctionNode(
                    id=fid,
                    module=mod_id,
                    name=fdef.name,
                    qualname=qn,
                    signature=_render_signature(fdef),
                    line=line,
                    doc=_first_paragraph(ast.get_docstring(fdef)),
                    provenance="ast",
                )
            )
            functions[qn] = fdef
            all_function_ids.add(fid)

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
            for child in ast.walk(fdef):
                if not isinstance(child, ast.Call):
                    continue
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

    return nodes, edges