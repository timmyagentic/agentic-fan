#!/usr/bin/env python3
"""Check release-sized version changes and the PR's bilingual changelogs."""

import argparse
import ast
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-(?:alpha|beta|rc)\.[1-9]\d*)?")
CHANGELOGS = {"CHANGELOG.md", "CHANGELOG.zh-Hans.md"}


def parse_version(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid version: {value!r}")
    return tuple(int(match[index]) for index in (1, 2, 3)), match[4]


def validate_version(package, metadata, released, level="patch"):
    if package != metadata:
        raise ValueError("agenticfan/__init__.py and pyproject.toml versions differ")
    current, prerelease = parse_version(package)
    stable, stable_prerelease = parse_version(released.removeprefix("v"))
    if stable_prerelease:
        raise ValueError("The baseline must be a published stable release")
    if level == "patch":
        expected = (stable[0], stable[1], stable[2] + 1)
        if current == stable and not prerelease:
            return
    elif level == "minor":
        expected = (stable[0], stable[1] + 1, 0)
    elif level == "major":
        expected = (stable[0] + 1, 0, 0)
    else:
        raise ValueError(f"Unknown release level: {level}")
    if current != expected:
        target = ".".join(map(str, expected))
        raise ValueError(f"Version {package} violates {level} policy from {released}; expected {target}")


def source_versions(root):
    module = ast.parse((root / "agenticfan/__init__.py").read_text())
    package = next(ast.literal_eval(node.value) for node in module.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__"
                           for target in node.targets))
    metadata = (root / "pyproject.toml").read_text()
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", metadata)
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*(?:#.*)?$', project[1], re.MULTILINE) if project else None
    if not match:
        raise ValueError("Missing project.version in pyproject.toml")
    return package, match[1]


def validate_changelogs(root, changed=None):
    for name in sorted(CHANGELOGS):
        text = (root / name).read_text()
        if not re.search(r"(?m)^## Unreleased\s*$", text):
            raise ValueError(f"{name} must contain an Unreleased section")
    if changed is not None and not CHANGELOGS.issubset(changed):
        missing = ", ".join(sorted(CHANGELOGS - set(changed)))
        raise ValueError(f"PR must update both changelogs; missing: {missing}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--released-version", required=True, help="Latest published stable tag, e.g. v0.4.1")
    parser.add_argument("--release-level", choices=("patch", "minor", "major"), default="patch",
                        help="minor/major require explicit user authorization and the matching PR label")
    parser.add_argument("--base", help="PR base SHA; enables changed-changelog checks")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        package, metadata = source_versions(ROOT)
        validate_version(package, metadata, args.released_version, args.release_level)
        changed = None
        if args.base:
            changed = subprocess.run(["git", "diff", "--name-only", f"{args.base}...{args.head}"],
                                     cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
        validate_changelogs(ROOT, changed)
    except (ValueError, OSError, StopIteration, SyntaxError, subprocess.CalledProcessError) as error:
        print(f"Repository policy failed: {error}", file=sys.stderr)
        return 1
    print(f"Repository policy passed: {package}; published {args.released_version}; level {args.release_level}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
