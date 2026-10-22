"""
Apply a MapInfo workspace (.wor) to the QGIS project that is open in this QGIS.

Runs inside QGIS (e.g. `qg1 execute_code --file ...`), see apply().

What is converted, per workspace layer (matched to project layers by table alias == layer name):
  - Global Pen/Brush/Symbol/Line  -> symbol (Symbol for points, Line for lines, Pen+Brush for polygons).
    Pen/brush/symbol codes are translated by GDAL's own MapInfo driver (the same translation QGIS
    uses for the per-object "embedded" styles), not by hand-made tables.
  - Display Global / Off          -> single symbol from the Global style (real legend swatch)
    Display Graphic               -> single symbol from the uniform per-object style stored in the table
                                     (embedded per-object styles only if the objects differ)
  - Zoom (min, max) Units "m"     -> layer scale range (see ZOOM_WINDOW_M)
  - Label ... With FIELD          -> labeling: font, size, colour, bold/italic/underline/caps/halo,
                                     position, parallel (lines), overlap, duplicates, callout, label zoom range
  - Map CoordSys/Center/Zoom      -> project CRS check and canvas extent
Layer visibility ("Display Off") is NOT changed.
"""
import os
import re
import tempfile

from osgeo import ogr
from qgis.core import (
    QgsWkbTypes,
    Qgis, QgsEmbeddedSymbolRenderer, QgsFillSymbol, QgsPointPatternFillSymbolLayer, QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings,
    QgsProject, QgsRectangle, QgsSimpleFillSymbolLayer, QgsSimpleLineCallout, QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

# MapInfo zoom distances are map-window widths in metres. QGIS scale = distance / window width on
# screen, so a window width has to be assumed: 0.30 m (~11.8 in) makes 750 m -> 1:2500.
ZOOM_WINDOW_M = 0.30

# Display Global layers that must still use the style stored in the table (confirmed against
# MapInfo output; the workspace's Global orange fill is not what is shown for BANGUNAN).
OBJECT_STYLE_GLOBAL_LAYERS = {"BANGUNAN"}

_QUAD = {
    "Above Left": "AboveLeft", "Above": "Above", "Above Right": "AboveRight",
    "Left": "Left", "Center": "Over", "Right": "Right",
    "Below Left": "BelowLeft", "Below": "Below", "Below Right": "BelowRight",
}


def parse(text):
    """Parse the parts of a .wor file that matter: layer order, Layer blocks, CoordSys/Center/Zoom."""
    m = re.search(r"^Map From(.*?)^\s+Position", text, re.S | re.M)
    names = re.findall(r"[A-Za-z0-9_]+", m.group(1))
    ws = {"names": names, "layers": {}, "files": {}}
    for path, alias in re.findall(r'Open Table "([^"]+)"\s+As\s+(\w+)', text, re.I):
        ws["files"][path.replace("\\", "/").split("/")[-1].upper()] = alias.upper()

    cs = re.search(r'CoordSys Earth Projection (\d+), (\d+), "m", ([\d.\-]+), ([\d.\-]+)', text)
    ws["coordsys"] = (int(cs.group(1)), int(cs.group(2)), float(cs.group(3)), float(cs.group(4))) if cs else None
    c = re.search(r"Center \(([\d.\-]+),([\d.\-]+)\)\s+Zoom ([\d.]+)", text)
    ws["view"] = (float(c.group(1)), float(c.group(2)), float(c.group(3))) if c else None

    blocks = re.split(r"^\s*Layer (\d+)\s*$", text, flags=re.M)
    for i in range(1, len(blocks), 2):
        idx, body = int(blocks[i]), blocks[i + 1].split("Set Window")[0]
        d = {"alias": names[idx - 1]}
        d["display"] = re.search(r"Display (\w+)", body).group(1)
        # Global clause: any subset of Pen/Brush/Symbol/Line may be present; MapInfo defaults fill the rest.
        gl = re.search(r"^\s*Global\b(.*)$", body, re.M)
        gtxt = gl.group(1) if gl else ""
        for key, default in (("pen", [1, 2, 0]), ("brush", [1, 16777215, 16777215]),
                             ("symbol", [35, 0, 12]), ("line", [1, 2, 0])):
            mm = re.search(key.capitalize() + r" \((\d+),(\d+),(\d+)", gtxt)
            d[key] = [int(x) for x in mm.groups()] if mm else default
        z = re.search(r'^\s+Zoom \((\d+), *(\d+)\) Units "m"( Off)?', body, re.M)
        d["zoom"] = (int(z.group(1)), int(z.group(2))) if z and not z.group(3) else None
        lb = re.search(r'Label Line (\w+) Position ([A-Za-z ]+?) Font \("([^"]*)",(\d+),(\d+),(\d+)(?:,(\d+))?\)', body)
        f = re.search(r"With (.+?)\s*$", body, re.M)  # a field name or a MapInfo expression
        p = re.search(r"Parallel (On|Off) Auto (On|Off) Overlap (On|Off) Duplicates (On|Off)", body)
        v = re.search(r"Visibility (On|Off|Zoom \((\d+), *(\d+)\))", body)
        d["label"] = {
            "callout": lb.group(1), "position": lb.group(2).strip(), "family": lb.group(3),
            "style": int(lb.group(4)), "size": int(lb.group(5)), "color": int(lb.group(6)),
            "bg": int(lb.group(7)) if lb.group(7) else None,
            "field": f.group(1), "parallel": p.group(1) == "On", "overlap": p.group(3) == "On",
            "duplicates": p.group(4) == "On", "offset": int(re.search(r"Offset (\d+)", body).group(1)),
            "visible": v.group(1) != "Off",
            "zoom": (int(v.group(2)), int(v.group(3))) if v.group(2) else None,
        } if lb and f and p and v else None
        ws["layers"][d["alias"].upper()] = d
    return ws


# ---- MapInfo style -> OGR style string -> QGIS symbol -------------------------------------------

_tmpdir = None
_count = 0
_cache = {}


def _gdal_style(geom, style):
    """Round-trip a style string through GDAL's MapInfo driver; it adds ogr-pen/brush/sym ids and dash patterns."""
    global _tmpdir, _count
    if (geom, style) in _cache:
        return _cache[(geom, style)]
    _tmpdir = _tmpdir or tempfile.mkdtemp(prefix="mws_")
    _count += 1
    path = os.path.join(_tmpdir, f"s{_count}.tab")
    drv = ogr.GetDriverByName("MapInfo File")
    gt, wkt = {"point": (ogr.wkbPoint, "POINT(1 1)"), "line": (ogr.wkbLineString, "LINESTRING(0 0,1 1)"),
               "polygon": (ogr.wkbPolygon, "POLYGON((0 0,1 0,1 1,0 0))")}[geom]
    ds = drv.CreateDataSource(path)
    lyr = ds.CreateLayer("t", None, gt)
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
    f.SetStyleString(style)
    lyr.CreateFeature(f)
    ds = None
    rds = ogr.Open(path)  # keep a reference: the layer dies with its datasource
    out = next(iter(rds.GetLayer(0))).GetStyleString()
    rds = None
    _cache[(geom, style)] = out
    return out


def _hex(n):
    return "#%06x" % n


def _pen(w, pat, col):
    width = f"{w}px" if w <= 7 else f"{(w - 10) / 10:g}pt"  # MapInfo: 1-7 px, 11+ = 1/10 pt steps
    return f'PEN(c:{_hex(col)},w:{width},id:"mapinfo-pen-{pat}")'


def _parse_ogr_style(style):
    """'BRUSH(fc:#..,id:"..");PEN(w:1px,..)' -> {'BRUSH': {...}, 'PEN': {...}}"""
    parts = {}
    for name, body in re.findall(r"(\w+)\((.*?)\)(?=;|$)", style):
        parts[name] = {m.group(1): m.group(2).strip('"')
                       for m in re.finditer(r'(\w+):("[^"]*"|[^,]*)', body)}
    return parts


def _length(text):
    """'1px' / '0.5pt' / '12 2px' -> ([numbers], render unit)"""
    unit = Qgis.RenderUnit.Pixels if text.endswith("px") else Qgis.RenderUnit.Points
    return [float(x) for x in re.findall(r"[\d.]+", text)], unit


def _line_layer(pen):
    (w,), unit = _length(pen.get("w", "1px"))
    layer = QgsSimpleLineSymbolLayer(QColor(pen.get("c", "#000000")), w)
    layer.setWidthUnit(unit)
    layer.setPenCapStyle(Qt.RoundCap)
    layer.setPenJoinStyle(Qt.RoundJoin)
    ogr_id = int(re.search(r"ogr-pen-(\d+)", pen.get("id", "ogr-pen-0")).group(1))
    if ogr_id == 1:  # MapInfo pattern 1 = invisible
        layer.setPenStyle(Qt.NoPen)
    elif "p" in pen:  # dash/gap lengths from GDAL's MapInfo pattern table
        dashes, dunit = _length(pen["p"])
        layer.setUseCustomDashPattern(True)
        layer.setCustomDashVector(dashes)
        layer.setCustomDashPatternUnit(dunit)
    return layer


# OGR symbol ids: 0 +, 1 x, 2 circle, 3 filled circle, 4 square, 5 filled square, 6 triangle,
# 7 filled triangle, 8 star, 9 filled star, 10 vertical bar
_MARKERS = {0: ("Cross", False), 1: ("Cross2", False), 2: ("Circle", False), 3: ("Circle", True),
            4: ("Square", False), 5: ("Square", True), 6: ("Triangle", False), 7: ("Triangle", True),
            8: ("Star", False), 9: ("Star", True), 10: ("Line", False)}


# MapInfo draws its filled standard symbols with a black outline (checked against eKadas output:
# 32 = red square with black border, 34 = white circle with black ring / black dot, 35 = black star).
# GDAL's ogr-sym ids give the shapes (35 = star, 34 = circle, 32 = square) but no outline.
# Format: (shape, black outline, size factor)
_MI_SYMBOLS = {32: ("Square", True, 1.0), 34: ("Circle", True, 1.0), 35: ("Star", True, 1.0)}


# MapInfo stipple brushes that GDAL reports as plain solid, drawn as textures instead.
# pattern -> (kind, pitch px, dot px); checked against MapInfo output:
#   12 (BANGUNAN): mostly solid foreground with sparse white specks -> "speckle"
#   17 (TOWNKG):   fine foreground dots on a clear background        -> "dots"
# Other stipple patterns (e.g. 15 on the lot layers) are still solid.
_BRUSH_TEXTURE = {12: ("speckle", 8, 1.2), 17: ("dots", 3, 1.6)}


def _dot_marker(size_px, color):
    m = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, size_px)
    m.setSizeUnit(Qgis.RenderUnit.Pixels)
    m.setColor(color)
    m.setStrokeStyle(Qt.NoPen)
    return m


