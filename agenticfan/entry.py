"""Finder opens a window; explicit commands retain the JSON CLI."""
from __future__ import annotations

import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--internal-helper"] or (len(args) == 2 and args[0] in
            ("--internal-helper-install", "--internal-helper-uninstall")):
        from .protocol import emit, success, failure
        from .errors import AgenticFanError
        try:
            if args == ["--internal-helper"]:
                from .helper import serve
                return serve()
            from .helper_install import install_privileged, uninstall_privileged
            action = install_privileged if args[0].endswith("-install") else uninstall_privileged
            emit(success(action(int(args[1]))))
            return 0
        except Exception as exc:
            error = exc if isinstance(exc, AgenticFanError) else AgenticFanError("HELPER_ERROR", str(exc))
            emit(failure(error))
            return error.exit_code
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
