from pathlib import Path

import yaml
from pydantic import ValidationError

from adlife.config.models import AppConfig

MAX_YAML_BYTES = 1_048_576
MAX_YAML_DEPTH = 32
MAX_YAML_EVENTS = 50_000


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            raise yaml.YAMLError("duplicate mapping key")
        return mapping


def load_yaml_document(path: Path) -> object:
    """Read bounded UTF-8 YAML without aliases, duplicate keys, or executable tags.

    Parse errors deliberately omit source snippets: files can accidentally contain
    credentials, which must not become diagnostic text or chained exceptions.
    """
    with path.open("rb") as stream:
        raw = stream.read(MAX_YAML_BYTES + 1)
    if len(raw) > MAX_YAML_BYTES:
        raise ValueError(f"{path}: YAML exceeds the {MAX_YAML_BYTES}-byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError(f"{path}: YAML must be UTF-8") from None
    try:
        depth = 0
        for count, event in enumerate(yaml.parse(text), start=1):
            if isinstance(event, yaml.AliasEvent):
                raise yaml.YAMLError("aliases are unsupported")
            if isinstance(event, (yaml.SequenceStartEvent, yaml.MappingStartEvent)):
                depth += 1
            elif isinstance(event, (yaml.SequenceEndEvent, yaml.MappingEndEvent)):
                depth -= 1
            if depth > MAX_YAML_DEPTH or count > MAX_YAML_EVENTS:
                raise yaml.YAMLError("document exceeds structural limits")
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError, ValueError):
        raise ValueError(
            f"{path}: configuration could not be parsed; YAML must have unique keys, "
            "no aliases or custom tags, and bounded nesting"
        ) from None


def load_app_config(path: Path) -> AppConfig:
    document = load_yaml_document(path)

    if not isinstance(document, dict):
        raise ValueError(f"{path}: configuration must contain a mapping")

    try:
        return AppConfig.model_validate(document)
    except ValidationError as exc:
        reasons = "; ".join(
            error["msg"] for error in exc.errors(include_input=False, include_context=False)
        )
        raise ValueError(f"{path}: invalid configuration: {reasons}") from None
