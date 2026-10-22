"""Create an independent QGIS project from declarative WOR settings."""
from dataclasses import dataclass
from pathlib import Path
import json
import math
import os
import re
import tempfile

from osgeo import osr
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsDataSourceUri, QgsEmbeddedSymbolRenderer,
    QgsFeatureRequest, QgsMapInfoSymbolConversionContext, QgsMapInfoSymbolConverter,
    QgsProject, QgsProviderRegistry, QgsRasterLayer, QgsRectangle,
    QgsReferencedRectangle, QgsSingleSymbolRenderer, QgsSymbol, QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor, QFontInfo, QFont
from . import mapinfo_workspace_style as legacy
from .wor_model import read_workspace


@dataclass
class SourceSpec:
    kind: str = "tab"
    path: str = ""
    host: str = ""
    port: str = ""
    database: str = ""
    username: str = ""
    authcfg: str = ""
    schema: str = ""
    table: str = ""
    geometry: str = "GEOLOC"
    key: str = ""
    source_crs: str = ""
    integrated: bool = False
    allow_graphic_fallback: bool = False


def workspace_crs(definition):
    osr.UseExceptions()
    try:
        sr = osr.SpatialReference()
        result = sr.ImportFromMICoordSys(definition)
        if result != 0:
            raise ValueError("GDAL rejected coordinate system")
        crs = QgsCoordinateReferenceSystem()
        if not crs.createFromWkt(sr.ExportToWkt()) or not crs.isValid():
            raise ValueError("QGIS rejected coordinate system")
        return crs
    except Exception as e:
        raise ValueError("Cannot translate the complete WOR CoordSys. No fallback CRS has been assigned.") from e


def _load_layer(alias, source):
    if source.kind == "xyz":
        from urllib.parse import urlsplit, urlencode
        url = urlsplit(source.path)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            raise ValueError(f"{alias}: XYZ requires an HTTP(S) URL without embedded credentials.")
        if url.query:
            raise ValueError(f"{alias}: XYZ URLs with query tokens are not supported; use a public tile template without secrets.")
        if not all(token in source.path for token in ("{x}", "{y}", "{z}")):
            raise ValueError(f"{alias}: XYZ URL must contain {{z}}, {{x}} and {{y}} placeholders.")
        layer = QgsRasterLayer(urlencode({"type": "xyz", "url": source.path, "zmin": "0", "zmax": "19"}), alias, "wms")
        if not layer.isValid():
            raise ValueError(f"{alias}: QGIS could not create the XYZ tile source.")
    elif source.kind == "tab":
        path = Path(source.path)
        if path.suffix.lower() != ".tab" or not path.is_file():
            raise ValueError(f"{alias}: choose an existing .TAB file.")
        # DB-linked TABs must not be implicitly executed as local vector data.
        header = path.read_bytes()[:131072].decode("latin1")
        if re.search(r"DATALINK\\|\bType\s+(?:LINKED|ODBC|ORACLE)\b", header, re.I):
            raise ValueError(f"{alias}: database-linked TAB. Select SQL Server or Oracle explicitly.")
        layer = QgsVectorLayer(str(path), alias, "ogr")
        if not layer.isValid():
            layer = QgsRasterLayer(str(path), alias, "gdal")
        if not layer.isValid():
            raise ValueError(f"{alias}: TAB cannot be opened. Check companion files; tiled/web-service TABs may require a native QGIS source.")
    elif source.kind in {"mssql", "oracle"}:
        if not QgsProviderRegistry.instance().providerMetadata(source.kind):
            raise ValueError(f"{alias}: {source.kind} provider is not available in this QGIS installation.")
        if not all((source.host, source.database, source.schema, source.table, source.geometry)):
            raise ValueError(f"{alias}: database host/service, database, schema, table and geometry are required.")
        if not source.authcfg and not (source.kind == "mssql" and source.integrated):
            raise ValueError(f"{alias}: use a QGIS authentication configuration, or SQL Server Windows integrated authentication.")
        uri = QgsDataSourceUri()
        uri.setConnection(source.host, source.port, source.database,
                          "" if source.integrated else source.username, "", QgsDataSourceUri.SslPrefer, source.authcfg)
        uri.setDataSource(source.schema, source.table, source.geometry, "", source.key)
        layer = QgsVectorLayer(uri.uri(False), alias, source.kind)
        if not layer.isValid():
            raise ValueError(f"{alias}: database layer cannot be opened. Check connection/authentication, client drivers and table/geometry/key settings in QGIS.")
        if source.key and layer.fields().indexFromName(source.key) < 0:
            raise ValueError(f"{alias}: selected key column is not present in the database layer.")
    else:
        raise ValueError(f"{alias}: unsupported source type; choose TAB, SQL Server or Oracle.")
    if source.source_crs:
        override = QgsCoordinateReferenceSystem(source.source_crs)
        if not override.isValid():
            raise ValueError(f"{alias}: invalid source CRS override.")
        layer.setCrs(override)
    if not layer.crs().isValid():
        raise ValueError(f"{alias}: source CRS is unknown. Assign its actual CRS explicitly; do not assume it equals the project CRS.")
    if isinstance(layer, QgsVectorLayer):
        if layer.geometryType() not in (Qgis.GeometryType.Point, Qgis.GeometryType.Line, Qgis.GeometryType.Polygon):
            raise ValueError(f"{alias}: non-spatial or mixed/unknown geometry TAB requires separate geometry-specific layers first.")
        # Refuse mixed geometry rather than silently dropping other feature types.
        if source.kind == "tab":
            from osgeo import ogr
            ds = ogr.Open(str(source.path), 0)
            if not ds:
                raise ValueError(f"{alias}: GDAL cannot validate TAB geometry.")
            ogr_layer = ds.GetLayer(0)
            advertised = ogr.GT_Flatten(ogr_layer.GetGeomType())
            if advertised in (ogr.wkbUnknown, ogr.wkbGeometryCollection):
                # MapInfo often advertises Unknown even when every object is a polygon.
                families = set()
                for feature in ogr_layer:
                    geometry = feature.GetGeometryRef()
                    if geometry is None or geometry.IsEmpty():
                        continue
                    kind = ogr.GT_Flatten(geometry.GetGeometryType())
                    families.add({ogr.wkbMultiPoint: ogr.wkbPoint,
                                  ogr.wkbMultiLineString: ogr.wkbLineString,
                                  ogr.wkbMultiPolygon: ogr.wkbPolygon}.get(kind, kind))
                    if len(families) > 1 or kind == ogr.wkbGeometryCollection:
                        raise ValueError(f"{alias}: mixed TAB geometry requires splitting before conversion.")
            ds = None
    return layer


