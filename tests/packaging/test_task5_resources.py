import subprocess
from pathlib import Path
from zipfile import ZipFile

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NAME_RESOURCE_MEMBER = "adlife/resources/names/fictional.yaml"


def test_built_wheel_contains_fictional_name_resource(tmp_path: Path) -> None:
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = tuple(tmp_path.glob("*.whl"))
    assert len(wheels) == 1

    with ZipFile(wheels[0]) as archive:
        assert _NAME_RESOURCE_MEMBER in archive.namelist()
