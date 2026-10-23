#!/bin/bash
# Install a verified AgenticFan DMG and its Agent integration. No Python required.
set -euo pipefail

repository="timmyagentic/agentic-fan"
artifact="AgenticFan-Apple-Silicon-development-DMG"
agent="codex"
user_root="$HOME"
run_id=""
dmg=""
work=""
stage=""
mounted=0
official_release=0

fail() { printf 'AgenticFan: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage: bash scripts/install-macos.sh [options]
  --agent codex|claude|both  Agent skill destinations (default: codex)
  --run-id ID               Install from this successful GitHub Actions run
  --dmg PATH                Use a local DMG with its .dmg.sha256 sidecar
  --user-root PATH          User directory (default: current home)
  --help                    Show this help

Installs ~/Applications/AgenticFan.app, ~/.local/bin/agenticfan, and the Agent skill.
Adds a managed PATH block to .zprofile and .bash_profile, backing up originals.
By default downloads the public stable Release anonymously with curl.
Only --run-id development builds require gh with repository access.
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
      printf 'AgenticFan: disk image could not be detached; retained temporary directory %s\n' "$work" >&2
      return
    fi
  fi
  [ -z "$stage" ] || /bin/rm -rf "$stage"
  [ -z "$work" ] || /bin/rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
work="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/agenticfan-install.XXXXXXXX")"

if [ -z "$dmg" ] && [ -n "$run_id" ]; then
  command -v gh >/dev/null || fail 'GitHub CLI is required only for --run-id development builds; omit --run-id to install the public Release.'
  run_info="$(gh api "repos/$repository/actions/runs/$run_id" \
    --jq '[.conclusion, .head_repository.full_name, .path, .event] | @tsv')"
  IFS=$'\t' read -r conclusion source_repo workflow event <<< "$run_info"
  [ "$conclusion" = success ] && [ "$source_repo" = "$repository" ] && \
    [ "$workflow" = .github/workflows/macos-dmg.yml ] || fail 'The selected run is not a successful AgenticFan DMG build'
  case "$event" in push|workflow_dispatch) ;; *) fail 'Only repository push or manual builds are installable' ;; esac
  gh run download "$run_id" --repo "$repository" --name "$artifact" --dir "$work/package"
  shopt -s nullglob
  images=("$work/package"/AgenticFan-*-arm64*.dmg)
  [ "${#images[@]}" -eq 1 ] || fail 'Expected exactly one Apple Silicon DMG in the artifact'
  dmg="${images[0]}"
elif [ -z "$dmg" ]; then
  command -v curl >/dev/null || fail 'curl is required to download the public Release'
  /bin/mkdir "$work/package"
  curl --fail --silent --show-error --location --retry 3 --proto '=https' \
    --output "$work/release.json" "https://api.github.com/repos/$repository/releases/latest"
  release_tag="$(/usr/bin/plutil -extract tag_name raw -o - "$work/release.json")"
  draft="$(/usr/bin/plutil -extract draft raw -o - "$work/release.json")"
  prerelease="$(/usr/bin/plutil -extract prerelease raw -o - "$work/release.json")"
  [ "$draft" = false ] && [ "$prerelease" = false ] || fail 'Expected a published stable release'
  [[ "$release_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Invalid stable release tag'
  dmg_name="AgenticFan-${release_tag#v}-arm64.dmg"
  dmg="$work/package/$dmg_name"
  release_url="https://github.com/$repository/releases/download/$release_tag"
  curl --fail --silent --show-error --location --retry 3 --proto '=https' \
    --output "$dmg" "$release_url/$dmg_name"
  curl --fail --silent --show-error --location --retry 3 --proto '=https' \
    --output "$dmg.sha256" "$release_url/$dmg_name.sha256"
  official_release=1
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
source_app="$work/mount/AgenticFan.app"
[ -d "$source_app" ] || fail 'The DMG does not contain AgenticFan.app'
identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$source_app/Contents/Info.plist")"
[ "$identifier" = io.github.timmyagentic.agenticfan ] || fail 'Unexpected app bundle identifier'
/usr/bin/codesign --verify --deep --strict "$source_app" >&2
if ! /usr/sbin/spctl --assess --type execute "$source_app" >&2; then
  [ "$official_release" -eq 0 ] || fail 'The public Release failed Gatekeeper verification; nothing was installed'
  printf 'AgenticFan: this development app is not Gatekeeper-approved. System security settings are unchanged; macOS may require user approval before launch.\n' >&2
fi

app_parent="$user_root/Applications"
destination="$app_parent/AgenticFan.app"
/bin/mkdir -p "$app_parent"
[ ! -L "$destination" ] || fail 'Existing AgenticFan.app is a symlink; it was not replaced'
same=0
if [ -e "$destination" ]; then
  [ -d "$destination" ] || fail 'Existing AgenticFan.app is not an application directory'
  existing_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$destination/Contents/Info.plist")"
  [ "$existing_id" = "$identifier" ] || fail 'Existing application has a different identifier; it was not replaced'
  if /usr/bin/codesign --verify --deep --strict "$destination" 2>/dev/null; then
    old_hash="$(/usr/bin/codesign -d --verbose=4 "$destination" 2>&1 | /usr/bin/sed -n 's/^CDHash=//p')"
    new_hash="$(/usr/bin/codesign -d --verbose=4 "$source_app" 2>&1 | /usr/bin/sed -n 's/^CDHash=//p')"
    if [ -n "$old_hash" ] && [ "$old_hash" = "$new_hash" ]; then same=1; fi
  fi
fi
if [ "$same" -eq 0 ]; then
  stage="$(/usr/bin/mktemp -d "$app_parent/.agenticfan-stage.XXXXXXXX")"
  /usr/bin/ditto "$source_app" "$stage/AgenticFan.app"
  /usr/bin/codesign --verify --deep --strict "$stage/AgenticFan.app" >&2
  if [ -e "$destination" ]; then
    backup="$app_parent/AgenticFan.previous.$(/bin/date +%Y%m%d%H%M%S)-$$.app"
    /bin/mv "$destination" "$backup"
    printf 'AgenticFan: previous application retained at %s\n' "$backup" >&2
  fi
  /bin/mv "$stage/AgenticFan.app" "$destination"
fi
executable="$destination/Contents/MacOS/agenticfan"
"$executable" schema >/dev/null
"$executable" agent install --target "$agent" --user-root "$user_root"
"$executable" doctor
printf 'AgenticFan: app, CLI, and Agent skill installed. Use %s now; new login shells can use agenticfan. Reload your Agent skills if needed.\n' "$user_root/.local/bin/agenticfan" >&2
