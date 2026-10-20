"""Read the declarative map subset of a MapInfo workspace; never execute MapBasic."""
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
import re


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
UNITS = {"m": 1.0, "km": 1000.0, "ft": 0.3048, "mi": 1609.344,
         "in": 0.0254, "cm": 0.01, "mm": 0.001, "yd": 0.9144}
CHARSETS = {"windowslatin1": "cp1252", "utf-8": "utf-8-sig", "utf8": "utf-8-sig",
            "windowslatin2": "cp1250", "windowscyrillic": "cp1251",
            "windowsgreek": "cp1253", "windowsturkish": "cp1254",
            "windowsbalticrim": "cp1257", "windowschinesesimp": "gbk",
            "windowschinesetrad": "big5", "windowsjapanese": "cp932"}


@dataclass
class LayerSpec:
    alias: str
    path: str
    display: str = "Graphic"
    style: dict = field(default_factory=lambda: {
        "pen": [1, 2, 0], "brush": [1, 16777215, 16777215],
        "symbol": [35, 0, 12], "line": [1, 2, 0]})
    label: dict | None = None
    zoom: tuple[float, float] | None = None  # metres, map window width
    warnings: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class Workspace:
    path: Path
    layers: list[LayerSpec]
    coordsys: str
    center: tuple[float, float] | None = None
    zoom_m: float | None = None
    window_width_m: float | None = None
    aspect: float | None = None
    warnings: list[str] = field(default_factory=list)


def distance(value, unit):
    if unit.lower() not in UNITS:
        raise ValueError(f"Unsupported distance unit: {unit}")
    return float(value) * UNITS[unit.lower()]


def table_path(workspace, reference):
    """Resolve without touching a potentially unavailable network share."""
    p = PureWindowsPath(reference)
    if not p.suffix:
        p = p.with_suffix(".tab")
    if p.drive and not p.root:
        raise ValueError("Drive-relative TAB paths need an explicit absolute path.")
    if p.is_absolute() or p.root:
        return str(p)
    return str(Path(workspace).parent.joinpath(*p.parts))


def _range(text, prefix):
    m = re.search(prefix + rf'\s*\(\s*({NUMBER})\s*,\s*({NUMBER})\s*\)(?:\s+Units\s+"([^"]+)")?(\s+Off)?', text, re.I | re.M)
    if not m or m.group(4):
        return None
    values = tuple(distance(v, m.group(3) or "m") for v in m.group(1, 2))
    if values[0] < 0 or values[1] <= values[0]:
        raise ValueError("Zoom bounds must satisfy 0 <= minimum < maximum.")
    return values


