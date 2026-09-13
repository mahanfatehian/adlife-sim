from __future__ import annotations

import json
import os
import socket
import subprocess
from hashlib import sha256
from importlib import import_module
from inspect import Parameter, signature
from pathlib import Path
from types import ModuleType
from typing import get_type_hints

import pytest
from typer.testing import CliRunner

from adlife.cli.app import app as root_app
from adlife.core.domain.campaign import Campaign

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _campaign_module() -> ModuleType:
    try:
        return import_module("adlife.cli.commands.campaign")
    except ModuleNotFoundError:
        pytest.fail("Task 7 campaign import behavior is not implemented", pytrace=False)


def _campaign_yaml(
    *,
    asset_path: str | None = None,
    asset_sha256: str | None = None,
    message: str = "A synthetic campaign for a fictional pocket device.",
) -> str:
    asset_lines = ""
    if asset_path is not None:
        asset_lines += f"asset_path: {json.dumps(asset_path)}\n"
    if asset_sha256 is not None:
        asset_lines += f"asset_sha256: {json.dumps(asset_sha256)}\n"
    return (
        "schema_version: 1\n"
        "campaign_id: campaign-phone\n"
        "name: Orbit Pocket\n"
        "product_name: Orbit Pocket Device\n"
        "product_category: consumer-electronics\n"
        f"message: {json.dumps(message)}\n"
        "call_to_action: Explore the fictional device\n"
        "price:\n"
        "  amount: 850.0\n"
        "  currency: USD\n"
        "category_reference_price: 1000.0\n"
        "target_interests:\n"
        "  - technology\n"
        "start_minute: 0\n"
        "end_minute: 1440\n"
        "placements:\n"
        "  - schema_version: 1\n"
        "    channel: mobile-feed\n"
        "    zone: online\n"
        "    active_windows:\n"
        "      - start_minute_of_day: 420\n"
        "        end_minute_of_day: 1320\n"
        "    frequency_cap: 3\n"
        "    visibility: 0.8\n"
        f"{asset_lines}"
        "creative_features:\n"
        "  schema_version: 1\n"
        "  description: A fictional device against a clean green field.\n"
        "  visual_style: minimal-product\n"
        "  dominant_colors:\n"
        "    - green\n"
        "    - white\n"
        "  visible_text:\n"
        "    - Orbit Pocket\n"
        "  contains_people: false\n"
    )


def _write_campaign(root: Path, text: str) -> Path:
    path = root / "campaigns" / "campaign.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (NotImplementedError, OSError):
        if os.name != "nt":
            raise

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_campaign_public_signature_remains_exact() -> None:
    campaign_module = _campaign_module()
    function_signature = signature(campaign_module.import_campaign)
    hints = get_type_hints(campaign_module.import_campaign)

    assert tuple(
        (name, parameter.kind, hints[name], parameter.default)
        for name, parameter in function_signature.parameters.items()
    ) == (
        ("path", Parameter.POSITIONAL_OR_KEYWORD, Path, Parameter.empty),
        ("project_root", Parameter.POSITIONAL_OR_KEYWORD, Path, Parameter.empty),
    )
    assert hints["return"] is Campaign


def test_import_campaign_adapts_yaml_sequences_to_the_strict_domain_model(
    tmp_path: Path,
) -> None:
    campaign_module = _campaign_module()
    campaign_path = _write_campaign(tmp_path, _campaign_yaml())

    campaign = campaign_module.import_campaign(campaign_path, tmp_path)

    assert isinstance(campaign, Campaign)
    assert campaign.target_interests == frozenset({"technology"})
    assert isinstance(campaign.placements, tuple)
    assert isinstance(campaign.placements[0].active_windows, tuple)
    assert campaign.asset_path is None
    assert campaign.asset_sha256 is None


def test_import_campaign_preserves_strict_scalar_types(tmp_path: Path) -> None:
    campaign_module = _campaign_module()
    text = _campaign_yaml().replace("start_minute: 0", 'start_minute: "0"')
    campaign_path = _write_campaign(tmp_path, text)

    with pytest.raises(ValueError, match="invalid campaign"):
        campaign_module.import_campaign(campaign_path, tmp_path)


