from __future__ import annotations

import unittest

from erpnext_ua.ua_item_specs.domain import (
	CHECK,
	DATA,
	DATE,
	FLOAT,
	HTML,
	INT,
	MULTISELECT,
	SELECT,
	TEXT,
	VALUE_FIELD_BY_TYPE,
	configured_bounds,
	format_display_value,
	is_blank,
	is_out_of_range,
	is_valid_fieldname,
	merge_group_specifications,
	parse_multi_value,
	repeated_values,
	round_to_precision,
	serialize_multi_value,
	unknown_options,
	value_field_for,
)


class TestFieldnameRules(unittest.TestCase):
	def test_slug_accepts_lowercase_latin_digits_and_underscore(self):
		self.assertTrue(is_valid_fieldname("blade_length"))
		self.assertTrue(is_valid_fieldname("steel2"))

	def test_slug_rejects_anything_that_breaks_api_consumers(self):
		self.assertFalse(is_valid_fieldname("Blade_Length"))
		self.assertFalse(is_valid_fieldname("2steel"))
		self.assertFalse(is_valid_fieldname("blade-length"))
		self.assertFalse(is_valid_fieldname("довжина"))
		self.assertFalse(is_valid_fieldname(""))
		self.assertFalse(is_valid_fieldname(None))


class TestValueStorage(unittest.TestCase):
	def test_every_type_has_its_own_column(self):
		self.assertEqual(len(set(VALUE_FIELD_BY_TYPE.values())), len(VALUE_FIELD_BY_TYPE))
		self.assertEqual(value_field_for(INT), "value_int")
		self.assertEqual(value_field_for(MULTISELECT), "value_multi")

	def test_unknown_type_is_rejected_rather_than_guessed(self):
		with self.assertRaises(ValueError):
			value_field_for("Currency")


class TestMultiValue(unittest.TestCase):
	def test_json_array_round_trips(self):
		self.assertEqual(parse_multi_value('["сталь", "титан"]'), ["сталь", "титан"])
		self.assertEqual(serialize_multi_value(["сталь"]), '["сталь"]')

	def test_comma_separated_import_is_accepted(self):
		self.assertEqual(parse_multi_value("сталь, титан"), ["сталь", "титан"])

	def test_empty_payloads_read_as_no_values(self):
		self.assertEqual(parse_multi_value(None), [])
		self.assertEqual(parse_multi_value(""), [])
		self.assertEqual(parse_multi_value("[]"), [])

	def test_duplicates_and_unknown_options_are_reported(self):
		self.assertEqual(repeated_values(["a", "b", "a"]), ["a"])
		self.assertEqual(repeated_values(["a", "b"]), [])
		self.assertEqual(unknown_options(["a", "x"], ["a", "b"]), ["x"])


class TestBlankness(unittest.TestCase):
	def test_unticked_checkbox_is_an_answer_not_a_gap(self):
		self.assertFalse(is_blank(CHECK, 0))
		self.assertFalse(is_blank(CHECK, None))

	def test_zero_counts_as_filled_because_frappe_has_no_empty_number(self):
		self.assertFalse(is_blank(INT, 0))
		self.assertFalse(is_blank(FLOAT, 0.0))

	def test_missing_and_whitespace_values_are_blank(self):
		self.assertTrue(is_blank(DATA, None))
		self.assertTrue(is_blank(DATA, "   "))
		self.assertTrue(is_blank(INT, None))
		self.assertTrue(is_blank(MULTISELECT, "[]"))
		self.assertFalse(is_blank(MULTISELECT, '["сталь"]'))


class TestNumericRules(unittest.TestCase):
	def test_zero_bound_means_no_limit(self):
		# Frappe stores an unset Float as 0; treating that as a real bound would reject
		# every negative value and, with both bounds at 0, everything except 0 itself.
		self.assertEqual(configured_bounds(0, 0), (None, None))
		self.assertEqual(configured_bounds(None, None), (None, None))
		self.assertEqual(configured_bounds(10, 0), (10.0, None))
		self.assertEqual(configured_bounds(0, 100), (None, 100.0))
		self.assertEqual(configured_bounds(-50, 50), (-50.0, 50.0))

	def test_unset_bounds_do_not_reject_anything(self):
		minimum, maximum = configured_bounds(0, 0)
		self.assertFalse(is_out_of_range(-1000, minimum, maximum))
		self.assertFalse(is_out_of_range(1000, minimum, maximum))

	def test_bounds_are_inclusive(self):
		self.assertFalse(is_out_of_range(50, 50, 100))
		self.assertFalse(is_out_of_range(100, 50, 100))
		self.assertTrue(is_out_of_range(49.9, 50, 100))
		self.assertTrue(is_out_of_range(100.1, 50, 100))

	def test_missing_bound_does_not_constrain(self):
		self.assertFalse(is_out_of_range(1000, None, None))
		self.assertFalse(is_out_of_range(None, 50, 100))

	def test_precision_rounds_only_when_configured(self):
		self.assertEqual(round_to_precision(12.3456, 2), 12.35)
		self.assertEqual(round_to_precision(12.3456, 0), 12.3456)
		self.assertEqual(round_to_precision(12.3456, None), 12.3456)


