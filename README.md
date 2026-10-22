# qg — command-line control for QGIS

`qg` is a terminal toolkit for controlling running QGIS instances: manage layers and projects, run processing algorithms, execute arbitrary PyQGIS code, and render screenshots — all from the command line. It is **not** a Model Context Protocol project; the two components are simply the **qg CLI** (client) and the **qg server** (running inside QGIS).

No installation, no plugin manager, no third-party Python packages: the client is standard-library-only Python, and the server is injected into QGIS automatically.

## Architecture

```
qg CLI (client)  ── TCP JSON, one short-lived connection per command ──►  qg server (inside QGIS)
   src/qg/qg.py                                                        qg_plugin/
```

- **qg CLI** (`src/qg/qg.py`): an argparse-driven Python client, standard library only. Every command opens its own short-lived JSON-over-TCP connection to the server and closes it when the response arrives — there is no long-lived session to manage or drop.
- **qg server** (`qg_plugin/`): a socket server that runs inside QGIS on the main thread, driven by a `QTimer`. Requests are handled one at a time on QGIS's own event loop, so they always see live, thread-safe access to the project, layers and iface.
- **Ports**: port = `9875 + instance number`. Up to six parallel QGIS instances listen on **9876–9881** (`qg1` → 9876, `qg6` → 9881). An explicit `--port` overrides this.

## Install

1. Clone this repository. There is nothing to `pip install`.
2. Have Python 3 on your `PATH` and QGIS 3.x installed (the CLI locates `qgis.bat` automatically, or see `QGIS_BAT` below).
3. Use the wrapper scripts in `src/qg/bin/`: `qg.bat` (generic passthrough) and `qg1.bat` … `qg6.bat` (pre-select instances 1–6). Each wrapper resolves its path relative to its own location, so they work wherever the repo is cloned.

### Adding qg to PATH

To call `qg`, `qg1`, … from any shell, put `src/qg/bin` on your `PATH`:

- **Manually**: open System Properties → Environment Variables, and append the `bin` folder's full path to your user `PATH`.
- **Automatically**: run `install.bat` (see below), the bundled installer script, which adds the folder to your `PATH` for you.

## Usage

```
qg [options] COMMAND ...
```

Start QGIS, check it, run commands, stop it:

```bat
qg1 start                        :: launch QGIS instance 1 with the server on port 9876
qg1 start --project C:/data/x.qgz
qg1 status                       :: running? (with pid)
qg1 ping                         :: connectivity check
qg1 stop                         :: quit QGIS (never prompts; unsaved changes lost)
qg1 stop --save                  :: save the project first
qg1 stop --force                 :: kill the process listening on the port
```

### Instance wrappers

| Wrapper | Instance | Port |
|---|---|---|
| `qg.bat` | none — use `--instance`/`--port` yourself | default |
| `qg1.bat` | 1 | 9876 |
| `qg2.bat` | 2 | 9877 |
| `qg3.bat` | 3 | 9878 |
| `qg4.bat` | 4 | 9879 |
| `qg5.bat` | 5 | 9880 |
| `qg6.bat` | 6 | 9881 |

Each wrapper just forwards all arguments to `qg.py` with the instance preselected: `qg3 ping` ≡ `python ../qg.py --instance 3 ping`.

### Command reference

| Command | Description |
|---|---|
| `start` | Launch QGIS for this instance with the qg server on its port |
| `stop` | Close QGIS on this instance (`--save` to save first, `--force` to kill) |
| `status` | Show whether QGIS is running on this instance |
| `ping` | Check connectivity |
| `get_qgis_info` | Get QGIS information |
| `get_project_info` | Get current project information |
| `get_layers` | List all layers in the current project |
| `load_project` | Load a project |
| `create_new_project` | Create a new project and save it |
| `save_project` | Save project (optionally to PATH) |
| `add_vector_layer` | Add a vector layer |
| `add_raster_layer` | Add a raster layer |
| `remove_layer` | Remove a layer by ID |
| `zoom_to_layer` | Zoom to a layer's extent |
| `get_layer_features` | Get features from a vector layer (`--limit N`, default 10) |
| `execute_processing` | Run a processing algorithm; PARAMETERS is JSON (`@file.json` or `-` for stdin also work) |
| `render_map` | Render the map view to an image (`--width`, `--height`) |
| `screenshot` | Screenshot the QGIS window (or just the map canvas with `--canvas`) to PNG/JPG |
| `execute_code` | Execute PyQGIS code: CODE string, `--file FILE`, or stdin |

