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

    def test_key_falls_back_to_generated_key_file(self, tmp_path):
        import os
        from utils import crypto
        with patch('config.settings.settings.CARD_ENCRYPTION_KEY', None), \
             patch('config.settings.settings.DATA_DIR', str(tmp_path / "a")):
            token = encrypt_json({"x": 1})
            assert decrypt_json(token) == {"x": 1}
            key_file = tmp_path / "a" / "secret.key"
            assert key_file.is_file() and len(key_file.read_text().strip()) == 64
            if os.name != "nt":
                assert oct(key_file.stat().st_mode & 0o777) == "0o600"
            assert crypto._local_key_material() == key_file.read_text().strip()  # reused, not regenerated
        with patch('config.settings.settings.CARD_ENCRYPTION_KEY', None), \
             patch('config.settings.settings.DATA_DIR', str(tmp_path / "b")):
            with pytest.raises(DecryptionError):
                decrypt_json(token)  # a different machine/key file cannot read it
