"""WBSK white-box AES-256 envelope (China BYD app transport layer).

Port of BYD-re ``wbsk.js``; tables from ``data/wbsk_tables.json``.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.resources
import json
import struct
from dataclasses import dataclass
from typing import ClassVar

from pybyd.exceptions import BydCryptoError

NIBBLE_ENCODE = [0x0, 0x8, 0x4, 0xC, 0x1, 0x9, 0x5, 0xD, 0x2, 0xA, 0x6, 0xE, 0x3, 0xB, 0x7, 0xF]
NIBBLE_DECODE = [0x0, 0x4, 0x8, 0xC, 0x2, 0x6, 0xA, 0xE, 0x1, 0x5, 0x9, 0xD, 0x3, 0x7, 0xB, 0xF]

MYSTERY_ENCODE: list[int] = [NIBBLE_ENCODE[NIBBLE_ENCODE[n ^ 8]] for n in range(16)]

WBSK_KEYS: dict[str, str] = {
    "outerEncryptKey": (
        "4dca015d9f0488cdea45e890de3b9c4d16c9f82e1082e295c8312d34da7214b805bdec33d8473ab04c84a51eebee4fd5efee21ed403a159a083dbb2854c92719d8f24dd3002ce675c4b930fd5f410ebe56d9594532f9c109b7f2dc58eebd83a83cc948fd3dc0b696add8b06d19efa7c8c04d17f60d144d943e21ef4add5af566ef14241de9c3bb03cf9b9d3c5d042caa1fcdf222e02ba7cf577cc70375d0b4e7e3340278e56ddee1a180451b3a04f25fe34f0d1f05ec426b0de801e7d7382ecf2c3ab7be923c2d5ff0c33eaa4c45c71b258045f68bd7ad0f594ff86785611f67f30da78dfa9b427f04d625a2c61e2db62e1fe7d4"
    ),
    "outerDecryptKey": (
        "72ca0163b22e2973656a67ac1ae1490a61133824a0cd235bcfcd6032dba79d3ca51b1c4d1b03068566ff084645dcc9e6e8a28b39c71e72dec3fe4074109a84f5564d3f43f4854fb634bf633dabe218a5b73470dff70b07161b76c74d92bffa15bcb7fa4fc448a0fe83b62c9dd97f36d1d1d7613028041bb1dd328397bbf8c8bf6f81321f5e2d4982761a375bded52a1de198169839fabad771bc677b57f806c1ca385f43627e9a5081c43d7c9d9fa86e7e78cc0a8050a2420a76c842abbe93eb38f2487bdf93087cd24097a16539da2f86feb693432daf8f0618cbf97ffea3a762b7b91050f8634a8d3ceb25ea7d2b3264a77337"
    ),
    "innerEncryptKey": (
        "9fca018f72712b15e23c275ea5e06a92d8b98404cf0bf960955596ff47dd2adf8f9e0c3ca1363e8be88cb6fced211933e5c3484c20c7bc3ae1eb8541027fef4a20b2f302d93582f7f4349fef05c16d389956ae9f2a7aea278fd5232229e38caca017ffbaf5138d2cadbca917b4694fb2882a64809c095387b7353608ca3a17913e5863770465986995c684ea7db01e0c35c69fd169a8e14ec5123beb5b8dad6e1c5198f34ed1c44d5f9b15035673df5953e5e42351f58052c1483fa4cf93c646a396081355a46d5a7e0ce30d54049802829cd78c1a77c7db8f74acb244b73c5147f161ee25bd702112cec97c339a6b3314527d12"
    ),
    "innerDecryptKey": (
        "71ca0160b689febe1c11e07bc8f8cec81decf71b6c3e0be299a35211888c2fb177958e57a6e971d0a874ecb50991786faf3a34b178f13a668bd14b81a82d3f799f6f0c8bd002406c8b6fdd54b3bb30c0c7d27c906dba87decde28717a0874abacf41755646b4a2c06854615ab00ae53136cbea3302b047659e7a42f792a7369fc130d8ffdc114a7a2cf2fa669b9b337905ff58fe3cc40b9b1edf37ebe50d36b3416abbd32837895b8ea1f22b9eab35efd791d9153208630297b8b953a9ca33265854c33959979b9eb1d049326986851170f4b51d151f43a30c6298a8c03503477336b2000c49746181ca30eabded6d3088b7f615"
    ),
    "outerEncryptIv": "91339992399838993130933138923692",
    "outerDecryptIv": "54cc5558c551c155c4c05cc4c158ca58",
    "innerEncryptIv": "a8bb9ab895ba95363a81b1949da68184",
}


@dataclass(frozen=True, slots=True)
class ParsedWbcKey:
    key_data: bytes
    key_size_bits: int
    num_rounds: int
    is_decrypt: int
    block_size: int
    mode: int


def _decode_byte_table(encoded: dict[str, str], name: str) -> bytes:
    b64 = encoded.get(name)
    if not isinstance(b64, str) or not b64:
        raise BydCryptoError(f"Missing embedded WBSK table: {name}")
    buf = base64.b64decode(b64)
    if len(buf) != 256:
        raise BydCryptoError(f"WBSK table {name} has unexpected size {len(buf)} (expected 256)")
    return buf


def _decode_u32_table(encoded: dict[str, str], name: str) -> list[int]:
    b64 = encoded.get(name)
    if not isinstance(b64, str) or not b64:
        raise BydCryptoError(f"Missing embedded WBSK table: {name}")
    buf = base64.b64decode(b64)
    if len(buf) != 1024:
        raise BydCryptoError(f"WBSK table {name} has unexpected size {len(buf)} (expected 1024)")
    return [struct.unpack_from("<I", buf, i * 4)[0] for i in range(256)]


def _load_wbsk_tables_json() -> dict[str, str]:
    try:
        ref = importlib.resources.files("pybyd").joinpath("data/wbsk_tables.json")
        raw = ref.read_bytes()
    except FileNotFoundError as exc:
        raise BydCryptoError(
            "wbsk_tables.json not found in pybyd package data. "
            "Reinstall/upgrade pybyd so wheel data files are included."
        ) from exc
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise BydCryptoError("wbsk_tables.json must contain a JSON object")
    return {str(k): str(v) for k, v in data.items()}


class WbskTables:
    """Loaded WBSK lookup tables (singleton per process)."""

    _instance: ClassVar[WbskTables | None] = None

    def __init__(self, encoded: dict[str, str]) -> None:
        self.enc_init_xor = _decode_byte_table(encoded, "encInitXor")
        self.enc_round_xor = _decode_byte_table(encoded, "encRoundXor")
        self.enc_sbox = _decode_byte_table(encoded, "encSbox")
        self.enc_final_xor = _decode_byte_table(encoded, "encFinalXor")
        self.enc_te0 = _decode_u32_table(encoded, "encTe0")
        self.enc_te1 = _decode_u32_table(encoded, "encTe1")
        self.enc_te2 = _decode_u32_table(encoded, "encTe2")
        self.enc_te3 = _decode_u32_table(encoded, "encTe3")
        self.dec_init_xor = _decode_byte_table(encoded, "decInitXor")
        self.dec_round_xor = _decode_byte_table(encoded, "decRoundXor")
        self.dec_inv_sbox = _decode_byte_table(encoded, "decInvSbox")
        self.dec_final_xor = _decode_byte_table(encoded, "decFinalXor")
        self.dec_td0 = _decode_u32_table(encoded, "decTd0")
        self.dec_td1 = _decode_u32_table(encoded, "decTd1")
        self.dec_td2 = _decode_u32_table(encoded, "decTd2")
        self.dec_td3 = _decode_u32_table(encoded, "decTd3")

    @classmethod
    def get(cls) -> WbskTables:
        if cls._instance is None:
            cls._instance = cls(_load_wbsk_tables_json())
        return cls._instance


def prot_xor(table: bytes, a: int, b: int) -> int:
    hi = table[((a >> 4) << 4) ^ (b >> 4)] & 0xF0
    lo = (table[((a & 0xF) << 4) ^ (b & 0xF)] >> 4) & 0x0F
    return hi | lo


def parse_wbc_key(hex_str: str) -> ParsedWbcKey:
    raw = bytes.fromhex(hex_str)
    if len(raw) < 5:
        raise BydCryptoError(f"WBC key blob too short: {len(raw)} bytes")
    mode = raw[0] ^ raw[3]
    key_data = bytearray(len(raw) - 4)
    for i in range(4, len(raw)):
        key_data[i - 4] = raw[i] ^ raw[i % 3]
    key_data_b = bytes(key_data)

    key_size_bits: int
    if mode in (0, 1):
        key_size_bits = 0x80
    elif mode in (2, 3):
        key_size_bits = 0xC0
    elif mode in (4, 5):
        key_size_bits = 0x80
    elif mode in (6, 7):
        key_size_bits = 0x40
    elif mode in (8, 9):
        key_size_bits = 0xC0
    elif mode in (0xA, 0xB):
        key_size_bits = 0x80
    elif mode in (0xC, 0xD):
        key_size_bits = 0x80
    elif mode in (0xE, 0xF):
        key_size_bits = 0xC0
    elif mode in (0x10, 0x11):
        key_size_bits = 0x100
    elif mode in (0x12, 0x13):
        key_size_bits = 0x40
    elif mode in (0x14, 0x15):
        key_size_bits = 0xC0
    elif mode in (0x16, 0x17):
        key_size_bits = 0x80
    else:
        raise BydCryptoError(f"Unknown WBC mode: 0x{mode:x}")

    num_rounds = (key_size_bits >> 5) + 6
    is_decrypt = mode & 1
    block_size = 8 if mode in (6, 7, 0x12, 0x13) else 16
    return ParsedWbcKey(
        key_data=key_data_b,
        key_size_bits=key_size_bits,
        num_rounds=num_rounds,
        is_decrypt=is_decrypt,
        block_size=block_size,
        mode=mode,
    )


def wbc_encrypt_block(t: WbskTables, inp: bytes, key_data: bytes, num_rounds: int) -> bytes:
    state = bytearray(16)
    temp1 = bytearray(16)
    temp2 = bytearray(16)
    for i in range(16):
        state[i] = prot_xor(t.enc_init_xor, inp[i], key_data[i])

    te1i = [5, 9, 13, 1]
    te2i = [10, 14, 2, 6]
    te3i = [15, 3, 7, 11]
    for r in range(1, num_rounds):
        for c in range(4):
            v = t.enc_te0[state[c * 4]]
            temp1[c * 4] = (v >> 24) & 0xFF
            temp1[c * 4 + 1] = (v >> 16) & 0xFF
            temp1[c * 4 + 2] = (v >> 8) & 0xFF
            temp1[c * 4 + 3] = v & 0xFF
        for c in range(4):
            v = t.enc_te1[state[te1i[c]]]
            temp2[c * 4] = (v >> 24) & 0xFF
            temp2[c * 4 + 1] = (v >> 16) & 0xFF
            temp2[c * 4 + 2] = (v >> 8) & 0xFF
            temp2[c * 4 + 3] = v & 0xFF
        for i in range(16):
            temp1[i] = prot_xor(t.enc_round_xor, temp1[i], temp2[i])
        for c in range(4):
            v = t.enc_te2[state[te2i[c]]]
            temp2[c * 4] = (v >> 24) & 0xFF
            temp2[c * 4 + 1] = (v >> 16) & 0xFF
            temp2[c * 4 + 2] = (v >> 8) & 0xFF
            temp2[c * 4 + 3] = v & 0xFF
        for i in range(16):
            temp1[i] = prot_xor(t.enc_round_xor, temp1[i], temp2[i])
        for c in range(4):
            v = t.enc_te3[state[te3i[c]]]
            temp2[c * 4] = (v >> 24) & 0xFF
            temp2[c * 4 + 1] = (v >> 16) & 0xFF
            temp2[c * 4 + 2] = (v >> 8) & 0xFF
            temp2[c * 4 + 3] = v & 0xFF
        for i in range(16):
            temp1[i] = prot_xor(t.enc_round_xor, temp1[i], temp2[i])
        rk_off = r * 16
        for i in range(16):
            state[i] = prot_xor(t.enc_round_xor, temp1[i], key_data[rk_off + i])

    sr = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]
    for i in range(16):
        temp1[i] = t.enc_sbox[state[sr[i]]]
    output = bytearray(16)
    frk_off = num_rounds * 16
    for i in range(16):
        output[i] = prot_xor(t.enc_final_xor, temp1[i], key_data[frk_off + i])
    return bytes(output)


def wbc_decrypt_block(t: WbskTables, inp: bytes, key_data: bytes, num_rounds: int) -> bytes:
    state = bytearray(16)
    temp1 = bytearray(16)
    temp2 = bytearray(16)
    for i in range(16):
        state[i] = prot_xor(t.dec_init_xor, inp[i], key_data[i])

    td1i = [13, 1, 5, 9]
    td2i = [10, 14, 2, 6]
    td3i = [7, 11, 15, 3]
    for r in range(1, num_rounds):
        for c in range(4):
            v = t.dec_td0[state[c * 4]]
            temp1[c * 4] = (v >> 24) & 0xFF
            temp1[c * 4 + 1] = (v >> 16) & 0xFF
            temp1[c * 4 + 2] = (v >> 8) & 0xFF
            temp1[c * 4 + 3] = v & 0xFF
        for c in range(4):
            v = t.dec_td1[state[td1i[c]]]
            temp2[c * 4] = (v >> 24) & 0xFF
            temp2[c * 4 + 1] = (v >> 16) & 0xFF
            temp2[c * 4 + 2] = (v >> 8) & 0xFF
            temp2[c * 4 + 3] = v & 0xFF
        for i in range(16):
            temp1[i] = prot_xor(t.dec_round_xor, temp1[i], temp2[i])
        for c in range(4):
            v = t.dec_td2[state[td2i[c]]]
            temp2[c * 4] = (v >> 24) & 0xFF
            temp2[c * 4 + 1] = (v >> 16) & 0xFF
            temp2[c * 4 + 2] = (v >> 8) & 0xFF
            temp2[c * 4 + 3] = v & 0xFF
        for i in range(16):
            temp1[i] = prot_xor(t.dec_round_xor, temp1[i], temp2[i])
        for c in range(4):
            v = t.dec_td3[state[td3i[c]]]
            temp2[c * 4] = (v >> 24) & 0xFF
            temp2[c * 4 + 1] = (v >> 16) & 0xFF
            temp2[c * 4 + 2] = (v >> 8) & 0xFF
            temp2[c * 4 + 3] = v & 0xFF
        for i in range(16):
            temp1[i] = prot_xor(t.dec_round_xor, temp1[i], temp2[i])
        rk_off = r * 16
        for i in range(16):
            state[i] = prot_xor(t.dec_round_xor, temp1[i], key_data[rk_off + i])

    inv_sr = [0, 13, 10, 7, 4, 1, 14, 11, 8, 5, 2, 15, 12, 9, 6, 3]
    for i in range(16):
        temp1[i] = t.dec_inv_sbox[state[inv_sr[i]]]
    output = bytearray(16)
    frk_off = num_rounds * 16
    for i in range(16):
        output[i] = prot_xor(t.dec_final_xor, temp1[i], key_data[frk_off + i])
    return bytes(output)


def wbc_encrypt_cbc(
    t: WbskTables, plaintext: bytes, key_data: bytes, num_rounds: int, iv: bytes
) -> bytes:
    block_count = len(plaintext) // 16
    output = bytearray(len(plaintext))
    prev = iv
    for b in range(block_count):
        block = bytearray(16)
        for i in range(16):
            block[i] = plaintext[b * 16 + i] ^ prev[i]
        enc = wbc_encrypt_block(t, bytes(block), key_data, num_rounds)
        output[b * 16 : b * 16 + 16] = enc
        prev = enc
    return bytes(output)


def wbc_decrypt_cbc(
    t: WbskTables, ciphertext: bytes, key_data: bytes, num_rounds: int, iv: bytes
) -> bytes:
    block_count = len(ciphertext) // 16
    output = bytearray(len(ciphertext))
    prev = iv
    for b in range(block_count):
        block = ciphertext[b * 16 : b * 16 + 16]
        dec = wbc_decrypt_block(t, block, key_data, num_rounds)
        for i in range(16):
            output[b * 16 + i] = dec[i] ^ prev[i]
        prev = block
    return bytes(output)


def nibble_encode(buf: bytes) -> bytes:
    out = bytearray(len(buf))
    for i, byte in enumerate(buf):
        out[i] = (NIBBLE_ENCODE[byte >> 4] << 4) | NIBBLE_ENCODE[byte & 0xF]
    return bytes(out)


def nibble_decode(buf: bytes) -> bytes:
    out = bytearray(len(buf))
    for i, byte in enumerate(buf):
        out[i] = (NIBBLE_DECODE[byte >> 4] << 4) | NIBBLE_DECODE[byte & 0xF]
    return bytes(out)


def strip_pkcs7(buf: bytes) -> bytes:
    if not buf:
        return buf
    pad_val = buf[-1]
    if pad_val < 1 or pad_val > 16:
        return buf
    for i in range(len(buf) - pad_val, len(buf)):
        if buf[i] != pad_val:
            return buf
    return buf[: len(buf) - pad_val]


def add_pkcs7(buf: bytes, block_size: int = 16) -> bytes:
    remainder = len(buf) % block_size
    pad = block_size if remainder == 0 else block_size - remainder
    return buf + bytes([pad]) * pad


def wbc_input_encode(buf: bytes) -> bytes:
    out = bytearray(len(buf))
    for i, byte in enumerate(buf):
        out[i] = (MYSTERY_ENCODE[byte >> 4] << 4) | MYSTERY_ENCODE[byte & 0xF]
    return bytes(out)


def wbc_output_decode(buf: bytes) -> bytes:
    out = bytearray(len(buf))
    for i, byte in enumerate(buf):
        out[i] = (
            (NIBBLE_ENCODE[NIBBLE_ENCODE[byte >> 4]] << 4)
            | NIBBLE_ENCODE[NIBBLE_ENCODE[byte & 0xF]]
        )
    return bytes(out)


def add_wbc_pkcs7(buf: bytes, block_size: int = 16) -> bytes:
    remainder = len(buf) % block_size
    pad_n = block_size if remainder == 0 else block_size - remainder
    pad_byte = (MYSTERY_ENCODE[pad_n >> 4] << 4) | MYSTERY_ENCODE[pad_n & 0xF]
    return buf + bytes([pad_byte]) * pad_n


def decrypt_wbsk_envelope(
    t: WbskTables, base64_str: str, outer_key_hex: str, inner_key_hex: str, outer_session_iv_hex: str
) -> str:
    raw = base64.b64decode(base64_str.strip())
    outer_encoded = nibble_encode(raw) + bytes(256)
    outer_key = parse_wbc_key(outer_key_hex)
    outer_iv = bytes.fromhex(outer_session_iv_hex)
    outer_decrypted = wbc_decrypt_cbc(t, outer_encoded, outer_key.key_data, outer_key.num_rounds, outer_iv)
    outer_content = strip_pkcs7(nibble_decode(outer_decrypted[: len(raw)]))
    content_len = len(outer_content)
    inner_b64 = outer_content[: content_len - 16].decode("latin-1")
    inner_iv = outer_decrypted[content_len - 16 : content_len]
    inner_raw = base64.b64decode(inner_b64)
    inner_encoded = nibble_encode(inner_raw) + bytes(256)
    inner_key = parse_wbc_key(inner_key_hex)
    inner_decrypted = wbc_decrypt_cbc(t, inner_encoded, inner_key.key_data, inner_key.num_rounds, inner_iv)
    inner_content = strip_pkcs7(nibble_decode(inner_decrypted[: len(inner_raw)]))
    return inner_content.decode("utf-8")


def encrypt_wbsk_envelope(
    t: WbskTables,
    plaintext: str,
    inner_enc_key_hex: str,
    inner_enc_iv_hex: str,
    outer_enc_key_hex: str,
    outer_enc_iv_hex: str,
) -> str:
    plain_buf = plaintext.encode("utf-8")
    inner_padded = add_wbc_pkcs7(wbc_input_encode(plain_buf))
    inner_key = parse_wbc_key(inner_enc_key_hex)
    inner_iv = bytes.fromhex(inner_enc_iv_hex)
    inner_encrypted = wbc_encrypt_cbc(t, inner_padded, inner_key.key_data, inner_key.num_rounds, inner_iv)
    inner_raw = wbc_output_decode(inner_encrypted)
    inner_b64 = base64.b64encode(inner_raw).decode("ascii")
    outer_content_plain = inner_b64.encode("latin-1") + wbc_output_decode(inner_iv)
    outer_mystery = add_wbc_pkcs7(wbc_input_encode(outer_content_plain))
    outer_key = parse_wbc_key(outer_enc_key_hex)
    outer_iv = bytes.fromhex(outer_enc_iv_hex)
    outer_encrypted = wbc_encrypt_cbc(t, outer_mystery, outer_key.key_data, outer_key.num_rounds, outer_iv)
    return base64.b64encode(wbc_output_decode(outer_encrypted)).decode("ascii")


def encrypt_envelope(plaintext: str) -> str:
    t = WbskTables.get()
    return encrypt_wbsk_envelope(
        t,
        plaintext,
        WBSK_KEYS["innerEncryptKey"],
        WBSK_KEYS["innerEncryptIv"],
        WBSK_KEYS["outerEncryptKey"],
        WBSK_KEYS["outerEncryptIv"],
    )


def decrypt_envelope(base64_str: str) -> str:
    t = WbskTables.get()
    return decrypt_wbsk_envelope(
        t,
        base64_str,
        WBSK_KEYS["outerDecryptKey"],
        WBSK_KEYS["innerDecryptKey"],
        WBSK_KEYS["outerDecryptIv"],
    )


class WbskCodec:
    """Stateless codec matching BangcleCodec's str-in/str-out envelope contract."""

    async def async_load_tables(self) -> None:
        """Pre-load JSON tables off the event loop (parity with ``BangcleCodec``)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, WbskTables.get)

    def encode_envelope(self, plaintext: str | bytes) -> str:
        text = plaintext.decode("utf-8") if isinstance(plaintext, bytes) else plaintext
        return encrypt_envelope(text)

    def decode_envelope(self, envelope: str) -> bytes:
        return decrypt_envelope(envelope).encode("utf-8")
