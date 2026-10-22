# Contributing to qg

Thank you for your interest in contributing! This project provides `qg`, a
command-line remote-control client for [QGIS](https://qgis.org/). It talks to
a small JSON-over-TCP server (the "qg server") that runs inside QGIS — either
via the bundled `qg_plugin` dock widget or via `qg start` / `qg_startup.py`.

## Getting Started

1. **Clone the Repository**
   ```bash
   git clone https://github.com/Hua0717/QG_CLI.git
   cd QG_CLI
   ```

2. **Install Prerequisites**
   - QGIS 3.X (tested on 3.22)
   - Python 3.12 or newer
   - No third-party Python dependencies — `qg` and the plugin are stdlib-only.

3. **Set Up the QGIS Plugin** (optional; `qg start` runs the server directly
   from this repo without installing the plugin)
   Create a symlink from this repo's `qg_plugin` folder to your QGIS
   profile plugin directory.

   On Windows PowerShell:
   ```powershell
   $src = "$(pwd)\qg_plugin"
   $dst = "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\qg"
   New-Item -ItemType SymbolicLink -Path $dst -Target $src
   ```

   Restart QGIS, go to `Plugins` > `Manage and Install Plugins`, search for
   **qg**, and enable it. Then open the qg server dock and click
   **Start Server**.

4. **Try the CLI**
   ```bash
   python src/qg/qg.py --help
   qg start                # launch QGIS with the server on its port
   qg ping                 # verify the connection
   qg layers               # list layers in the current project
   ```

## Development Workflow

- Start the qg server in QGIS (dock widget or `qg start`).
- Run `qg` subcommands against it and make your changes.
- Run the tests: `python -m pytest tests/`.

## Contributing Guidelines

- Keep PRs focused on a single change.
- Write clear commit messages.
- Update docs if behavior changes.
- Be cautious when using `execute_code` / the `exec` subcommand (it runs
  arbitrary PyQGIS inside the live QGIS instance).

## Reporting Issues

- Use [GitHub Issues](https://github.com/Hua0717/QG_CLI/issues).
- Include OS, QGIS version, and error logs where relevant.
