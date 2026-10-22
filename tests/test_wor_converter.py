"""Local-only PyQGIS integration tests; run using bin/python-qgis.bat."""
import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qgis.core import QgsApplication, QgsFeatureRequest, Qgis, QgsProject
from osgeo import ogr, osr
from qg.wor_model import read_workspace, parse_workspace
from qg.wor_converter import SourceSpec, build_project, convert_workspace, workspace_crs

ROOT = Path(__file__).resolve().parents[1]
COORDSYS = 'CoordSys Earth Projection 30, 33, "m", 101.9397026917, 2.7121205083, 0, 0'


class ConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory()
        cls.app = QgsApplication([], False, cls.profile.name)
        cls.app.initQgis()
        ogr.UseExceptions()
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name) / "lots.tab"
        sr = osr.SpatialReference()
        sr.ImportFromMICoordSys(COORDSYS)
        ds = ogr.GetDriverByName("MapInfo File").CreateDataSource(str(cls.path))
        layer = ds.CreateLayer("lots", sr, ogr.wkbPolygon)
        for name in ("NOPETA", "NEGERI", "LOT", "KETERANGAN"):
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
        for i in range(20):
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetField("NOPETA", f"P{i}")
            feature.SetGeometry(ogr.CreateGeometryFromWkt(f"POLYGON(({i} 0,{i+1} 0,{i+1} 1,{i} 0))"))
            feature.SetStyleString('BRUSH(fc:#ff0000,id:"mapinfo-brush-2");PEN(c:#000000,w:1px,id:"mapinfo-pen-2")' if i < 19 else 'BRUSH(fc:#0000ff,id:"mapinfo-brush-2");PEN(c:#000000,w:1px,id:"mapinfo-pen-2")')
            layer.CreateFeature(feature)
        feature = layer = ds = None

    @classmethod
    def tearDownClass(cls):
        gc.collect()
        cls.folder.cleanup()
        cls.app.exitQgis()
        cls.profile.cleanup()

    def sample(self):
        return read_workspace(ROOT / "examples" / "IndexMap.wor")

    def sources(self, ws):
        return {s.alias: SourceSpec(path=str(self.path)) for s in ws.layers}

    def test_crs_full_definition(self):
        crs = workspace_crs(COORDSYS)
        self.assertTrue(crs.isValid())
        self.assertIn("Cassini", crs.toWkt())
        self.assertIn("101.9397026917", crs.toWkt())
        self.assertIn("298.257222101", crs.toWkt())

    def test_order_visibility_labels_scales_and_embedded(self):
        ws = self.sample()
        project, report = build_project(ws, self.sources(ws))
        nodes = project.layerTreeRoot().findLayers()
        self.assertEqual([n.name() for n in nodes], [s.alias for s in ws.layers])
        self.assertEqual([n.itemVisibilityChecked() for n in nodes], [s.display != "Off" for s in ws.layers])
        self.assertEqual([n.layer().labelsEnabled() for n in nodes], [False, False, False, True] + [False]*7)
        graphic = nodes[3].layer()
        self.assertEqual(graphic.renderer().type(), "embeddedSymbol")
        features = list(graphic.getFeatures(QgsFeatureRequest().setFlags(Qgis.FeatureRequestFlag.EmbeddedSymbols)))
        self.assertEqual(len(features), 20)
        self.assertNotEqual(features[0].embeddedSymbol().color().name(), features[-1].embeddedSymbol().color().name())
        lot = nodes[4].layer()
        self.assertTrue(lot.hasScaleBasedVisibility())
        self.assertAlmostEqual(lot.minimumScale(), 10000 / ws.window_width_m)
        extent = project.viewSettings().defaultViewExtent()
        self.assertAlmostEqual(extent.center().x(), ws.center[0])
        self.assertAlmostEqual(extent.width(), ws.zoom_m)
        self.assertTrue(report["warnings"])
        project.clear()

    def test_generic_workspaces_use_their_own_names_crs_order_and_view(self):
        from test_wor_model import small_workspace
        definitions = (
            ('CoordSys Earth Projection 8, 104, "m", 15, 0, 0.9996, 500000, 0', "UTM survey"),
            ('CoordSys Earth Projection 10, 104, "m", 0', "Mercator assets"),
        )
        for coordsys, name in definitions:
            with self.subTest(workspace=name):
                path = self.path.parent / f"{name}.wor"
                ws = parse_workspace(small_workspace(
                    tables='Open Table "first" As Parcels\nOpen Table "second" As Assets',
                    aliases="Assets,Parcels", coordsys=coordsys,
                    view='  Center (500000,1200000)\n  Zoom 2 Units "km"',
                    body=('    Display Off\n  Layer 2\n    Display Global\n'
                          '    Global Pen (2,2,255) Brush (2,65280,16777215)\n'
                          '    Label Line None Position Center Font ("Arial",0,9,0)\n'
                          '      With NOPETA\n      Auto On Visibility On'),
                ), path)
                output = path.with_suffix(".qgs")
                report = convert_workspace(ws, self.sources(ws), output)
                project = QgsProject()
                try:
                    self.assertTrue(project.read(str(output)))
                    self.assertEqual(project.title(), name)
                    self.assertEqual(project.crs(), workspace_crs(coordsys))
                    nodes = project.layerTreeRoot().findLayers()
                    self.assertEqual([n.name() for n in nodes], ["Assets", "Parcels"])
                    self.assertEqual([n.itemVisibilityChecked() for n in nodes], [False, True])
                    self.assertEqual([n.layer().labelsEnabled() for n in nodes], [False, True])
                    self.assertAlmostEqual(project.viewSettings().defaultViewExtent().width(), 2000)
                    self.assertEqual([layer["alias"] for layer in report["layers"]], ["Assets", "Parcels"])
                finally:
                    project.clear()

    def test_native_symbols_and_labels_render(self):
        from qgis.core import QgsMapSettings, QgsMapRendererSequentialJob, QgsRectangle
        from qgis.PyQt.QtCore import QSize
        ws = self.sample()
        project, _ = build_project(ws, self.sources(ws))
        settings = QgsMapSettings()
        settings.setDestinationCrs(project.crs())
        settings.setLayers([n.layer() for n in project.layerTreeRoot().findLayers() if n.itemVisibilityChecked()])
        settings.setExtent(QgsRectangle(-1, -1, 21, 2))
        settings.setOutputSize(QSize(800, 300))
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        self.assertFalse(job.renderedImage().isNull())
        self.assertEqual(job.errors(), [])
        self.assertTrue(job.renderedImage().save(str(self.path.parent / "render-smoke.png")))
        project.clear()

    def test_save_reopen_no_overwrite(self):
        ws = self.sample()
        output = Path(self.folder.name) / "converted.qgs"
        report = convert_workspace(ws, self.sources(ws), output)
        self.assertTrue(output.is_file())
        self.assertTrue(Path(report["report"]).is_file())
        check = QgsProject()
        self.assertTrue(check.read(str(output)))
        self.assertEqual(len(check.mapLayers()), 11)
        self.assertEqual(check.crs(), workspace_crs(ws.coordsys))
        check.clear()
        original = output.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            convert_workspace(ws, self.sources(ws), output)
        self.assertEqual(original, output.read_bytes())
        self.assertNotIn("password=", original.decode("utf-8"))

    def test_invalid_source_does_not_write(self):
        ws = self.sample()
        sources = self.sources(ws)
        sources[ws.layers[0].alias] = SourceSpec(path=str(self.path.parent / "missing.tab"))
        output = self.path.parent / "invalid.qgs"
        with self.assertRaisesRegex(ValueError, "existing .TAB"):
            convert_workspace(ws, sources, output)
        self.assertFalse(output.exists())

    def test_graphic_database_is_not_silently_flattened(self):
        from qg.wor_converter import _style_layer
        ws = self.sample()
        from qgis.core import QgsVectorLayer
        layer = QgsVectorLayer(str(self.path), "test", "ogr")
        with self.assertRaisesRegex(ValueError, "per-object styles"):
            _style_layer(layer, ws.layers[3], SourceSpec(kind="mssql"), ws.window_width_m, [])

    def test_explicit_graphic_database_fallback_is_reported(self):
        from qg.wor_converter import _style_layer
        from qgis.core import QgsVectorLayer
        ws = self.sample()
        layer = QgsVectorLayer(str(self.path), "test", "ogr")
        warnings = []
        _style_layer(layer, ws.layers[3], SourceSpec(kind="mssql", allow_graphic_fallback=True), ws.window_width_m, warnings)
        self.assertEqual(layer.renderer().type(), "singleSymbol")
        self.assertTrue(any("USER-ACCEPTED FALLBACK" in n for n in warnings))

    def test_mixed_geometry_is_rejected(self):
        from qg.wor_converter import _load_layer
        path = self.path.parent / "mixed.tab"
        ds = ogr.GetDriverByName("MapInfo File").CreateDataSource(str(path))
        layer = ds.CreateLayer("mixed", None, ogr.wkbUnknown)
        for wkt in ("POINT(1 1)", "LINESTRING(0 0,1 1)"):
            f = ogr.Feature(layer.GetLayerDefn())
            f.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
            layer.CreateFeature(f)
        f = layer = ds = None
        with self.assertRaisesRegex(ValueError, "mixed"):
            _load_layer("mixed", SourceSpec(path=str(path), source_crs="EPSG:3857"))

    def test_linked_tab_requires_explicit_database_source(self):
        from qg.wor_converter import _load_layer
        path = self.path.parent / "linked.tab"
        path.write_text('!table\nmetadata\n"DATALINK\\\\ConnectionString" = "not-a-real-server"\n', encoding="ascii")
        with self.assertRaisesRegex(ValueError, "database-linked"):
            _load_layer("linked", SourceSpec(path=str(path)))

    def test_database_choices_require_credentials_without_connecting(self):
        from qg.wor_converter import _load_layer
        source = SourceSpec(kind="mssql", host="never-connect.invalid", database="not-real",
                            schema="dbo", table="lots", geometry="geom", key="id")
        with self.assertRaisesRegex(ValueError, "authentication|provider"):
            _load_layer("db", source)

    def test_database_key_is_optional_and_explicit_keys_are_checked(self):
        from unittest.mock import patch
        from qgis.core import QgsDataSourceUri, QgsVectorLayer
        from qg.wor_converter import _load_layer
        captured = []

        class LocalDatabaseLayer(QgsVectorLayer):
            def __init__(self, uri, alias, provider):
                captured.append((QgsDataSourceUri(uri).keyColumn(), provider))
                super().__init__("Point?crs=EPSG:3857&field=id:integer", alias, "memory")

        # Substitute local memory layers: never contact a database during these tests.
        with patch("qg.wor_converter.QgsVectorLayer", LocalDatabaseLayer), \
             patch("qg.wor_converter.QgsProviderRegistry"):
            for provider in ("oracle", "mssql"):
                for key in ("", "id", "missing"):
                    with self.subTest(provider=provider, key=key):
                        source = SourceSpec(kind=provider, host="never-connect.invalid", database="test",
                                            schema="owner", table="lots", geometry="geom", authcfg="abcdefg", key=key)
                        if key == "missing":
                            with self.assertRaisesRegex(ValueError, "selected key column"):
                                _load_layer("lots", source)
                        else:
                            layer = _load_layer("lots", source)
                            self.assertTrue(layer.isValid())
                            del layer
                        self.assertEqual(captured[-1], (key, provider))

    def test_selected_source_crs_is_independent(self):
        ws = self.sample()
        sources = self.sources(ws)
        sources[ws.layers[0].alias].source_crs = "EPSG:3857"
        project, _ = build_project(ws, sources)
        self.assertEqual(project.layerTreeRoot().findLayers()[0].layer().crs().authid(), "EPSG:3857")
        self.assertNotEqual(project.crs().authid(), "EPSG:3857")
        project.clear()

    def test_unknown_choice_and_invalid_crs(self):
        ws = self.sample()
        with self.assertRaisesRegex(ValueError, "every workspace"):
            build_project(ws, {})
        with self.assertRaises(ValueError):
            workspace_crs("garbage")


if __name__ == "__main__":
    unittest.main(verbosity=2)
