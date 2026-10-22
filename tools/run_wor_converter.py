"""Run with QGIS's python-qgis.bat; no package installation is needed."""
from __future__ import annotations

import gc
import os
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    try:
        from qgis.core import QgsApplication, QgsProject
        from qgis.PyQt import sip
        from qgis.PyQt.QtWidgets import QMessageBox
    except ImportError:
        print("QGIS Python is unavailable. Run launch_wor_converter.bat, or use an installed QGIS bin/python-qgis.bat.", file=sys.stderr)
        return 1

    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    profile = appdata / "QGIS" / "QGIS3" / "profiles" / "default"
    # customConfigPath preserves access to the user's existing qgis-auth.db.
    app = QgsApplication([], True, str(profile))
    window = None
    try:
        app.initQgis()
        # Import the UI only after QGIS providers/authentication are initialized.
        from qg.wor_converter_app import main as show_window
        window = show_window()
        return app.exec()
    except Exception:
        # A provider/startup exception may contain a connection URI. Do not print it.
        QMessageBox.critical(None, "WOR converter could not start", "The converter could not start. Use an installed QGIS 3.x Python runtime and check that the repository files and QGIS providers are available.")
        return 1
    finally:
        # Release Python/Qt objects and layer providers before exitQgis().
        if window is not None:
            window.close()
            sip.delete(window)
            window = None
        QgsProject.instance().clear()
        gc.collect()
        app.exitQgis()
        del app


if __name__ == "__main__":
    raise SystemExit(main())
