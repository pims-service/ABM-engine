"""Encryption of integration credentials (issue #76): round trip, rotation, fail closed, and a
wrapper that never prints, copies into a cache or leaks into a traceback."""

from __future__ import annotations

import copy
import pickle
import traceback

import pytest
from cryptography.fernet import Fernet, InvalidToken

from apps.integrations import crypto
from apps.integrations.crypto import (
    Credentials,
    CredentialsNotSetError,
    DecryptionError,
    EncryptionConfigError,
    mask,
)
from tests.factories_integrations import FAKE_SECRET


def new_key() -> str:
    return crypto.generate_key()


# ------------------------------------------------------------------ round trip


def test_round_trip_and_ciphertext_hides_the_secret() -> None:
    token = Credentials({"api_key": FAKE_SECRET}).token()
    assert FAKE_SECRET not in token
    assert "api_key" not in token
    assert Credentials.sealed(token).reveal() == {"api_key": FAKE_SECRET}
    assert Credentials.sealed(token).get("api_key") == FAKE_SECRET


def test_two_encryptions_of_the_same_value_differ() -> None:
    assert Credentials({"a": FAKE_SECRET}).token() != Credentials({"a": FAKE_SECRET}).token()


def test_empty_credentials_have_no_token() -> None:
    empty = Credentials.empty()
    assert not empty
    assert empty.token() == ""
    assert empty.reveal() == {}


def test_a_missing_name_is_a_safe_error() -> None:
    with pytest.raises(CredentialsNotSetError) as info:
        Credentials({"api_key": FAKE_SECRET}).get("other")
    assert FAKE_SECRET not in str(info.value)


# ------------------------------------------------------------------ keys and rotation


def test_old_key_decrypts_and_the_new_key_encrypts(settings) -> None:
    old, new = new_key(), new_key()
    settings.INTEGRATION_ENCRYPTION_KEYS = [old]
    old_token = Credentials({"api_key": FAKE_SECRET}).token()

    settings.INTEGRATION_ENCRYPTION_KEYS = [new, old]  # newest first
    assert Credentials.sealed(old_token).get("api_key") == FAKE_SECRET  # old still reads
    new_token = Credentials({"api_key": FAKE_SECRET}).token()
    assert Fernet(new.encode()).decrypt(new_token.encode())  # written with the NEW key
    with pytest.raises(InvalidToken):
        Fernet(old.encode()).decrypt(new_token.encode())

    rotated = crypto.reencrypt_token(old_token)
    assert Fernet(new.encode()).decrypt(rotated.encode())
    settings.INTEGRATION_ENCRYPTION_KEYS = [new]  # old key retired
    assert Credentials.sealed(rotated).get("api_key") == FAKE_SECRET


def test_wrong_key_fails_closed_without_plaintext(settings) -> None:
    settings.INTEGRATION_ENCRYPTION_KEYS = [new_key()]
    token = Credentials({"api_key": FAKE_SECRET}).token()
    settings.INTEGRATION_ENCRYPTION_KEYS = [new_key()]
    with pytest.raises(DecryptionError) as info:
        Credentials.sealed(token).reveal()
    assert FAKE_SECRET not in str(info.value)
    assert "INTEGRATION_ENCRYPTION_KEYS" in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_a_tampered_token_fails_closed() -> None:
    token = Credentials({"api_key": FAKE_SECRET}).token()
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        Credentials.sealed(tampered).reveal()


def test_garbage_is_not_returned_as_plaintext() -> None:
    with pytest.raises(DecryptionError):
        Credentials.sealed("plain text that is not a token").reveal()


def test_no_keys_fails_closed(settings) -> None:
    settings.INTEGRATION_ENCRYPTION_KEYS = []
    with pytest.raises(EncryptionConfigError):
        Credentials({"a": "b"}).token()
    with pytest.raises(EncryptionConfigError):
        crypto.decrypt_text("gAAAAAx")


def test_an_invalid_key_names_its_position_not_its_value(settings) -> None:
    bad = "not-a-fernet-key-" + "z" * 20  # not a real secret
    settings.INTEGRATION_ENCRYPTION_KEYS = [new_key(), bad]
    with pytest.raises(EncryptionConfigError) as info:
        crypto.get_cipher()
    assert "entry 2" in str(info.value)
    assert bad not in str(info.value)


# ------------------------------------------------------------------ the wrapper never prints


def test_repr_str_and_format_never_show_the_value() -> None:
    sealed = Credentials.sealed(Credentials({"api_key": FAKE_SECRET}).token())
    for creds in (Credentials({"api_key": FAKE_SECRET}), sealed):
        assert FAKE_SECRET not in repr(creds)
        assert FAKE_SECRET not in str(creds)
        assert FAKE_SECRET not in f"{creds}"
        assert FAKE_SECRET not in "%s %r" % (creds, creds)  # noqa: UP031
        assert FAKE_SECRET not in repr([creds]) + repr({"k": creds})
    assert repr(Credentials.empty()) == "Credentials(empty)"


def test_a_traceback_with_the_wrapper_in_scope_shows_no_secret() -> None:
    creds = Credentials({"api_key": FAKE_SECRET})
    try:
        raise RuntimeError(f"failed with {creds}")
    except RuntimeError as exc:
        text = "".join(traceback.format_exception(exc))
    assert FAKE_SECRET not in text


def test_the_wrapper_cannot_be_pickled_hashed_or_iterated() -> None:
    creds = Credentials({"api_key": FAKE_SECRET})
    with pytest.raises(TypeError):
        pickle.dumps(creds)
    with pytest.raises(TypeError):
        hash(creds)
    with pytest.raises(TypeError):
        list(creds)  # type: ignore[call-overload]


def test_in_memory_copies_are_allowed_and_equal() -> None:
    creds = Credentials({"api_key": FAKE_SECRET})
    assert copy.deepcopy(creds) == creds
    assert copy.copy(creds) == creds
    assert creds != Credentials({"api_key": "other"})
    assert Credentials.empty() == Credentials.empty()


# ------------------------------------------------------------------ mask


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        ("", "****"),
        ("abcd", "****"),
        ("a" * 15, "****"),
        ("x" * 12 + "abcd", "****abcd"),
        ("y" * 40 + "wxyz", "****wxyz"),
    ],
)
def test_mask_shows_the_last_four_only_for_long_secrets(secret: str, expected: str) -> None:
    assert mask(secret) == expected


def test_mask_all() -> None:
    assert crypto.mask_all({"a": "short", "b": "z" * 20 + "1234"}) == {
        "a": "****",
        "b": "****1234",
    }
