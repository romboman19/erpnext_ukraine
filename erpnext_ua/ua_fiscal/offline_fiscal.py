"""Офлайн-фіскалізація: контрольні числа і ланцюг гешів (Опис АРІ ЄВПЕЗ).

Фіскальний номер офлайн документа:
	<ІдОфлайнСесії>.<ЛокНомерВСесії>.<КонтрольнеЧисло>

Контрольне число — 4 молодші десяткові розряди CRC32 (як беззнакового числа)
від рядка полів, розділених комами; провідні нулі відкидаються; 0 → 1.
"""

import hashlib
import zlib


def control_number(
	seed: str,
	order_date: str,  # ДДММРРРР
	order_time: str,  # ГГХХСС
	local_num: str | int,
	registrar_fiscal_num: str | int,
	registrar_local_num: str | int,
	total_sum: str | None = None,  # формат "0.00", лише для чеків з сумою
	prev_doc_hash: str | None = None,
) -> int:
	parts = [
		str(seed),
		order_date,
		order_time,
		str(local_num),
		str(registrar_fiscal_num),
		str(registrar_local_num),
	]
	if total_sum is not None:
		parts.append(total_sum)
	if prev_doc_hash:
		parts.append(prev_doc_hash)
	line = ",".join(parts)
	crc = zlib.crc32(line.encode("utf-8")) & 0xFFFFFFFF
	# ДПС рахує CRC32 за DamienG-реалізацією: ComputeHash повертає big-endian
	# байти, які потім читаються як little-endian uint — тобто byteswap zlib-значення
	# (перевірено на тест-векторі з офіційного опису АРІ)
	crc = int.from_bytes(crc.to_bytes(4, "big"), "little")
	check = int(str(crc)[-4:])
	return check or 1


def offline_fiscal_number(session_id: int | str, local_num_in_session: int, check: int) -> str:
	return f"{session_id}.{local_num_in_session}.{check}"


def doc_hash(signed_document: bytes) -> str:
	"""SHA-256 геш документа (підписаного блоку) для PREVDOCHASH, hex."""
	return hashlib.sha256(signed_document).hexdigest()


def build_offline_package(signed_documents: list[bytes]) -> bytes:
	"""Формує пакет офлайн документів для ендпоінта /pck.

	Формат (перевірено на еталонному offline_packet.bin ДПС):
		<розмір док.1 (4 байти LE)><док.1><розмір док.2 (4 байти LE)><док.2>...
	Кожен документ — окремо підписаний CMS (attached). До 100 документів у пакеті.
	Весь пакет далі засвідчується КЕП відправника перед відправкою.
	"""
	import struct

	if len(signed_documents) > 100:
		raise ValueError("У пакеті офлайн документів не може бути більше 100 документів")
	parts = []
	for doc in signed_documents:
		parts.append(struct.pack("<I", len(doc)))
		parts.append(doc)
	return b"".join(parts)
