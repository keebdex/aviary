"""Ids that must match the ones catalog-sync made."""

import zlib


def crc32_hex(text: str) -> str:
    """npm `crc`'s crc32(text).toString(16): CRC-32 of the UTF-8 bytes, in
    lowercase hex without leading zeros."""
    return format(zlib.crc32(text.encode("utf-8")), "x")
