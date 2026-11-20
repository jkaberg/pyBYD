"""Fast path for the Bangcle white-box cipher.

In ``_bangcle_block`` every output byte of a round is made from four table
bytes, one nibble at a time, through a chain of three 256-entry pages of the
XOR table: page 0 combines operands 0 and 1, page 1 its result and operand 2,
page 2 that result and operand 3. Each page turns out to be a plain XOR of
its two input nibbles under fixed maps of the nibbles, so a whole chain is::

    decode(chain(a0, a1, a2, a3)) == f0(a0) ^ f1(a1) ^ f2(a2) ^ f3(a3)

:func:`fold_tables` finds those maps and applies them to the round tables
ahead of time. An output byte of a round is then four table lookups XORed
together, as in a plain table AES, instead of a chain of dependent lookups.

The invariant between rounds: every byte of the folded state is ``decode`` of
the byte ``_bangcle_block`` has at the same position, with one ``decode`` per
round and state byte. The next round's tables (and the last table) are indexed
through the inverses, so they read the byte the original tables expect.

Nothing is assumed about the table file. ``_coordinates`` only proposes a
``decode`` for the last page of a chain; ``_split_page`` then checks the
identity above for all 256 entries of each of the three pages. If any page of
a table file is not such an XOR, :func:`fold_tables` returns ``None`` and the
caller keeps using ``_bangcle_block``, which stays the reference
implementation.

The two directions then run differently. CBC decryption has no dependency
between blocks, so :func:`decrypt_cbc` takes each state byte of all blocks as
one ``bytes`` column and does a lookup for the whole column with
``bytes.translate``. CBC encryption needs the previous block's result, so
:func:`encrypt_cbc` goes block by block, with the tables merged into one
integer per state byte value.
"""

from __future__ import annotations

from typing import NamedTuple

from pybyd._crypto._bangcle_block import BangcleTables

# (state byte position, 256-byte table): one lookup, ``table[state[position]]``.
_Lookup = tuple[int, bytes]

# For each of a block's sixteen state bytes, a table of 256 integers: that
# byte's contribution to the whole next state as a little-endian number.
_StateTables = tuple[list[int], ...]

_WORD_PAGE_LEN = 1024  # 256 little-endian 32-bit words
_XOR_PAGE_LEN = 256
_XOR_PAGES_PER_ROUND = 96  # 4 rows x 4 columns x 2 nibbles x 3 chain steps

_LOW_NIBBLE = bytes(value & 15 for value in range(256))
_IDENTITY = bytes(range(256))
# _XOR_WITH[v] is a translate table that XORs a nibble with v.
_XOR_WITH = [bytes(value ^ v for value in range(256)) for v in range(16)]


class _NotFoldable(Exception):
    """A page of the XOR table is not an XOR of its inputs under fixed maps."""


class _FoldedCipher(NamedTuple):
    """One direction of the cipher. State byte ``i`` is block byte ``i``."""

    # rounds[n][i]: the four lookups that XOR to byte i of the state after round n.
    rounds: tuple[tuple[tuple[_Lookup, ...], ...], ...]
    # last[i]: the lookup that gives byte i of the output block.
    last: tuple[_Lookup, ...]


class FoldedTables(NamedTuple):
    """What :func:`encrypt_cbc` and :func:`decrypt_cbc` need, derived from one :class:`BangcleTables`."""

    encrypt_rounds: tuple[_StateTables, ...]
    encrypt_last: _StateTables
    decrypt: _FoldedCipher


def fold_tables(tables: BangcleTables) -> FoldedTables | None:
    """Fold the XOR pages of *tables* into its round tables.

    Returns ``None`` if the tables do not have the structure the fast path
    relies on. The result gives the same output as ``_bangcle_block`` for
    every input: the structure is checked for every entry of every page.
    """
    try:
        encrypt = _fold(range(9), tables.round, tables.xor, tables.final, tables.perm_encrypt, (0, 1, 2, 3))
        decrypt = _fold(
            range(9, 0, -1), tables.inv_round, tables.inv_xor, tables.inv_first, tables.perm_decrypt, (0, 3, 2, 1)
        )
    except (_NotFoldable, ValueError, IndexError):
        # a malformed table file falls back to the reference cipher too
        return None
    return FoldedTables(
        tuple(_state_tables(layer) for layer in encrypt.rounds),
        _state_tables(tuple((lookup,) for lookup in encrypt.last)),
        decrypt,
    )