def _symbol_from_ogr(geom, parts):
    """Build a QGIS symbol from a parsed OGR style (as returned by GDAL for MapInfo styles)."""
    if geom == "point":
        st = parts["SYMBOL"]
        (size,), unit = _length(st.get("s", "12pt"))
        color = QColor(st.get("c", "#000000"))
        mi_id = int(re.search(r"mapinfo-sym-(\d+)", st["id"]).group(1))
        if mi_id in _MI_SYMBOLS:  # codes whose real MapInfo look differs from GDAL's ogr-sym mapping
            name, black_outline, scale = _MI_SYMBOLS[mi_id]
            m = QgsSimpleMarkerSymbolLayer(getattr(Qgis.MarkerShape, name), size * scale)
            m.setColor(color)
            if black_outline:
                m.setStrokeColor(QColor("#000000"))
                m.setStrokeWidth(0.2)
            else:
                m.setStrokeStyle(Qt.NoPen)
        else:
            ogr_id = int(re.search(r"ogr-sym-(\d+)", st["id"]).group(1))
            name, filled = _MARKERS.get(ogr_id, ("Circle", True))
            m = QgsSimpleMarkerSymbolLayer(getattr(Qgis.MarkerShape, name), size)
            m.setColor(color if filled else QColor(0, 0, 0, 0))
            m.setStrokeColor(color)
            m.setStrokeWidth(0.2)
        m.setSizeUnit(unit)
        return QgsMarkerSymbol([m])
    if geom == "line":
        return QgsLineSymbol([_line_layer(parts["PEN"])])
    layers = []
    brush = parts.get("BRUSH")
    fill = QgsSimpleFillSymbolLayer(QColor(brush["fc"]) if brush and "fc" in brush else QColor("#ffffff"))
    fill.setStrokeStyle(Qt.NoPen)
    # GDAL has no equivalent for MapInfo hatch/stipple patterns other than 3-8; it reports them as solid
    if not brush or int(re.search(r"ogr-brush-(\d+)", brush.get("id", "ogr-brush-1")).group(1)) == 1:
        fill.setBrushStyle(Qt.NoBrush)
    layers.append(fill)
    mi = re.search(r"mapinfo-brush-(\d+)", brush.get("id", "")) if brush else None
    texture = _BRUSH_TEXTURE.get(int(mi.group(1))) if mi else None
    if texture:
        kind, pitch, dot = texture
        if kind == "dots":  # no solid fill: only foreground dots
            fill.setBrushStyle(Qt.NoBrush)
        pat = QgsPointPatternFillSymbolLayer()
        pat.setSubSymbol(QgsMarkerSymbol([_dot_marker(dot, QColor("#ffffff") if kind == "speckle" else QColor(brush["fc"]))]))
        pat.setDistanceX(pitch)
        pat.setDistanceY(pitch)
        pat.setDistanceXUnit(Qgis.RenderUnit.Pixels)
        pat.setDistanceYUnit(Qgis.RenderUnit.Pixels)
        layers.append(pat)
    if "PEN" in parts:
        layers.append(_line_layer(parts["PEN"]))
    return QgsFillSymbol(layers)


