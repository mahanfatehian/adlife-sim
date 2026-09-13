from pathlib import Path

import yaml
from pydantic import ValidationError

from adlife.config.models import AppConfig


def load_app_config(path: Path) -> AppConfig:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: configuration could not be parsed: {exc}") from exc

    if not isinstance(document, dict):
        raise ValueError(f"{path}: configuration must contain a mapping")

    try:
        return AppConfig.model_validate(document)
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid configuration: {exc}") from exc
