# Installing AdLife Lab

Three ways in, from simplest to most involved. All of them give you the same CLI.

## From a package index (uv tool install)

```bash
uv tool install adlife-sim
adlife --version
adlife doctor --offline
```

`uv tool install` puts the `adlife` executable on your PATH in uv's tool directory —
no virtualenv activation, no shell startup files edited, no administrator rights. Until
the first index release exists, install from source (below); the README's marked
installation block is rewritten to this form by `scripts/configure_repository.py` after
the first release.

A specific version:

```bash
ADLIFE_VERSION=0.1.0 uv tool install adlife-sim==0.1.0
```

## From source

```bash
git clone https://github.com/mahanfatehian/adlife-sim.git
cd adlife-sim
uv sync
uv run adlife --version
```

For a permanent tool install straight from the checkout:

```bash
uv tool install .
adlife --version
```

## One-line installers

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
command and exits rather than chaining a second remote script), `ADLIFE_VERSION`
selects a version, and both installers fail clearly if the package has not been
published yet.

## Frozen binaries

Each release ships native one-directory builds, no installation needed:

| Asset | Platform |
| --- | --- |
| `adlife-vX.Y.Z-linux-x86_64.tar.gz` | Linux x86_64 |
| `adlife-vX.Y.Z-macos-x86_64.tar.gz` | macOS Intel |
| `adlife-vX.Y.Z-macos-aarch64.tar.gz` | macOS Apple Silicon |
| `adlife-vX.Y.Z-windows-x86_64.zip` | Windows x86_64 |

Verify before extracting — the release's `SHA256SUMS` is the manifest:

```bash
sha256sum --check --ignore-missing SHA256SUMS
tar -xzf adlife-v0.1.0-linux-x86_64.tar.gz   # or: Expand-Archive …zip on Windows
./adlife/adlife --version
./adlife/adlife doctor --offline
```

The frozen binary contains no model weights and works in `rules` and `mock` modes with
no network at all.

## Verify the installation

```bash
adlife doctor --offline
```

checks versions, packaged resources, offline provider construction, storage
writability, and the console encoding — with zero network access.

## Troubleshooting

- **`cp1252` / UTF-8 warnings on Windows.** The default Windows console encoding is
  not UTF-8. Set `PYTHONUTF8=1` (PowerShell: `$env:PYTHONUTF8=1`) for reliable
  non-ASCII output; doctor reports this check explicitly.
- **`adlife` not on PATH after `uv tool install`.** Open a new terminal so the PATH
  change takes effect, or run `uv tool update-shell`.
- **Installer exits 2 with "uv is required".** That is the contract working: install
  uv from <https://docs.astral.sh/uv/>, then rerun the installer yourself.