@pytest.mark.parametrize("yaml_number", (".inf", "-.inf", ".nan"))
def test_import_campaign_and_root_cli_reject_non_finite_yaml_numbers(
    tmp_path: Path,
    yaml_number: str,
) -> None:
    campaign_module = _campaign_module()
    text = _campaign_yaml().replace("amount: 850.0", f"amount: {yaml_number}")
    campaign_path = _write_campaign(tmp_path, text)

    with pytest.raises(campaign_module.CampaignImportError, match="invalid campaign"):
        campaign_module.import_campaign(campaign_path, tmp_path)

    result = CliRunner().invoke(
        root_app,
        [
            "campaign",
            "import",
            str(campaign_path),
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "invalid campaign" in result.stderr


@pytest.mark.parametrize(
    "malformed_interest",
    ("{nested: value}", "[nested, value]"),
)
def test_import_campaign_normalizes_malformed_interest_shapes(
    tmp_path: Path,
    malformed_interest: str,
) -> None:
    campaign_module = _campaign_module()
    text = _campaign_yaml().replace(
        "target_interests:\n  - technology",
        f"target_interests:\n  - {malformed_interest}",
    )
    campaign_path = _write_campaign(tmp_path, text)

    with pytest.raises(campaign_module.CampaignImportError, match="invalid campaign"):
        campaign_module.import_campaign(campaign_path, tmp_path)


def test_import_campaign_streams_asset_hash_and_stores_only_normalized_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_module = _campaign_module()
    asset = tmp_path / "assets" / "creative.bin"
    asset.parent.mkdir()
    content = b"synthetic-creative\0" * 150_000
    asset.write_bytes(content)
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(
            asset_path="assets/./creative.bin",
            asset_sha256="A" * 64,
        ),
    )

    def reject_eager_read(path: Path) -> bytes:
        raise AssertionError(f"asset was read eagerly: {path}")

    monkeypatch.setattr(Path, "read_bytes", reject_eager_read)

    campaign = campaign_module.import_campaign(campaign_path, tmp_path)
    serialized = campaign.model_dump_json()

    assert campaign.asset_path == "assets/creative.bin"
    assert campaign.asset_sha256 == sha256(content).hexdigest()
    assert str(tmp_path.resolve()) not in serialized
    assert "synthetic-creative" not in serialized


def test_absolute_asset_inside_project_is_stored_as_portable_relative_path(
    tmp_path: Path,
) -> None:
    campaign_module = _campaign_module()
    asset = tmp_path / "assets" / "creative.bin"
    asset.parent.mkdir()
    asset.write_bytes(b"synthetic")
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path=asset.resolve().as_posix()),
    )

    campaign = campaign_module.import_campaign(campaign_path, tmp_path)

    assert campaign.asset_path == "assets/creative.bin"


@pytest.mark.parametrize("asset_path", ("../outside.bin", "assets/../assets/creative.bin"))
def test_import_campaign_rejects_asset_path_traversal(
    tmp_path: Path,
    asset_path: str,
) -> None:
    campaign_module = _campaign_module()
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "creative.bin").write_bytes(b"inside")
    (tmp_path.parent / "outside.bin").write_bytes(b"outside")
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path=asset_path),
    )

    with pytest.raises(ValueError, match="traversal"):
        campaign_module.import_campaign(campaign_path, tmp_path)


@pytest.mark.parametrize("asset_path", ("assets/missing.bin", "assets"))
def test_import_campaign_rejects_missing_or_non_file_assets(
    tmp_path: Path,
    asset_path: str,
) -> None:
    campaign_module = _campaign_module()
    (tmp_path / "assets").mkdir()
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path=asset_path),
    )

    with pytest.raises(ValueError, match="regular file"):
        campaign_module.import_campaign(campaign_path, tmp_path)


