"""Serve and inspect the Italian, same-origin tournament interface."""

from fastapi.testclient import TestClient
from html.parser import HTMLParser
from io import BytesIO

from PIL import Image

from app.main import create_app


class StructureParser(HTMLParser):
    """Track explicit element ancestry for the form/manager sibling contract."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ancestors = {}
        self.form_closed_before_fieldset = False
        self.dialog_forms = []
        self.dialog_close_buttons = []
        self.hero_mascots = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and any(parent[1].get("id") == "confirm-dialog" for parent in self.stack):
            self.dialog_forms.append(attrs)
        if "data-dialog-close" in attrs:
            self.dialog_close_buttons.append((tag, attrs))
        if "mascot--" in attrs.get("class", ""):
            self.hero_mascots.append(attrs)
        element = (tag, attrs)
        element_id = attrs.get("id")
        if element_id:
            self.ancestors[element_id] = tuple(self.stack)
        if tag not in self.VOID:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                if tag == "form" and any(parent[0] == "fieldset" for parent in self.stack[index + 1:]):
                    self.form_closed_before_fieldset = True
                del self.stack[index:]
                return


def test_root_and_static_assets_are_served_as_local_web_assets(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert 'lang="it"' in page.text
        assert 'name="viewport"' in page.text
        for path, mime in (("/static/style.css", "text/css"), ("/static/app.js", "javascript")):
            asset = client.get(path)
            assert asset.status_code == 200
            assert mime in asset.headers["content-type"]


def test_public_and_admin_entry_points_are_labeled_and_keyboard_friendly(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        page = client.get("/").text
    for marker in ('id="public-view"', 'id="admin-view"', 'aria-label="Navigazione principale"', 'id="login-form"', 'id="tournament-form"', 'id="players-list"'):
        assert marker in page
    assert '<h1 id="public-title">Mana Arena</h1>' in page
    assert "Il tavolo è" not in page
    assert "La strategia incontra" not in page
    assert "prefers-reduced-motion" in client.get("/static/style.css").text
    assert "scroll-margin-top" in client.get("/static/style.css").text
    assert "visibilitychange" in client.get("/static/app.js").text
    assert "document.visibilityState" in client.get("/static/app.js").text


def test_tournament_manager_is_outside_hidden_form_and_fieldsets_are_closed(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        parser = StructureParser()
        parser.feed(client.get("/").text)
    assert not parser.form_closed_before_fieldset
    assert parser.stack == []
    manager_path = parser.ancestors["tournament-manager"]
    manager_ids = [attrs.get("id") for _, attrs in manager_path]
    assert "admin-events" in manager_ids
    assert manager_ids[-1] == "admin-events"
    assert "tournament-form" not in manager_ids
    assert not any(tag == "fieldset" for tag, _ in manager_path)


def test_confirmation_dialog_close_control_is_not_a_form_submit(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        parser = StructureParser()
        parser.feed(client.get("/").text)
    assert parser.dialog_forms == []
    assert parser.dialog_close_buttons == [("button", {"type": "button", "class": "dialog-close", "data-dialog-close": None, "aria-label": "Chiudi"})]


def test_hero_mascots_are_five_local_decorative_wubrg_assets(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    names = ("white", "blue", "black", "red", "green")
    with TestClient(app) as client:
        parser = StructureParser()
        parser.feed(client.get("/").text)
        assets = [client.get(f"/static/mascots/{name}.png") for name in names]
    assert [image.get("src") for image in parser.hero_mascots] == [f"/static/mascots/{name}.png" for name in names]
    assert all(image.get("alt") == "" and image.get("aria-hidden") == "true" for image in parser.hero_mascots)
    assert all(response.status_code == 200 and response.headers.get("content-type", "").startswith("image/png") for response in assets)
    assert all(response.content.startswith(b"\x89PNG\r\n\x1a\n") for response in assets)
    for response in assets:
        with Image.open(BytesIO(response.content)) as image:
            assert image.mode == "RGBA"
            assert image.getextrema()[3][0] == 0


def test_static_interface_uses_only_local_assets_and_has_admin_workflow_controls(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        page = client.get("/").text
        js = client.get("/static/app.js").text
        css = client.get("/static/style.css").text
    assert "https://" not in page + js + css
    for marker in ("/api/public/tournaments", "/api/admin/players", "/api/admin/tournaments", "/api/admin/backup", "X-CSRF-Token", "createObjectURL"):
        assert marker in js
    for marker in ("result", "withdrawals", "rollback", "reset", "archive", "reopen", "audit", "export.csv", "restore", "password"):
        assert marker in js
    assert "bracket_slot" in js
    assert "single_elim" in js