Run `qg COMMAND --help` for the options of an individual command. Exit status is `0` on success, `1` on a QGIS error or connection failure.

### Examples

Add a layer and inspect it:

```bat
qg1 add_vector_layer C:/data/a.shp --name Colonias
qg1 get_layers
qg1 get_layer_features <layer_id> --limit 5
qg1 zoom_to_layer <layer_id>
```

Run a processing algorithm — inline JSON, or via `@file` to avoid shell-quoting pain:

```bat
qg1 execute_processing native:buffer "{\"INPUT\":\"Colonias\",\"DISTANCE\":10,\"OUTPUT\":\"b.gpkg\"}"
qg1 execute_processing native:buffer @params.json
```

`params.json`:

```json
{"INPUT": "Colonias", "DISTANCE": 10, "OUTPUT": "b.gpkg"}
```

Execute PyQGIS code (string, file, or stdin):

```bat
qg1 execute_code "print(QgsProject.instance().count())"
qg1 execute_code --file script.py
type script.py | qg1 execute_code
```

Render and screenshot:

```bat
qg1 render_map out.png --width 1200 --height 800
qg1 screenshot shot.png             :: whole QGIS window
qg1 screenshot shot.png --canvas    :: map canvas only
```

## Configuration

| Env var | Purpose |
|---|---|
| `QGIS_BAT` | Full path to `qgis.bat` (e.g. `C:\Program Files\QGIS 3.40\bin\qgis.bat`). If unset, the CLI searches `C:/Program Files/QGIS 3*/bin/` and `C:/OSGeo4W*/bin/` |
| `QG_REPO` | Set automatically by `qg start` — the repo root, so the injected startup script can import the plugin directly from the repo |
| `QG_PORT` | Default port when neither `--instance` nor `--port` is given (server side: the port the injected server listens on) |

CLI options:

- `--instance, -i {1..6}` — instance number; port = 9875 + N (set by the `qg1`…`qg6` wrappers)
- `--port PORT` — explicit port, overrides `--instance`
- `--host HOST` — connect to a host other than `localhost`
- `--timeout TIMEOUT` — response timeout in seconds (default 300; raise it for heavy processing)
- `--raw` — print compact JSON instead of pretty-printed

## How the server loads

No plugin installation is needed. `qg start` launches QGIS as:

```
qgis.bat --profile qgN --code qg_startup.py
```

with `QG_PORT` and `QG_REPO` set in the environment. `qg_startup.py` (`src/qg/qg_startup.py`) runs inside QGIS at startup, adds the repo root to `sys.path`, imports the server from `qg_plugin/` directly, and starts it on the instance's port. Because the plugin is imported from the repo rather than copied into the QGIS profile, **edits to repo files take effect the next time you `qg start`** — no reinstall step.

QGIS profiles (`qg1` … `qg6`, or `qg<port>` for an explicit port) keep the six instances' settings and window layouts separate. Pass `--profile default` to `start` to reuse your main profile.

## Caveats

- **Localhost-only, unauthenticated socket.** Anything that can reach the port can send commands, including `execute_code` — that is arbitrary code execution inside QGIS. Do not expose the port beyond your machine, and be aware other local users/processes can connect.
- **Requests serialize.** The server runs on QGIS's main thread via a `QTimer`; a long-running handler (e.g. a heavy processing algorithm) freezes the QGIS window until it finishes. Use `--timeout` generously for long jobs.
- **`qg stop` never prompts.** Without `--save`, unsaved project changes are lost; with `--force` (or when a graceful quit is refused), the process listening on the port is killed outright.

## Related

- `docs/wor-converter.md` — a WOR (MapInfo workspace) converter tooling also lives in this repo.
- `src/qg/bin/README.md` — details on the wrapper scripts and troubleshooting.
