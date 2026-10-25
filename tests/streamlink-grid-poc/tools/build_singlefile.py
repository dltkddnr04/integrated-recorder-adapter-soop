#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
"""Deterministic, scope-aware AST linker for this PoC (not a general bundler).

No code is executed while linking. Internal imports become static name bindings;
module globals get distinct prefixes, while Python symbol tables preserve locals,
closures, class attributes and comprehension scopes. Unknown constructs fail closed.
"""

import argparse
import ast
import hashlib
from pathlib import Path
import symtable

ROOT = Path(__file__).resolve().parents[1]


def internal(node):
    if not isinstance(node, ast.ImportFrom):
        return None
    if node.level == 1 and node.module:
        return node.module
    if node.level == 0 and node.module and node.module.startswith("soop_grid."):
        return node.module.split(".", 1)[1]
    if node.level:
        raise ValueError("unsupported relative import")
    return None


def prefix(module, name):
    return f"_sg_{module}_{name}"


class Linker(ast.NodeTransformer):
    def __init__(self, module, table, names, dependencies):
        self.module = module
        self.scopes = [table]
        self.names = names
        self.dependencies = dependencies
        self.used_children = set()
        self.comp_locals = []

    def child(self, name, line):
        children = [
            x
            for x in self.scopes[-1].get_children()
            if x.get_name() == name and x.get_lineno() == line and x.get_id() not in self.used_children
        ]
        if len(children) != 1:
            raise ValueError(f"ambiguous scope: {self.module}:{line}:{name}")
        self.used_children.add(children[0].get_id())
        return children[0]

    def global_name(self, name):
        if name not in self.names or any(name in scope for scope in self.comp_locals):
            return name
        symbol = self.scopes[-1].lookup(name)
        if len(self.scopes) == 1 or symbol.is_global():
            return self.names[name]
        return name

    def visit_Name(self, node):
        node.id = self.global_name(node.id)
        return node

    def visit_Import(self, node):
        if len(self.scopes) == 1:
            for alias in node.names:
                name = alias.asname or alias.name
                if "." in alias.name and not alias.asname:
                    raise ValueError("dotted import requires an alias")
                alias.asname = self.names[name]
        return node

    def visit_ImportFrom(self, node):
        target = internal(node)
        if target:
            result = []
            for alias in node.names:
                if alias.name == "*" or alias.name not in self.dependencies[target]:
                    raise ValueError("unknown internal import")
                name = alias.asname or alias.name
                name = self.global_name(name)
                result.append(
                    ast.Assign(
                        targets=[ast.Name(id=name, ctx=ast.Store())],
                        value=ast.Name(id=self.dependencies[target][alias.name], ctx=ast.Load()),
                    )
                )
            return result
        if len(self.scopes) == 1:
            for alias in node.names:
                if alias.name == "*":
                    raise ValueError("star import unsupported")
                alias.asname = self.names[alias.asname or alias.name]
        return node

    def visit_FunctionDef(self, node):
        original = node.name
        table = self.child(original, node.lineno)
        node.name = self.global_name(original) if len(self.scopes) == 1 else original
        node.decorator_list = [self.visit(x) for x in node.decorator_list]
        node.args.defaults = [self.visit(x) for x in node.args.defaults]
        node.args.kw_defaults = [self.visit(x) if x else None for x in node.args.kw_defaults]
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.annotation:
                arg.annotation = self.visit(arg.annotation)
        if node.returns:
            node.returns = self.visit(node.returns)
        self.scopes.append(table)
        node.body = self.statements(node.body)
        self.scopes.pop()
        return node

    def visit_AsyncFunctionDef(self, node):
        raise ValueError("async functions unsupported by this linker")

    def visit_ClassDef(self, node):
        table = self.child(node.name, node.lineno)
        node.name = self.global_name(node.name) if len(self.scopes) == 1 else node.name
        node.bases = [self.visit(x) for x in node.bases]
        node.keywords = [self.visit(x) for x in node.keywords]
        node.decorator_list = [self.visit(x) for x in node.decorator_list]
        self.scopes.append(table)
        node.body = self.statements(node.body)
        self.scopes.pop()
        return node

    def visit_Lambda(self, node):
        table = self.child("lambda", node.lineno)
        node.args.defaults = [self.visit(x) for x in node.args.defaults]
        self.scopes.append(table)
        node.body = self.visit(node.body)
        self.scopes.pop()
        return node

    def comprehension(self, node, name):
        # Python 3.12+ inlines list/set/dict comprehensions (PEP 709).
        matches = [
            x
            for x in self.scopes[-1].get_children()
            if x.get_name() == name and x.get_lineno() == node.lineno
        ]
        table = self.child(name, node.lineno) if matches else None
        if table is None and name == "genexpr":
            raise ValueError("generator scope unavailable")
        node.generators[0].iter = self.visit(node.generators[0].iter)
        targets = {n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)}
        self.comp_locals.append(targets)
        if table is not None:
            self.scopes.append(table)
        for i, generator in enumerate(node.generators):
            if generator.is_async:
                raise ValueError("async comprehension unsupported")
            if i:
                generator.iter = self.visit(generator.iter)
            generator.target = self.visit(generator.target)
            generator.ifs = [self.visit(x) for x in generator.ifs]
        if isinstance(node, ast.DictComp):
            node.key, node.value = self.visit(node.key), self.visit(node.value)
        else:
            node.elt = self.visit(node.elt)
        if table is not None:
            self.scopes.pop()
        self.comp_locals.pop()
        return node

    def visit_ListComp(self, node):
        return self.comprehension(node, "listcomp")

    def visit_SetComp(self, node):
        return self.comprehension(node, "setcomp")

    def visit_DictComp(self, node):
        return self.comprehension(node, "dictcomp")

    def visit_GeneratorExp(self, node):
        return self.comprehension(node, "genexpr")

    def statements(self, statements):
        result = []
        for statement in statements:
            item = self.visit(statement)
            result.extend(item if isinstance(item, list) else [item])
        return result


