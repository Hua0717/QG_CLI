"""Standalone WOR converter window. The runner must initialize QgsApplication first."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from qgis.PyQt.QtCore import QEventLoop, Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QAbstractItemView,
)

from .wor_model import read_workspace, table_path
from .wor_converter import SourceSpec, convert_workspace


REPO_ROOT = Path(__file__).resolve().parents[2]


def suggested_output(workspace_path):
    """Use the selected workspace name and directory, never a sample-specific target."""
    return Path(workspace_path).absolute().with_suffix(".qgs")


def safe_text(value: object) -> str:
    """Never expose connection URIs or credentials in the UI/log."""
    text = str(value)
    # A provider diagnostic can include a whole multi-line connection string.
    if re.search(r"[a-z][a-z0-9+.-]*://|\b(?:password|passwd|pwd)\s*[:=]|\b(?:dbname|authcfg|host|server|user|uid|service)\s*=", text, re.I):
        return "Connection details omitted. Check the selected source and QGIS authentication configuration."
    return text


class SourceDialog(QDialog):
    """Explicitly confirm one source; editing is local until Confirm is pressed."""

    def __init__(self, alias: str, source: SourceSpec, wor_directory: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure source — " + safe_text(alias))
        self.resize(620, 570)
        self.wor_directory = wor_directory
        self.source = source
        layout = QVBoxLayout(self)
        explanation = QLabel("Choose this layer's actual data source. No connection is opened until conversion.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.kind = QComboBox()
        for label, kind in (("TAB file", "tab"), ("SQL Server", "mssql"), ("Oracle", "oracle"), ("XYZ tiles (web TAB replacement)", "xyz")):
            self.kind.addItem(label, kind)
        self.kind.setCurrentIndex(max(0, self.kind.findData(source.kind)))
        form.addRow("Source type", self.kind)
        self.fields = {}
        self.labels = {}
        self.tab_row = QWidget()
        path_layout = QHBoxLayout(self.tab_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.fields["path"] = QLineEdit(source.path)
        self.tab_browse = QPushButton("Browse…")
        self.tab_browse.clicked.connect(self.browse_tab)
        path_layout.addWidget(self.fields["path"], 1)
        path_layout.addWidget(self.tab_browse)
        self.labels["path"] = QLabel("TAB path")
        form.addRow(self.labels["path"], self.tab_row)
        for key, label, hint in (
            ("host", "Host / server", "Server name; do not paste a connection URI"),
            ("port", "Port (optional)", "Provider default if blank"),
            ("database", "Database / Oracle service", "Enter explicitly; never inferred from the WOR path"),
            ("schema", "Schema", "Owner/schema of the table"),
            ("table", "Table", "Editable alias default — verify the actual table name"),
            ("geometry", "Geometry column", "GEOLOC"),
            ("key", "Stable unique key (optional)", "Leave blank for QGIS provider detection; specify if needed for a view"),
            ("username", "Username (optional)", "Prefer credentials stored in the QGIS authentication configuration"),
            ("authcfg", "QGIS authcfg ID", "Existing authentication configuration ID; not a password"),
            ("source_crs", "Source CRS override (optional)", "For example EPSG:3376; only if the source CRS is known"),
        ):
            field = QLineEdit(str(getattr(source, key) or ""))
            field.setPlaceholderText(hint)
            self.fields[key] = field
            self.labels[key] = QLabel(label)
            form.addRow(self.labels[key], field)
        self.integrated = QCheckBox("Use Windows integrated authentication (SQL Server only)")
        self.integrated.setChecked(source.integrated)
        form.addRow(self.integrated)
        self.graphic_fallback = QCheckBox("Allow reported Global-style fallback when database has no MapInfo object styles")
        self.graphic_fallback.setChecked(source.allow_graphic_fallback)
        self.graphic_fallback.setToolTip("Explicitly accept loss of per-object Graphic styles for this database source. A conversion warning will be reported.")
        form.addRow(self.graphic_fallback)
        layout.addLayout(form)
        note = QLabel("No password is requested or stored. Set up credentials in QGIS first, then enter its authcfg ID. "
                      "An encrypted authentication database may need its master password unlocked for this process.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Confirm source")
        buttons.accepted.connect(self.confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.update_fields)
        self.integrated.toggled.connect(self.update_fields)
        self.update_fields()

    def update_fields(self, *_):
        kind = self.kind.currentData()
        is_tab = kind == "tab"
        is_xyz = kind == "xyz"
        is_database = kind in ("mssql", "oracle")
        self.tab_row.setEnabled(is_tab or is_xyz)
        self.tab_browse.setEnabled(is_tab)
        self.labels["path"].setEnabled(is_tab or is_xyz)
        self.labels["path"].setText("XYZ URL template" if is_xyz else "TAB path")
        self.fields["path"].setPlaceholderText("https://service.example/{z}/{x}/{y}.png" if is_xyz else "MapInfo TAB file")
        self.integrated.setEnabled(kind == "mssql")
        self.graphic_fallback.setEnabled(is_database)
        for key, field in self.fields.items():
            if key == "path":
                continue
            enabled = is_database or (is_tab and key == "source_crs")
            if key in ("username", "authcfg") and kind == "mssql" and self.integrated.isChecked():
                enabled = False
            field.setEnabled(enabled)
            self.labels[key].setEnabled(enabled)

    def browse_tab(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Select TAB source", self.fields["path"].text() or str(self.wor_directory), "MapInfo TAB (*.tab *.TAB);;All files (*)")
        if filename:
            self.fields["path"].setText(filename)

    def confirm(self):
        values = {key: field.text().strip() for key, field in self.fields.items()}
        kind = self.kind.currentData()
        integrated = kind == "mssql" and self.integrated.isChecked()
        if kind == "tab":
            if not values["path"]:
                QMessageBox.warning(self, "Source required", "Choose this layer's TAB file.")
                return
            path = Path(values["path"])
            if not path.is_absolute():
                path = self.wor_directory / path
            values["path"] = str(path)
            # Do not probe a network path here; the conversion engine validates it.
            self.source = SourceSpec(kind="tab", path=values["path"], source_crs=values["source_crs"])
        elif kind == "xyz":
            from urllib.parse import urlsplit
            try:
                url = urlsplit(values["path"])
                valid = (url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password and not url.query
                         and all(token in values["path"] for token in ("{z}", "{x}", "{y}")))
            except ValueError:
                valid = False
            if not valid:
                QMessageBox.warning(self, "Invalid XYZ source", "Enter the original service's public, token-free HTTP(S) URL template containing {z}, {x} and {y}, without username/password or query parameters.")
                return
            self.source = SourceSpec(kind="xyz", path=values["path"])
        else:
            missing = [label for key, label in (("host", "host/server"), ("database", "database/service"), ("schema", "schema"), ("table", "table"), ("geometry", "geometry column")) if not values[key]]
            if missing:
                QMessageBox.warning(self, "Source required", "Enter " + ", ".join(missing) + ".")
                return
            if values["port"] and (not values["port"].isdigit() or not 1 <= int(values["port"]) <= 65535):
                QMessageBox.warning(self, "Invalid port", "Port must be blank or a number from 1 to 65535.")
                return
            if not integrated and not values["authcfg"]:
                QMessageBox.warning(self, "Authentication required", "Enter an existing QGIS authcfg ID, or use Windows integrated authentication for SQL Server.")
                return
            if integrated:
                values["username"] = ""
                values["authcfg"] = ""
            values["path"] = ""
            self.source = SourceSpec(kind=kind, integrated=integrated, allow_graphic_fallback=self.graphic_fallback.isChecked(), **values)
        self.accept()


class ConverterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MapInfo WOR to QGIS")
        self.resize(1080, 760)
        self.workspace = None
        self.wor_path = None
        self.sources = {}
        self.confirmed = set()
        self.busy = False
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        title = QLabel("Convert a MapInfo workspace to a QGIS project")
        title.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 6px;")
        layout.addWidget(title)
        instruction = QLabel("1. Load a WOR   2. Configure and confirm every source   3. Choose an output and convert")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        wor_row = QHBoxLayout()
        self.wor_edit = QLineEdit()
        self.wor_edit.setReadOnly(True)
        self.wor_edit.setPlaceholderText("No workspace loaded")
        self.load_button = QPushButton("Open WOR…")
        self.load_button.clicked.connect(self.browse_wor)
        wor_row.addWidget(self.wor_edit, 1)
        wor_row.addWidget(self.load_button)
        layout.addLayout(wor_row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Layer alias", "WOR display", "Source type", "Source / table", "Status"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.cellDoubleClicked.connect(lambda *_: self.configure_selected())
        layout.addWidget(self.table, 2)
        source_row = QHBoxLayout()
        self.source_status = QLabel("Load a workspace to begin.")
        self.configure_button = QPushButton("Configure selected source…")
        self.configure_button.clicked.connect(self.configure_selected)
        source_row.addWidget(self.source_status, 1)
        source_row.addWidget(self.configure_button)
        layout.addLayout(source_row)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output QGS"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Select a WOR to suggest its matching .qgs filename")
        self.output_edit.textChanged.connect(self.update_actions)
        self.output_button = QPushButton("Browse…")
        self.output_button.clicked.connect(self.browse_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)
        conversion_row = QHBoxLayout()
        self.progress = QLabel("Ready. Loading a WOR does not connect to a database.")
        self.progress.setWordWrap(True)
        self.convert_button = QPushButton("Convert to QGS")
        self.convert_button.clicked.connect(self.convert)
        conversion_row.addWidget(self.progress, 1)
        conversion_row.addWidget(self.convert_button)
        layout.addLayout(conversion_row)
        layout.addWidget(QLabel("Parser and conversion messages"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Warnings and conversion results appear here. Connection URIs and passwords are never logged.")
        layout.addWidget(self.log, 1)
        self.update_actions()

    def log_message(self, message):
        self.log.appendPlainText(safe_text(message))

    def browse_wor(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Open MapInfo workspace", str(self.wor_path.parent if self.wor_path else REPO_ROOT), "MapInfo workspace (*.wor *.WOR)")
        if filename:
            self.load_workspace(Path(filename))

    def load_workspace(self, path):
        try:
            workspace = read_workspace(path)
            aliases = [layer.alias for layer in workspace.layers]
            if len(aliases) != len(set(aliases)):
                raise ValueError("Layer aliases must be unique before sources can be configured.")
            wor_path = Path(path).absolute()
            sources = {
                layer.alias: SourceSpec(kind="tab", path=table_path(wor_path, layer.path or layer.alias), table=layer.alias)
                for layer in workspace.layers
            }
        except ValueError as error:
            QMessageBox.warning(self, "Cannot load workspace", safe_text(error))
            return
        except Exception:
            QMessageBox.warning(self, "Cannot load workspace", "The workspace could not be read. Check the file location, permissions and format.")
            return
        self.workspace = workspace
        self.wor_path = wor_path
        self.wor_edit.setText(str(self.wor_path))
        self.sources = sources
        self.confirmed.clear()
        self.log.clear()
        self.log_message("Workspace parsed locally. No data sources have been opened.")
        self.log_message("Relative TAB paths use the loaded WOR directory. Confirm every source before conversion.")
        self.output_edit.setText(str(suggested_output(self.wor_path)))
        self.setWindowTitle(f"MapInfo WOR to QGIS — {self.wor_path.name}")
        for warning in workspace.warnings:
            self.log_message("Parser: " + warning)
        self.refresh_table()
        self.progress.setText("Workspace loaded. Configure and confirm every source; defaults are not confirmed.")

    def refresh_table(self):
        self.table.setRowCount(len(self.workspace.layers))
        for row, layer in enumerate(self.workspace.layers):
            source = self.sources[layer.alias]
            description = source.path if source.kind == "tab" else ("Explicit XYZ template configured" if source.kind == "xyz" else ".".join(part for part in (source.schema, source.table) if part))
            for column, value in enumerate((layer.alias, str(layer.display), {"tab": "TAB", "mssql": "SQL Server", "oracle": "Oracle", "xyz": "XYZ tiles"}[source.kind], description, "Confirmed" if layer.alias in self.confirmed else "Needs confirmation")):
                self.table.setItem(row, column, QTableWidgetItem(safe_text(value)))
        self.update_actions()

    def configure_selected(self):
        row = self.table.currentRow()
        if self.busy or not self.workspace or row < 0:
            return
        layer = self.workspace.layers[row]
        source = self.sources[layer.alias]
        # Switching a previously confirmed TAB to a server still offers an editable alias, not a guessed table.
        if not source.table:
            source = replace(source, table=layer.alias)
        dialog = SourceDialog(layer.alias, source, self.wor_path.parent, self)
        if dialog.exec() == QDialog.Accepted:
            self.sources[layer.alias] = dialog.source
            self.confirmed.add(layer.alias)
            self.refresh_table()
            self.table.selectRow(row)

    def browse_output(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save QGIS project", self.output_edit.text(), "QGIS project (*.qgs)", options=QFileDialog.DontConfirmOverwrite)
        if filename:
            if not Path(filename).suffix:
                filename += ".qgs"
            self.output_edit.setText(filename)

    def update_actions(self, *_):
        loaded = self.workspace is not None and bool(self.workspace.layers)
        ready = loaded and len(self.confirmed) == len(self.workspace.layers)
        self.configure_button.setEnabled(not self.busy and loaded and self.table.currentRow() >= 0)
        self.convert_button.setEnabled(not self.busy and ready and bool(self.output_edit.text().strip()))
        if loaded:
            self.source_status.setText(f"{len(self.confirmed)} of {len(self.workspace.layers)} sources confirmed")
        for widget in (self.load_button, self.output_button, self.output_edit, self.table):
            widget.setEnabled(not self.busy)

    def convert(self):
        if self.busy or not self.workspace or len(self.confirmed) != len(self.workspace.layers):
            return
        output = Path(self.output_edit.text().strip())
        if not output.is_absolute() or output.suffix.lower() != ".qgs":
            QMessageBox.warning(self, "Invalid output", "Choose an absolute output path ending in .qgs.")
            return
        overwrite = output.exists()
        if overwrite:
            answer = QMessageBox.question(self, "Replace existing project?", f"This project already exists:\n{output}\n\nReplace it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self.busy = True
        self.progress.setText("Converting… Opening selected sources and writing the project. Database connections may take time.")
        self.update_actions()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        try:
            result = convert_workspace(self.workspace, self.sources, output, overwrite=overwrite)
            for warning in result.get("warnings", []):
                self.log_message("Conversion: " + warning)
            self.log_message("Saved project: " + str(result.get("output", output)))
            if result.get("report"):
                self.log_message("Conversion report: " + str(result["report"]))
            self.progress.setText("Conversion complete. Review messages, then open the saved QGS in QGIS.")
        except ValueError as error:
            message = safe_text(error)
            self.log_message("Conversion failed: " + message)
            self.progress.setText("Conversion failed. Review the source settings and messages.")
            QMessageBox.warning(self, "Conversion failed", message)
        except Exception:
            # Provider exceptions can include passwords/URIs; never show raw text or traceback.
            self.log_message("Conversion failed. Check source settings, permissions, provider availability and QGIS authentication.")
            self.progress.setText("Conversion failed. Review the messages and source settings.")
            QMessageBox.warning(self, "Conversion failed", "The project could not be converted. Check source settings, provider availability, authentication and output permissions.")
        finally:
            QApplication.restoreOverrideCursor()
            self.busy = False
            self.update_actions()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            event.accept()


def main():
    """Show and return the standalone window; the caller owns the Qt event loop."""
    window = ConverterWindow()
    window.show()
    return window
