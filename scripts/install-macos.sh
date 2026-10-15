#!/bin/bash
# Install a verified MacFan DMG and its Agent integration. No Python required.
set -euo pipefail

repository="timmyagentic/agentic-mac-fans-control"
artifact="MacFan-Apple-Silicon-development-DMG"
agent="codex"
user_root="$HOME"
run_id=""
dmg=""
work=""
stage=""
mounted=0

fail() { printf 'MacFan: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage: bash scripts/install-macos.sh [options]
  --agent codex|claude|both  Agent skill destinations (default: codex)
  --run-id ID               Install from this successful GitHub Actions run
  --dmg PATH                Use a local DMG with its .dmg.sha256 sidecar
  --user-root PATH          User directory (default: current home)
  --help                    Show this help

Installs ~/Applications/MacFan.app, ~/.local/bin/macfan, and the Agent skill.
Adds a managed PATH block to .zprofile and .bash_profile, backing up originals.
Without --dmg, requires gh with access to timmyagentic/agentic-mac-fans-control.
Never changes fan settings, Gatekeeper configuration, or sudo permissions.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --agent|--run-id|--dmg|--user-root)
      [ "$#" -ge 2 ] || fail "$1 requires a value"
      case "$1" in
        --agent) agent="$2" ;;
        --run-id) run_id="$2" ;;
        --dmg) dmg="$2" ;;
        --user-root) user_root="$2" ;;
      esac
      shift 2 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