class TestDisplayValue(unittest.TestCase):
	def test_numbers_carry_their_unit(self):
		self.assertEqual(format_display_value(INT, 47, unit="см"), "47 см")
		self.assertEqual(format_display_value(FLOAT, 4.5, unit="мм", spec_precision=2), "4.50 мм")
		self.assertEqual(format_display_value(INT, 47), "47")

	def test_float_without_precision_drops_trailing_zeros(self):
		self.assertEqual(format_display_value(FLOAT, 4.50), "4.5")
		self.assertEqual(format_display_value(FLOAT, 4.0), "4")

	def test_checkbox_renders_in_ukrainian_and_is_never_empty(self):
		self.assertEqual(format_display_value(CHECK, 1), "Так")
		self.assertEqual(format_display_value(CHECK, 0), "Ні")
		self.assertEqual(format_display_value(CHECK, None), "Ні")

	def test_select_prefers_the_label_over_the_stored_value(self):
		labels = {"s30v": "CPM S30V"}
		self.assertEqual(format_display_value(SELECT, "s30v", option_labels=labels), "CPM S30V")
		self.assertEqual(format_display_value(SELECT, "n690", option_labels=labels), "n690")

	def test_multiselect_joins_labels(self):
		labels = {"a": "Сталь", "b": "Титан"}
		self.assertEqual(
			format_display_value(MULTISELECT, '["a", "b"]', option_labels=labels),
			"Сталь, Титан",
		)

	def test_markup_is_stripped_and_long_text_truncated(self):
		self.assertEqual(format_display_value(HTML, "<p>Опис <b>товару</b></p>"), "Опис товару")
		long_text = "я" * 200
		rendered = format_display_value(TEXT, long_text)
		self.assertEqual(len(rendered), 140)
		self.assertTrue(rendered.endswith("…"))

	def test_dates_render_in_ukrainian_order(self):
		self.assertEqual(format_display_value(DATE, "2026-07-26"), "26.07.2026")

	def test_blank_values_render_as_empty_string(self):
		self.assertEqual(format_display_value(DATA, None), "")
		self.assertEqual(format_display_value(SELECT, ""), "")


class TestInheritance(unittest.TestCase):
	def test_descendant_overrides_ancestor_but_keeps_its_position(self):
		merged = merge_group_specifications(
			[
				("Ножі", [{"specification": "Сталь", "is_mandatory": 0}, {"specification": "Довжина"}]),
				("Ножі / Складані", [{"specification": "Сталь", "is_mandatory": 1}]),
			]
		)

		self.assertEqual([row["specification"] for row in merged], ["Сталь", "Довжина"])
		self.assertEqual(merged[0]["is_mandatory"], 1)
		self.assertEqual(merged[0]["source_item_group"], "Ножі / Складані")
		self.assertEqual(merged[1]["source_item_group"], "Ножі")

	def test_own_rows_follow_inherited_ones(self):
		merged = merge_group_specifications(
			[
				("Ножі", [{"specification": "Сталь"}]),
				("Ножі / Складані", [{"specification": "Замок"}]),
			]
		)
		self.assertEqual([row["specification"] for row in merged], ["Сталь", "Замок"])

	def test_default_value_is_taken_from_the_closest_descendant(self):
		merged = merge_group_specifications(
			[
				("Ножі", [{"specification": "Сталь", "default_value": "N690"}]),
				("Ножі / Складані", [{"specification": "Сталь", "default_value": "S30V"}]),
			]
		)
		self.assertEqual(merged[0]["default_value"], "S30V")

	def test_blank_and_empty_levels_are_ignored(self):
		merged = merge_group_specifications(
			[("Ножі", []), ("Ножі / Складані", [{"specification": "  "}, {"specification": "Сталь"}])]
		)
		self.assertEqual([row["specification"] for row in merged], ["Сталь"])


if __name__ == "__main__":
	unittest.main()
