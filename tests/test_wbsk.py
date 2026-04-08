"""Golden vectors from BYD-re ``scripts/test_wbsk.js``."""

from __future__ import annotations

import json

import pytest

from pybyd._crypto import wbsk


def test_parse_wbc_key_encrypt_mode() -> None:
    enc_key = (
        "9fca018f72712b15e23c275ea5e06a92d8b98404cf0bf960955596ff47dd2adf8f9e0c3ca1363e8be88cb6fced211933e5c3484c20c7bc3ae1eb8541027fef4a20b2f302d93582f7f4349fef05c16d389956ae9f2a7aea278fd5232229e38caca017ffbaf5138d2cadbca917b4694fb2882a64809c095387b7353608ca3a17913e5863770465986995c684ea7db01e0c35c69fd169a8e14ec5123beb5b8dad6e1c5198f34ed1c44d5f9b15035673df5953e5e42351f58052c1483fa4cf93c646a396081355a46d5a7e0ce30d54049802829cd78c1a77c7db8f74acb244b73c5147f161ee25bd702112cec97c339a6b3314527d12"
    )
    parsed = wbsk.parse_wbc_key(enc_key)
    assert parsed.mode == 0x10
    assert parsed.num_rounds == 14


def test_parse_wbc_key_decrypt_mode() -> None:
    dec_key = (
        "72ca0163b22e2973656a67ac1ae1490a61133824a0cd235bcfcd6032dba79d3ca51b1c4d1b03068566ff084645dcc9e6e8a28b39c71e72dec3fe4074109a84f5564d3f43f4854fb634bf633dabe218a5b73470dff70b07161b76c74d92bffa15bcb7fa4fc448a0fe83b62c9dd97f36d1d1d7613028041bb1dd328397bbf8c8bf6f81321f5e2d4982761a375bded52a1de198169839fabad771bc677b57f806c1ca385f43627e9a5081c43d7c9d9fa86e7e78cc0a8050a2420a76c842abbe93eb38f2487bdf93087cd24097a16539da2f86feb693432daf8f0618cbf97ffea3a762b7b91050f8634a8d3ceb25ea7d2b3264a77337"
    )
    parsed = wbsk.parse_wbc_key(dec_key)
    assert parsed.mode == 0x11


def test_wbc_encrypt_block() -> None:
    t = wbsk.WbskTables.get()
    enc_key = wbsk.parse_wbc_key(
        "9fca018f72712b15e23c275ea5e06a92d8b98404cf0bf960955596ff47dd2adf8f9e0c3ca1363e8be88cb6fced211933e5c3484c20c7bc3ae1eb8541027fef4a20b2f302d93582f7f4349fef05c16d389956ae9f2a7aea278fd5232229e38caca017ffbaf5138d2cadbca917b4694fb2882a64809c095387b7353608ca3a17913e5863770465986995c684ea7db01e0c35c69fd169a8e14ec5123beb5b8dad6e1c5198f34ed1c44d5f9b15035673df5953e5e42351f58052c1483fa4cf93c646a396081355a46d5a7e0ce30d54049802829cd78c1a77c7db8f74acb244b73c5147f161ee25bd702112cec97c339a6b3314527d12"
    )
    enc_input = bytes.fromhex("f355d6f4f4c7d0d6d9d9ded855715572")
    enc_iv = bytes.fromhex("98893a8b94a08d89999b8f3684ab9589")
    xored = bytes(enc_input[i] ^ enc_iv[i] for i in range(16))
    enc_result = wbsk.wbc_encrypt_block(t, xored, enc_key.key_data, enc_key.num_rounds)
    assert enc_result.hex() == "a368c168ee2db39b7a555738e498f084"


def test_wbc_decrypt_block_cbc1() -> None:
    t = wbsk.WbskTables.get()
    dec_key = wbsk.parse_wbc_key(
        "72ca0163b22e2973656a67ac1ae1490a61133824a0cd235bcfcd6032dba79d3ca51b1c4d1b03068566ff084645dcc9e6e8a28b39c71e72dec3fe4074109a84f5564d3f43f4854fb634bf633dabe218a5b73470dff70b07161b76c74d92bffa15bcb7fa4fc448a0fe83b62c9dd97f36d1d1d7613028041bb1dd328397bbf8c8bf6f81321f5e2d4982761a375bded52a1de198169839fabad771bc677b57f806c1ca385f43627e9a5081c43d7c9d9fa86e7e78cc0a8050a2420a76c842abbe93eb38f2487bdf93087cd24097a16539da2f86feb693432daf8f0618cbf97ffea3a762b7b91050f8634a8d3ceb25ea7d2b3264a77337"
    )
    dec_input = bytes.fromhex("ac167b36509cd8b448bc5b6544ad0a80")
    dec_iv = bytes.fromhex("54cc5558c551c155c4c05cc4c158ca58")
    dec_block = wbsk.wbc_decrypt_block(t, dec_input, dec_key.key_data, dec_key.num_rounds)
    dec_result = bytes(dec_block[i] ^ dec_iv[i] for i in range(16))
    assert dec_result.hex() == "99c9911c5317c0595cc459c8d8d8dcc0"


