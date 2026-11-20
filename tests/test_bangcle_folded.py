"""Tests for pybyd._crypto._bangcle_folded – the fast cipher gives what _bangcle_block gives."""

from __future__ import annotations

import base64
import importlib.resources
import random
import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from pybyd._crypto import _bangcle_block as reference
from pybyd._crypto import _bangcle_folded as folded
from pybyd._crypto._bangcle_block import BangcleTables
from pybyd._crypto._bangcle_folded import FoldedTables, fold_tables
from pybyd._crypto._pkcs7 import add_pkcs7, strip_pkcs7
from pybyd._crypto.bangcle import BangcleCodec, _load_tables_from_bin

_ZERO_IV = bytes(16)
_PAGE = 256
_PAGES_PER_ROUND = 96
# Index of each table in the file, in the order of bangcle._TABLE_SPECS.
_FILE_INDEX = {"inv_round": 0, "inv_xor": 1, "inv_first": 2, "round": 3, "xor": 4, "final": 5}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shipped_file() -> bytes:
    return importlib.resources.files("pybyd").joinpath("data/bangcle_tables.bin").read_bytes()


@pytest.fixture(scope="module")
def tables() -> BangcleTables:
    return _load_tables_from_bin(_shipped_file())


@pytest.fixture(scope="module")
def fast(tables: BangcleTables) -> FoldedTables:
    result = fold_tables(tables)
    assert result is not None
    return result


def _assert_same_cipher(tables: BangcleTables, fast: FoldedTables, data: bytes, iv: bytes) -> None:
    assert folded.encrypt_cbc(fast, data, iv) == reference.encrypt_cbc(tables, data, iv)
    assert folded.decrypt_cbc(fast, data, iv) == reference.decrypt_cbc(tables, data, iv)


def _with_xor_entry(tables: BangcleTables, name: str, rnd: int, page: int, entry: int, flip: int) -> BangcleTables:
    """Copy of *tables* with one entry of one XOR page changed."""
    table = bytearray(getattr(tables, name))
    table[(rnd * _PAGES_PER_ROUND + page) * _PAGE + entry] ^= flip
    return tables._replace(**{name: bytes(table)})


# ---------------------------------------------------------------------------
# The shipped tables
# ---------------------------------------------------------------------------


def test_shipped_tables_fold(tables: BangcleTables) -> None:
    assert fold_tables(tables) is not None


def test_random_blocks_match_reference(tables: BangcleTables, fast: FoldedTables) -> None:
    rng = random.Random(0xB1D)
    for _ in range(300):
        _assert_same_cipher(tables, fast, rng.randbytes(16), _ZERO_IV)


def test_every_byte_value_at_every_position_matches_reference(tables: BangcleTables, fast: FoldedTables) -> None:
    # Block n is sixteen times byte n, so every entry of every first-round table is read.
    data = b"".join(bytes([value]) * 16 for value in range(256))
    for offset in range(0, len(data), 16):
        _assert_same_cipher(tables, fast, data[offset : offset + 16], _ZERO_IV)


def test_cbc_matches_reference_at_every_length_through_24_blocks(tables: BangcleTables, fast: FoldedTables) -> None:
    rng = random.Random(0xCBC)
    for blocks in range(25):
        _assert_same_cipher(tables, fast, rng.randbytes(16 * blocks), rng.randbytes(16))


@pytest.mark.parametrize("cbc", [folded.encrypt_cbc, folded.decrypt_cbc])
def test_cbc_rejects_what_reference_rejects(
    fast: FoldedTables, cbc: Callable[[FoldedTables, bytes, bytes], bytes]
) -> None:
    with pytest.raises(ValueError, match="not a multiple of 16"):
        cbc(fast, bytes(17), _ZERO_IV)
    with pytest.raises(ValueError, match="IV must be 16 bytes"):
        cbc(fast, bytes(16), bytes(15))


# ---------------------------------------------------------------------------
# Other table files
# ---------------------------------------------------------------------------


def test_fold_follows_the_tables_it_is_given(tables: BangcleTables, fast: FoldedTables) -> None:
    rng = random.Random(0xF01D)
    changed = {name: bytearray(getattr(tables, name)) for name in _FILE_INDEX}
    for name in ("round", "inv_round", "final", "inv_first"):
        for _ in range(64):
            changed[name][rng.randrange(len(changed[name]))] ^= rng.randrange(1, 256)
    # Reordering a page along its right input is another map of that operand's
    # nibble, so the page is still an XOR. Collapsing it makes the map constant.
    for name in ("xor", "inv_xor"):
        for rnd in range(10):
            for page in rng.sample(range(_PAGES_PER_ROUND), 12):
                start = (rnd * _PAGES_PER_ROUND + page) * _PAGE
                rows = [changed[name][start + 16 * right :][:16] for right in range(16)]
                order = rng.sample(range(16), 16) if page % 6 >= 4 else [rng.randrange(16) for _ in range(16)]
                changed[name][start : start + _PAGE] = b"".join(rows[right] for right in order)
    # A first page of zeros is an XOR as well: both of its maps are constant.
    changed["xor"][7 * _PAGE : 8 * _PAGE] = bytes(_PAGE)
    other = tables._replace(**{name: bytes(table) for name, table in changed.items()})

    other_fast = fold_tables(other)
    assert other_fast is not None
    data, iv = rng.randbytes(16 * 48), rng.randbytes(16)
    _assert_same_cipher(other, other_fast, data, iv)
    assert folded.encrypt_cbc(other_fast, data, iv) != folded.encrypt_cbc(fast, data, iv)
    assert folded.decrypt_cbc(other_fast, data, iv) != folded.decrypt_cbc(fast, data, iv)


