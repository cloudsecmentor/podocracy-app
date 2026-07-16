#!/usr/bin/env bash
# Build a double-clickable Podocracy.app that wraps the desktop runtime.
#
# The generated bundle is self-contained: it embeds the GUI runtime and the shared
# launcher logic, so it can be zipped and handed to a beginner. Run this on macOS to
# also generate the .icns app icon (needs `sips` and `iconutil`, both built into macOS).
#
# Usage:
#   ./scripts/make-macos-app.sh [output_dir]   # default output dir: ./dist
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${1:-$ROOT_DIR/dist}"
APP_NAME="Podocracy"
APP_DIR="$OUT_DIR/${APP_NAME}.app"
CONTENTS="$APP_DIR/Contents"
ICON_SRC="$ROOT_DIR/apps/web/icon-512.png"

VERSION_RAW="$(awk -F': *' '/^version:/ {print $2}' "$ROOT_DIR/release.yaml" 2>/dev/null | tr -d '"'"'"'[:space:]' || true)"
VERSION="${VERSION_RAW#v}"
[[ -z "$VERSION" ]] && VERSION="0.0.0"

for f in "$SCRIPT_DIR/podocracy-app-runtime.sh" "$SCRIPT_DIR/_launch-common.sh"; do
  if [[ ! -f "$f" ]]; then
    printf 'Missing required file: %s\n' "$f" >&2
    exit 1
  fi
done

printf 'Building %s (version %s)...\n' "$APP_DIR" "$VERSION"
rm -rf "$APP_DIR"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

# Shared runtime + launcher logic live inside the bundle so it stays self-contained.
cp "$SCRIPT_DIR/podocracy-app-runtime.sh" "$CONTENTS/Resources/podocracy-app-runtime.sh"
cp "$SCRIPT_DIR/_launch-common.sh" "$CONTENTS/Resources/_launch-common.sh"
chmod +x "$CONTENTS/Resources/podocracy-app-runtime.sh"

# Launcher stub that macOS runs when the app is opened.
cat > "$CONTENTS/MacOS/${APP_NAME}" <<'STUB'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec /bin/bash "$DIR/podocracy-app-runtime.sh"
STUB
chmod +x "$CONTENTS/MacOS/${APP_NAME}"

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key>
	<string>${APP_NAME}</string>
	<key>CFBundleDisplayName</key>
	<string>${APP_NAME}</string>
	<key>CFBundleIdentifier</key>
	<string>com.cloudsecmentor.podocracy</string>
	<key>CFBundleVersion</key>
	<string>${VERSION}</string>
	<key>CFBundleShortVersionString</key>
	<string>${VERSION}</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleExecutable</key>
	<string>${APP_NAME}</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>LSMinimumSystemVersion</key>
	<string>10.15</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>LSUIElement</key>
	<false/>
</dict>
</plist>
PLIST

make_icns() {
  local src="$1" out="$2"
  command -v sips >/dev/null 2>&1 || return 1
  command -v iconutil >/dev/null 2>&1 || return 1
  [[ -f "$src" ]] || return 1

  local iconset
  iconset="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$iconset"
  local size
  for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$src" --out "$iconset/icon_${size}x${size}.png" >/dev/null 2>&1 || return 1
    sips -z "$((size * 2))" "$((size * 2))" "$src" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null 2>&1 || return 1
  done
  iconutil -c icns "$iconset" -o "$out" >/dev/null 2>&1 || return 1
}

if make_icns "$ICON_SRC" "$CONTENTS/Resources/AppIcon.icns"; then
  printf 'App icon generated from %s\n' "$ICON_SRC"
else
  printf 'Note: skipped app icon (needs macOS sips/iconutil and %s). The app still works with a default icon.\n' "$ICON_SRC"
fi

printf '\nDone: %s\n' "$APP_DIR"
printf 'Try it:  open "%s"\n' "$APP_DIR"
printf 'Distribute: zip it, or (recommended) codesign + notarize before sharing widely.\n'
printf 'Unsigned apps: first launch needs right-click > Open (see docs/desktop-onboarding.md).\n'
