from __future__ import annotations

from local_control_center.shared.redaction import redact_secrets

REDACTED = "[redacted]"


def test_known_token_patterns_are_redacted() -> None:
    assert REDACTED in redact_secrets("token ghp_ABCDEFGHIJKL012345 here")
    assert REDACTED in redact_secrets("Authorization: Bearer abc.def.ghi")
    assert REDACTED in redact_secrets("use sk-ABCDEFGH012345 now")
    assert REDACTED in redact_secrets("password=hunter2&next")


def test_connection_strings_with_credentials_are_redacted() -> None:
    for dsn in (
        "postgresql://user:s3cr3t@db.internal:5432/app",
        "mongodb+srv://admin:p@ss@cluster0.example.net/prod",
        "redis://default:topsecret@cache:6379/0",
    ):
        cleaned = redact_secrets(dsn)
        assert cleaned == REDACTED or REDACTED in cleaned
        assert "s3cr3t" not in cleaned and "topsecret" not in cleaned


def test_pem_private_key_block_is_redacted() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDfake000\n"
        "abcDEF123456789secretkeymaterialthatmustnotleak==\n"
        "-----END RSA PRIVATE KEY-----"
    )
    cleaned = redact_secrets({"key": pem})["key"]
    assert "secretkeymaterial" not in cleaned
    assert REDACTED in cleaned


def test_non_secret_values_are_preserved() -> None:
    # A plain URL without embedded credentials must not be redacted.
    assert redact_secrets("https://example.com/path?ref=1") == "https://example.com/path?ref=1"
    assert redact_secrets("a normal sentence with no secrets") == "a normal sentence with no secrets"
    # Token counters are preserved (existing behavior).
    assert redact_secrets(42, key="total_tokens") == 42