def _parse_layer(spec, body):
    spec.raw = body
    m = re.search(r"^\s*Display\s+(\w+)", body, re.M | re.I)
    if m:
        spec.display = m.group(1).capitalize()
    if spec.display not in {"Graphic", "Global", "Off"}:
        raise ValueError(f"{spec.alias}: unsupported Display mode {spec.display}")
    gl = re.search(r"^\s*Global\b(.*?)(?=^\s*(?:Zoom|Label|Selectable|Display|Dim|Set|Undim)\b|\Z)", body, re.M | re.S | re.I)
    if gl:
        for key in spec.style:
            st = re.search(rf"\b{key}\s*\(([^)]*)\)", gl.group(1), re.I)
            if st:
                parts = [p.strip() for p in st.group(1).split(",")]
                if len(parts) != 3 or any(not re.fullmatch(NUMBER, p) for p in parts):
                    spec.warnings.append(f"Extended {key} syntax is not supported; standard fallback used.")
                    continue
                values = [float(p) for p in parts]
                spec.style[key] = [int(p) if p.is_integer() else p for p in values]
    spec.zoom = _range(body, r"^\s*Zoom")
    label_text = re.search(r"^\s*Label\b(.*)", body, re.M | re.S | re.I)
    if label_text:
        text = label_text.group(1)
        lb = re.search(rf'Line\s+(\w+)\s+Position\s+([A-Za-z ]+?)\s+Font\s*\("([^"]*)"\s*,\s*(\d+)\s*,\s*({NUMBER})\s*,\s*(\d+)(?:\s*,\s*(\d+))?\)', text, re.I)
        expr = re.search(r"^\s*With\s+(.+)$", text, re.M | re.I)
        if not lb or not expr:
            spec.warnings.append("Label syntax could not be translated; labeling disabled.")
        else:
            def flag(name, default=False):
                f = re.search(rf"\b{name}\s+(On|Off)\b", text, re.I)
                return f.group(1).lower() == "on" if f else default
            offset = re.search(rf"\bOffset\s+({NUMBER})", text, re.I)
            spec.label = {"callout": lb.group(1).capitalize(), "position": lb.group(2).strip().title(),
                          "family": lb.group(3), "style": int(lb.group(4)), "size": float(lb.group(5)),
                          "color": int(lb.group(6)), "bg": int(lb.group(7)) if lb.group(7) else None,
                          "field": expr.group(1).strip(), "parallel": flag("Parallel"),
                          "auto": flag("Auto"), "overlap": flag("Overlap"),
                          "duplicates": flag("Duplicates", True),
                          "offset": float(offset.group(1)) if offset else 0,
                          "visible": not bool(re.search(r"\bVisibility\s+Off", text, re.I)),
                          "zoom": _range(text, r"\bVisibility\s+Zoom")}
    for pattern, warning in (
        (r"\bPartialSegments[ \t]+(?:On|Off)\b", "Label PartialSegments behavior is not translated."),
        (r"\bSelectable[ \t]+(?:On|Off)\b", "MapInfo layer selectability is not transferred."),
        (r"^[ \t]*Arrows[ \t]+(?:On|Off)\b", "MapInfo line-arrow settings are not translated."),
    ):
        if re.search(pattern, body, re.I | re.M):
            spec.warnings.append(warning)
    if label_text and re.search(r"\bPen[ \t]*\(", label_text.group(1), re.I):
        spec.warnings.append("MapInfo label Pen styling is not translated.")
    if re.search(r"\b(?:Thematic|Override|GroupLayer|StyleOverride)\b", body, re.I):
        spec.warnings.append("Thematic/group/style overrides are not translated.")
    if re.search(r"^\s*(?:contrast|brightness|alpha|transparency|grayscale)\b", body, re.I | re.M):
        spec.warnings.append("MapInfo raster color/brightness/transparency adjustments are not translated.")


