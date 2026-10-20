# Standalone WOR to QGS converter

A Windows desktop app for converting a MapInfo `.wor` workspace to a QGIS `.qgs` project. This is a source-based app and launcher, not a bundled EXE or an exact-equivalence migration guarantee. It runs independently of the QGIS desktop interface: no MCP plugin, running QGIS session, `iface`, virtualenv, or pip installation is required. It uses the Python, Qt, data providers, and authentication support supplied by an installed QGIS runtime.

## Launch

Double-click `launch_wor_converter.bat` in the repository root. It discovers QGIS under Program Files, preferring a QGIS 3.x installation before other QGIS versions, then tries common OSGeo4W directories. QGIS 3.x is recommended. An explicit `QGIS_ROOT` takes precedence; it must name the installation directory, not `bin`:

```bat
set "QGIS_ROOT=C:\Program Files\QGIS 3.44.2"
C:\Users\hua71\qgis_mcp\launch_wor_converter.bat
```

Alternatively, invoke the runner with a known runtime:

```bat
"C:\Program Files\QGIS 3.44.2\bin\python-qgis.bat" "C:\Users\hua71\qgis_mcp\tools\run_wor_converter.py"
```

The runner adds this repository's `src` directory to `sys.path`, initializes `QgsApplication`, shows the standalone window, runs the Qt event loop, and destroys the window/layers before shutting QGIS down. Launching it with normal Windows Python is not supported.

## Convert a workspace

1. Select **Open WOR** and choose the original workspace. Parsing only reads the workspace; it does not probe TAB paths or connect to a server.
2. Select each layer and choose **Configure selected source** (or double-click its row).
3. Explicitly choose **TAB file**, **SQL Server**, **Oracle**, or **XYZ tiles (web TAB replacement)**, check its settings, and press **Confirm source**. Every layer must have a confirmed source before conversion is enabled. Cancelling a source dialog does not confirm or change it. Hidden WOR layers still need sources.
4. Check the suggested output, which uses the selected WOR's directory and filename with a `.qgs` extension: `Roads.wor` → `Roads.qgs`. You can browse to any writable directory instead. Output paths must be absolute and end in `.qgs`.
5. Select **Convert to QGS**. If the output already exists, the app asks whether to replace it, with **No** as the default. Merely opening the app, loading a workspace, or browsing an output does not write a project.
6. Read the separate **Parser and conversion messages** panel, then open the saved project in QGIS and verify its layers, visibility, styles, labels, CRS, and extent against the original.

Conversion runs synchronously. The window shows a busy cursor and a conversion message while providers are opening sources and the project is being written. It may not repaint during a slow network connection; wait for completion. Controls are disabled during conversion. There is no background worker or cancellation mechanism.

### Workspace selection and TAB paths

There is no fixed workspace, layer count, layer-name list, database, or output filename. Open your own `.wor` file. Loading another workspace replaces the layer list and source suggestions, clears all source confirmations, and suggests the new workspace's matching `.qgs` output.

The initial suggestion uses each WOR source path, adds `.tab` if it has no extension, and resolves relative paths against the directory of the **loaded WOR**, not the process working directory. These suggestions are never implicitly confirmed. The app does not test a network share while loading or confirming sources; provider access happens during conversion.

The files under `examples/` are test fixtures, not a restriction to particular maps. If you open a copied example, its relative paths resolve under that copy's directory; copying a WOR does not copy its TAB data. Choose the actual TAB files or explicit database sources. UNC paths are path suggestions, not inferred SQL Server connections.

### SQL Server and Oracle

Enter the actual host, optional port, database/Oracle service, schema, table, and geometry column. The stable unique key is optional. The alias is only an editable **table suggestion**: it is not evidence of the real database table name. No database name, server name, credentials, or source type is inferred from a WOR path.

- The default geometry column is `GEOLOC`; change it to the real geometry column when necessary.
- **Stable unique key is optional** for Oracle and SQL Server sources. Leave it blank to let the QGIS provider identify features using its normal discovery behavior. For ordinary Oracle tables this can use a discovered primary key or `ROWID`. Views or other sources where discovery fails may still need an explicit existing unique, non-null column. If a key is supplied, the converter checks that the column exists; it does not independently prove uniqueness. A blank key does not guarantee that a provider can open every table or view.
- A WOR `Display Graphic` layer normally relies on per-object MapInfo styles. If replacing it with a database source without those styles, conversion blocks by default. You may explicitly check **Allow reported Global-style fallback when database has no MapInfo object styles** for that layer. This loses per-object styling, uses the WOR Global style, and produces a conversion warning rather than silently flattening it.
- Enter an optional **Source CRS override**, such as `EPSG:3376`, only when you know the correct source CRS. It is an override, not a coordinate transformation.
- SQL Server offers **Windows integrated authentication**, which uses the current process identity. Username and authcfg fields are disabled/omitted for this choice.
- Otherwise, enter an existing **QGIS authcfg ID**. The optional username is not a password. The app requires an authcfg ID for non-integrated SQL Server and Oracle.
- Installed QGIS provider support, appropriate ODBC/Oracle clients, server access, and permissions are still required. The app does not install drivers or test database connectivity during parsing.

### Web TAB replacement with XYZ tiles

Some web-service TAB files, such as the sample `_OpenStreetMap` source, cannot be opened by GDAL as ordinary TAB files. Choose **XYZ tiles (web TAB replacement)** and enter the **original service's explicit URL template**, containing `{z}`, `{x}`, and `{y}` (for example, the template format `https://service.example/{z}/{x}/{y}.png`; this is not a real default endpoint). Check the original service configuration, licensing, and access policy. No public tile endpoint is assumed or contacted automatically, and no network request is made while loading or confirming the source. The QGIS WMS/XYZ provider is opened only on Convert; tiles may be requested then or when the saved project is displayed.

