from pathlib import Path


def resolve_project_path(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = (
        candidate.resolve() if candidate.is_absolute() else (resolved_root / candidate).resolve()
    )
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"path is outside project root: {candidate}")
    return resolved_candidate