@pytest.mark.parametrize(
    ("name", "rnd", "page", "entry"),
    [
        # Low and high nibble chains, each of their three pages, with the
        # changed entry on the left edge, the right edge and inside the page.
        ("xor", 0, 4, 3),
        ("xor", 0, 4, 16 * 5),
        ("xor", 0, 4, 16 * 5 + 3),
        ("xor", 3, 2, 16 * 9 + 7),
        ("xor", 8, 95, 16 * 2 + 1),
        ("xor", 5, 30, 0),
        ("inv_xor", 9, 1, 16 * 15 + 15),
        ("inv_xor", 4, 51, 16 * 3),
        ("inv_xor", 1, 93, 11),
    ],
)
def test_page_that_is_not_an_xor_is_not_folded(
    tables: BangcleTables, name: str, rnd: int, page: int, entry: int
) -> None:
    assert fold_tables(_with_xor_entry(tables, name, rnd, page, entry, flip=1)) is None


@pytest.mark.parametrize("name", ["round", "xor", "final", "inv_round", "inv_xor", "inv_first"])
def test_truncated_table_is_not_folded(tables: BangcleTables, name: str) -> None:
    table = getattr(tables, name)
    assert fold_tables(tables._replace(**{name: table[: len(table) // 2]})) is None


@pytest.mark.parametrize(
    ("name", "rnd", "flip"),
    [
        # Encryption runs rounds 0-8 and decryption rounds 9-1; the cipher
        # never reads the other round, or the high nibble of any entry.
        ("xor", 9, 1),
        ("inv_xor", 0, 1),
        ("xor", 2, 0x50),
        ("inv_xor", 7, 0xF0),
    ],
)
def test_entries_the_cipher_never_reads_do_not_matter(
    tables: BangcleTables, fast: FoldedTables, name: str, rnd: int, flip: int
) -> None:
    other = _with_xor_entry(tables, name, rnd, page=40, entry=16 * 6 + 2, flip=flip)
    other_fast = fold_tables(other)
    assert other_fast is not None
    data = random.Random(rnd).randbytes(16 * 8)
    _assert_same_cipher(other, other_fast, data, _ZERO_IV)
    assert folded.encrypt_cbc(other_fast, data, _ZERO_IV) == folded.encrypt_cbc(fast, data, _ZERO_IV)
    assert folded.decrypt_cbc(other_fast, data, _ZERO_IV) == folded.decrypt_cbc(fast, data, _ZERO_IV)


# ---------------------------------------------------------------------------
# BangcleCodec
# ---------------------------------------------------------------------------


def test_codec_envelopes_are_unchanged() -> None:
    # Recorded from the reference cipher before the fast path existed.
    codec = BangcleCodec()
    plaintext = '{"vin":"TESTVIN0000000001","note":"åäö"}'
    envelope = "FUgJ7QRq8SlNx5QyicqzuxabsI7IVAJaINiG568AujUhskUVFwx/nWTJDv2cXac2q"
    assert codec.encode_envelope(plaintext) == envelope
    assert codec.decode_envelope(envelope) == plaintext.encode("utf-8")


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 31, 32, 1000, 9700])
def test_codec_matches_reference_cipher(tables: BangcleTables, size: int) -> None:
    codec = BangcleCodec()
    plaintext = random.Random(size).randbytes(size)
    ciphertext = reference.encrypt_cbc(tables, add_pkcs7(plaintext), _ZERO_IV)
    envelope = "F" + base64.b64encode(ciphertext).decode("ascii")

    assert codec.encode_envelope(plaintext) == envelope
    assert codec.decode_envelope(envelope) == plaintext
    assert codec._folded is not None


def test_codec_falls_back_for_a_table_file_it_cannot_fold(tmp_path: Path) -> None:
    data = bytearray(_shipped_file())
    for name in ("xor", "inv_xor"):
        offset = struct.unpack_from("<I", data, 8 + 8 * _FILE_INDEX[name])[0]
        # The last page of a chain gives a nibble of the state. Zeros there
        # lose it, which no map of the next round's input can undo.
        start = offset + (3 * _PAGES_PER_ROUND + 11) * _PAGE
        data[start : start + _PAGE] = bytes(_PAGE)
    path = tmp_path / "tables.bin"
    path.write_bytes(data)
    other = _load_tables_from_bin(bytes(data))
    plaintext = random.Random(1).randbytes(100)

    codec = BangcleCodec(tables_path=path)
    envelope = codec.encode_envelope(plaintext)
    ciphertext = base64.b64decode(envelope[1:])

    assert codec._folded is None
    assert ciphertext == reference.encrypt_cbc(other, add_pkcs7(plaintext), _ZERO_IV)
    assert envelope != BangcleCodec().encode_envelope(plaintext)
    # The changed entries make the two directions different ciphers, so this
    # is not the plaintext; the reference cipher says what it is.
    assert codec.decode_envelope(envelope) == strip_pkcs7(reference.decrypt_cbc(other, ciphertext, _ZERO_IV))