Use a public, token-free template. Do not embed usernames, passwords, tokens, or API keys in the template: it is stored in the resulting project. Embedded URL username/password and all query parameters are rejected by the form and engine. This replacement is not exact MapInfo raster equivalence; raster adjustments from the WOR are reported as unconverted warnings. The table/log does not echo the full template.

## Authentication and secrets

There is deliberately **no plaintext password field**, password persistence, connection-URI entry, or source-mapping save file. Configure credentials in QGIS's Authentication settings first and copy the authentication configuration ID (`authcfg`) into the source dialog. The ID references the encrypted QGIS authentication database; it is not the password itself. The resulting QGS may reference that ID and therefore requires access to the matching authentication configuration on the machine/profile where it is opened.

The runner uses `%APPDATA%/QGIS/QGIS3/profiles/default` as the `QgsApplication` custom configuration path so it can use that profile's existing `qgis-auth.db`. It does not copy or replace the authentication database. Configurations in another QGIS profile are not automatically imported. Avoid editing authentication settings concurrently in another process.

An encrypted authentication database may require its **master password to be unlocked in this standalone process**. Unlocking it in an already-running QGIS desktop process does not guarantee this process is unlocked. Depending on the QGIS build, OS password helper, provider, and auth method, an interactive master-password prompt may be unavailable or the provider may not expand authcfg automatically. If authentication fails, check the profile, authcfg ID, password-helper setup, provider support, and QGIS authentication behavior. The converter does not bypass master-password protection or request/store the master password itself. SQL Server integrated authentication avoids this database credential lookup when supported.

Connection errors shown in the UI do not include raw provider exception tracebacks. Suspected connection URIs/credential-bearing diagnostics are suppressed in the log. Do not paste a URI, password, or master password into host, table, username, authcfg, or other fields. Review project files before sharing them; server names, table names, local paths, and authcfg references can still be sensitive.

## Conversion behavior

- The `Map From` list determines top-to-bottom QGIS layer order, independently of `Open Table` order. `Display Off` layers remain loaded and unchecked.
- GDAL translates the complete MapInfo `CoordSys` into QGIS WKT. Source layers retain their own CRS unless you explicitly assign a source-CRS override.
- Global pen/brush/marker styles use QGIS's native MapInfo symbol converter. `Display Graphic` TAB layers use per-feature embedded styles, without dominant-style simplification.
- Label definitions are retained where supported; automatic labels are enabled only for `Auto On` and non-Off label visibility. Supported expressions include field names and concatenations with strings and `Chr$()`; unsupported expressions disable labeling with a warning. Ignored selectability, partial-segment labels, label-pen styling, and line-arrow settings produce targeted warnings.
- Zoom-dependent visibility uses the saved physical map-window width. A maximized MapInfo window may have different dimensions, so screen-scale equivalence is not guaranteed.
- A uniquely named `*.conversion-*.json` report is saved alongside the project. Unsupported settings and accepted fallbacks appear there and in the app log.
- Multi-map workspaces, mixed geometry, unsupported source CRSs, and unreadable selected sources block export rather than silently omitting layers. Geographic-CRS view extents requiring geodesic width conversion are currently blocked.
- Referenced TAB data stays at its selected location; it is not copied or embedded in the QGS. Database data stays in the selected database. The project needs continued access to those sources.

## Verification

Run the parser/QGIS integration tests and standalone UI smoke separately using the installed QGIS runtime:

```bat
"C:\Program Files\QGIS 3.44.2\bin\python-qgis.bat" -m unittest discover -s C:\Users\hua71\qgis_mcp\tests -p "test_wor_*.py" -v
"C:\Program Files\QGIS 3.44.2\bin\python-qgis.bat" C:\Users\hua71\qgis_mcp\tests\smoke_wor_app.py
```

Parser tests cover all four example workspaces (11, 16, 32, and 52 map layers). Conversion tests use synthetic local TAB files and temporary QGS projects, including differently named workspaces with different projected CRSs, layer ordering and visibility. UI tests check arbitrary filenames, source suggestions, and switching workspaces. These tests do not establish full conversion of the four examples' original data, live SQL Server/Oracle access, or original-map pixel equivalence.

## Scope and troubleshooting

Mixed-geometry TAB sources must be split into geometry-specific layers before conversion. Tiled/web-service TABs may require a native QGIS source and can block conversion; not every TAB represents a directly readable local vector layer.

WOR is a scripting format, not just a layer list. The converter supports a defined subset and reports unsupported/mismatched settings as parser or conversion warnings. It does not run MapBasic commands. A saved project is not a guarantee of pixel-identical rendering or that every source will be available on another computer. Review all warnings and inspect the result in QGIS.

- **No runtime found:** install QGIS with Python support or set `QGIS_ROOT`; do not install a package named `qgis` with pip.
- **Runtime starts but the UI does not:** use a QGIS 3.x installation and ensure the runner and `src/qgis_mcp/wor_*.py` files are present in this checkout.
- **Convert remains disabled:** confirm every layer individually, including initially hidden layers, and enter an output path.
- **Wrong TAB location:** select the original WOR or browse to the actual TAB file; copying the WOR does not copy its data.
- **Database failure:** verify the explicit connection settings, matching provider/client installation, auth configuration, and source CRS/key/geometry settings. Loading a WOR intentionally does not validate these remotely.
- **Cannot save:** choose an existing writable output directory; the original WOR's directory may be read-only or on an unavailable share. Check sync-folder permissions/availability where relevant. Existing projects are replaced only after explicit confirmation.
