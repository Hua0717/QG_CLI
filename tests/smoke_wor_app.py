"""Standalone UI smoke tests; run separately with QGIS bin/python-qgis.bat.

Not named test_*.py: owns a GUI QgsApplication, so cannot share a process with
integration suites that initialize/shut down their own non-GUI application.
No real data providers, network connections, or output projects are opened.
"""
import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from qgis.core import QgsApplication
from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QMessageBox
from qgis_mcp import wor_converter_app as ui


class StandaloneUiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QgsApplication.instance() is not None:
            raise RuntimeError("Run smoke_wor_app.py in its own QGIS Python process.")
        cls.profile = tempfile.TemporaryDirectory()
        cls.app = QgsApplication([], True, cls.profile.name)
        cls.app.initQgis()

    @classmethod
    def tearDownClass(cls):
        gc.collect()
        cls.app.exitQgis()
        sip.delete(cls.app)
        del cls.app
        cls.profile.cleanup()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.windows = []
        self.window = ui.main()
        self.windows.append(self.window)
        self.window.load_workspace(ROOT / "examples" / "IndexMap.wor")
        self.window.output_edit.setText(str(Path(self.folder.name) / "converted.qgs"))

    def tearDown(self):
        for window in reversed(self.windows):
            window.close()
            sip.delete(window)
        self.windows.clear()
        del self.window
        gc.collect()
        self.folder.cleanup()

    def dialog(self):
        alias = self.window.workspace.layers[0].alias
        dialog = ui.SourceDialog(alias, self.window.sources[alias], self.window.wor_path.parent, self.window)
        self.windows.append(dialog)
        return dialog

    def confirm_all(self):
        # Exercise the actual Configure/Confirm handler, but bypass modal execution.
        with patch.object(ui.SourceDialog, "exec", return_value=ui.QDialog.Accepted):
            for row in range(self.window.table.rowCount()):
                self.window.table.selectRow(row)
                self.window.configure_selected()

    def test_sources_must_each_be_confirmed_and_cancel_is_noop(self):
        self.assertEqual(len(self.window.workspace.layers), 11)
        self.assertFalse(self.window.confirmed)
        self.assertFalse(self.window.convert_button.isEnabled())
        self.window.table.selectRow(0)
        self.assertTrue(self.window.configure_button.isEnabled())
        with patch.object(ui.SourceDialog, "exec", return_value=ui.QDialog.Rejected):
            self.window.configure_selected()
        self.assertFalse(self.window.confirmed)
        self.confirm_all()
        self.assertEqual(len(self.window.confirmed), 11)
        self.assertTrue(self.window.convert_button.isEnabled())
        self.window.load_workspace(ROOT / "examples" / "IndexMap.wor")
        self.assertFalse(self.window.confirmed)
        self.assertFalse(self.window.convert_button.isEnabled())

    def test_arbitrary_workspace_names_sources_and_switching(self):
        from test_wor_model import small_workspace
        folder = Path(self.folder.name)
        first = folder / "Road network.WOR"
        first.write_text(small_workspace(
            tables='Open Table "survey\\\\Roads.TAB" As Roads\nOpen Table "Parcels" As Parcels',
            aliases="Parcels,Roads",
            body="    Display Global\n  Layer 2\n    Display Off",
            coordsys='CoordSys Earth Projection 8, 104, "m", 15, 0, 0.9996, 500000, 0',
        ), encoding="cp1252")
        self.window.load_workspace(first)
        self.assertEqual([layer.alias for layer in self.window.workspace.layers], ["Parcels", "Roads"])
        self.assertEqual(self.window.table.rowCount(), 2)
        self.assertEqual(Path(self.window.output_edit.text()), folder / "Road network.qgs")
        self.assertEqual(Path(self.window.sources["Roads"].path), folder / "survey" / "Roads.TAB")
        self.assertEqual(Path(self.window.sources["Parcels"].path), folder / "Parcels.tab")
        self.confirm_all()
        self.assertTrue(self.window.convert_button.isEnabled())
        second = folder / "Utilities.wor"
        second.write_text(small_workspace(tables='Open Table "pipes" As Pipes', aliases="Pipes"), encoding="cp1252")
        self.window.load_workspace(second)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(set(self.window.sources), {"Pipes"})
        self.assertFalse(self.window.confirmed)
        self.assertFalse(self.window.convert_button.isEnabled())
        self.assertEqual(Path(self.window.output_edit.text()), folder / "Utilities.qgs")
        self.assertIn("Utilities.wor", self.window.windowTitle())
        self.assertFalse((folder / "Road network.qgs").exists())
        self.assertFalse((folder / "Utilities.qgs").exists())

    def test_bad_workspace_suggestion_preserves_previous_state(self):
        from test_wor_model import small_workspace
        previous = self.window.workspace
        previous_sources = self.window.sources
        previous_output = self.window.output_edit.text()
        path = Path(self.folder.name) / "drive-relative.wor"
        path.write_text(small_workspace(tables='Open Table "C:relative" As data'), encoding="cp1252")
        with patch.object(ui.QMessageBox, "warning") as warning:
            self.window.load_workspace(path)
            warning.assert_called_once()
        self.assertIs(self.window.workspace, previous)
        self.assertIs(self.window.sources, previous_sources)
        self.assertEqual(self.window.output_edit.text(), previous_output)

    def test_source_fields_xyz_and_explicit_graphic_fallback(self):
        dialog = self.dialog()
        self.assertEqual(dialog.kind.currentData(), "tab")
        self.assertFalse(dialog.graphic_fallback.isChecked())
        self.assertFalse(dialog.fields["host"].isEnabled())
        dialog.kind.setCurrentIndex(dialog.kind.findData("xyz"))
        self.assertTrue(dialog.fields["path"].isEnabled())
        self.assertFalse(dialog.tab_browse.isEnabled())
        self.assertFalse(dialog.fields["host"].isEnabled())
        self.assertFalse(dialog.graphic_fallback.isEnabled())
        dialog.fields["path"].setText("https://service.example/{z}/{x}/{y}.png")
        dialog.confirm()
        self.assertEqual(dialog.source.kind, "xyz")
        dialog.kind.setCurrentIndex(dialog.kind.findData("mssql"))
        self.assertTrue(dialog.graphic_fallback.isEnabled())
        for key, value in {"host": "server", "database": "db", "schema": "dbo", "table": "layer", "geometry": "GEOLOC", "key": "id", "authcfg": "abcdefg"}.items():
            dialog.fields[key].setText(value)
        dialog.graphic_fallback.setChecked(True)
        dialog.confirm()
        self.assertEqual(dialog.source.kind, "mssql")
        self.assertTrue(dialog.source.allow_graphic_fallback)
        dialog.integrated.setChecked(True)
        self.assertFalse(dialog.fields["authcfg"].isEnabled())
        dialog.confirm()
        self.assertTrue(dialog.source.integrated)
        self.assertFalse(dialog.source.authcfg)
        dialog.kind.setCurrentIndex(dialog.kind.findData("oracle"))
        self.assertFalse(dialog.integrated.isEnabled())
        self.assertTrue(dialog.fields["authcfg"].isEnabled())
        dialog.confirm()
        self.assertEqual(dialog.source.kind, "oracle")
        self.assertFalse(dialog.source.integrated)

    def test_database_source_can_be_confirmed_without_key(self):
        for provider in ("oracle", "mssql"):
            with self.subTest(provider=provider):
                dialog = self.dialog()
                dialog.kind.setCurrentIndex(dialog.kind.findData(provider))
                for key, value in {"host": "server", "database": "db", "schema": "owner",
                                   "table": "lots", "geometry": "geom", "authcfg": "abcdefg"}.items():
                    dialog.fields[key].setText(value)
                dialog.fields["key"].clear()
                self.assertIn("optional", dialog.labels["key"].text())
                with patch.object(ui.QMessageBox, "warning") as warning:
                    dialog.confirm()
                    warning.assert_not_called()
                self.assertEqual(dialog.result(), ui.QDialog.Accepted)
                self.assertEqual(dialog.source.kind, provider)
                self.assertEqual(dialog.source.key, "")

    def test_xyz_query_parameters_are_rejected(self):
        dialog = self.dialog()
        dialog.kind.setCurrentIndex(dialog.kind.findData("xyz"))
        dialog.fields["path"].setText("https://service.example/{z}/{x}/{y}.png?token=secret")
        with patch.object(ui.QMessageBox, "warning") as warning:
            dialog.confirm()
            warning.assert_called_once()
        self.assertEqual(dialog.source.kind, "tab")

    def test_existing_output_no_never_calls_converter(self):
        self.confirm_all()
        output = Path(self.window.output_edit.text())
        output.write_text("existing project sentinel", encoding="utf-8")
        with patch.object(ui.QMessageBox, "question", return_value=QMessageBox.No) as question, patch.object(ui, "convert_workspace") as convert:
            self.window.convert()
            question.assert_called_once()
            convert.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), "existing project sentinel")

    def test_mock_conversion_warnings_and_report_are_logged(self):
        self.confirm_all()
        output = Path(self.window.output_edit.text())
        report = str(Path(self.folder.name) / "conversion-report.json")
        with patch.object(ui, "convert_workspace", return_value={"output": str(output), "report": report, "warnings": ["Review converted labels."], "layers": []}) as convert:
            self.window.convert()
            convert.assert_called_once_with(self.window.workspace, self.window.sources, output, overwrite=False)
        log = self.window.log.toPlainText()
        self.assertIn("Conversion: Review converted labels.", log)
        self.assertIn("Conversion report: " + report, log)
        self.assertIn("Saved project: " + str(output), log)
        self.assertFalse(self.window.busy)
        self.assertTrue(self.window.convert_button.isEnabled())
        self.assertFalse(output.exists())

    def test_connection_diagnostics_are_redacted(self):
        for value in ("host=server dbname=db password=secret", "https://user:secret@example.test/", "pwd: secret"):
            self.assertIn("omitted", ui.safe_text(value))
            self.assertNotIn("secret", ui.safe_text(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
