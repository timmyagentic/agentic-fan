"""Finder opens a window; explicit commands retain the JSON CLI."""
from __future__ import annotations

import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--internal-guardian"]:
        from .guardian import main as guardian_main
        return guardian_main()
    if getattr(sys, "frozen", False):
        args = [arg for arg in args if not arg.startswith("-psn_")]
    gui = "--gui" in args or (getattr(sys, "frozen", False) and not args)
    if gui:
        if any(arg not in ("--gui", "--demo", "--gui-smoke-test") for arg in args):
            from .errors import invalid
            from .protocol import emit, failure
            emit(failure(invalid("GUI accepts only --gui, --demo and --gui-smoke-test."), "--demo" in args))
            return 2
        if "--gui-smoke-test" in args and "--demo" not in args:
            from .errors import invalid
            from .protocol import emit, failure
            emit(failure(invalid("GUI smoke testing requires --demo.")))
            return 2
        from .gui import main as gui_main
        return gui_main(demo="--demo" in args, smoke_test="--gui-smoke-test" in args)
    from .cli import main as cli_main
    return cli_main(args)
