"""
Run inside QGIS via `qgis --code` (see qg.py start). Starts the MCP socket
server from this repo's plugin on $QGIS_MCP_PORT, no plugin install/enable needed.
"""
import builtins
import os
import sys

from qgis.core import Qgis, QgsMessageLog
from qgis.utils import iface

# __file__ is not reliable under --code, so qg.py passes the repo root
_repo = os.environ["QG_REPO"]
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer

_port = int(os.environ.get("QGIS_MCP_PORT", "9876"))
_server = QgisMCPServer(port=_port, iface=iface)
if _server.start():
    builtins._qg_mcp_server = _server  # keep a reference alive
else:
    QgsMessageLog.logMessage(f"qg: could not start server on port {_port}", "QGIS MCP", Qgis.Critical)
