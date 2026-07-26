#!/usr/bin/env python3
"""Verify that everything ``hooks.py`` and the manifests point at really exists.

The app now carries several previously independent domains in one ``hooks.py``.
A typo in a handler path is invisible until the corresponding document is
submitted on a live site, so the check runs statically in CI: ``hooks.py`` is
executed (it has no Frappe imports), every registered target is resolved to a
module file, and the file is parsed to confirm the attribute exists.

Besides handler paths the check covers the front-end assets named in
``doctype_js``/``app_include_js``, the patches listed in ``patches.txt`` and the
module folders listed in ``modules.txt`` — all of them are strings that only fail
at runtime on a live site.

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
		targets += as_list(hooks.get(key, []))

	for events in hooks.get("doc_events", {}).values():
		for handlers in events.values():
			targets += handlers if isinstance(handlers, list) else [handlers]

	for value in hooks.get("scheduler_events", {}).values():
		if isinstance(value, dict):  # cron
			for handlers in value.values():
				targets += handlers
		else:
			targets += value

	for key in ("override_whitelisted_methods", "permission_query_conditions", "has_permission"):
		for handlers in hooks.get(key, {}).values():
			targets += as_list(handlers)

	for classes in hooks.get("extend_doctype_class", {}).values():
		targets += as_list(classes)
	for override in hooks.get("override_doctype_class", {}).values():
		targets += as_list(override)

	return set(targets)


def as_list(value) -> list[str]:
	return value if isinstance(value, list) else [value]


def collect_assets(hooks: dict) -> set[str]:
	"""Front-end files named in hooks, as paths relative to the app directory."""
	assets: list[str] = []
	for value in hooks.get("app_include_js", []):
		assets.append(value.removeprefix(f"/assets/{APP}/").replace("js/", "public/js/", 1))
	for key in ("doctype_js", "doctype_list_js"):
		for value in hooks.get(key, {}).values():
			assets += as_list(value)
	return set(assets)


def missing_asset(asset: str) -> str | None:
	if (REPO_ROOT / APP / asset).is_file():
		return None
	return f"asset not found: {APP}/{asset}"


def check_patches() -> list[tuple[str, str]]:
	failures = []
	for line in (REPO_ROOT / APP / "patches.txt").read_text(encoding="utf-8").splitlines():
		patch = line.strip()
		if not patch or patch.startswith(("#", "[")):
			continue
		reason = unresolved(f"{patch}.execute")
		if reason:
			failures.append((patch, reason))
	return failures


def check_modules() -> list[tuple[str, str]]:
	failures = []
	for line in (REPO_ROOT / APP / "modules.txt").read_text(encoding="utf-8").splitlines():
		module = line.strip()
		if not module:
			continue
		folder = REPO_ROOT / APP / module.lower().replace(" ", "_").replace("-", "_")
		if not folder.is_dir():
			failures.append((module, f"no module folder {folder.relative_to(REPO_ROOT)}"))
	return failures


def check_workspace_sidebars() -> list[tuple[str, str]]:
	"""Every public Workspace needs a matching Workspace Sidebar fixture.

	Frappe generates a Desktop Icon for each public Workspace by ``name``, and
	that icon links to a ``Workspace Sidebar`` doc of the same name. A Workspace
	shipped without its sidebar fixture passes ``bench migrate`` silently and
	only breaks the very first `after_install`/`after_migrate` run on a clean
	site, when Frappe tries to create that icon and fails.
	"""
	import json

	workspace_names = set()
	for path in REPO_ROOT.glob(f"{APP}/**/workspace/*/*.json"):
		workspace_names.add(json.loads(path.read_text(encoding="utf-8"))["name"])

	sidebar_names = set()
	for path in (REPO_ROOT / APP / "workspace_sidebar").glob("*.json"):
		sidebar_names.add(json.loads(path.read_text(encoding="utf-8"))["name"])

	return [
		(name, "no matching file under workspace_sidebar/")
		for name in sorted(workspace_names - sidebar_names)
	]


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
	hooks = load_hooks()
	targets = sorted(collect_targets(hooks))
	assets = sorted(collect_assets(hooks))

	failures = [(target, reason) for target in targets if (reason := unresolved(target))]
	failures += [(asset, reason) for asset in assets if (reason := missing_asset(asset))]
	failures += check_patches()
	failures += check_modules()
	failures += check_workspace_sidebars()

	for subject, reason in failures:
		print(f"BROKEN {subject}: {reason}", file=sys.stderr)

	print(
		f"checked {len(targets)} hook targets and {len(assets)} assets "
		f"plus patches.txt and modules.txt, {len(failures)} broken"
	)
	return 1 if failures else 0


if __name__ == "__main__":
	raise SystemExit(main())
