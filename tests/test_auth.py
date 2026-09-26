import pytest

from garmin_mcp import auth


@pytest.fixture(autouse=True)
def isolated_token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_DIR", tmp_path / "tokens")
    auth.reset_client()
    yield
    auth.reset_client()


def test_missing_tokens_means_not_logged_in():
    with pytest.raises(auth.NotLoggedIn, match="No valid Garmin session"):
        auth.get_client()


def test_corrupt_token_file_means_not_logged_in(tmp_path):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    (token_dir / "garmin_tokens.json").write_text("{not json")
    with pytest.raises(auth.NotLoggedIn):
        auth.get_client()


def test_login_hint_never_asks_for_password_in_chat():
    assert "Never ask for the password in chat" in auth.LOGIN_HINT
