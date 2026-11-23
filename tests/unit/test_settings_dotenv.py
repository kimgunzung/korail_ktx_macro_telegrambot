"""Unit tests for the dependency-free .env loader."""
import os
from unittest.mock import patch

from config.settings import load_dotenv_file


def _write(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestLoadDotenv:
    def test_sets_missing_variables_only(self, tmp_path):
        path = _write(tmp_path, "DOTENV_A=1\nDOTENV_B=2\n")
        with patch.dict(os.environ, {"DOTENV_B": "shell"}, clear=False):
            os.environ.pop("DOTENV_A", None)
            assert load_dotenv_file(path) == 1
            assert os.environ["DOTENV_A"] == "1"
            assert os.environ["DOTENV_B"] == "shell"  # shell/compose value wins

    def test_parses_comments_quotes_export_and_bom(self, tmp_path):
        path = _write(tmp_path, "\ufeff# comment\n\nexport DOTENV_Q=\"a b\"\nDOTENV_S='x=y'\nDOTENV_C=val # trailing\nBROKEN LINE\n")
        with patch.dict(os.environ, {}, clear=False):
            for key in ("DOTENV_Q", "DOTENV_S", "DOTENV_C"):
                os.environ.pop(key, None)
            assert load_dotenv_file(path) == 3
            assert os.environ["DOTENV_Q"] == "a b"
            assert os.environ["DOTENV_S"] == "x=y"
            assert os.environ["DOTENV_C"] == "val"

    def test_missing_file_is_noop(self, tmp_path):
        assert load_dotenv_file(str(tmp_path / "nope.env")) == 0