def _native_symbol(geom, style, warnings):
    context = QgsMapInfoSymbolConversionContext()
    cv = QgsMapInfoSymbolConverter
    def color(n):
        n = int(n)
        if not 0 <= n <= 0xffffff:
            raise ValueError("MapInfo color is outside the 24-bit RGB range.")
        return QColor(f"#{n:06x}")
    def line(values):
        width, pattern, rgb = values
        if width <= 0 or 7 < width < 11:
            raise ValueError("Unsupported MapInfo pen width.")
        unit = Qgis.RenderUnit.Pixels if width <= 7 else Qgis.RenderUnit.Points
        width = width if width <= 7 else (width - 10) / 10
        return cv.convertLineSymbol(int(pattern), context, color(rgb), width, unit)
    if geom == "point":
        shape, rgb, size = style["symbol"]
        symbol = cv.convertMarkerSymbol(int(shape), context, color(rgb), size, Qgis.RenderUnit.Points)
    elif geom == "line":
        symbol = line(style["line"])
    else:
        pattern, fg, bg = style["brush"]
        symbol = cv.convertFillSymbol(int(pattern), context, color(fg), color(bg))
        outline = line(style["pen"])
        if symbol is None or outline is None:
            raise ValueError("MapInfo fill/outline could not be translated by QGIS.")
        # Fill converter supplies its own outline; the WOR Pen clause owns the boundary.
        for i in range(symbol.symbolLayerCount()):
            sl = symbol.symbolLayer(i)
            if hasattr(sl, "setStrokeStyle"):
                from qgis.PyQt.QtCore import Qt
                sl.setStrokeStyle(Qt.NoPen)
        for i in range(outline.symbolLayerCount()):
            symbol.appendSymbolLayer(outline.symbolLayer(i).clone())
    warnings.extend(context.warnings())
    if geom == "polygon" and style["brush"][0] == 15:
        warnings.append("MapInfo brush 15 uses QGIS native MapInfo translation; visual pattern equivalence has not been verified.")
    if (style["pen"] if geom == "polygon" else style["line"])[1] == 20:
        warnings.append("MapInfo pen 20 uses QGIS native MapInfo translation; visual line-pattern equivalence has not been verified.")
    if symbol is None:
        raise ValueError("MapInfo symbol could not be translated by QGIS.")
    return symbol


