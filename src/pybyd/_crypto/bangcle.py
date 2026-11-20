"""Bangcle envelope encoding/decoding using white-box AES."""

from __future__ import annotations

import asyncio
import base64
import importlib.resources
import logging
import struct
from pathlib import Path

from pybyd._crypto._bangcle_block import (
    BangcleTables,
    decrypt_cbc,
    encrypt_cbc,
)
from pybyd._crypto._bangcle_folded import FoldedTables, fold_tables
from pybyd._crypto._bangcle_folded import decrypt_cbc as decrypt_cbc_folded
from pybyd._crypto._bangcle_folded import encrypt_cbc as encrypt_cbc_folded
from pybyd._crypto._pkcs7 import add_pkcs7, strip_pkcs7
from pybyd.exceptions import BangcleError, BangcleTableLoadError

_logger = logging.getLogger(__name__)

_ZERO_IV = b"\x00" * 16

# Binary table file format:
#   Magic: b"BGTB" (4 bytes)
#   Version: uint16 LE (2 bytes)
#   Table count: uint16 LE (2 bytes) = 8
#   Index: 8 entries of (offset: uint32 LE, length: uint32 LE) = 64 bytes
#   Data: concatenated raw table bytes
_MAGIC = b"BGTB"
_VERSION = 1
_TABLE_COUNT = 8
_HEADER_SIZE = 4 + 2 + 2  # magic + version + count
_INDEX_ENTRY_SIZE = 4 + 4  # offset + length
_INDEX_SIZE = _TABLE_COUNT * _INDEX_ENTRY_SIZE

# Expected sizes for each table, in order.
_TABLE_SPECS: list[tuple[str, int]] = [
    ("inv_round", 0x28000),
    ("inv_xor", 0x3C000),
    ("inv_first", 0x1000),
    ("round", 0x28000),
    ("xor", 0x3C000),
    ("final", 0x1000),
    ("perm_decrypt", 8),
    ("perm_encrypt", 8),
]


def _load_tables_from_bin(data: bytes) -> BangcleTables:
    """Parse the binary table file into a BangcleTables instance."""
    if len(data) < _HEADER_SIZE + _INDEX_SIZE:
        raise BangcleTableLoadError("Table file too short")

    magic = data[:4]
    if magic != _MAGIC:
        raise BangcleTableLoadError(f"Bad magic: expected {_MAGIC!r}, got {magic!r}")

    version = struct.unpack_from("<H", data, 4)[0]
    if version != _VERSION:
        raise BangcleTableLoadError(f"Unsupported table version: {version}")

    count = struct.unpack_from("<H", data, 6)[0]
    if count != _TABLE_COUNT:
        raise BangcleTableLoadError(f"Expected {_TABLE_COUNT} tables, got {count}")

    tables: list[bytes] = []
    for i in range(count):
        idx_offset = _HEADER_SIZE + i * _INDEX_ENTRY_SIZE
        offset, length = struct.unpack_from("<II", data, idx_offset)
        expected_name, expected_len = _TABLE_SPECS[i]

        if length != expected_len:
            raise BangcleTableLoadError(f"Table {expected_name}: expected {expected_len} bytes, got {length}")
        if offset + length > len(data):
            raise BangcleTableLoadError(f"Table {expected_name}: data extends beyond file")
        tables.append(data[offset : offset + length])

    return BangcleTables(*tables)


def _normalise_envelope_input(envelope: str) -> str:
    """Normalise a Bangcle envelope string for base64 decoding."""
    cleaned = envelope.replace(" ", "").replace("\t", "").replace("\n", "").replace("\r", "").strip()
    # URL-safe base64 normalization
    cleaned = cleaned.replace("-", "+").replace("_", "/")

    if not cleaned:
        raise BangcleError("Bangcle input is empty")
    if not cleaned.startswith("F"):
        raise BangcleError('Bangcle envelope must start with "F"')

    cleaned = cleaned[1:]  # strip F prefix
    remainder = len(cleaned) % 4
    if remainder != 0:
        cleaned += "=" * (4 - remainder)

    return cleaned


class BangcleCodec:
    """Encode and decode Bangcle envelopes using white-box AES."""

    def __init__(self, tables_path: Path | None = None) -> None:
        self._tables_path = tables_path
        self._tables: BangcleTables | None = None
        # The fast form of the tables, or None for a table file it cannot be derived from.
        self._folded: FoldedTables | None = None

    def _load_tables(self) -> BangcleTables:
        if self._tables is not None:
            return self._tables

        if self._tables_path is not None:
            _logger.debug("Loading Bangcle tables from %s", self._tables_path)
            try:
                raw = self._tables_path.read_bytes()
            except FileNotFoundError as exc:
                raise BangcleTableLoadError(f"Table file not found: {self._tables_path}") from exc
        else:
            _logger.debug("Loading Bangcle tables from package data")
            try:
                ref = importlib.resources.files("pybyd").joinpath("data/bangcle_tables.bin")
                raw = ref.read_bytes()
            except FileNotFoundError as exc:
                raise BangcleTableLoadError(
                    "bangcle_tables.bin not found in pybyd package data. "
                    "Reinstall/upgrade pybyd so wheel data files are included."
                ) from exc

        tables = _load_tables_from_bin(raw)
        self._folded = fold_tables(tables)
        if self._folded is None:
            _logger.debug("Bangcle tables cannot be folded; using the slow cipher")
        self._tables = tables
        _logger.debug("Bangcle tables loaded successfully")
        return tables

    async def async_load_tables(self) -> None:
        """Pre-load tables in an executor so the event loop is not blocked."""
        if self._tables is not None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_tables)

    def encode_envelope(self, plaintext: str | bytes) -> str:
        """Encode plaintext into a Bangcle envelope (``F`` + base64)."""
        tables = self._load_tables()
        plain_bytes = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        padded = add_pkcs7(plain_bytes)
        if self._folded is not None:
            ciphertext = encrypt_cbc_folded(self._folded, padded, _ZERO_IV)
        else:
            ciphertext = encrypt_cbc(tables, padded, _ZERO_IV)
        return "F" + base64.b64encode(ciphertext).decode("ascii")

    def decode_envelope(self, envelope: str) -> bytes:
        """Decode a Bangcle envelope back to plaintext bytes."""
        tables = self._load_tables()
        b64_payload = _normalise_envelope_input(envelope)
        try:
            ciphertext = base64.b64decode(b64_payload)
        except Exception as exc:
            raise BangcleError(f"Invalid base64 in Bangcle envelope: {exc}") from exc

        if not ciphertext:
            raise BangcleError("Bangcle ciphertext is empty")
        if len(ciphertext) % 16 != 0:
            raise BangcleError(f"Bangcle ciphertext length {len(ciphertext)} is not a multiple of 16")

        if self._folded is not None:
            plaintext = decrypt_cbc_folded(self._folded, ciphertext, _ZERO_IV)
        else:
            plaintext = decrypt_cbc(tables, ciphertext, _ZERO_IV)
        return strip_pkcs7(plaintext)
