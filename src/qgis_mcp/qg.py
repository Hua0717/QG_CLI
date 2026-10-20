#!/usr/bin/env python3
"""
qg - command line client for the QGIS MCP plugin (stdlib only).

Every MCP tool is a subcommand. Each invocation opens its own short-lived
connection, so there is no long-lived session for an agent to drop.

Instances: qg1..qg6 -> ports 9876..9881 (port = 9875 + N).
  qg1 ping            # QGIS instance 1 (port 9876)
  qg2 layers          # QGIS instance 2 (port 9877)
  qg --port 9900 ping # explicit port (or env QGIS_MCP_PORT)
"""

import argparse
import json
import os
import glob
import socket
import subprocess
import sys
import time

BASE_PORT = 9875  # qg1 -> 9876
MAX_INSTANCE = 6


def send(host, port, command, params, timeout):
    try:
        s = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        sys.exit(f"qg: cannot connect to QGIS at {host}:{port} ({e}). "
                 f"Start the server in the QGIS MCP plugin on that port.")
    try:
        s.settimeout(timeout)
        s.sendall(json.dumps({"type": command, "params": params}).encode("utf-8"))
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            try:
                return json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue
        sys.exit("qg: connection closed before a complete response arrived")
    except socket.timeout:
        sys.exit(f"qg: timed out after {timeout}s waiting for QGIS (use --timeout)")
    finally:
        s.close()


EXAMPLES = """examples:
  {n} start [--project FILE]     (launch QGIS with the server on this port)
  {n} status
  {n} stop [--save]
  {n} ping
  {n} get_layers
  {n} add_vector_layer C:/data/a.shp --name Colonias
  {n} get_layer_features <layer_id> --limit 5
  {n} execute_processing native:buffer '{{"INPUT":"Colonias","DISTANCE":10,"OUTPUT":"b.gpkg"}}'
  {n} execute_processing native:buffer @params.json
  {n} execute_code "print(QgsProject.instance().count())"
  {n} execute_code --file script.py        (or pipe code on stdin)
  {n} render_map out.png --width 1200 --height 800
  {n} screenshot shot.png [--canvas]
  {n} save_project

exit status: 0 on success, 1 on QGIS error or connection failure.
Run '{n} COMMAND --help' for command options.
"""


def pre_instance(argv):
    """Find --instance/-i before full parsing so --help can be tailored."""
    argv = sys.argv[1:] if argv is None else argv
    for i, a in enumerate(argv):
        if a in ("--instance", "-i") and i + 1 < len(argv) and argv[i + 1].isdigit():
            return int(argv[i + 1])
        if a.startswith("--instance="):
            return int(a.split("=", 1)[1]) if a.split("=", 1)[1].isdigit() else None
    return None


def ping(host, port):
    try:
        with socket.create_connection((host, port), timeout=2) as s:
            s.settimeout(5)
            s.sendall(b'{"type": "ping", "params": {}}')
            return b"pong" in s.recv(4096)
    except OSError:
        return False


def find_qgis_bat():
    env = os.environ.get("QGIS_BAT")
    if env:
        return env
    found = sorted(glob.glob("C:/Program Files/QGIS 3*/bin/qgis.bat")
                   + glob.glob("C:/OSGeo4W*/bin/qgis.bat"))
    if not found:
        sys.exit("qg: qgis.bat not found; set env QGIS_BAT to its full path")
    return found[-1]


