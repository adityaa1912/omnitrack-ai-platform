"""Migration safety validation for CI.

Checks the Alembic graph is a single linear head and that upgrade() paths
contain no destructive operations (drop table/column/index). downgrade()
bodies are not inspected — dropping there is the rollback contract.
Exits non-zero on multiple heads, missing parent revisions, or destructive
upgrades unless ``--allow-destructive`` is passed (which demotes them to
warnings, for reviewed intentional drops).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"

DESTRUCTIVE_CALLS = ("drop_table", "drop_column", "drop_index")


def _literal_targets(node: ast.AST) -> list[tuple[ast.Name, ast.expr]]:
    pairs: list[tuple[ast.Name, ast.expr]] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                pairs.append((target, node.value))
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        if isinstance(node.target, ast.Name):
            pairs.append((node.target, node.value))
    return pairs


def _extract(file_path: Path) -> dict:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    revision = None
    down_revision = None
    destructive_upgrade = []
    for node in ast.walk(tree):
        for target, value in _literal_targets(node):
            if target.id == "revision":
                revision = ast.literal_eval(value)
            elif target.id == "down_revision":
                down_revision = ast.literal_eval(value)
    for top in tree.body:
        if isinstance(top, ast.FunctionDef) and top.name == "upgrade":
            for child in ast.walk(top):
                if isinstance(child, ast.Call) and isinstance(
                    child.func, ast.Attribute
                ):
                    owner = child.func.value
                    if (
                        isinstance(owner, ast.Name)
                        and owner.id == "op"
                        and child.func.attr in DESTRUCTIVE_CALLS
                    ):
                        destructive_upgrade.append(child.func.attr)
    return {
        "revision": revision,
        "down_revision": down_revision,
        "destructive_upgrade": destructive_upgrade,
        "path": file_path.name,
    }


def load_revisions() -> list[dict]:
    return [
        _extract(p)
        for p in sorted(VERSIONS_DIR.glob("*.py"))
        if p.name != "__init__.py"
    ]


def validate(allow_destructive: bool = False) -> tuple[list[str], list[str]]:
    revisions = load_revisions()
    ids = [r["revision"] for r in revisions if r["revision"]]
    errors = []
    warnings = []
    if len(set(ids)) != len(ids):
        errors.append("duplicate revision ids present")
    parents = set()
    for r in revisions:
        dr = r["down_revision"]
        if isinstance(dr, str):
            parents.add(dr)
        elif isinstance(dr, (tuple, list)):
            parents.update(dr)
    known = set(ids)
    unknown = sorted(p for p in parents if p not in known)
    if unknown:
        errors.append(f"down_revision references missing revisions: {unknown}")
    heads = [i for i in ids if i not in parents]
    if len(heads) != 1:
        errors.append(
            f"expected exactly 1 migration head, found {len(heads)}: {heads}"
        )
    for r in revisions:
        for call in r["destructive_upgrade"]:
            message = f"{r['path']}: destructive upgrade op op.{call}()"
            if allow_destructive:
                warnings.append(message)
            else:
                errors.append(message)
    return errors, warnings


def main() -> int:
    allow = "--allow-destructive" in sys.argv
    errors, warnings = validate(allow_destructive=allow)
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        return 1
    print("MIGRATION CHECK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
