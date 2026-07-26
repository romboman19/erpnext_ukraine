#!/usr/bin/env python3
"""Verify that every dotted path in ``hooks.py`` resolves to a real function.

The app now carries several previously independent domains in one ``hooks.py``.
A typo in a handler path is invisible until the corresponding document is
submitted on a live site, so the check runs statically in CI: ``hooks.py`` is
executed (it has no Frappe imports), every registered target is resolved to a
module file, and the file is parsed to confirm the attribute exists.

    python3 tools/check_hooks.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APP = "erpnext_ua"
REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / APP / "hooks.py"

LIST_HOOKS = (
	"before_install",
	"after_install",
	"before_migrate",
	"after_migrate",
	"before_uninstall",
	"after_uninstall",
)


def load_hooks() -> dict:
	namespace: dict = {}
	exec(compile(HOOKS.read_text(encoding="utf-8"), str(HOOKS), "exec"), namespace)
	return namespace


def collect_targets(hooks: dict) -> set[str]:
	targets: list[str] = []
	for key in LIST_HOOKS:
		targets += hooks.get(key, [])

	for events in hooks.get("doc_events", {}).values():
		for handlers in events.values():
			targets += handlers if isinstance(handlers, list) else [handlers]

	for value in hooks.get("scheduler_events", {}).values():
		if isinstance(value, dict):  # cron
			for handlers in value.values():
				targets += handlers
		else:
			targets += value

	for handlers in hooks.get("override_whitelisted_methods", {}).values():
		targets += handlers if isinstance(handlers, list) else [handlers]

	return set(targets)


def defined_names(module_path: Path) -> set[str]:
	tree = ast.parse(module_path.read_text(encoding="utf-8"))
	names = set()
	for node in tree.body:
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			names.add(node.name)
		elif isinstance(node, ast.Import | ast.ImportFrom):
			names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
		elif isinstance(node, ast.Assign):
			names.update(t.id for t in node.targets if isinstance(t, ast.Name))
	return names


def unresolved(target: str) -> str | None:
	module, _, attribute = target.rpartition(".")
	if not module.startswith(APP):
		return None  # a handler owned by another installed app

	module_path = REPO_ROOT / (module.replace(".", "/") + ".py")
	if not module_path.exists():
		return f"module not found: {module_path.relative_to(REPO_ROOT)}"
	if attribute not in defined_names(module_path):
		return f"{attribute}() not defined in {module_path.relative_to(REPO_ROOT)}"
	return None


def main() -> int:
	targets = sorted(collect_targets(load_hooks()))
	failures = [(target, reason) for target in targets if (reason := unresolved(target))]

	for target, reason in failures:
		print(f"BROKEN HOOK {target}: {reason}", file=sys.stderr)

	print(f"checked {len(targets)} hook targets, {len(failures)} broken")
	return 1 if failures else 0


if __name__ == "__main__":
	raise SystemExit(main())
