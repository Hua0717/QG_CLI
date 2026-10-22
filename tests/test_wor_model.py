"""Standard-library regression tests for the declarative WOR reader.

Run with: python -m unittest discover -s tests -p test_wor_model.py -v
No QGIS installation or access to the workspace's table files is required.
"""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from qg.wor_model import parse_workspace, read_workspace, table_path


EXAMPLE = REPO_ROOT / "examples" / "IndexMap.wor"
MAP_ORDER = [
    "Indexmap_1ch", "Indexmap_2ch", "Indexmap_4ch", "Indexmap",
    "NDCDB_CCL_LOT", "REL_NDCDB_CCL_LOT", "NEW_PDUK_CCL_LOT",
    "C3_CCL_LOT", "NDCDB_LOT", "Admbdy_Daerah", "_OpenStreetMap",
]
CRS = 'CoordSys Earth Projection 30, 33, "m", 101.9397026917, 2.7121205083, 0, 0'


class IndexMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = read_workspace(EXAMPLE)

    def test_eleven_layers_follow_map_from_not_open_table_order(self):
        layers = self.workspace.layers
        self.assertEqual(len(layers), 11)
        self.assertEqual([layer.alias for layer in layers], MAP_ORDER)
        self.assertEqual(layers[0].path, r"Index Map\Indexmap_1ch")
        self.assertEqual(layers[3].path, r"Index Map\Indexmap_8ch")

    def test_exactly_four_layers_are_display_off(self):
        self.assertEqual(
            [layer.alias for layer in self.workspace.layers if layer.display == "Off"],
            ["Indexmap_1ch", "Indexmap_2ch", "Indexmap_4ch", "_OpenStreetMap"],
        )
        self.assertEqual(self.workspace.layers[3].display, "Graphic")
        self.assertTrue(all(layer.display == "Global" for layer in self.workspace.layers[4:10]))

    def test_five_cadastral_layers_have_zero_to_ten_thousand_metre_zoom_bounds(self):
        expected = {alias: (0.0, 10000.0) for alias in MAP_ORDER[4:9]}
        self.assertEqual(
            {layer.alias: layer.zoom for layer in self.workspace.layers if layer.zoom is not None},
            expected,
        )

    def test_only_indexmap_has_automatic_labels(self):
        self.assertEqual(
            [layer.alias for layer in self.workspace.layers if layer.label and layer.label["auto"]],
            ["Indexmap"],
        )
        label = self.workspace.layers[3].label
        self.assertEqual(label["field"], "NOPETA")
        self.assertEqual(label["family"], "Arial")
        self.assertEqual(label["size"], 9.0)
        self.assertEqual(label["position"], "Center")
        self.assertTrue(label["visible"])
        self.assertTrue(label["parallel"])
        self.assertFalse(label["overlap"])
        self.assertTrue(label["duplicates"])
        self.assertEqual(label["offset"], 2.0)
        self.assertIsNone(self.workspace.layers[-1].label)

    def test_full_crs_and_map_view_are_preserved(self):
        self.assertEqual(self.workspace.coordsys, CRS)
        self.assertEqual(self.workspace.center, (28661.12556, 13555.53492))
        self.assertEqual(self.workspace.zoom_m, 223101.4574)

    def test_saved_window_width_and_aspect_survive_maximized_window(self):
        self.assertAlmostEqual(self.workspace.window_width_m, 5.29167 * 0.0254)
        self.assertAlmostEqual(self.workspace.aspect, 5.29167 / 3.01042)
        self.assertTrue(any("maximized" in warning.lower() for warning in self.workspace.warnings))


def small_workspace(*, tables='Open Table "data" As data', aliases="data", body="", view="", coordsys=CRS):
    return (
        f"!Workspace\n!Version 650\n!Charset WindowsLatin1\n{tables}\n"
        f"Map From {aliases}\n"
        '  Width 5 Units "in" Height 3 Units "in"\n'
        f"Set Map\n  {coordsys}\n{view}\n"
        f"Set Map\n  Layer 1\n{body}\n"
    )