def _fold(
    rounds: range,
    words: bytes,
    xor: bytes,
    last: bytes,
    perm: bytes,
    last_shifts: tuple[int, int, int, int],
) -> _FoldedCipher:
    """Fold one direction: *rounds* of the *words* and *xor* tables, then the *last* table."""
    shifts = [perm[operand * 2] & 3 for operand in range(4)]
    # restore[4 * row + col] maps a folded state byte back to the original one.
    restore = [_IDENTITY] * 16
    folded_rounds: list[tuple[tuple[_Lookup, ...], ...]] = []

    for rnd in rounds:
        words_base = rnd * 16 * _WORD_PAGE_LEN
        xor_base = rnd * _XOR_PAGES_PER_ROUND * _XOR_PAGE_LEN
        previous = restore
        restore = [_IDENTITY] * 16
        layer: list[tuple[_Lookup, ...]] = []

        for row in range(4):
            for col in range(4):
                first = xor_base + (row * 24 + col * 6) * _XOR_PAGE_LEN
                pages = [xor[first + step * _XOR_PAGE_LEN : first + (step + 1) * _XOR_PAGE_LEN] for step in range(6)]
                low_maps, low_restore = _split_chain(pages[0], pages[2], pages[4])
                high_maps, high_restore = _split_chain(pages[1], pages[3], pages[5])
                restore[4 * row + col] = _byte_map(low_restore, high_restore)

                lookups: list[_Lookup] = []
                for operand in range(4):
                    # The operand is byte `col` of a word that the round table
                    # gives for state byte `position`. Map it for its place in
                    # the chain, then reorder the table so that the folded
                    # state byte indexes it.
                    position = 4 * ((row + shifts[operand]) & 3) + operand
                    start = words_base + position * _WORD_PAGE_LEN
                    column = words[start + col : start + _WORD_PAGE_LEN : 4]
                    column = column.translate(_byte_map(low_maps[operand], high_maps[operand]))
                    lookups.append((position, previous[position].translate(column)))
                layer.append(tuple(lookups))

        folded_rounds.append(tuple(layer))

    last_layer: list[_Lookup] = []
    for target in range(16):
        row, col = divmod(target, 4)
        position = 4 * ((row + last_shifts[col]) & 3) + col
        page = last[position * 256 : (position + 1) * 256]
        last_layer.append((position, restore[position].translate(page)))

    return _FoldedCipher(tuple(folded_rounds), tuple(last_layer))


def _state_tables(layer: tuple[tuple[_Lookup, ...], ...]) -> _StateTables:
    """Merge the lookups of one layer into one table of integers per state byte."""
    tables = [[0] * 256 for _ in range(16)]
    for target, lookups in enumerate(layer):
        shift = 8 * target
        for position, table in lookups:
            tables[position] = [merged | value << shift for merged, value in zip(tables[position], table, strict=True)]
    return tuple(tables)


def _byte_map(low: bytes, high: bytes) -> bytes:
    """Translate table that maps a byte's low nibble with *low* and its high nibble with *high*."""
    return bytes(hi << 4 | lo for hi in high for lo in low)


def _invert(values: bytes) -> bytes:
    """Invert a map of the sixteen nibbles, which must be a bijection."""
    if len(set(values)) != 16:
        raise _NotFoldable
    inverse = bytearray(16)
    for index, value in enumerate(values):
        inverse[value] = index
    return bytes(inverse)


def _coordinates(page: bytes) -> bytes:
    """Propose XOR coordinates for the sixteen output nibbles of *page*.

    *page* holds ``output[left + 16 * right]``, already reduced to nibbles.
    The result gives every output nibble a number such that, if the page is
    an XOR at all, the number of an output is the XOR of the numbers of its
    inputs. ``_split_page`` checks whether it is.
    """
    # With either input held at zero, its partner enumerates the outputs.
    left = _invert(page[:16])
    right = _invert(page[::16])
    # The output at (0, 0) is the origin. Take the first output not reached
    # yet and translate everything reached so far by it, until all are reached.
    values = [page[0]]
    seen = {page[0]}
    for fresh in range(16):
        if fresh in seen:
            continue
        right_input = right[fresh]
        for value in values[:]:
            translated = page[left[value] + 16 * right_input]
            if translated in seen:
                raise _NotFoldable
            seen.add(translated)
            values.append(translated)
        if len(values) == 16:
            return _invert(bytes(values))
    raise _NotFoldable


def _split_page(page: bytes, decode: bytes) -> tuple[bytes, bytes]:
    """Write *page* as ``decode[output] == left[a] ^ right[b]``, checking all 256 entries."""
    decoded = page.translate(decode * 16)
    left = decoded[:16]
    right = decoded[::16].translate(_XOR_WITH[left[0]])
    if decoded != b"".join(left.translate(_XOR_WITH[value]) for value in right):
        raise _NotFoldable
    return left, right


