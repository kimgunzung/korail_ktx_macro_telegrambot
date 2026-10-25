"""Unit tests for the at-rest encryption of card details."""
from unittest.mock import patch

import pytest

from utils.crypto import DecryptionError, decrypt_json, encrypt_json


class TestEncryptJson:
    def test_round_trip(self):
        data = {"number": "4111111111111111", "verify_number": "900101", "installment": 0}
        token = encrypt_json(data)
        assert decrypt_json(token) == data

    def test_ciphertext_hides_plaintext_and_is_randomized(self):
        data = {"number": "4111111111111111"}
        a, b = encrypt_json(data), encrypt_json(data)
        assert "4111" not in a and "number" not in a
        assert a != b  # fresh nonce every time

    def test_wrong_key_fails(self):
        with patch('config.settings.settings.CARD_ENCRYPTION_KEY', 'key-one'):
            token = encrypt_json({"x": 1})
        with patch('config.settings.settings.CARD_ENCRYPTION_KEY', 'key-two'):
            with pytest.raises(DecryptionError):
                decrypt_json(token)

    def test_tampering_detected(self):
        token = encrypt_json({"x": 1})
        tampered = token[:-6] + ("AAAAAA" if not token.endswith("AAAAAA") else "BBBBBB")
        with pytest.raises(DecryptionError):
            decrypt_json(tampered)

    def test_garbage_is_decryption_error(self):
        with pytest.raises(DecryptionError):
            decrypt_json("not-a-token")

    def test_key_falls_back_to_bot_token(self):
        with patch('config.settings.settings.CARD_ENCRYPTION_KEY', None), \
             patch('config.settings.settings.TELEGRAM_BOT_TOKEN', 'tok-a'):
            token = encrypt_json({"x": 1})
            assert decrypt_json(token) == {"x": 1}
        with patch('config.settings.settings.CARD_ENCRYPTION_KEY', None), \
             patch('config.settings.settings.TELEGRAM_BOT_TOKEN', 'tok-b'):
            with pytest.raises(DecryptionError):
                decrypt_json(token)