def _label_supported(expression):
    # Deliberately small allowlist; never silently reinterpret numeric/arithmetic expressions.
    token = r'(?:[A-Za-z_][A-Za-z_0-9]*|"(?:[^"]|"")*"|Chr\$\(\d+\))'
    return bool(re.fullmatch(rf"\s*{token}(?:\s*\+\s*{token})*\s*", expression, re.I))


def _style_layer(layer, spec, source, width_m, warnings):
    warnings.extend(spec.warnings)
    if not isinstance(layer, QgsVectorLayer):
        warnings.append("Raster source loaded; MapInfo vector style/label clauses are not applicable.")
        return
    geom = {Qgis.GeometryType.Point: "point", Qgis.GeometryType.Line: "line",
            Qgis.GeometryType.Polygon: "polygon"}[layer.geometryType()]
    if spec.display == "Graphic":
        if source.kind != "tab":
            if not source.allow_graphic_fallback:
                raise ValueError(f"{spec.alias}: Display Graphic needs TAB per-object styles. Select a TAB or explicitly allow a reported Global-style fallback for this database source.")
            layer.setRenderer(QgsSingleSymbolRenderer(_native_symbol(geom, spec.style, warnings)))
            warnings.append("USER-ACCEPTED FALLBACK: database has no MapInfo per-object styles; workspace Global style used instead. Original symbology is not preserved.")
        else:
            request = QgsFeatureRequest().setFlags(Qgis.FeatureRequestFlag.EmbeddedSymbols).setLimit(1)
            feature = next(layer.getFeatures(request), None)
            if feature is not None and feature.embeddedSymbol() is None:
                raise ValueError(f"{spec.alias}: provider does not supply embedded TAB object styles.")
            layer.setRenderer(QgsEmbeddedSymbolRenderer(QgsSymbol.defaultSymbol(layer.geometryType())))
            warnings.append("Per-object TAB symbols retained with QGIS embedded renderer; MapInfo text objects/custom fonts may differ.")
    else:
        layer.setRenderer(QgsSingleSymbolRenderer(_native_symbol(geom, spec.style, warnings)))
        if spec.display == "Off":
            warnings.append("Hidden layer: its Global style is retained; WOR does not identify its previous active style mode.")
    if spec.zoom:
        if not width_m:
            raise ValueError(f"{spec.alias}: scale-dependent visibility requires map window dimensions.")
        low, high = spec.zoom
        layer.setScaleBasedVisibility(True)
        layer.setMinimumScale(high / width_m)
        layer.setMaximumScale(low / width_m)
    if spec.label:
        lab = dict(spec.label)
        if not _label_supported(lab["field"]):
            warnings.append("Unsupported label expression; labeling disabled.")
            layer.setLabelsEnabled(False)
            return
        zoom = lab["zoom"]
        lab["zoom"] = None  # legacy uses a fixed physical window width: set correctly below
        labeling, note = legacy.make_labeling(layer, geom, lab)
        if labeling:
            settings = labeling.settings()
            if zoom:
                if not width_m:
                    raise ValueError(f"{spec.alias}: label zoom requires map window dimensions.")
                settings.scaleVisibility = True
                settings.minimumScale = zoom[1] / width_m
                settings.maximumScale = zoom[0] / width_m
                labeling.setSettings(settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(lab["auto"] and lab["visible"])
            if lab["auto"] and lab["visible"]:
                warnings.append("QGIS automatic label placement is an approximation of MapInfo; manual labels are not transferred.")
            if QFontInfo(QFont(lab["family"])).family().casefold() != lab["family"].casefold():
                warnings.append(f"Font {lab['family']} was substituted by the installed Qt font system.")
            if lab["callout"] == "Arrow":
                warnings.append("Arrow callout uses a simple QGIS line without MapInfo arrowhead geometry.")
            if not lab["duplicates"]:
                warnings.append("Duplicate-label suppression uses a proximity approximation, not MapInfo's exact rule.")
        else:
            layer.setLabelsEnabled(False)
        if note:
            warnings.append(note)


def build_project(workspace, sources):
    """Validate all choices and build a fresh project. May connect to selected sources."""
    if isinstance(workspace, (str, Path)):
        workspace = read_workspace(workspace)
    if set(sources) != {s.alias for s in workspace.layers}:
        raise ValueError("Choose and confirm a source for every workspace layer.")
    crs = workspace_crs(workspace.coordsys)
    project = QgsProject()
    project.setCrs(crs)
    project.setFilePathStorage(Qgis.FilePathType.Absolute)
    project.setTitle(workspace.path.stem)
    report = {"workspace": str(workspace.path), "warnings": list(workspace.warnings), "layers": []}
    order = []
    for spec in workspace.layers:
        source = sources[spec.alias]
        try:
            layer = _load_layer(spec.alias, source)
            notes = []
            _style_layer(layer, spec, source, workspace.window_width_m, notes)
        except ValueError:
            raise
        except Exception as e:
            # Provider exception text can contain a connection URI: never expose it.
            raise ValueError(f"{spec.alias}: source/style conversion failed ({type(e).__name__}). Check the source and QGIS installation.") from e
        project.addMapLayer(layer, False)
        node = project.layerTreeRoot().addLayer(layer)
        node.setItemVisibilityChecked(spec.display != "Off")
        order.append(layer)
        report["layers"].append({"alias": spec.alias, "provider": layer.providerType(),
                                 "visible": spec.display != "Off", "warnings": notes})
        report["warnings"].extend(f"{spec.alias}: {n}" for n in notes)
    project.layerTreeRoot().setCustomLayerOrder(order)
    project.layerTreeRoot().setHasCustomLayerOrder(True)
    if workspace.center and workspace.zoom_m and workspace.aspect:
        if crs.isGeographic():
            raise ValueError("Geographic workspace view widths require a geodesic calculation; export is blocked rather than guessing the extent.")
        from qgis.core import QgsUnitTypes
        factor = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
        if not factor or not math.isfinite(factor):
            raise ValueError("Cannot translate workspace extent units.")
        w = workspace.zoom_m / factor
        h = w / workspace.aspect
        x, y = workspace.center
        project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(QgsRectangle(x-w/2, y-h/2, x+w/2, y+h/2), crs))
    else:
        report["warnings"].append("Incomplete workspace center/zoom/window dimensions: no initial extent was assigned.")
    report["warnings"].append("Visual equivalence is not guaranteed: MapInfo and QGIS rendering/label engines differ. Review the resulting map.")
    return project, report


