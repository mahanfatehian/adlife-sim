"""Rewrite the README installation block from the repository's own origin.

``scripts/configure_repository.py`` is the one tool allowed to rewrite the README's
machine-editable installation block. It reads ``git remote get-url origin``, accepts
SSH and HTTPS GitHub URLs, validates owner/repository characters, and replaces only
what sits between the two markers with the concrete tag-pinned installer URL — never a
variable, never an example account. When the origin is missing or not GitHub, it exits
without changing any file.

Run it after the first published release tag:

    ADLIFE_VERSION=0.1.0 python scripts/configure_repository.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

START = "<!-- adlife-install:start -->"
END = "<!-- adlife-install:end -->"


def parse_github_slug(remote: str) -> str:
    """Extract the validated ``owner/repository`` slug from a git origin URL."""
    remote = remote.strip()
    if remote.startswith("git@github.com:"):
        slug = remote.removeprefix("git@github.com:")
    elif remote.startswith("https://github.com/"):
        slug = remote.removeprefix("https://github.com/")
    else:
        raise ValueError("origin is not a GitHub SSH or HTTPS URL")
    slug = slug.removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug) is None:
        raise ValueError("invalid GitHub owner/repository")
    return slug


def installation_block(slug: str, version: str) -> str:
    """The exact block written between the markers: concrete, tag-pinned, variable-free."""
    url = f"https://raw.githubusercontent.com/{slug}/v{version}/scripts/install.sh"
    return f"{START}\ncurl -fsSL {url} | sh\n{END}"


def current_origin() -> str:
    completed = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("no git origin is configured")
    return completed.stdout.strip()


def rewrite_readme(readme: Path, slug: str, version: str) -> bool:
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise ValueError(f"{readme} does not contain the {START} markers")
    before_marker, rest = text.split(START, 1)
    _, after_marker = rest.split(END, 1)
    updated = before_marker + installation_block(slug, version) + after_marker
    if updated == text:
        return False
    readme.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    readme = Path(os.environ.get("ADLIFE_README", "README.md"))
    version = os.environ.get("ADLIFE_VERSION") or "0.1.0"
    origin_override = os.environ.get("ADLIFE_ORIGIN")

    try:
        remote = origin_override if origin_override else current_origin()
        slug = parse_github_slug(remote)
        changed = rewrite_readme(readme, slug, version)
    except ValueError as error:
        print(f"configure_repository: {error}; no files changed.", file=sys.stderr)
        raise SystemExit(2) from error

    print(f"installation block {'updated' if changed else 'already current'} ({slug}, v{version}).")


if __name__ == "__main__":
    main()