def make_symbol(geom, d):
    """Symbol for the workspace layer's Global style."""
    if geom == "point":
        shape, col, size = d["symbol"]
        style = f'SYMBOL(c:{_hex(col)},s:{size}pt,id:"mapinfo-sym-{shape}")'
    elif geom == "line":
        style = _pen(*d["line"])
    else:
        pat, fc, bc = d["brush"]
        style = f'BRUSH(fc:{_hex(fc)},bc:{_hex(bc)},id:"mapinfo-brush-{pat}");' + _pen(*d["pen"])
    return _symbol_from_ogr(geom, _parse_ogr_style(_gdal_style(geom, style)))


def sample_object_style(layer, geom, limit=2000, dominance=0.95, scan_limit=30000):
    """The per-object style stored in the .tab, if one style covers >= `dominance` of the sampled
    styled objects. Returns (symbol or None, description)."""
    try:
        ds = ogr.Open(layer.source().split("|")[0])
        lyr = ds.GetLayer(0)
        counts, matched, dim = {}, 0, {"point": 0, "line": 1, "polygon": 2}[geom]
        for i, f in enumerate(lyr):  # tables can mix geometry types: only sample this layer's kind
            g = f.GetGeometryRef()
            if g is not None and g.GetDimension() == dim:
                matched += 1
                st = f.GetStyleString()
                if st:
                    counts[st] = counts.get(st, 0) + 1
            if matched >= limit or i >= scan_limit:
                break
        ds = None
    except Exception:
        return None, "cannot read object styles (not a file-based table)"
    if not counts:
        return None, "objects have no style"
    best, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / sum(counts.values())
    if share < dominance:
        return None, f"object styles differ ({len(counts)} styles, top {share:.0%})"
    parts = _parse_ogr_style(best)
    need = {"point": "SYMBOL", "line": "PEN", "polygon": "PEN"}[geom]
    if need not in parts:
        return None, "object style has no " + need
    return _symbol_from_ogr(geom, parts), f"object style ({share:.0%} of {sum(counts.values())})"