def test_wbc_decrypt_block_cbc2() -> None:
    t = wbsk.WbskTables.get()
    dec2_key = wbsk.parse_wbc_key(
        "71ca0160b689febe1c11e07bc8f8cec81decf71b6c3e0be299a35211888c2fb177958e57a6e971d0a874ecb50991786faf3a34b178f13a668bd14b81a82d3f799f6f0c8bd002406c8b6fdd54b3bb30c0c7d27c906dba87decde28717a0874abacf41755646b4a2c06854615ab00ae53136cbea3302b047659e7a42f792a7369fc130d8ffdc114a7a2cf2fa669b9b337905ff58fe3cc40b9b1edf37ebe50d36b3416abbd32837895b8ea1f22b9eab35efd791d9153208630297b8b953a9ca33265854c33959979b9eb1d049326986851170f4b51d151f43a30c6298a8c03503477336b2000c49746181ca30eabded6d3088b7f615"
    )
    dec2_input = bytes.fromhex("9ca134a1bb87dc5de9666ec1b251f012")
    dec2_iv = bytes.fromhex("5115c91d52901715555d1fca129d5615")
    dec2_block = wbsk.wbc_decrypt_block(t, dec2_input, dec2_key.key_data, dec2_key.num_rounds)
    dec2_result = bytes(dec2_block[i] ^ dec2_iv[i] for i in range(16))
    assert dec2_result.hex() == "de4458d0d01c52585757595344c644ca"


def test_nibble_encode_decode() -> None:
    nib_test = bytes([0x93])
    enc = wbsk.nibble_encode(nib_test)
    assert enc[0].to_bytes(1, "big").hex() == "ac"
    assert wbsk.nibble_decode(enc) == nib_test


@pytest.fixture
def test_envelope() -> str:
    return (
        "k0rtymBTcdIh022mIpcJEMGrPoHX2Qz2jwcluQ300c0X2oQVmVi92chOWl6iWgTiy6cteRnVFgm8SWVamBCTRTJNJ46s79Yrj3PqTACkyi178Q1iWq3onlsyenp8Rc9XEw+mqFmA9H8ls8pABVpW+tLfGipTZycvbkp9SS6wTmdmHd4+POky6LV4r3XeQ+qchV6Ldkkvd6CwHL4U5VvPiFSP26G9YoZCS5ddr8sbPnGW3cOKwMh2x2e6lZMNtTbE5dQd2sct9pm7/pKhs72EeOV36g+Y+iZvEIOVixAVNf+ipnRN/TiNA6sG36b/9vF5SBz7kCs2fmTCNf9eU4qka4t/MBPGFXV8ybNNQtfF6FZub0NrhkiPT1QDXfr2mfoe8/29Wf/kISW4E3aRKXmWW4fLuzUpdhYirjzie6ROwkoEGM9UVQVV5h3Ff4m5lcEIQQmXgoX98qyHKBpWDdq7bsTkpFY1MgzNEkH88aO6mLkv9AF9Xlm+Q9ehtVA0eYfaE0+Tf5boUD/vSTlPljxmLwTq2VfXjYIioxpMr1HGx6LRUDFzNUjL9hsM/Fi2hXRsTl194nnrrvqw1tfKim7m5k4KXMRnvcoHEiAZCaxSxZVUhyVcnPvHbmsp9/0LZPLKOrromENif2lJ4a1L7cTsmnt+OaoffcIOCouh2JY5+CgoMYwg+3wg4YUdDhogOxqJweQnlB7xFNnjCzhk0p7BuQpMq4tIa6LG3ddfL+Zug1Q4SYHWxMU5SEAdOujT4pwCuzDAxOt0cVISiDbPh7YGy5uesILUA1/JSj/IrQZg5BzzWChKPU18eoyTdSj631sJLKVolOH4UjXU0riZDuxtFtXkn9xjNKoUjX/Km0bFZT2ivHCA29kqME8vEWybrXt8Rt9+ksV52ymzLnHiU61TlYWtYU6rmTfo5pGjWRELaxhJbZBjK7Y9B87Wn2rJey/b+HO+y4WqL0IUs4mspS/ocP4UExRGQLvN+jWN2DrmMz7fi0VXYXCrnJ1Q1aCbhLvrtySBH7qOhsEBMmOqHA7n0P4be4jOyo/0WxVs0AwlQhgjPNstQErRp7A/dkY/p+uYr8OT5GsCQ+f4fsZeQ3+DIsv3sWXbn7OMIxmFaJZ5pMkCl2SbPiehUWdIL3vrcRtVR8CT3JSaI9BpOo9CRvkhZvjBYhaMwEJPljXepPxwFiiZJRzytzZAJq0HPuVamP1tE7T4tWDXgHTgycQeMHE6E+HeKrOFaCDIr09vpIhfHohvkUs0bEyiSzejmqJHyV7MW1vJ5v2V9l5SoQYMhvKuwL/MujQlsQEqi97+U25+w5Y="
    )


