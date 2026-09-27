"""Static scanner for reference repositories (formula discovery and anchor drift).

Security: downloaded repositories are untrusted. Files are only read and parsed
with :func:`ast.parse`; nothing is imported, executed or installed, no hooks
or scripts run, and only ``*.py`` files under a size cap are opened. Output
contains locations, symbol names and hashes, never third-party source text.

Two jobs:

* **anchor check** - every registry observation names ``file``/``symbol``/
  ``target``. The scanner finds that node, records its line and a fingerprint
  (hash of the node's AST dump, insensitive to formatting and comments) and
  reports ``ok`` / ``moved`` / ``changed`` / ``missing`` relative to the
  registry. A changed or missing anchor means the upstream formula must be
  re-reviewed before it can support a rule again.
* **discovery** - assignments to gear-vocabulary names (``rb``, ``dg``,
  ``pressure_angle_t``, ``lead`` ...) that no registry observation cites yet.
  They are unreviewed candidates for normalization, never knowledge.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.knowledge.registry import FORMULAS_FILE, Registry, get_registry

MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__", ".tox", "site-packages",
             "translations", "icons", "resources", "data"}
VOCAB = re.compile(
    r"^(?:self\.)?(?:d[abfgw0]|db|rb|ra|rd|rf|rr|r0|dd|d0|rref|rb_?\w*|pitch\w*|m_?[nt]|mt|at0|a0|inv_\w+|"
    r"pressure_angle\w*|transverse_\w+|normal_\w+|lead\w*|twist\w*|addendum\w*|dedendum\w*|clearance|"
    r"base_radius|pitch_radius|root_radius|addendum_radius|z_ring|ring_z|dist|alpha_w\w*|s0|adn|ddn|"
    r"outer_height|inner_height|involute\w*|gamma\w*|axial_\w+|ratio\w*)$", re.IGNORECASE)


@dataclass
class Located:
    line: int
    end_line: int
    fingerprint: str


def fingerprint(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, annotate_fields=False, include_attributes=False).encode()).hexdigest()[:16]


def _parse(path: Path) -> ast.Module | None:
    if not path.is_file() or path.suffix != ".py" or path.stat().st_size > MAX_FILE_BYTES:
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:
        return None


def _children(body: list[ast.stmt], name: str) -> ast.AST | None:
    for node in body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _resolve_symbol(tree: ast.Module, symbol: str) -> ast.AST | None:
    node: ast.AST = tree
    for part in [p for p in symbol.split(".") if p]:
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            return None
        found = _children(body, part)
        if found is None:
            return None
        node = found
    return node


def _targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target]
    return []


def locate(tree: ast.Module, symbol: str, target: str) -> Located | None:
    """Find ``target`` inside ``symbol``.

    ``target`` forms: an assignment target (``self.dg``, ``rb``), ``return``,
    ``arg:<name>`` (default value of a parameter) or ``if:<test>`` (an if test).
    """
    scope = _resolve_symbol(tree, symbol)
    if scope is None:
        return None
    nodes = sorted((n for n in ast.walk(scope) if hasattr(n, "lineno")), key=lambda n: (n.lineno, n.col_offset))
    if target.startswith("arg:"):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        name = target[4:]
        args = scope.args
        positional = args.posonlyargs + args.args
        defaults = dict(zip([a.arg for a in positional[len(positional) - len(args.defaults):]], args.defaults))
        defaults.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
        node = defaults.get(name)
        return Located(node.lineno, node.end_lineno or node.lineno, fingerprint(node)) if node is not None else None
    for node in nodes:
        if target == "return" and isinstance(node, ast.Return) and node.value is not None:
            return Located(node.lineno, node.end_lineno or node.lineno, fingerprint(node.value))
        if target.startswith("if:") and isinstance(node, ast.If) and ast.unparse(node.test) == target[3:]:
            return Located(node.lineno, node.end_lineno or node.lineno, fingerprint(node.test))
        for t in _targets(node):
            if ast.unparse(t) == target:
                value = getattr(node, "value", None) or node
                return Located(node.lineno, node.end_lineno or node.lineno, fingerprint(value))
    return None


def _git_head(repo: Path) -> str | None:
    try:  # read-only plumbing command; hooks never run for rev-parse
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                             check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _py_files(root: Path) -> list[Path]:
    out = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        out.append(path)
    return sorted(out)


def _qualname(stack: list[str]) -> str:
    return ".".join(stack)


def discover(root: Path) -> list[dict[str, Any]]:
    """Assignments to gear-vocabulary names, with their enclosing symbol (no source text)."""
    found: list[dict[str, Any]] = []
    for path in _py_files(root):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()

        def visit(node: ast.AST, stack: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, [*stack, child.name])
                    continue
                for t in _targets(child):
                    text = ast.unparse(t)
                    if VOCAB.match(text):
                        value = getattr(child, "value", None) or child
                        found.append({"file": rel, "symbol": _qualname(stack), "target": text, "line": child.lineno,
                                      "fingerprint": fingerprint(value)})
                visit(child, stack)

        visit(tree, [])
    return found


def scan(repos_root: Path, registry: Registry | None = None) -> dict[str, Any]:
    """Check every registry anchor and list unreviewed discoveries per repository."""
    registry = registry or get_registry()
    anchors: list[dict[str, Any]] = []
    cited: set[tuple[str, str, str, str]] = set()
    trees: dict[tuple[str, str], ast.Module | None] = {}
    for rule in registry.rules:
        for o in rule.observations:
            cited.add((o.repo, o.file, o.symbol, o.target))
            key = (o.repo, o.file)
            if key not in trees:
                trees[key] = _parse(repos_root / o.repo / o.file)
            tree = trees[key]
            loc = locate(tree, o.symbol, o.target) if tree is not None else None
            if tree is None:
                state = "repo_or_file_missing"
            elif loc is None:
                state = "missing"
            elif o.fingerprint and loc.fingerprint != o.fingerprint:
                state = "changed"
            elif o.line and loc.line != o.line:
                state = "moved"
            else:
                state = "ok"
            anchors.append({"rule_id": rule.id, "repo": o.repo, "file": o.file, "symbol": o.symbol, "target": o.target,
                            "state": state, "recorded_line": o.line, "line": loc.line if loc else None,
                            "recorded_fingerprint": o.fingerprint, "fingerprint": loc.fingerprint if loc else None})
    repos = []
    discoveries: list[dict[str, Any]] = []
    for meta in registry.repositories:
        if not meta.get("inspected"):
            continue
        root = repos_root / meta["name"]
        if not root.is_dir():
            repos.append({"name": meta["name"], "present": False})
            continue
        head = _git_head(root)
        found = discover(root)
        new = [d for d in found if (meta["name"], d["file"], d["symbol"], d["target"]) not in cited]
        discoveries += [{"repo": meta["name"], **d} for d in new]
        repo_anchors = [a for a in anchors if a["repo"] == meta["name"]]
        repos.append({
            "name": meta["name"], "present": True, "origin": meta.get("origin"), "license": meta.get("license"),
            "registry_commit": meta.get("commit"), "scanned_commit": head,
            "commit_matches_registry": head == meta.get("commit") if head else None,
            "python_files": len(_py_files(root)), "vocabulary_assignments": len(found),
            "unreviewed_discoveries": len(new),
            "anchors": {s: sum(1 for a in repo_anchors if a["state"] == s)
                        for s in ("ok", "moved", "changed", "missing", "repo_or_file_missing")},
        })
    return {"schema": "dayanera.reference_scan/1", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "method": "static ast.parse only; no import/exec/install; *.py under 1 MB; source text never stored",
            "repositories": repos, "anchors": anchors, "unreviewed_discoveries": discoveries}


def update_registry_anchors(result: dict[str, Any], path: Path = FORMULAS_FILE) -> int:
    """Write scanned line numbers and fingerprints back into the registry JSON.

    Only anchors in state ``ok``/``moved`` (or never fingerprinted) are updated;
    a ``changed`` formula keeps its old fingerprint so review is forced.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    by_key = {(a["rule_id"], a["repo"], a["file"], a["symbol"], a["target"]): a for a in result["anchors"]}
    updated = 0
    for rule in data["rules"]:
        for o in rule.get("observations", []):
            a = by_key.get((rule["id"], o["repo"], o["file"], o.get("symbol", ""), o["target"]))
            if not a or a["line"] is None:
                continue
            if a["state"] in ("ok", "moved") or not o.get("fingerprint"):
                if o.get("line") != a["line"] or o.get("fingerprint") != a["fingerprint"]:
                    o["line"], o["fingerprint"] = a["line"], a["fingerprint"]
                    updated += 1
    if updated:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated
