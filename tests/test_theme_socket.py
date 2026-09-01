from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from lilies.core.components import ComponentAction, ComponentRegistry, ConfirmationRequired, build_registry
from lilies.core.desktop import DesktopIndex
from lilies.core.permissions import PermissionMode
from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker, Risk
from lilies.core.socket_server import LocalSocketServer
from lilies.core.shell import ShellController
from lilies.core.themes import ThemeManifest


def test_theme_manifest_supports_two_renderers(tmp_path):
    (tmp_path / "asset.png").write_bytes(b"x")
    manifest = tmp_path / "theme.json"
    manifest.write_text(json.dumps({
        "id": "first", "version": "0.1", "title": "初遇",
        "renderers": ["scene2d", "video"], "defaultRenderer": "scene2d",
        "assets": {"background": "asset.png"}
    }), "utf-8")
    theme = ThemeManifest.load(manifest)
    assert theme.default_renderer == "scene2d"
    assert theme.asset("background") == tmp_path / "asset.png"


def test_theme_manifest_exposes_icon_pack_and_layer_assets(tmp_path):
    (tmp_path / "icons.json").write_text("{}", "utf-8")
    for name in ("back.svg", "front.svg", "glow.svg", "cord.svg", "dust.svg"):
        (tmp_path / name).write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", "utf-8")
    manifest = tmp_path / "theme.json"
    manifest.write_text(json.dumps({
        "id": "layered", "version": "0.1", "title": "分层主题",
        "renderers": ["scene2d", "video"], "defaultRenderer": "scene2d",
        "iconPack": "icons.json",
        "assets": {
            "cartonBack": "back.svg", "cartonForeground": "front.svg",
            "crackGlow": "glow.svg", "redCord": "cord.svg", "dust": "dust.svg",
        },
    }), "utf-8")
    theme = ThemeManifest.load(manifest)
    assert theme.public()["iconPack"] == "icons.json"
    assert set(theme.public()["assets"]) >= {"cartonBack", "cartonForeground", "crackGlow", "redCord", "dust"}


def test_first_encounter_master_assets_are_high_dpi_and_transparent():
    project = Path(__file__).resolve().parents[1]
    theme = ThemeManifest.load(project / "themes" / "first-encounter" / "theme.json")
    with Image.open(theme.asset("background")) as background:
        assert background.size == (3840, 2400)
    for key in ("lilith", "desktopPet"):
        with Image.open(theme.asset(key)) as cutout:
            assert cutout.mode == "RGBA"
            low, high = cutout.getchannel("A").getextrema()
            assert low == 0
            assert high == 255
    video = theme.asset("video")
    assert video is not None and video.suffix.lower() == ".mp4" and video.stat().st_size > 500_000


def test_first_encounter_exposes_visual_tokens_and_character_states():
    project = Path(__file__).resolve().parents[1]
    theme = ThemeManifest.load(project / "themes" / "first-encounter" / "theme.json")
    public = theme.public()
    assert public["palette"]["surface"] == "#fffdf8"
    assert public["ui"]["language"] == "paper-porcelain"
    assert public["character"]["defaultPose"] == "idle-prayer"
    assert "reading" in public["character"]["poses"]
    assert "summer-cotton-dress" in public["character"]["outfits"]
    bundles = {value.pose_id: value for value in theme.pose_bundles()}
    assert bundles["reading"].artwork_asset == "poseExpansionSheet"
    assert bundles["reading"].sprite_id == "reading"
    assert bundles["resting"].public()["spriteId"] == "resting"
    sheet = theme.asset("poseExpansionSheet")
    assert sheet is not None
    with Image.open(sheet) as artwork:
        assert artwork.size == (1230, 1278)
        assert artwork.mode == "RGBA"


def test_theme_rejects_disabled_dangling_optional_pose_reference(tmp_path):
    project = Path(__file__).resolve().parents[1]
    source = project / "themes" / "first-encounter" / "theme.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["iconPack"] = ""
    raw["character"]["habitatPoseVariants"]["desktop-prayer"].update(
        {
            "optionalArtworkAsset": "poseMissing",
            "optionalArtworkEnabled": False,
            "artworkOutfits": ["first-encounter"],
        }
    )
    manifest = tmp_path / "theme.json"
    manifest.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="optional habitat artwork asset is missing"):
        ThemeManifest.load(manifest)


def test_theme_allows_complete_dormant_pose_but_rejects_enabling_missing_file(
    tmp_path,
):
    project = Path(__file__).resolve().parents[1]
    source = project / "themes" / "first-encounter" / "theme.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["iconPack"] = ""
    manifest = tmp_path / "theme.json"
    manifest.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    ThemeManifest.load(manifest)

    raw["character"]["habitatPoseVariants"]["micro-corner-grip"][
        "optionalArtworkEnabled"
    ] = True
    manifest.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="enabled optional habitat artwork file is missing"):
        ThemeManifest.load(manifest)


def test_theme_rejects_unknown_renderer(tmp_path):
    manifest = tmp_path / "theme.json"
    manifest.write_text(json.dumps({
        "id": "bad", "version": "1", "title": "bad",
        "renderers": ["html"], "defaultRenderer": "html"
    }), "utf-8")
    with pytest.raises(ValueError):
        ThemeManifest.load(manifest)


def test_socket_requires_token_and_exposes_declared_actions(tmp_path):
    database = Database(tmp_path / "lilies.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    registry.register(ComponentAction(
        "demo", "read", "read", "read test", Risk.READ,
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _payload: {"value": 7},
    ))
    server = LocalSocketServer(registry, tmp_path, port=0)
    server.start()
    try:
        port = int(server.endpoint.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(json.dumps({"id": 1, "method": "components.list", "auth": "wrong"}).encode() + b"\n")
            denied = json.loads(client.makefile("rb").readline())
        assert denied["ok"] is False
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(json.dumps({"id": 2, "method": "components.invoke", "auth": server.token,
                                       "params": {"componentId": "demo", "actionId": "read"}}).encode() + b"\n")
            allowed = json.loads(client.makefile("rb").readline())
        assert allowed["result"]["result"]["value"] == 7
    finally:
        server.stop()


def test_socket_cannot_forge_mutation_confirmation(tmp_path):
    database = Database(tmp_path / "lilies.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    registry.register(ComponentAction(
        "demo", "mutate", "mutate", "write test", Risk.MUTATE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _payload: {"changed": True},
    ))
    server = LocalSocketServer(registry, tmp_path, port=0)
    server.start()
    try:
        response = server.dispatch({
            "id": 7,
            "auth": server.token,
            "method": "components.invoke",
            "params": {"componentId": "demo", "actionId": "mutate", "confirmed": True},
        })
        assert response["ok"] is False
        assert response["confirmationRequired"] is True
    finally:
        server.stop()


def test_component_parameters_are_validated_and_forged_tools_are_rejected(tmp_path):
    database = Database(tmp_path / "lilies.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    registry.register(ComponentAction(
        "demo", "choose", "choose", "validation test", Risk.READ,
        {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer", "minimum": 1, "maximum": 3}},
            "additionalProperties": False,
        },
        lambda payload: payload["value"],
    ))
    with pytest.raises(ValueError):
        registry.invoke("demo", "choose", {"value": 5})
    with pytest.raises(ValueError):
        registry.invoke("demo", "choose", {"value": 2, "extra": True})
    with pytest.raises(KeyError):
        registry.by_tool_name("shell_exec")
    assert registry.invoke("demo", "choose", {"value": 2})["result"] == 2


def test_component_permission_allow_confirm_deny_and_audit(tmp_path):
    database = Database(tmp_path / "lilies.db")
    broker = PermissionBroker(database)
    registry = ComponentRegistry(database, broker)
    registry.register(ComponentAction(
        "demo", "write", "write", "mutation", Risk.MUTATE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _payload: {"changed": True},
    ))
    with pytest.raises(ConfirmationRequired):
        registry.invoke("demo", "write", {}, origin="model")
    allowed = registry.invoke("demo", "write", {}, origin="model", confirmed=True)
    assert allowed["result"]["changed"] is True
    broker.set_mode(PermissionMode.TRUSTED)
    database.set_setting("trusted_allowlist", ["demo.write"])
    assert registry.invoke("demo", "write", {}, origin="model")["result"]["changed"] is True
    with database.connect() as db:
        decisions = [row[0] for row in db.execute("SELECT decision FROM audit_log ORDER BY created_at")]
    assert "confirm" in decisions
    assert decisions.count("allow") >= 2


def test_reading_card_components_are_registered_with_risk_boundaries(tmp_path):
    database = Database(tmp_path / "lilies.db")
    broker = PermissionBroker(database)
    project = Path(__file__).resolve().parents[1]
    registry = build_registry(
        database,
        broker,
        DesktopIndex(database),
        ShellController(database, tmp_path, smoke=True),
        ThemeManifest.load(project / "themes" / "first-encounter" / "theme.json"),
        lambda: {"ready": True},
    )
    actions = {
        (value["componentId"], value["actionId"]): value["risk"]
        for value in registry.list()
    }
    assert actions[("reading-cards", "search")] == "read"
    assert actions[("reading-cards", "save")] == "mutate"
    assert actions[("reading-cards", "delete")] == "destructive"
    assert registry.invoke("reading-cards", "search", {})["result"] == []
    with pytest.raises(ConfirmationRequired):
        registry.invoke(
            "reading-cards",
            "save",
            {"sourceText": "source", "answer": "answer"},
            origin="model",
        )
