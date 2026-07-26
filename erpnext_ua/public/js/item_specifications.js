(() => {
	const SPEC_TABLE = "ua_specifications";
	const VALUE_TABLE = "UA Item Specification Value";
	const API = "erpnext_ua.ua_item_specs.api";

	// One page load asks for each specification's options at most once; a category with
	// twenty Select rows would otherwise fire twenty identical calls on every grid render.
	const optionCache = new Map();

	function getOptions(specification) {
		if (!specification) {
			return Promise.resolve([]);
		}
		if (optionCache.has(specification)) {
			return Promise.resolve(optionCache.get(specification));
		}
		return frappe
			.call({ method: `${API}.get_specification_options`, args: { specification } })
			.then((response) => {
				const options = response.message || [];
				optionCache.set(specification, options);
				return options;
			});
	}

	function parseMulti(raw) {
		if (!raw) {
			return [];
		}
		try {
			const parsed = JSON.parse(raw);
			return Array.isArray(parsed) ? parsed : [String(parsed)];
		} catch (error) {
			return String(raw)
				.split(",")
				.map((part) => part.trim())
				.filter(Boolean);
		}
	}

	function valueField(fieldType) {
		return {
			Data: "value_data",
			Text: "value_text",
			HTML: "value_html",
			Int: "value_int",
			Float: "value_float",
			Select: "value_select",
			MultiSelect: "value_multi",
			Check: "value_check",
			Date: "value_date",
		}[fieldType];
	}

	function applyDefault(row, spec) {
		const field = valueField(spec.field_type);
		if (!field || spec.default_value === null || spec.default_value === undefined || spec.default_value === "") {
			return;
		}
		row[field] =
			spec.field_type === "MultiSelect"
				? JSON.stringify(parseMulti(spec.default_value))
				: spec.default_value;
	}

	/** Add the category's mandatory rows to the open form without saving.
	 *
	 * Saving here would be self-defeating: a freshly added mandatory row is empty by
	 * definition, so the server would immediately reject it. The user fills the rows in
	 * and saves once. `sync_item_specifications` is the server-side equivalent for scripts.
	 */
	function applyCategorySet(frm, { announce = true } = {}) {
		if (!frm.doc.item_group) {
			return;
		}
		frappe
			.call({ method: `${API}.get_group_specifications`, args: { item_group: frm.doc.item_group } })
			.then((response) => {
				const specifications = response.message || [];
				const present = new Set((frm.doc[SPEC_TABLE] || []).map((row) => row.specification));
				let added = 0;

				specifications.forEach((spec) => {
					if (!spec.is_mandatory || !spec.is_active || present.has(spec.specification)) {
						return;
					}
					const row = frm.add_child(SPEC_TABLE, {
						specification: spec.specification,
						spec_label: spec.spec_label,
						field_type: spec.field_type,
						unit: spec.unit,
						is_mandatory: 1,
						source: "Category",
					});
					applyDefault(row, spec);
					added += 1;
				});

				if (added) {
					frm.refresh_field(SPEC_TABLE);
				}
				if (!announce) {
					return;
				}
				frappe.show_alert(
					added
						? { message: __("Додано обов'язкових характеристик: {0}", [added]), indicator: "blue" }
						: { message: __("Усі обов'язкові характеристики вже присутні"), indicator: "green" }
				);
			});
	}

	function editMultiSelect(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		getOptions(row.specification).then((options) => {
			if (!options.length) {
				frappe.msgprint(__("Для характеристики «{0}» не задано дозволених значень.", [row.spec_label || row.specification]));
				return;
			}
			const selected = parseMulti(row.value_multi);
			const dialog = new frappe.ui.Dialog({
				title: row.spec_label || row.specification,
				fields: [
					{
						fieldname: "values",
						fieldtype: "MultiCheck",
						columns: 2,
						options: options.map((option) => ({
							label: option.label,
							value: option.value,
							checked: selected.includes(option.value),
						})),
					},
				],
				primary_action_label: __("Зберегти"),
				primary_action: () => {
					const picked = (dialog.get_values() || {}).values || [];
					frappe.model.set_value(cdt, cdn, "value_multi", JSON.stringify(picked));
					dialog.hide();
				},
			});
			dialog.show();
		});
	}

	/** Suggest the allowed values without pretending to enforce them.
	 *
	 * Swapping a grid cell's fieldtype at render time is the brittle trick this module
	 * deliberately avoids; the server rejects anything outside the option list anyway,
	 * so the client only has to make the right values easy to pick.
	 */
	function bindSelectSuggestions(control, specification) {
		if (!control || !control.$input) {
			return;
		}
		getOptions(specification).then((options) => {
			const list = options.map((option) => option.value);
			if (control.uaSpecsAwesomplete) {
				control.uaSpecsAwesomplete.list = list;
				return;
			}
			control.uaSpecsAwesomplete = new Awesomplete(control.$input.get(0), {
				minChars: 0,
				maxItems: 50,
				list,
			});
			control.$input.on("focus", function () {
				$(this).trigger("input");
			});
		});
	}

	frappe.ui.form.on("Item", {
		setup(frm) {
			frm.set_query("specification", SPEC_TABLE, () => {
				const used = (frm.doc[SPEC_TABLE] || []).map((row) => row.specification).filter(Boolean);
				const filters = { is_active: 1 };
				if (used.length) {
					filters.name = ["not in", used];
				}
				return { filters };
			});
		},

		refresh(frm) {
			if (frm.is_new()) {
				return;
			}
			frm.add_custom_button(__("Оновити з категорії"), () => applyCategorySet(frm), __("Характеристики"));
			if (frm.doc.variant_of) {
				frm.add_custom_button(
					__("Оновити з шаблону"),
					() => {
						frappe.confirm(
							__("Значення характеристик буде взято з шаблону {0}. Продовжити?", [frm.doc.variant_of]),
							() => {
								frappe
									.call({ method: `${API}.copy_specifications_from_template`, args: { item: frm.doc.name } })
									.then(() => frm.reload_doc());
							}
						);
					},
					__("Характеристики")
				);
			}
		},

		item_group(frm) {
			// Changing the category never removes what is already filled in; it only pulls
			// in the mandatory rows the new category adds.
			applyCategorySet(frm, { announce: false });
		},
	});

	frappe.ui.form.on(VALUE_TABLE, {
		form_render(frm, cdt, cdn) {
			const row = locals[cdt][cdn];
			const grid = frm.fields_dict[SPEC_TABLE] && frm.fields_dict[SPEC_TABLE].grid;
			const gridRow = grid && grid.grid_rows_by_docname[cdn];
			if (!gridRow) {
				return;
			}
			if (row.field_type === "Select") {
				bindSelectSuggestions(gridRow.get_field("value_select"), row.specification);
			}
			if (row.field_type === "MultiSelect") {
				const control = gridRow.get_field("value_multi");
				if (control && control.$input) {
					control.$input
						.prop("readonly", true)
						.css("cursor", "pointer")
						.off("click.uaspecs")
						.on("click.uaspecs", () => editMultiSelect(frm, cdt, cdn));
				}
			}
		},

		specification(frm, cdt, cdn) {
			const row = locals[cdt][cdn];
			if (!row.specification) {
				return;
			}
			// A hand-added row is Manual until the server confirms it belongs to the category set.
			frappe.model.set_value(cdt, cdn, "source", "Manual");
		},
	});
})();