# ---- labels --------------------------------------------------------------------------------------

def mapinfo_label_expression(text, names):
    """MapInfo label expression (FIELD+Chr$(13)+FIELD2+"M") -> (QGIS expression, missing field or None).
    A plain field name comes back unchanged with is_expression False via the caller."""
    parts, missing = [], None
    for tok in re.findall(r'"[^"]*"|Chr\$\(\d+\)|[A-Za-z_][A-Za-z0-9_]*', text):
        if tok.startswith('"'):
            parts.append("'" + tok[1:-1].replace("'", "''") + "'")
        elif tok.upper().startswith("CHR$"):
            parts.append(f"char({'10' if tok[5:-1] == '13' else tok[5:-1]})")  # MapInfo CR -> newline
        elif tok.upper() in names:
            parts.append(f"coalesce(\"{names[tok.upper()]}\", '')")
        else:
            missing = missing or tok
    return " || ".join(parts), missing


def make_labeling(layer, geom, lab):
    names = {f.name().upper(): f.name() for f in layer.fields()}
    s = QgsPalLayerSettings()
    if lab["field"].upper() in names:
        s.fieldName = names[lab["field"].upper()]
        s.isExpression = False
    else:
        expr, missing = mapinfo_label_expression(lab["field"], names)
        if missing or not expr or not re.search(r"[+]", lab["field"]):
            return None, f"field {missing or lab['field']} not in layer"
        s.fieldName = expr
        s.isExpression = True

    fmt = QgsTextFormat()
    font = QFont(lab["family"])
    st = lab["style"]
    font.setBold(bool(st & 1))
    font.setItalic(bool(st & 2))
    font.setUnderline(bool(st & 4))
    fmt.setFont(font)
    fmt.setSize(lab["size"])
    fmt.setSizeUnit(Qgis.RenderUnit.Points)
    fmt.setColor(QColor(_hex(lab["color"])))
    if st & 256:
        fmt.setCapitalization(Qgis.Capitalization.AllUppercase)
    if st & 32 and lab["bg"] is not None:  # halo
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1)
        buf.setSizeUnit(Qgis.RenderUnit.Points)
        buf.setColor(QColor(_hex(lab["bg"])))
        fmt.setBuffer(buf)
    s.setFormat(fmt)

    pos = lab["position"]
    if geom == "line":
        if lab["parallel"]:
            s.placement = Qgis.LabelPlacement.Line
            flag = {"Above": Qgis.LabelLinePlacementFlag.AboveLine,
                    "Below": Qgis.LabelLinePlacementFlag.BelowLine}.get(pos, Qgis.LabelLinePlacementFlag.OnLine)
            s.lineSettings().setPlacementFlags(flag | Qgis.LabelLinePlacementFlag.MapOrientation)
        else:
            s.placement = Qgis.LabelPlacement.Horizontal
        s.dist = lab["offset"]
        s.distUnits = Qgis.RenderUnit.Points
    else:  # points and polygons: anchor the label box on the point / centroid
        s.placement = Qgis.LabelPlacement.OverPoint
        s.quadOffset = getattr(QgsPalLayerSettings, "Quadrant" + _QUAD.get(pos, "Over"))
        s.xOffset = lab["offset"] if "Right" in pos else -lab["offset"] if "Left" in pos else 0
        s.yOffset = lab["offset"] if "Above" in pos else -lab["offset"] if "Below" in pos else 0
        s.offsetUnits = Qgis.RenderUnit.Points

    s.placementSettings().setOverlapHandling(
        Qgis.LabelOverlapHandling.AllowOverlapIfRequired if lab["overlap"]
        else Qgis.LabelOverlapHandling.PreventOverlap)
    note = ""
    if not lab["duplicates"]:  # Duplicates Off: don't repeat the same text close by
        t = s.thinningSettings()
        if hasattr(t, "setMinimumDistanceToDuplicates"):
            t.setMinimumDistanceToDuplicates(20)
            t.setMinimumDistanceToDuplicatesUnit(Qgis.RenderUnit.Millimeters)
        else:
            note = "duplicate-removal not supported by this QGIS"
    if lab["callout"] in ("Arrow", "Simple"):
        c = QgsSimpleLineCallout()
        c.setEnabled(True)
        s.setCallout(c)
        s.callout().setEnabled(True)
    if lab["zoom"]:
        s.scaleVisibility = True
        s.minimumScale = lab["zoom"][1] / ZOOM_WINDOW_M
        s.maximumScale = lab["zoom"][0] / ZOOM_WINDOW_M
    return QgsVectorLayerSimpleLabeling(s), note


