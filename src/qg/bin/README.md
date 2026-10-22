# qg - qg CLI

`qg` is a small command line client for the [qg server](../../../README.md). Every MCP tool is exposed as a subcommand, and each invocation opens its own short-lived connection to the socket server running inside QGIS, so there is no long-lived session for an agent to drop. It is written with the Python standard library only — no `uv` or third-party packages needed.

## Components

- **`qg.py`** (`src/qg/qg.py`): the CLI itself.
- **`qg.bat`, `qg1.bat` … `qg6.bat`** (this folder): thin Windows wrappers. Each one runs `python "%~dp0..\qg.py"` — a relative path resolved from the batch file's own location — so the wrappers keep working wherever the repo is cloned, without any installation step.

## Installation / Prerequisites

- Python 3 on your `PATH` (the wrappers invoke `python` directly).
- QGIS 3.x with the `qg_plugin` installed (see the project README for plugin installation).
- The qg server must be running in QGIS on the instance's port (start it from the plugin dialog, or use `qg start`).

The `start` subcommand locates QGIS via the `QGIS_BAT` environment variable or by searching `C:/Program Files/QGIS 3*/bin/qgis.bat` and `C:/OSGeo4W*/bin/qgis.bat`.

To make `qg` available on your `PATH`, add this `bin` folder to `PATH`.

## Usage

```
qg [options] COMMAND ...
```

Every MCP tool is a subcommand. Run `qg COMMAND --help` for the options of an individual command.

Subcommands:

| Command | Description |
|---|---|
| `start` | Launch QGIS for this instance with the MCP server on its port |
| `stop` | Close QGIS on this instance (never prompts; unsaved changes are lost) |
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
| `get_layer_features` | Get features from a vector layer |
| `execute_processing` | Run a processing algorithm; PARAMETERS is JSON, `@file.json`, or `-` for stdin |
| `render_map` | Render the map view to an image |
| `screenshot` | Screenshot of the QGIS window (or just the map canvas) to a PNG/JPG file |
| `execute_code` | Execute PyQGIS code: CODE string, `--file FILE`, or stdin |

Options:

| Option | Description |
|---|---|
| `--instance, -i {1..6}` | Instance number; port is `9875 + N`. Set by the `qg1`…`qg6` wrappers |
| `--port PORT` | Explicit port (overrides `--instance`) |
| `--host HOST` | Host to connect to |
| `--timeout TIMEOUT` | Response timeout, seconds |
| `--raw` | Print compact JSON |

Exit status: `0` on success, `1` on QGIS error or connection failure.

## Per-instance wrappers

Six QGIS instances can be controlled in parallel, each on its own port. The wrappers in this folder are shortcuts that pre-select the instance number:

| Wrapper | Instance | Port |
|---|---|---|
| `qg.bat` | (none; use `--instance`/`--port` yourself) | default |
| `qg1.bat` | 1 | 9876 |
| `qg2.bat` | 2 | 9877 |
| `qg3.bat` | 3 | 9878 |
| `qg4.bat` | 4 | 9879 |
| `qg5.bat` | 5 | 9880 |
| `qg6.bat` | 6 | 9881 |

Each wrapper simply invokes `python "%~dp0..\qg.py" [--instance N] %*`, so any arguments you pass are forwarded to `qg.py` unchanged. For example, `qg3 ping` is equivalent to `python ../qg.py --instance 3 ping`.

## Examples

```bat
qg1 start --project C:/data/cdmx.qgz   (launch QGIS with the server on this port)
qg1 status
qg1 stop --save
qg1 ping
qg1 get_layers
qg1 add_vector_layer C:/data/a.shp --name Colonias
qg1 get_layer_features <layer_id> --limit 5
qg1 execute_processing native:buffer "{\"INPUT\":\"Colonias\",\"DISTANCE\":10,\"OUTPUT\":\"b.gpkg\"}"
qg1 execute_processing native:buffer @params.json
qg1 execute_code "print(QgsProject.instance().count())"
qg1 execute_code --file script.py      (or pipe code on stdin)
qg1 render_map out.png --width 1200 --height 800
qg1 screenshot shot.png --canvas
qg1 save_project
```

Explicit port or instance, when not using a wrapper:

```
qg --port 9900 ping    (or set env QG_PORT)
qg --instance 2 layers
```

## Troubleshooting

- **"cannot connect to QGIS at HOST:PORT"** — the qg server is not running on that port. Start it from the qg server dialog, or use `qgN start` to launch QGIS with the server enabled.
- **"already running on port N"** (from `start`) — an instance is already listening on that port. Use `qgN status` or `qgN ping` to confirm.
- **"port N is in use by another process (pid P)"** — another process holds the port. Stop it, or choose a different port with `--port`.
- **"timed out after Ns waiting for QGIS (use --timeout)"** — QGIS took longer than the timeout to answer (common with heavy processing algorithms). Retry with a larger `--timeout`.
- **"connection closed before a complete response arrived"** — QGIS closed the connection mid-response; check the QGIS Python console/plugin log for a crash.
- **`qg start` fails with "qgis.bat not found"** — set the `QGIS_BAT` environment variable to the full path of your `qgis.bat` (e.g. `C:\Program Files\QGIS 3.40\bin\qgis.bat`).
- **Wrong instance replies** — confirm the port mapping (port = 9875 + instance number) matches the port set in the QGIS plugin on that instance.
