"""Crash-injection child for the model-based session daemon tests (W16).

Not a test module. A real subprocess running a real SuperpowerDaemon on a
real Unix socket. It seeds a store, serves, writes a readiness marker with
the socket path, and serves forever until the parent SIGKILLs it. The parent
then asserts the store survived exactly the acknowledged writes and that a
fresh daemon recovers on the same paths (stale lock included).

Usage: ``python tests/_model_daemon_crash_child.py <workdir>``
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))


def main() -> int:
    workdir = Path(sys.argv[1])

    from _serve_helpers import make_config  # noqa: PLC0415 - test helper import
    from know_your_context.serve.superpower import SuperpowerDaemon  # noqa: PLC0415

    config = make_config(workdir, idle_timeout_seconds=3600.0)
    daemon = SuperpowerDaemon(config)
    daemon.start()
    thread = threading.Thread(target=daemon.serve_forever, daemon=True)
    thread.start()
    (workdir / "ready.txt").write_text(str(config.paths.socket))
    thread.join()  # serve until SIGKILL; no cleanup of any kind
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