def build():
    paths = {p.stem: p for p in sorted((ROOT / "soop_grid").glob("*.py")) if p.stem != "__init__"}
    paths["plugin"] = ROOT / "plugins" / "soopgrid.py"
    sources = {name: p.read_text() for name, p in paths.items()}
    trees = {name: ast.parse(source) for name, source in sources.items()}
    tables = {name: symtable.symtable(source, str(paths[name]), "exec") for name, source in sources.items()}
    names = {
        name: {
            symbol.get_name(): prefix(name, symbol.get_name())
            for symbol in table.get_symbols()
            if symbol.is_assigned() or symbol.is_imported()
        }
        for name, table in tables.items()
    }
    # Include deferred imports in the graph. This source set has no internal cycle;
    # reject a future cycle rather than adding runtime import machinery.
    dependencies = {
        name: {internal(n) for n in ast.walk(tree) if internal(n)} for name, tree in trees.items()
    }
    ordered, pending = [], set(paths)
    while pending:
        ready = sorted(name for name in pending if dependencies[name] <= set(ordered))
        if not ready:
            raise ValueError("cyclic/unknown internal dependency")
        ordered.extend(ready)
        pending.difference_update(ready)
    header = '''# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
# Source: https://github.com/integrated-recorder/source.soop
# License: https://www.gnu.org/licenses/agpl-3.0.html
# Generated by tools/build_singlefile.py. Edit modular sources, then regenerate.
"""
$description Experimental native TCP Grid receiver for authorized SOOP live streams.
$url play.sooplive.com
$url play.sooplive.co.kr
$url play.afreecatv.com
$type live
"""
'''
    parts = [header]
    for name in ordered:
        digest = hashlib.sha256(sources[name].encode()).hexdigest()
        tree = trees[name]
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
            tree.body.pop(0)  # Module docstring becomes a section comment, not stray expression.
        linker = Linker(name, tables[name], names[name], names)
        tree.body = linker.statements(tree.body)
        ast.fix_missing_locations(tree)
        parts.append(f"\n# Module: {name}; source SHA-256: {digest}\n" + ast.unparse(tree) + "\n")
    parts.append("\n__plugin__ = " + names["plugin"]["__plugin__"] + "\n")
    # Static name map for offline parity tests, not a runtime loader or import hook.
    parts.append("\n__grid_names__ = " + repr(names) + "\n")
    result = "\n".join(parts)
    tree = ast.parse(result, feature_version=(3, 10))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").startswith("soop_grid")):
            raise ValueError("unresolved internal import")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("exec", "eval", "__import__")
        ):
            raise ValueError("dynamic execution forbidden")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "soop.py")
    args = parser.parse_args()
    result = build()
    digest = hashlib.sha256(result.encode()).hexdigest()
    checksum = args.output.parent / "SHA256SUMS"
    checksum_text = f"{digest}  {args.output.name}\n"
    if args.check:
        if (
            not args.output.exists()
            or args.output.read_text() != result
            or not checksum.exists()
            or checksum.read_text() != checksum_text
        ):
            raise SystemExit("single-file output stale; regenerate")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
        checksum.write_text(checksum_text)
    print(digest)


if __name__ == "__main__":
    main()
