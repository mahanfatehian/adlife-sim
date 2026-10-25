# Installing AdLife Lab

AdLife Lab is a Python package that you run with [uv](https://docs.astral.sh/uv/). Every
installation path below gives you the same `adlife` command: the browser app
(`adlife app`) and the command-line engine (`adlife demo`, `adlife run`, `adlife city-run`,
and the rest). There is no separate standalone executable.

## From source (works today)

```bash
git clone https://github.com/mahanfatehian/adlife-sim.git
cd adlife-sim
uv sync
uv run adlife --version
```

Run commands from the checkout with `uv run`, for example `uv run adlife app` for the
browser app or `uv run adlife demo` for the offline engine demonstration.

For a permanent `adlife` command on your PATH, straight from the checkout:

```bash
uv tool install .
adlife --version
```

## From a package index (after the first release)

AdLife is not published on the Python Package Index (PyPI) yet. Once the first release is
published, this installs it without cloning:

```bash
uv tool install adlife-sim
adlife --version
adlife doctor --offline
```

`uv tool install` puts the `adlife` executable on your PATH in uv's tool directory —
no virtualenv activation, no shell startup files edited, no administrator rights. After the
first release, `scripts/configure_repository.py` rewrites the README's marked installation
block to a tag-pinned installer URL.

A specific version:

```bash
uv tool install adlife-sim==0.1.0
```

## One-line installers (after the first release)

Once the first release is published, the installers install the package (via uv) and
run the health checks for you:

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/mahanfatehian/adlife-sim/v0.1.0/scripts/install.sh | sh
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Installer contract: no `sudo`/admin, no shell-profile edits, uv is a **requirement**
(the installer prints the official [uv installation](https://docs.astral.sh/uv/)
URL and exits rather than chaining a second remote script), `ADLIFE_VERSION`
selects a version, and both installers fail clearly if the package has not been
published yet.

## Verify the installation

```bash
adlife doctor --offline
```

(From a source checkout: `uv run adlife doctor --offline`.) It checks versions, packaged
resources, offline provider construction, storage writability, and the console encoding —
with zero network access.

## Troubleshooting

- **`cp1252` / UTF-8 warnings on Windows.** The default Windows console encoding is
  not UTF-8. Set `PYTHONUTF8=1` (PowerShell: `$env:PYTHONUTF8=1`) for reliable
  non-ASCII output; doctor reports this check explicitly.
- **`adlife` not on PATH after `uv tool install`.** Open a new terminal so the PATH
  change takes effect, or run `uv tool update-shell`.
- **Installer exits 2 with "uv is required".** That is the contract working: install
  uv from <https://docs.astral.sh/uv/>, then rerun the installer yourself.