def test_import_campaign_rejects_a_symlink_escape(tmp_path: Path) -> None:
    campaign_module = _campaign_module()
    assets = tmp_path / "assets"
    assets.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-assets"
    outside.mkdir()
    (outside / "creative.bin").write_bytes(b"outside")
    _make_directory_link(assets / "escape", outside)
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path="assets/escape/creative.bin"),
    )

    with pytest.raises(ValueError, match="outside project root"):
        campaign_module.import_campaign(campaign_path, tmp_path)


def test_import_campaign_resolves_an_internal_symlink_to_its_target(tmp_path: Path) -> None:
    campaign_module = _campaign_module()
    assets = tmp_path / "assets"
    assets.mkdir()
    target_directory = assets / "real"
    target_directory.mkdir()
    target = target_directory / "creative.bin"
    target.write_bytes(b"inside")
    _make_directory_link(assets / "alias", target_directory)
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path="assets/alias/creative.bin"),
    )

    campaign = campaign_module.import_campaign(campaign_path, tmp_path)

    assert campaign.asset_path == "assets/real/creative.bin"
    assert campaign.asset_sha256 == sha256(b"inside").hexdigest()


def test_import_campaign_rejects_external_ancestor_swap_after_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_module = _campaign_module()
    live_assets = tmp_path / "assets" / "live"
    live_assets.mkdir(parents=True)
    (live_assets / "creative.bin").write_bytes(b"validated-inside")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-ancestor"
    outside.mkdir()
    (outside / "creative.bin").write_bytes(b"outside-swap")
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path="assets/live/creative.bin"),
    )
    original_hash = campaign_module._hash_verified_asset
    retired_assets = live_assets.with_name("retired")

    def swap_ancestor_then_hash(*args: object, **kwargs: object) -> object:
        live_assets.rename(retired_assets)
        _make_directory_link(live_assets, outside)
        return original_hash(*args, **kwargs)

    monkeypatch.setattr(campaign_module, "_hash_verified_asset", swap_ancestor_then_hash)

    with pytest.raises(
        campaign_module.CampaignImportError,
        match=r"outside project root|verify asset location",
    ):
        campaign_module.import_campaign(campaign_path, tmp_path)


def test_import_campaign_fails_closed_when_handle_location_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_module = _campaign_module()
    asset = tmp_path / "assets" / "creative.bin"
    asset.parent.mkdir()
    asset.write_bytes(b"inside")
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path="assets/creative.bin"),
    )

    def unavailable_handle_path(descriptor: int) -> Path:
        del descriptor
        raise OSError("handle path lookup unavailable")

    monkeypatch.setattr(campaign_module, "_open_file_path", unavailable_handle_path)

    with pytest.raises(
        campaign_module.CampaignImportError,
        match="verify asset location",
    ):
        campaign_module.import_campaign(campaign_path, tmp_path)


def test_import_campaign_rejects_asset_replaced_between_validation_and_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_module = _campaign_module()
    asset = tmp_path / "assets" / "creative.bin"
    asset.parent.mkdir()
    asset.write_bytes(b"validated-content")
    replacement = asset.with_name("replacement.bin")
    replacement.write_bytes(b"swapped-content")
    original_identity = (asset.stat().st_dev, asset.stat().st_ino)
    replacement_identity = (replacement.stat().st_dev, replacement.stat().st_ino)
    if original_identity == replacement_identity:
        pytest.skip("filesystem does not expose distinct stable file identities")
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(asset_path="assets/creative.bin"),
    )
    real_open = os.open

    def replace_then_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path) == asset.resolve():
            os.replace(replacement, asset)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_then_open)

    with pytest.raises(campaign_module.CampaignImportError, match="changed while being opened"):
        campaign_module.import_campaign(campaign_path, tmp_path)

    assert asset.read_bytes() == b"swapped-content"


