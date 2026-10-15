# Build on an Apple Silicon Mac using packaging/macos/build.py.
import os
from pathlib import Path
import runpy

root = Path(SPECPATH).resolve().parents[1]
version = runpy.run_path(str(root / "macfan" / "__init__.py"))["__version__"]
analysis = Analysis(
    [str(root / "packaging" / "macos" / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "macfan/agent_skill"), "macfan/agent_skill")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="macfan",
    # Required for the CLI and guardian's JSON stdin/stdout, including when
    # the same executable is run by the GUI's administrator authorization.
    console=True,
    debug=False,
    strip=False,
    upx=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=os.environ.get("MACFAN_CODESIGN_IDENTITY") or None,
    entitlements_file=None,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="MacFan",
)
app = BUNDLE(
    collection,
    name="MacFan.app",
    bundle_identifier="io.github.timmyagentic.macfan",
    info_plist={
        "CFBundleDisplayName": "MacFan",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSBackgroundOnly": False,
        "LSUIElement": False,
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
    },
)