def convert_workspace(workspace, sources, output, overwrite=False):
    """Atomic project publication. Existing files require an explicit caller decision."""
    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != ".qgs":
        raise ValueError("Output must have a .qgs extension.")
    if output.exists() and not overwrite:
        raise ValueError("Output already exists. Choose another name or explicitly confirm replacement.")
    if not output.parent.is_dir():
        raise ValueError("Output directory does not exist; create/select an existing directory.")
    project, report = build_project(workspace, sources)
    report["output"] = str(output)
    fd, tempname = tempfile.mkstemp(suffix=".qgs", prefix=".wor-", dir=output.parent)
    os.close(fd)
    temp = Path(tempname)
    try:
        if not project.write(str(temp)):
            raise ValueError("QGIS could not write the staged project.")
        # Check the serialized project with a new project before publishing it.
        check = QgsProject()
        if not check.read(str(temp)) or len(check.mapLayers()) != len(sources):
            raise ValueError("Saved project failed reopen verification.")
        expected = [s.alias for s in (read_workspace(workspace).layers if isinstance(workspace, (str, Path)) else workspace.layers)]
        if [n.name() for n in check.layerTreeRoot().findLayers()] != expected:
            raise ValueError("Saved project failed layer-order verification.")
        if any(not layer.isValid() for layer in check.mapLayers().values()):
            raise ValueError("A saved data source failed reopen verification.")
        check.clear()
        # Providers must retain authcfg references, never expanded passwords in a QGS.
        from xml.etree import ElementTree
        xml = ElementTree.parse(temp)
        for node in xml.findall(".//datasource"):
            text = node.text or ""
            if re.search(r"\b(?:password|pwd)\s*=\s*(?:'[^']+'|\"[^\"]+\"|[^'\"\s][^\s]*)", text, re.I):
                raise ValueError("A provider expanded credentials into its source URI. Project was not saved; configure credential-safe authentication in QGIS.")
        if overwrite:
            os.replace(temp, output)
        elif os.name == "nt":
            os.rename(temp, output)  # Windows refuses replacement even if a target appeared meanwhile
        else:
            os.link(temp, output)
            temp.unlink()
        # Unique report names avoid replacing unrelated previous reports.
        try:
            fd, reportname = tempfile.mkstemp(prefix=output.stem + ".conversion-", suffix=".json", dir=output.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False)
            report["report"] = reportname
        except OSError:
            report["warnings"].append("Project saved, but the diagnostic report could not be written; retain the warnings shown in the app.")
        return report
    finally:
        project.clear()
        if temp.exists():
            temp.unlink()
