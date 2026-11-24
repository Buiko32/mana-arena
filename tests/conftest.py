import pytest


@pytest.fixture(autouse=True)
def configure_test_access(monkeypatch):
    monkeypatch.setenv("SHARED_ACCESS_PASSWORD", "Shared-access-passphrase-42")
    monkeypatch.setenv("COOKIE_SECURE", "false")