def test_decrypt_wbsk_envelope_golden(test_envelope: str) -> None:
    t = wbsk.WbskTables.get()
    outer_key = "72ca0163b22e2973656a67ac1ae1490a61133824a0cd235bcfcd6032dba79d3ca51b1c4d1b03068566ff084645dcc9e6e8a28b39c71e72dec3fe4074109a84f5564d3f43f4854fb634bf633dabe218a5b73470dff70b07161b76c74d92bffa15bcb7fa4fc448a0fe83b62c9dd97f36d1d1d7613028041bb1dd328397bbf8c8bf6f81321f5e2d4982761a375bded52a1de198169839fabad771bc677b57f806c1ca385f43627e9a5081c43d7c9d9fa86e7e78cc0a8050a2420a76c842abbe93eb38f2487bdf93087cd24097a16539da2f86feb693432daf8f0618cbf97ffea3a762b7b91050f8634a8d3ceb25ea7d2b3264a77337"
    inner_key = "71ca0160b689febe1c11e07bc8f8cec81decf71b6c3e0be299a35211888c2fb177958e57a6e971d0a874ecb50991786faf3a34b178f13a668bd14b81a82d3f799f6f0c8bd002406c8b6fdd54b3bb30c0c7d27c906dba87decde28717a0874abacf41755646b4a2c06854615ab00ae53136cbea3302b047659e7a42f792a7369fc130d8ffdc114a7a2cf2fa669b9b337905ff58fe3cc40b9b1edf37ebe50d36b3416abbd32837895b8ea1f22b9eab35efd791d9153208630297b8b953a9ca33265854c33959979b9eb1d049326986851170f4b51d151f43a30c6298a8c03503477336b2000c49746181ca30eabded6d3088b7f615"
    outer_iv = "54cc5558c551c155c4c05cc4c158ca58"
    result = wbsk.decrypt_wbsk_envelope(t, test_envelope, outer_key, inner_key, outer_iv)
    expected = '{"appChannel":"99","deviceName":"XIAOMIPOCO F1","deviceType":"0","devicename":"XIAOMIPOCO F1","functionType":"0","identifier":"","identifierType":2,"imeiMD5":"B3F1683F5F6D4AFA68D5E6C8403EB762","mobileBrand":"XIAOMI","mobileModel":"POCO F1","networkOperator":"\u65e0","networkType":"WiFi","osType":"Android","osVersion":"15","random":"4147E42713B5443D92C4FD83A4A36DF4","reqTimestamp":"1772115257753","sign":"","softType":"0","targetBrand":"1","timeStamp":"1772115257","vehicleBrand":"1","version":"502","ostype":"and","imei":"unknown","mac":"unknown","model":"unknown","sdk":"unknown","serviceTime":"1772115257808","mod":"unknown","checkcode":"2eb5bb65dc1c49496824ba0462f62b2aa99ffcbbe62680028a41a197a6045718"}'
    assert json.loads(result) == json.loads(expected)


def test_encrypt_decrypt_roundtrip() -> None:
    payload = '{"hello":"world","num":42}'
    enc = wbsk.encrypt_envelope(payload)
    dec = wbsk.decrypt_envelope(enc)
    assert json.loads(dec) == json.loads(payload)


def test_cn_payload_roundtrip() -> None:
    cn_payload = json.dumps(
        {
            "appChannel": "99",
            "deviceName": "XIAOMIPOCO F1",
            "deviceType": "0",
            "functionType": "0",
            "identifier": "13145797664",
            "identifierType": "0",
            "imeiMD5": "B3F1683F5F6D4AFA68D5E6C8403EB762",
            "mobileBrand": "XIAOMI",
            "mobileModel": "POCO F1",
            "networkType": "WiFi",
            "osType": "Android",
            "osVersion": "15",
            "random": "4147E42713B5443D92C4FD83A4A36DF4",
            "reqTimestamp": "1772115257753",
            "sign": "",
            "softType": "0",
            "targetBrand": "1",
            "timeStamp": "1772115257",
            "vehicleBrand": "1",
            "version": "502",
        },
        separators=(",", ":"),
    )
    enc = wbsk.encrypt_envelope(cn_payload)
    dec = wbsk.decrypt_envelope(enc)
    assert json.loads(dec) == json.loads(cn_payload)


def test_wbsk_codec_interface() -> None:
    codec = wbsk.WbskCodec()
    enc = codec.encode_envelope('{"a":1}')
    assert codec.decode_envelope(enc).decode("utf-8") == '{"a":1}'