def find_definition(ws, layer):
    """Workspace layer definition for a QGIS layer: by alias, else by table file name.
    Also tries the layer name without a leading 'T_' (PostGIS copies of .tab tables)."""
    names = [layer.name().upper()]
    plain = re.sub(r' [(][A-Za-z0-9]+[)]$', '', layer.name()).upper()  # suffix added by arrange()
    if plain != names[0]:
        names.append(plain)
    if names[0].startswith("T_"):
        names.append(names[0][2:])
    src = layer.source().split("|")[0]
    if os.path.exists(src):
        names.append(os.path.splitext(os.path.basename(src))[0].upper())
    for n in names:
        for key in (n, ws["files"].get(n)):
            if key in ws["layers"]:
                return ws["layers"][key]
    return None


def arrange(ws, rename=True, reorder=True):
    """Name layers after their workspace alias and order the layer tree like the workspace's Map From
    (first listed = drawn on top). A table that QGIS split by geometry type keeps its alias plus
    ' (point|line|polygon)', points above lines above polygons. Layers not in the workspace go last."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    report = []
    rank = {n.upper(): i for i, n in enumerate(ws["names"])}
    items = []
    for pos, node in enumerate(root.findLayers()):
        layer = node.layer()
        d = find_definition(ws, layer) if isinstance(layer, QgsVectorLayer) else None
        gk = int(layer.geometryType()) if d else 0
        items.append(((rank[d["alias"].upper()] if d else len(rank), gk, pos), node, layer, d))
    items.sort(key=lambda t: t[0])

    if rename:
        uses = {}
        for _, _, _, d in items:
            if d:
                uses[d["alias"]] = uses.get(d["alias"], 0) + 1
        for _, node, layer, d in items:
            if not d:
                continue
            new = d["alias"]
            if uses[d["alias"]] > 1:
                new += " (" + QgsWkbTypes.displayString(layer.wkbType()) + ")"
            if layer.name() != new:
                report.append(f"NAME  {layer.name()} -> {new}")
                layer.setName(new)

    if reorder:
        if any(n.parent() is not root for _, n, _, _ in items):
            report.append("WARN  layers are inside groups: order not changed")
        else:
            for i, (_, node, layer, _) in enumerate(items):
                if root.findLayers()[i].layerId() != layer.id():
                    root.insertChildNode(i, node.clone())
                    root.removeChildNode(node)
            report.append("ORDER layer tree reordered to the workspace's Map From order")

    # MapInfo "Display Off" = the layer is switched off in the map; Graphic/Global = shown.
    hidden = []
    for _, _, layer, d in items:
        if d:
            node = root.findLayer(layer.id())
            node.setItemVisibilityChecked(d["display"] != "Off")
            if d["display"] == "Off":
                hidden.append(layer.name())
    matched = sum(1 for it in items if it[3])
    report.append(f"VISIBLE {matched - len(hidden)} layers on, {len(hidden)} off (Display Off in the workspace)")
    return report


# ---- apply -----------------------------------------------------------------------------------------

def apply(wor_path, set_view=True, set_crs=True, log_path=None, arrange_layers=False):
    """Apply the workspace. log_path: optional file that gets one flushed line per step (for diagnosing hangs)."""
    from qgis.utils import iface

    def log(msg):
        if log_path:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(msg + "\n")

    with open(wor_path, encoding="latin-1") as fh:
        ws = parse(fh.read())
    project = QgsProject.instance()
    report, done = [], 0
    first_crs = None

    for node in project.layerTreeRoot().findLayers():
        layer = node.layer()
        if not isinstance(layer, QgsVectorLayer):
            continue
        d = find_definition(ws, layer)
        if d is None:
            report.append(f"SKIP  {layer.name()}: not in workspace")
            continue
        geom = {0: "point", 1: "line", 2: "polygon"}.get(int(layer.geometryType()))
        sym = make_symbol(geom, d) if geom else None
        if sym is None:
            report.append(f"SKIP  {layer.name()} ({layer.wkbType()}): no usable geometry type / symbol")
            continue
        if first_crs is None and layer.crs().isValid():
            first_crs = layer.crs()
        log(f"start {layer.name()}")

        # Single symbols give real legend swatches (the embedded renderer shows a generic icon).
        # Display Global: the workspace Global style (object styles are ignored, as in MapInfo).
        # Display Graphic/Off: MapInfo draws each object's own style, so use the style stored in the
        # table when one dominates; otherwise keep the embedded per-object styles (Global as fallback).
        if d["display"] == "Global" and layer.name().upper() not in OBJECT_STYLE_GLOBAL_LAYERS:
            layer.setRenderer(QgsSingleSymbolRenderer(sym))
            note_style = "global style"
        else:
            obj, why = sample_object_style(layer, geom)
            if obj is not None:
                layer.setRenderer(QgsSingleSymbolRenderer(obj))
                note_style = why
            elif why.startswith("object styles differ"):
                layer.setRenderer(QgsEmbeddedSymbolRenderer(sym))
                note_style = "embedded per-object styles; " + why
            elif why == "objects have no style":  # MapInfo draws such objects with its default style
                dflt = dict(d, pen=[1, 2, 0], line=[1, 2, 0], brush=[1, 16777215, 16777215], symbol=[35, 0, 12])
                layer.setRenderer(QgsSingleSymbolRenderer(make_symbol(geom, dflt)))
                note_style = "MapInfo default style (objects have no style)"
            else:
                layer.setRenderer(QgsSingleSymbolRenderer(sym))
                note_style = "global style; " + why

        if d["zoom"]:
            layer.setScaleBasedVisibility(True)
            layer.setMinimumScale(d["zoom"][1] / ZOOM_WINDOW_M)
            layer.setMaximumScale(d["zoom"][0] / ZOOM_WINDOW_M)
        else:
            layer.setScaleBasedVisibility(False)

        lab, note = (make_labeling(layer, geom, d["label"]) if d["label"] else (None, "no label settings"))
        if lab and d["label"]["visible"]:
            layer.setLabeling(lab)
            layer.setLabelsEnabled(True)
        else:
            layer.setLabelsEnabled(False)
            if lab is None and note:
                report.append(f"WARN  {layer.name()}: labels not applied ({note})")
        if lab and note:
            report.append(f"NOTE  {layer.name()}: {note}")
        log(f"styled {layer.name()}")
        done += 1
        report.append(f"OK    {layer.name():28s} {geom:8s} display={d['display']:7s} style={note_style} | "
                      f"zoom={d['zoom']} label={d['label']['field'] if d['label'] else None}")

    # Layers without a CRS (e.g. PostGIS "srid=0") hold workspace coordinates: give them the same
    # CRS as the workspace's file-based layers.
    cs = ws["coordsys"]
    if first_crs and first_crs.isValid():
        for node in project.layerTreeRoot().findLayers():
            lyr = node.layer()
            if isinstance(lyr, QgsVectorLayer) and not lyr.crs().isValid() and find_definition(ws, lyr):
                lyr.setCrs(first_crs)
                report.append(f"CRS   {lyr.name()}: had no CRS, set to the workspace Cassini CRS")

    # CRS: workspace is Cassini-Soldner (proj 30) with these origin parameters
    if set_crs and first_crs and cs:
        proj = first_crs.toProj()
        ok = ("+proj=cass" in proj and abs(float(re.search(r"\+lon_0=([\d.\-]+)", proj).group(1)) - cs[2]) < 1e-6
              and abs(float(re.search(r"\+lat_0=([\d.\-]+)", proj).group(1)) - cs[3]) < 1e-6)
        if ok:
            if project.crs() != first_crs:
                project.setCrs(first_crs)
                report.append("CRS   project CRS set to the layers' Cassini CRS")
            else:
                report.append("CRS   project CRS already matches workspace (Cassini, lon_0/lat_0 equal)")
        else:
            report.append(f"WARN  CRS mismatch: workspace {cs} vs layers {proj}")

    if set_view and ws["view"]:
        cx, cy, w = ws["view"]
        canvas = iface.mapCanvas()
        h = w * canvas.height() / max(canvas.width(), 1)
        canvas.setExtent(QgsRectangle(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
        canvas.refresh()
        report.append(f"VIEW  centre ({cx}, {cy}) width {w} m (scale ~1:{canvas.scale():,.0f})")

    matched = {find_definition(ws, n.layer())["alias"] for n in project.layerTreeRoot().findLayers()
               if isinstance(n.layer(), QgsVectorLayer) and find_definition(ws, n.layer())}
    for alias in ws["names"]:
        if alias not in matched:
            report.append(f"NOTE  workspace layer {alias}: no matching vector layer in the project")
    if arrange_layers:  # after matching: renaming changes what find_definition sees
        report.extend(arrange(ws))
    for node in project.layerTreeRoot().findLayers():  # one repaint pass at the end
        if isinstance(node.layer(), QgsVectorLayer):
            node.layer().triggerRepaint()
    log("done")
    report.append(f"DONE  {done} layers styled")
    print("\n".join(report))
    return done


def apply_scales(wor_path):
    """Set only the scale-dependent visibility (layer Zoom and label Visibility Zoom) from the workspace.
    Symbology, labels and CRS are left as they are."""
    with open(wor_path, encoding="latin-1") as fh:
        ws = parse(fh.read())
    project = QgsProject.instance()
    report = []
    for node in project.layerTreeRoot().findLayers():
        layer = node.layer()
        if not isinstance(layer, QgsVectorLayer):
            continue
        d = find_definition(ws, layer)
        if d is None:
            report.append(f"SKIP  {layer.name()}: not in workspace")
            continue
        if d["zoom"]:
            layer.setMinimumScale(d["zoom"][1] / ZOOM_WINDOW_M)
            layer.setMaximumScale(d["zoom"][0] / ZOOM_WINDOW_M)
            layer.setScaleBasedVisibility(True)
            lz = f"layer 1:{d['zoom'][1] / ZOOM_WINDOW_M:,.0f} .. 1:{d['zoom'][0] / ZOOM_WINDOW_M:,.0f}"
        else:
            layer.setScaleBasedVisibility(False)
            layer.setMinimumScale(0)
            layer.setMaximumScale(0)
            lz = "layer always visible"
        lab = layer.labeling()
        lbz = "no labels"
        if lab is not None and hasattr(lab, "settings"):
            s = lab.settings()
            lz2 = d["label"]["zoom"] if d["label"] else None
            if lz2:
                s.scaleVisibility = True
                s.minimumScale = lz2[1] / ZOOM_WINDOW_M
                s.maximumScale = lz2[0] / ZOOM_WINDOW_M
                lbz = f"labels 1:{s.minimumScale:,.0f} .. 1:{s.maximumScale:,.0f}"
            else:
                s.scaleVisibility = False
                lbz = "labels follow layer"
            lab.setSettings(s)
        layer.triggerRepaint()
        report.append(f"OK    {layer.name():16s} {lz:36s} {lbz}")
    print("\n".join(report))