def _split_chain(page0: bytes, page1: bytes, page2: bytes) -> tuple[tuple[bytes, bytes, bytes, bytes], bytes]:
    """Split a chain of three pages into its four operand maps and the inverse of its ``decode``."""
    # Only the low nibble of an entry is ever used.
    page0, page1, page2 = (page.translate(_LOW_NIBBLE) for page in (page0, page1, page2))
    decode = _coordinates(page2)
    # Each page's left input is the output of the page before. Decoding that
    # output with the map its consumer applies makes the carried maps cancel,
    # which leaves the four operand maps.
    carry, fourth = _split_page(page2, decode)
    carry, third = _split_page(page1, carry)
    first, second = _split_page(page0, carry)
    return (first, second, third, fourth), _invert(decode)


def decrypt_cbc(tables: FoldedTables, data: bytes, iv: bytes) -> bytes:
    """Decrypt data in CBC mode; the same as ``_bangcle_block.decrypt_cbc``."""
    if len(data) % 16 != 0:
        raise ValueError(f"Ciphertext length {len(data)} is not a multiple of 16")
    if len(iv) != 16:
        raise ValueError(f"IV must be 16 bytes, got {len(iv)}")
    if not data:
        return b""

    # columns[i] holds state byte i of every block, so one translate is one
    # lookup for all blocks at once.
    count = len(data) // 16
    columns = [data[position::16] for position in range(16)]
    from_bytes = int.from_bytes
    for layer in tables.decrypt.rounds:
        columns = [
            (
                from_bytes(columns[p0].translate(t0), "little")
                ^ from_bytes(columns[p1].translate(t1), "little")
                ^ from_bytes(columns[p2].translate(t2), "little")
                ^ from_bytes(columns[p3].translate(t3), "little")
            ).to_bytes(count, "little")
            for (p0, t0), (p1, t1), (p2, t2), (p3, t3) in layer
        ]
    decrypted = bytearray(len(data))
    for target, (position, table) in enumerate(tables.decrypt.last):
        decrypted[target::16] = columns[position].translate(table)

    # Every block is XORed with the ciphertext block before it, the first with the IV.
    chained = from_bytes(decrypted, "little") ^ from_bytes(iv + data[:-16], "little")
    return chained.to_bytes(len(data), "little")


def encrypt_cbc(tables: FoldedTables, data: bytes, iv: bytes) -> bytes:
    """Encrypt data in CBC mode; the same as ``_bangcle_block.encrypt_cbc``."""
    if len(data) % 16 != 0:
        raise ValueError(f"Plaintext length {len(data)} is not a multiple of 16")
    if len(iv) != 16:
        raise ValueError(f"IV must be 16 bytes, got {len(iv)}")

    rounds = tables.encrypt_rounds
    l0, l1, l2, l3, l4, l5, l6, l7, l8, l9, l10, l11, l12, l13, l14, l15 = tables.encrypt_last
    result = bytearray()
    prev = int.from_bytes(iv, "little")

    for offset in range(0, len(data), 16):
        state = (int.from_bytes(data[offset : offset + 16], "little") ^ prev).to_bytes(16, "little")
        for t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15 in rounds:
            state = (
                t0[state[0]]
                ^ t1[state[1]]
                ^ t2[state[2]]
                ^ t3[state[3]]
                ^ t4[state[4]]
                ^ t5[state[5]]
                ^ t6[state[6]]
                ^ t7[state[7]]
                ^ t8[state[8]]
                ^ t9[state[9]]
                ^ t10[state[10]]
                ^ t11[state[11]]
                ^ t12[state[12]]
                ^ t13[state[13]]
                ^ t14[state[14]]
                ^ t15[state[15]]
            ).to_bytes(16, "little")
        prev = (
            l0[state[0]]
            ^ l1[state[1]]
            ^ l2[state[2]]
            ^ l3[state[3]]
            ^ l4[state[4]]
            ^ l5[state[5]]
            ^ l6[state[6]]
            ^ l7[state[7]]
            ^ l8[state[8]]
            ^ l9[state[9]]
            ^ l10[state[10]]
            ^ l11[state[11]]
            ^ l12[state[12]]
            ^ l13[state[13]]
            ^ l14[state[14]]
            ^ l15[state[15]]
        )
        result += prev.to_bytes(16, "little")

    return bytes(result)
