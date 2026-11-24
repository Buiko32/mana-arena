"""Test client that passes the shared gate for existing app behavior tests."""

from fastapi.testclient import TestClient as _TestClient


class TestClient(_TestClient):
    def __enter__(self):
        entered = super().__enter__()
        response = self.post(
            "/api/access/login",
            json={"password": "Shared-access-passphrase-42"},
        )
        if response.status_code != 200 or not self.cookies.get("mana_device"):
            raise AssertionError(f"shared access login failed: {response.status_code} {response.text}")
        return entered
