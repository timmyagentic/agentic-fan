#!/usr/bin/env python3
"""Compile the native macOS surface without rebuilding the monitoring core."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def build(output):
    if sys.platform != 'darwin':
        raise RuntimeError('The native interface requires macOS and Xcode Command Line Tools.')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-O', '-target', 'arm64-apple-macos15.0',
                    str(ROOT / 'native/Models.swift'), str(ROOT / 'native/Command.swift'),
                    str(ROOT / 'native/MenuPopover.swift'), str(ROOT / 'native/LoginItem.swift'),
                    str(ROOT / 'native/HookDocument.swift'), str(ROOT / 'native/HookCodeEditor.swift'), str(ROOT / 'native/Hooks.swift'),
                    str(ROOT / 'native/AgenticFanUI.swift'),
                    '-framework', 'SwiftUI', '-framework', 'AppKit', '-framework', 'ServiceManagement',
                    '-o', str(output)], check=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/native/AgenticFanUI')
    print(build(parser.parse_args().output))
