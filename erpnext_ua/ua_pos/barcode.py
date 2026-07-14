"""Compact, dependency-free Code 128 barcodes for POS return lookup."""

from __future__ import annotations

import base64
import html
import uuid


CODE128_PATTERNS = (
	"212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212",
	"221213", "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221",
	"223211", "221132", "221231", "213212", "223112", "312131", "311222", "321122", "321221",
	"312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321",
	"112313", "132113", "132311", "211313", "231113", "231311", "112133", "112331", "132131",
	"113123", "113321", "133121", "313121", "211331", "231131", "213113", "213311", "213131",
	"311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111",
	"111224", "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
	"122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111", "111242",
	"121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
	"214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311",
	"113141", "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)


def encode_lookup_token(value: str) -> str:
	"""Encode a UUID as a scanner-friendly 22-character base64url token."""
	try:
		return base64.urlsafe_b64encode(uuid.UUID(str(value)).bytes).decode("ascii").rstrip("=")
	except (ValueError, TypeError, AttributeError):
		return str(value or "").strip()


def decode_lookup_token(value: str) -> str:
	"""Restore the stored UUID while remaining compatible with legacy text scans."""
	value = str(value or "").strip()
	if len(value) != 22:
		return value
	try:
		return str(uuid.UUID(bytes=base64.urlsafe_b64decode(value + "==")))
	except (ValueError, TypeError):
		return value


def code128_svg_data_uri(value: str) -> str:
	"""Render Code 128-B locally so lookup data never leaves ERPNext."""
	value = str(value or "")
	if not value or any(ord(char) < 32 or ord(char) > 126 for char in value):
		raise ValueError("Code 128 підтримує лише друковані ASCII-символи")
	codes = [104, *(ord(char) - 32 for char in value)]
	checksum = (104 + sum(index * code for index, code in enumerate(codes[1:], start=1))) % 103
	codes.extend((checksum, 106))
	quiet = 10
	x = quiet
	rects = []
	for code in codes:
		bar = True
		for width_text in CODE128_PATTERNS[code]:
			width = int(width_text)
			if bar:
				rects.append(f'<rect x="{x}" y="0" width="{width}" height="56"/>')
			x += width
			bar = not bar
	width = x + quiet
	label = html.escape(value)
	svg = (
		f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 72" role="img" '
		f'aria-label="Код повернення {label}"><rect width="100%" height="100%" fill="white"/>'
		f'<g fill="black">{"".join(rects)}</g><text x="{width / 2:g}" y="69" text-anchor="middle" '
		f'font-family="monospace" font-size="9">{label}</text></svg>'
	)
	return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode("ascii")
