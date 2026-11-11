"""Execute Python modules/scripts with external networking denied before imports."""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-report", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--module")
    target.add_argument("--script", type=Path)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if options.audit_report.exists():
        raise FileExistsError("Use a new offline audit report path")
    attempts = []
    def deny_network(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            attempts.append(event)
            raise RuntimeError("External networking disabled for fifth-stage offline verification")
    sys.addaudithook(deny_network)
    # Avoid importing a local real credential into CLI tests. MockTransport tests
    # configure explicit dummy values. This does not write or change .env.
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["OPENAI_MODEL"] = ""
    forwarded = options.args[1:] if options.args[:1] == ["--"] else options.args
    sys.argv = [options.module or str(options.script), *forwarded]
    exit_code = 0
    try:
        if options.module:
            runpy.run_module(options.module, run_name="__main__", alter_sys=True)
        else:
            sys.path.insert(0, str(options.script.resolve().parent))
            runpy.run_path(str(options.script), run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1 if exc.code else 0
        raise
    except BaseException:
        exit_code = 1
        raise
    finally:
        options.audit_report.parent.mkdir(parents=True, exist_ok=True)
        options.audit_report.write_text(json.dumps({
            "execution_mode": "offline", "external_network_attempts": attempts,
            "actual_external_connections": 0, "exit_code": exit_code,
            "local_env_file_modified": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
