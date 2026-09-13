from typer.testing import CliRunner

from adlife.cli.app import app

runner = CliRunner()


def test_version_is_available() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "adlife 0.1.0"


def test_help_lists_product_name() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AdLife Lab" in result.stdout
