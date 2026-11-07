from __future__ import annotations

import base64
import hashlib
import hmac

from conductor.cloudstack.signing import java_urlencode, sign, signing_string


def test_signing_string_is_sorted_and_lowercased() -> None:
    params = {"command": "listZones", "apiKey": "SYNTHETICKEY", "response": "json"}
    text = signing_string(params)
    assert text == "apikey=synthetickey&command=listzones&response=json"


def test_java_urlencode_matches_expected_quirks() -> None:
    assert java_urlencode("a b") == "a%20b"
    assert java_urlencode("~") == "%7E"
    assert java_urlencode("*") == "*"
    assert java_urlencode("a+b") == "a%2Bb"


def test_signature_matches_manual_hmac() -> None:
    params = {"command": "listZones", "apiKey": "k", "response": "json"}
    expected = base64.b64encode(
        hmac.new(
            b"synthetic-secret",
            b"apikey=k&command=listzones&response=json",
            hashlib.sha1,
        ).digest()
    ).decode()
    assert sign(params, "synthetic-secret") == expected


def test_none_values_are_excluded() -> None:
    assert signing_string({"a": "x", "b": None}) == "a=x"