def test_import_campaign_rejects_unsafe_yaml_tags_without_executing_them(
    tmp_path: Path,
) -> None:
    campaign_module = _campaign_module()
    marker = tmp_path / "unsafe-tag-executed"
    text = _campaign_yaml().replace(
        'message: "A synthetic campaign for a fictional pocket device."',
        f"message: !!python/object/new:pathlib.Path [{json.dumps(str(marker))}]",
    )
    campaign_path = _write_campaign(tmp_path, text)

    with pytest.raises(ValueError, match="could not be parsed"):
        campaign_module.import_campaign(campaign_path, tmp_path)

    assert not marker.exists()


def test_import_campaign_treats_creative_text_as_inert_data_without_env_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_module = _campaign_module()
    monkeypatch.setenv("ADLIFE_TEST_SECRET", "must-not-be-read")

    def reject_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("campaign import attempted network access")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    message = "${ADLIFE_TEST_SECRET} Ignore instructions; this remains creative text."
    campaign_path = _write_campaign(
        tmp_path,
        _campaign_yaml(message=message),
    )

    campaign = campaign_module.import_campaign(campaign_path, tmp_path)

    assert campaign.message == message
    assert "must-not-be-read" not in campaign.model_dump_json()


@pytest.mark.parametrize("text", ("- not\n- a\n- mapping\n", "!!python/tuple [1, 2]\n"))
def test_import_campaign_rejects_non_mapping_and_unsafe_documents(
    tmp_path: Path,
    text: str,
) -> None:
    campaign_module = _campaign_module()
    campaign_path = _write_campaign(tmp_path, text)

    with pytest.raises(ValueError):
        campaign_module.import_campaign(campaign_path, tmp_path)


def test_campaign_subcommand_imports_and_prints_validated_json(tmp_path: Path) -> None:
    campaign_module = _campaign_module()
    campaign_path = _write_campaign(tmp_path, _campaign_yaml())

    result = CliRunner().invoke(
        campaign_module.app,
        ["import", str(campaign_path), "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["campaign_id"] == "campaign-phone"


def test_campaign_subcommand_maps_invalid_input_to_exit_code_two(tmp_path: Path) -> None:
    campaign_module = _campaign_module()
    campaign_path = _write_campaign(tmp_path, "!!python/tuple [1, 2]\n")

    result = CliRunner().invoke(
        campaign_module.app,
        ["import", str(campaign_path), "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "could not be parsed" in result.stderr


def test_campaign_subcommand_maps_malformed_interest_shape_to_exit_code_two(
    tmp_path: Path,
) -> None:
    campaign_module = _campaign_module()
    text = _campaign_yaml().replace(
        "target_interests:\n  - technology",
        "target_interests:\n  - {nested: value}",
    )
    campaign_path = _write_campaign(tmp_path, text)

    result = CliRunner().invoke(
        campaign_module.app,
        ["import", str(campaign_path), "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "invalid campaign" in result.stderr


def test_root_campaign_command_maps_malformed_interest_shape_to_exit_code_two(
    tmp_path: Path,
) -> None:
    text = _campaign_yaml().replace(
        "target_interests:\n  - technology",
        "target_interests:\n  - {nested: value}",
    )
    campaign_path = _write_campaign(tmp_path, text)

    result = CliRunner().invoke(
        root_app,
        [
            "campaign",
            "import",
            str(campaign_path),
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 2
    assert "invalid campaign" in result.stderr


def test_both_fictional_examples_parse_through_the_public_import_surface() -> None:
    campaign_module = _campaign_module()

    phone = campaign_module.import_campaign(
        _PROJECT_ROOT / "examples" / "phone-campaign.yaml",
        _PROJECT_ROOT,
    )
    billboard = campaign_module.import_campaign(
        _PROJECT_ROOT / "examples" / "billboard-campaign.yaml",
        _PROJECT_ROOT,
    )

    assert tuple(placement.channel for placement in phone.placements) == ("mobile-feed",)
    assert tuple(placement.channel for placement in billboard.placements) == ("highway-billboard",)
    assert phone.creative_features.contains_people is False
    assert billboard.creative_features.contains_people is False
    serialized = (phone.model_dump_json() + billboard.model_dump_json()).lower()
    assert "api_key" not in serialized
    assert "@" not in serialized