def parse_workspace(text, path):
    maps = list(re.finditer(r"^\s*Map\s+From\s+", text, re.M | re.I))
    if len(maps) != 1:
        raise ValueError("Exactly one Map From window is supported; split multi-map workspaces first.")
    tables = {}
    for m in re.finditer(r'^\s*Open\s+Table\s+"((?:[^"]|"")+)"(?:\s+As\s+(\w+))?', text, re.M | re.I):
        ref = m.group(1).replace('""', '"')
        alias = m.group(2) or PureWindowsPath(ref).stem
        if alias.upper() in tables:
            raise ValueError(f"Duplicate table alias: {alias}")
        tables[alias.upper()] = (alias, ref)
    start = maps[0].end()
    end = re.search(r"^\s*(?:Position|Width|Height|Set|Create|Dim)\b", text[start:], re.M | re.I)
    order_text = text[start:start + end.start()] if end else text[start:]
    aliases = [s.strip().strip('"') for s in order_text.replace("\n", " ").split(",") if s.strip()]
    if not aliases or any(not re.fullmatch(r"\w+", a) for a in aliases):
        raise ValueError("Map From must contain a comma-separated list of table aliases.")
    if len({a.upper() for a in aliases}) != len(aliases):
        raise ValueError("Repeated aliases in one map are not supported.")
    layers = []
    for alias in aliases:
        if alias.upper() not in tables:
            raise ValueError(f"Map layer {alias} has no matching Open Table statement.")
        layers.append(LayerSpec(alias, tables[alias.upper()][1]))
    cs = re.search(r"^\s*(CoordSys\s+.+)$", text, re.M | re.I)
    if not cs:
        raise ValueError("Workspace has no explicit CoordSys; refusing to guess its CRS.")
    ws = Workspace(Path(path).resolve(), layers, cs.group(1).strip())
    c = re.search(rf"\bCenter\s*\(\s*({NUMBER})\s*,\s*({NUMBER})\s*\)", text, re.I)
    if c:
        ws.center = (float(c.group(1)), float(c.group(2)))
    z = re.search(rf'^\s*Zoom\s+({NUMBER})\s+Units\s+"([^"]+)"', text, re.M | re.I)
    if z:
        ws.zoom_m = distance(z.group(1), z.group(2))
    size = re.search(rf'\bWidth\s+({NUMBER})\s+Units\s+"([^"]+)"\s+Height\s+({NUMBER})\s+Units\s+"([^"]+)"', text, re.I)
    if size:
        ws.window_width_m = distance(size.group(1), size.group(2))
        height = distance(size.group(3), size.group(4))
        if ws.window_width_m <= 0 or height <= 0:
            raise ValueError("Map window dimensions must be positive.")
        ws.aspect = ws.window_width_m / height
    if re.search(r"Set\s+Window\s+\w+\s+Max\b", text, re.I):
        ws.warnings.append("MapInfo maximized window dimensions are unknown; zoom/scale use the saved window width, not the maximized display.")
    blocks = list(re.finditer(r"^\s*Layer\s+(\d+)\s*$", text, re.M | re.I))
    seen = set()
    for i, m in enumerate(blocks):
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= len(layers) or idx in seen:
            raise ValueError("Invalid or duplicate numbered Layer block.")
        seen.add(idx)
        body = text[m.end():blocks[i + 1].start() if i + 1 < len(blocks) else len(text)]
        _parse_layer(layers[idx], body)
    if len(seen) != len(layers):
        ws.warnings.append("Some layers have no Layer block; MapInfo default object styling is assumed.")
    if re.search(r"^[ \t]*(?:Select|Run|Include|Create[ \t]+(?:Thematic|Map|Layout)|Set[ \t]+Map[ \t]+Layer[ \t]+\w+)\b", text, re.M | re.I):
        ws.warnings.append("Workspace contains commands outside the supported declarative map subset. No MapBasic/SQL commands are executed.")
    # Surface unrecognized statements: a WOR is a program, not just a data file.
    known = re.compile(r'^(?:!|Open\s+Table\b|Map\s+From\b|Position\b|Width\b|Height\b|Set\s+(?:Map\s*$|Window\b)|'
                       r'image\s+reprojection\s+always\s+resampling\s+nearestneighbor\b|CoordSys\b|Center\b|Zoom\b|'
                       r'Preserve\s+Zoom\b|Distance\b|Layer\s+\d+\b|Display\b|Global\b|Label\b|With\b|Parallel\b|'
                       r'Visibility\b|Selectable\b|StatusBar\b|Dim\b|Undim\b|WorkspaceMaximizedWindow\s*=|'
                       r'contrast\b|brightness\b|alpha\b|transparency\b|color\b|grayscale\b)', re.I)
    for line in text.splitlines():
        statement = line.strip()
        if not statement or known.match(statement):
            continue
        if statement.endswith(",") or re.fullmatch(r"[\w,\s]+", statement) and all(t.strip().upper() in tables for t in statement.split(",") if t.strip()):
            continue  # continuation of a multiline Map From list
        token = re.match(r"[A-Za-z_]+", statement)
        ws.warnings.append(f"Untranslated workspace statement ({token.group(0) if token else 'unknown syntax'}); inspect the original WOR.")
    if not ws.window_width_m:
        ws.warnings.append("No map-window width: scale-dependent visibility cannot be translated.")
    ws.warnings = list(dict.fromkeys(ws.warnings))
    return ws


def read_workspace(path):
    raw = Path(path).read_bytes()
    header = re.search(br'!Charset\s+([^\r\n]+)', raw[:4096], re.I)
    charset = header.group(1).decode("ascii").strip().lower() if header else "windowslatin1"
    if charset not in CHARSETS:
        raise ValueError(f"Unsupported workspace charset: {charset}; save a UTF-8 copy first.")
    try:
        return parse_workspace(raw.decode(CHARSETS[charset]), path)
    except UnicodeError as e:
        raise ValueError("Workspace text does not match its declared charset.") from e