case "$agent" in codex|claude|both) ;; *) fail 'Agent must be codex, claude, or both' ;; esac
[ "$(/usr/bin/uname -s)" = Darwin ] || fail 'Requires an Apple Silicon Mac running macOS 15 or later'
[ "$(/usr/bin/uname -m)" = arm64 ] || fail 'Requires arm64; use a native terminal, not Rosetta'
macos_version="$(/usr/bin/sw_vers -productVersion)"
[ "${macos_version%%.*}" -ge 15 ] || fail 'Requires macOS 15 or later'
[ "$(/usr/bin/id -u)" -ne 0 ] || fail 'Run as your normal user, without sudo'
case "$user_root" in /*) ;; *) fail '--user-root must be an absolute path' ;; esac
[ -z "$run_id" ] || [[ "$run_id" =~ ^[0-9]+$ ]] || fail '--run-id must be an integer'
[ -z "$dmg" ] || [ -z "$run_id" ] || fail 'Choose --dmg or --run-id, not both'

cleanup() {
  if [ "$mounted" -eq 1 ]; then
    if ! /usr/bin/hdiutil detach "$work/mount" >&2; then
      printf 'MacFan: disk image could not be detached; retained temporary directory %s\n' "$work" >&2
      return
    fi
  fi
  [ -z "$stage" ] || /bin/rm -rf "$stage"
  [ -z "$work" ] || /bin/rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
work="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/macfan-install.XXXXXXXX")"

if [ -z "$dmg" ]; then
  command -v gh >/dev/null || fail 'GitHub CLI is needed to download this private-repository artifact. Install gh, or use --dmg with an already downloaded package.'
  if [ -z "$run_id" ]; then
    branch="$(gh api "repos/$repository" --jq .default_branch)"
    run_id="$(gh run list --repo "$repository" --workflow macos-dmg.yml --branch "$branch" \
      --event push --status success --limit 1 --json databaseId --jq '.[0].databaseId // empty')"
  fi
  [[ "$run_id" =~ ^[0-9]+$ ]] || fail 'No successful DMG build is available yet'
  run_info="$(gh api "repos/$repository/actions/runs/$run_id" \
    --jq '[.conclusion, .head_repository.full_name, .path, .event] | @tsv')"
  IFS=$'\t' read -r conclusion source_repo workflow event <<< "$run_info"
  [ "$conclusion" = success ] && [ "$source_repo" = "$repository" ] && \
    [ "$workflow" = .github/workflows/macos-dmg.yml ] || fail 'The selected run is not a successful MacFan DMG build'
  case "$event" in push|workflow_dispatch) ;; *) fail 'Only repository push or manual builds are installable' ;; esac
  gh run download "$run_id" --repo "$repository" --name "$artifact" --dir "$work/package"
  shopt -s nullglob
  images=("$work/package"/MacFan-*-arm64*.dmg)
  [ "${#images[@]}" -eq 1 ] || fail 'Expected exactly one Apple Silicon DMG in the artifact'
  dmg="${images[0]}"
fi

[ -f "$dmg" ] && [ -f "$dmg.sha256" ] || fail 'Both the DMG and its .dmg.sha256 sidecar are required'
dmg_name="$(/usr/bin/basename "$dmg")"
IFS=' ' read -r expected expected_name < "$dmg.sha256"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] && [ "$expected_name" = "$dmg_name" ] || fail 'Invalid checksum sidecar'
actual="$(/usr/bin/shasum -a 256 "$dmg")"
[ "${actual%% *}" = "$expected" ] || fail 'DMG checksum mismatch; nothing was installed'
/usr/bin/hdiutil verify "$dmg" >&2
/bin/mkdir "$work/mount"
/usr/bin/hdiutil attach "$dmg" -readonly -nobrowse -mountpoint "$work/mount" >&2
mounted=1
source_app="$work/mount/MacFan.app"
[ -d "$source_app" ] || fail 'The DMG does not contain MacFan.app'
identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$source_app/Contents/Info.plist")"
[ "$identifier" = io.github.timmyagentic.macfan ] || fail 'Unexpected app bundle identifier'
/usr/bin/codesign --verify --deep --strict "$source_app" >&2
if ! /usr/sbin/spctl --assess --type execute "$source_app" >&2; then
  printf 'MacFan: this development app is not Gatekeeper-approved. System security settings are unchanged; macOS may require user approval before launch.\n' >&2
fi

app_parent="$user_root/Applications"
destination="$app_parent/MacFan.app"
/bin/mkdir -p "$app_parent"
[ ! -L "$destination" ] || fail 'Existing MacFan.app is a symlink; it was not replaced'
same=0
if [ -e "$destination" ]; then
  [ -d "$destination" ] || fail 'Existing MacFan.app is not an application directory'
  existing_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$destination/Contents/Info.plist")"
  [ "$existing_id" = "$identifier" ] || fail 'Existing application has a different identifier; it was not replaced'
  if /usr/bin/codesign --verify --deep --strict "$destination" 2>/dev/null; then
    old_hash="$(/usr/bin/codesign -d --verbose=4 "$destination" 2>&1 | /usr/bin/sed -n 's/^CDHash=//p')"
    new_hash="$(/usr/bin/codesign -d --verbose=4 "$source_app" 2>&1 | /usr/bin/sed -n 's/^CDHash=//p')"
    if [ -n "$old_hash" ] && [ "$old_hash" = "$new_hash" ]; then same=1; fi
  fi
fi
if [ "$same" -eq 0 ]; then
  stage="$(/usr/bin/mktemp -d "$app_parent/.macfan-stage.XXXXXXXX")"
  /usr/bin/ditto "$source_app" "$stage/MacFan.app"
  /usr/bin/codesign --verify --deep --strict "$stage/MacFan.app" >&2
  if [ -e "$destination" ]; then
    backup="$app_parent/MacFan.previous.$(/bin/date +%Y%m%d%H%M%S)-$$.app"
    /bin/mv "$destination" "$backup"
    printf 'MacFan: previous application retained at %s\n' "$backup" >&2
  fi
  /bin/mv "$stage/MacFan.app" "$destination"
fi
executable="$destination/Contents/MacOS/macfan"
"$executable" schema >/dev/null
"$executable" agent install --target "$agent" --user-root "$user_root"
"$executable" doctor
printf 'MacFan: app, CLI, and Agent skill installed. Use %s now; new login shells can use macfan. Reload your Agent skills if needed.\n' "$user_root/.local/bin/macfan" >&2
