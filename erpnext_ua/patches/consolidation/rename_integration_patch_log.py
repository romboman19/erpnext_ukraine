"""Transfer the `ukrainian_integrations` records to the consolidated app.

The integrations domain used to ship as its own Frappe app. Its data — DocTypes,
documents, custom fields — is untouched by the consolidation, but three kinds of
row still name the old app and would otherwise be wrong:

`Patch Log` stores the dotted path of every applied patch. The five integration
patches moved to `erpnext_ua.patches.*`, so Frappe would consider them pending
and run them again on a site where they already ran. They are written to be
idempotent, but re-running a data migration on live data is not something to
rely on when a rename is all that is needed.

`Module Def.app_name` and `Workspace.app` decide which app owns a module and
where the desk looks for its workspace. Left alone, both keep pointing at an app
that is no longer installed.

The patch is safe on a site that never had the old app: every step is a
conditional update.
"""

from __future__ import annotations

import frappe

OLD_APP = "ukrainian_integrations"
NEW_APP = "erpnext_ua"

# Only the app prefix changed, so the mapping is derived from the shared tail.
MOVED_PATCHES = (
	"patches.v0_5.move_ecommerce_item_mapping_to_module",
	"patches.v0_5.convert_ecommerce_channel_custom_field_to_data",
	"patches.v0_5.backfill_ecommerce_item_mapping",
	"patches.v0_6.register_ecommerce_module_and_sync_mapping",
	"patches.v0_6.create_ocstore_defaults_and_migrate_channels",
)
PATCH_RENAMES = {f"{OLD_APP}.{tail}": f"{NEW_APP}.{tail}" for tail in MOVED_PATCHES}

MODULES = ("Ukrainian Integrations", "Ecommerce")


def execute() -> None:
	rename_patch_log()
	reassign_modules()
	reassign_workspaces()


def rename_patch_log() -> None:
	"""Mark the moved patches as applied under their new dotted path."""
	for old_patch, new_patch in PATCH_RENAMES.items():
		if not frappe.db.exists("Patch Log", {"patch": old_patch}):
			continue
		if frappe.db.exists("Patch Log", {"patch": new_patch}):
			# Both paths recorded: the new one already counts as applied, so the
			# stale row is only noise.
			frappe.db.delete("Patch Log", {"patch": old_patch})
			continue
		frappe.db.set_value(
			"Patch Log",
			{"patch": old_patch},
			"patch",
			new_patch,
			update_modified=False,
		)


def reassign_modules() -> None:
	for module in MODULES:
		if frappe.db.get_value("Module Def", module, "app_name") == OLD_APP:
			frappe.db.set_value("Module Def", module, "app_name", NEW_APP, update_modified=False)


def reassign_workspaces() -> None:
	if not frappe.db.has_column("Workspace", "app"):
		return
	for name in frappe.get_all("Workspace", filters={"app": OLD_APP}, pluck="name"):
		frappe.db.set_value("Workspace", name, "app", NEW_APP, update_modified=False)