class WorkspaceParsingTests(unittest.TestCase):
    def parse(self, text):
        return parse_workspace(text, REPO_ROOT / "examples" / "synthetic.wor")

    def test_all_example_workspaces_have_dynamic_layer_counts(self):
        for name, count in (("IndexMap", 11), ("Stratum", 16), ("ePA", 32), ("ePUQT", 52)):
            with self.subTest(workspace=name):
                workspace = read_workspace(REPO_ROOT / "examples" / f"{name}.wor")
                self.assertEqual(len(workspace.layers), count)
                self.assertEqual(len({layer.alias for layer in workspace.layers}), count)
                if name != "ePUQT":
                    self.assertFalse(any("outside the supported" in w for w in workspace.warnings))

    def test_standard_set_map_newline_layer_is_not_an_unsupported_command(self):
        workspace = self.parse(small_workspace())
        self.assertFalse(any("outside the supported" in w for w in workspace.warnings))
        workspace = self.parse(small_workspace() + "\nSet Map Layer 0 Selectable\n")
        self.assertTrue(any("outside the supported" in w for w in workspace.warnings))

    def test_ignored_layer_and_label_modifiers_have_targeted_warnings(self):
        workspace = self.parse(small_workspace(body=(
            '    Display Global\n    Selectable Off\n    Arrows On\n'
            '    Label Line None Position Center Font ("Arial",0,9,0)\n'
            '      With title\n      Auto On PartialSegments On Pen (1,2,0)'
        )))
        warnings = "\n".join(workspace.layers[0].warnings)
        for setting in ("PartialSegments", "selectability", "line-arrow", "label Pen"):
            self.assertIn(setting, warnings)

    def test_windows_latin1_header_decodes_cp1252_non_ascii(self):
        text = small_workspace(
            tables='Open Table "données\\café €" As data',
            body='    Display Graphic\n'
                 '    Label Line None Position Center Font ("Frutiger €",0,9,0)\n'
                 '      With "café – €"\n'
                 '      Auto On Visibility On',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latin1.wor"
            path.write_bytes(text.encode("cp1252"))
            workspace = read_workspace(path)
        self.assertEqual(workspace.layers[0].path, "données\\café €")
        self.assertEqual(workspace.layers[0].label["family"], "Frutiger €")
        self.assertEqual(workspace.layers[0].label["field"], '"café – €"')

    def test_units_convert_scientific_notation_map_zoom(self):
        metres_per_unit = {
            "m": 1.0, "km": 1000.0, "ft": 0.3048, "mi": 1609.344,
            "in": 0.0254, "cm": 0.01, "mm": 0.001, "yd": 0.9144,
        }
        for unit, factor in metres_per_unit.items():
            with self.subTest(unit=unit):
                workspace = self.parse(small_workspace(view=f'  Zoom +1.25e2 Units "{unit.upper()}"'))
                self.assertAlmostEqual(workspace.zoom_m, 125.0 * factor)

    def test_scientific_notation_center_and_mixed_window_units(self):
        text = small_workspace(view='  Center (-1.2e+3,+.5E-2)\n  Zoom .25e1 Units "km"')
        text = text.replace(
            'Width 5 Units "in" Height 3 Units "in"',
            'Width +2.54e1 Units "cm" Height .5e1 Units "in"',
        )
        workspace = self.parse(text)
        self.assertEqual(workspace.center, (-1200.0, 0.005))
        self.assertEqual(workspace.zoom_m, 2500.0)
        self.assertAlmostEqual(workspace.window_width_m, 0.254)
        self.assertAlmostEqual(workspace.aspect, 2.0)

    def test_layer_zoom_units_and_scientific_notation(self):
        workspace = self.parse(small_workspace(
            body='    Display Global\n    Zoom (+.5e-1, 2.E+1) Units "km"',
        ))
        self.assertEqual(workspace.layers[0].zoom, (50.0, 20000.0))

    def test_zoom_off_disables_layer_bounds(self):
        workspace = self.parse(small_workspace(body='    Zoom (0, 1e1) Units "km" Off'))
        self.assertIsNone(workspace.layers[0].zoom)

    def test_rejects_map_alias_without_open_table(self):
        with self.assertRaisesRegex(ValueError, "no matching Open Table"):
            self.parse(small_workspace(aliases="missing"))

    def test_rejects_duplicate_open_table_alias_case_insensitively(self):
        tables = 'Open Table "first" As data\nOpen Table "second" As DATA'
        with self.assertRaisesRegex(ValueError, "Duplicate table alias"):
            self.parse(small_workspace(tables=tables))

    def test_rejects_duplicate_alias_in_map_from_case_insensitively(self):
        with self.assertRaisesRegex(ValueError, "Repeated aliases"):
            self.parse(small_workspace(aliases="data,DATA"))

    def test_rejects_multiple_map_windows(self):
        with self.assertRaisesRegex(ValueError, "Exactly one Map From"):
            self.parse(small_workspace() + "\nMap From data\nSet Map\n")

    def test_rejects_missing_map_window(self):
        with self.assertRaisesRegex(ValueError, "Exactly one Map From"):
            self.parse(small_workspace().replace("Map From data", ""))

    def test_rejects_missing_explicit_crs(self):
        with self.assertRaisesRegex(ValueError, "no explicit CoordSys"):
            self.parse(small_workspace(coordsys=""))


class TablePathTests(unittest.TestCase):
    def test_relative_extensionless_reference_resolves_against_workspace_not_cwd(self):
        workspace = REPO_ROOT / "examples" / "IndexMap.wor"
        expected = workspace.parent / "Index Map" / "Indexmap_8ch.tab"
        self.assertEqual(table_path(workspace, r"Index Map\Indexmap_8ch"), str(expected))

    def test_unc_extensionless_reference_stays_unc_and_adds_tab_without_io(self):
        reference = r"\\unavailable-server\share\Index Map\Indexmap_8ch"
        # Any filesystem probe could block indefinitely against an offline share.
        with patch("pathlib.Path.resolve", side_effect=AssertionError("must not resolve paths")), \
             patch("pathlib.Path.stat", side_effect=AssertionError("must not stat paths")), \
             patch("pathlib.Path.exists", side_effect=AssertionError("must not test existence")), \
             patch("pathlib.Path.open", side_effect=AssertionError("must not open tables")), \
             patch("builtins.open", side_effect=AssertionError("must not open tables")):
            actual = table_path(REPO_ROOT / "examples" / "IndexMap.wor", reference)
        self.assertEqual(actual, reference + ".tab")

    def test_explicit_tab_extension_is_not_duplicated(self):
        reference = r"\\unavailable-server\share\existing.TAB"
        self.assertEqual(table_path(EXAMPLE, reference), reference)


if __name__ == "__main__":
    unittest.main()