def listening_pid(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            return parts[4]
    return None


def do_start(args, port, instance):
    if ping(args.host, port):
        sys.exit(f"qg: already running on port {port}")
    if listening_pid(port):
        sys.exit(f"qg: port {port} is in use by another process (pid {listening_pid(port)})")
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [find_qgis_bat(), "--profile", args.profile or f"qg{instance or port}",
           "--code", os.path.join(here, "qg_startup.py")]
    if args.project:
        cmd.append(os.path.abspath(args.project))
    env = dict(os.environ, QGIS_MCP_PORT=str(port),
               QG_REPO=os.path.abspath(os.path.join(here, "..", "..")))
    subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
    if args.no_wait:
        print(f"qg: launching QGIS for port {port}")
        return 0
    deadline = time.time() + args.wait
    while time.time() < deadline:
        if ping(args.host, port):
            print(f"qg: started (port {port}, profile {cmd[2]})")
            return 0
        time.sleep(1)
    print(f"qg: QGIS launched but port {port} not answering after {args.wait}s", file=sys.stderr)
    return 1


def do_stop(args, port):
    pid = listening_pid(port)
    if args.force:
        if not pid:
            sys.exit(f"qg: nothing listening on port {port}")
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
        print(f"qg: killed pid {pid} (port {port})")
        return 0
    if not ping(args.host, port):
        print(f"qg: not running on port {port}")
        return 0
    res = send(args.host, port, "quit_qgis", {"save": args.save}, 30)
    if res.get("status") != "success":
        sys.exit(f"qg: quit refused: {res.get('message')}. Use '--force' to kill pid {pid}.")
    for _ in range(30):
        if not ping(args.host, port):
            print(f"qg: stopped (port {port})")
            return 0
        time.sleep(0.5)
    sys.exit(f"qg: still running on port {port}; use '--force' to kill pid {pid}")


def build_parser(instance=None):
    if instance in range(1, MAX_INSTANCE + 1):
        name = f"qg{instance}"
        desc = (f"{name}: command line client for QGIS instance {instance} "
                f"(localhost:{BASE_PORT + instance}). One short-lived connection per call.\n"
                f"In QGIS: QGIS MCP dock > Server Port = {BASE_PORT + instance} > Start Server.")
    else:
        name = "qg"
        desc = __doc__
    p = argparse.ArgumentParser(prog=name, description=desc,
                                epilog=EXAMPLES.format(n=name),
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance", "-i", type=int, choices=range(1, MAX_INSTANCE + 1),
                   help="instance 1-6 (port 9875+N); set by the qg1..qg6 wrappers")
    p.add_argument("--port", type=int, help="explicit port (overrides instance)")
    p.add_argument("--host", default="localhost")
    p.add_argument("--timeout", type=float, default=300, help="response timeout, seconds")
    p.add_argument("--raw", action="store_true", help="print compact JSON")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def cmd(name, help_, *args):
        sp = sub.add_parser(name, help=help_, description=help_)
        for a, kw in args:
            sp.add_argument(a, **kw)
        return sp

    str_ = {"type": str}
    sp = cmd("start", "Launch QGIS for this instance with the MCP server on its port",
             ("--project", {"help": "project file to open"}),
             ("--profile", {"help": "QGIS profile (default: qgN; 'default' for your main one)"}),
             ("--wait", {"type": int, "default": 90, "help": "seconds to wait for the server"}),
             ("--no-wait", {"action": "store_true"}))
    sp = cmd("stop", "Close QGIS on this instance (never prompts; unsaved changes are lost)",
             ("--save", {"action": "store_true", "help": "save the project first"}),
             ("--force", {"action": "store_true", "help": "kill the process listening on the port"}))
    cmd("status", "Show whether QGIS is running on this instance")
    cmd("ping", "Check connectivity")
    cmd("get_qgis_info", "Get QGIS information")
    cmd("get_project_info", "Get current project information")
    cmd("get_layers", "List all layers in the current project")
    cmd("load_project", "Load a project", ("path", str_))
    cmd("create_new_project", "Create a new project and save it", ("path", str_))
    cmd("save_project", "Save project (optionally to PATH)", ("path", {"nargs": "?"}))
    cmd("add_vector_layer", "Add a vector layer", ("path", str_),
        ("--provider", {"default": "ogr"}), ("--name", {}))
    cmd("add_raster_layer", "Add a raster layer", ("path", str_),
        ("--provider", {"default": "gdal"}), ("--name", {}))
    cmd("remove_layer", "Remove a layer by ID", ("layer_id", str_))
    cmd("zoom_to_layer", "Zoom to a layer's extent", ("layer_id", str_))
    cmd("get_layer_features", "Get features from a vector layer", ("layer_id", str_),
        ("--limit", {"type": int, "default": 10}))
    cmd("execute_processing", "Run a processing algorithm; PARAMETERS is JSON "
        "(or @file.json, or - for stdin)", ("algorithm", str_), ("parameters", str_))
    cmd("render_map", "Render the map view to an image", ("path", str_),
        ("--width", {"type": int, "default": 800}),
        ("--height", {"type": int, "default": 600}))
    cmd("screenshot", "Screenshot of the QGIS window (or just the map canvas) to a PNG/JPG file",
        ("path", str_),
        ("--canvas", {"action": "store_true", "help": "only the map canvas, not the whole window"}))
    cmd("execute_code", "Execute PyQGIS code: CODE string, --file FILE, or stdin",
        ("code", {"nargs": "?"}), ("--file", {"help": "read code from file"}))
    return p


def load_json(text):
    if text == "-":
        text = sys.stdin.read()
    elif text.startswith("@"):
        with open(text[1:], encoding="utf-8") as f:
            text = f.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(f"qg: invalid JSON for parameters: {e}")


def main(argv=None):
    args = build_parser(pre_instance(argv)).parse_args(argv)
    if args.port:
        port = args.port
    elif args.instance:
        port = BASE_PORT + args.instance
    else:
        port = int(os.environ.get("QGIS_MCP_PORT", BASE_PORT + 1))

    if args.cmd == "start":
        sys.exit(do_start(args, port, args.instance))
    if args.cmd == "stop":
        sys.exit(do_stop(args, port))
    if args.cmd == "status":
        up = ping(args.host, port)
        print(f"port {port}: {'running' if up else 'not running'}"
              + (f" (pid {listening_pid(port)})" if up else ""))
        sys.exit(0 if up else 1)

    params = {k: v for k, v in vars(args).items()
              if k not in ("cmd", "instance", "port", "host", "timeout", "raw") and v is not None}
    if args.cmd == "screenshot":
        params = {"path": os.path.abspath(args.path),
                  "target": "canvas" if args.canvas else "window"}
    if args.cmd == "execute_processing":
        params["parameters"] = load_json(args.parameters)
    if args.cmd == "execute_code":
        code = params.pop("code", None)
        path = params.pop("file", None)
        if path:
            with open(path, encoding="utf-8") as f:
                code = f.read()
        elif code is None or code == "-":
            code = sys.stdin.read()
        params = {"code": code}

    result = send(args.host, port, args.cmd, params, args.timeout)
    if args.raw:
        print(json.dumps(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if isinstance(result, dict) and result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
