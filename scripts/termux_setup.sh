#!/data/data/com.termux/files/usr/bin/sh
# Termux-side setup for MVwifiAuto Android integration.
#
# Run this inside Termux after cloning the repo:
#   cd ~/wifi-portal-automation
#   sh scripts/termux_setup.sh
#
# This script:
#   1. Creates the wrapper script for Termux:Tasker
#   2. Enables allow-external-apps in termux.properties
#   3. Installs the Python package if not already installed
#   4. Applies Android power-management protections (needs root)
#   5. Prints next steps for Tasker setup

set -e

PREFIX="/data/data/com.termux/files/usr"
HOME_DIR="/data/data/com.termux/files/home"
TASKER_DIR="$HOME_DIR/.termux/tasker"
PROPS_FILE="$HOME_DIR/.termux/termux.properties"

echo "=== MVwifiAuto Termux Setup ==="
echo ""

# --- Step 1: Install Python package ---
echo "[1/5] Checking wpa-android installation..."
if command -v wpa-android >/dev/null 2>&1; then
    echo "  wpa-android already installed: $(which wpa-android)"
else
    echo "  Installing wpa-auto package..."
    if [ ! -f "$HOME_DIR/MVwifiAuto/pyproject.toml" ]; then
        echo "  ERROR: ~/wifi-portal-automation not found. Clone the repo first:"
        echo "    git clone https://github.com/yaconsult/MVwifiAuto.git ~/wifi-portal-automation"
        exit 1
    fi
    cd "$HOME_DIR/MVwifiAuto"
    pip install -e .
    echo "  Installed: $(which wpa-android)"
fi
echo ""

# --- Step 2: Create wrapper scripts ---
echo "[2/5] Installing wrapper scripts for Termux:Tasker..."
ANDROID_SRC="$(cd "$(dirname "$0")/.." && pwd)/android"
if [ ! -f "$ANDROID_SRC/mvwifi_portal" ]; then
    ANDROID_SRC="$HOME_DIR/MVwifiAuto/android"
fi
mkdir -p "$TASKER_DIR"
for name in mvwifi_portal costco_portal costco_probe wifi_nudge cmvwifi_nudge; do
    cp "$ANDROID_SRC/$name" "$TASKER_DIR/$name"
    chmod 755 "$TASKER_DIR/$name"
    echo "  Created: $TASKER_DIR/$name"
done
echo ""

# --- Step 3: Enable allow-external-apps ---
echo "[3/5] Configuring termux.properties..."
mkdir -p "$HOME_DIR/.termux"
if [ -f "$PROPS_FILE" ]; then
    if grep -q "^allow-external-apps" "$PROPS_FILE"; then
        echo "  allow-external-apps already set"
    else
        echo "allow-external-apps = true" >> "$PROPS_FILE"
        echo "  Added allow-external-apps = true"
    fi
else
    echo "allow-external-apps = true" > "$PROPS_FILE"
    echo "  Created $PROPS_FILE with allow-external-apps = true"
fi

# Reload Termux settings if the command exists
if command -v termux-reload-settings >/dev/null 2>&1; then
    termux-reload-settings
    echo "  Reloaded Termux settings"
fi
echo ""

# --- Step 4: Apply Android power-management protections ---
echo "[4/5] Applying Android power-management protections..."
# Without these, Android can kill Termux mid-execution and the
# Termux:Tasker plugin result is lost (Tasker reports error code 2).
if su -c 'id' >/dev/null 2>&1; then
    su -c 'dumpsys deviceidle whitelist +com.termux' >/dev/null 2>&1 \
        && echo "  Termux added to battery optimization whitelist" \
        || echo "  WARNING: could not modify battery whitelist"
    su -c 'settings put global settings_enable_monitor_phantom_procs false' \
        && echo "  Phantom process killer disabled" \
        || echo "  WARNING: could not disable phantom process killer"
    # Lets the wrapper re-apply the setting itself after OS updates.
    su -c 'pm grant com.termux android.permission.WRITE_SECURE_SETTINGS' \
        && echo "  WRITE_SECURE_SETTINGS granted (wrapper self-heal)" \
        || echo "  WARNING: could not grant WRITE_SECURE_SETTINGS"
else
    echo "  Root not available — apply these manually instead:"
    echo "    1. Settings -> Apps -> Termux -> Battery -> Unrestricted"
    echo "    2. From a PC: adb shell settings put global \\"
    echo "       settings_enable_monitor_phantom_procs false"
fi
echo ""

# --- Step 5: Verify ---
echo "[5/5] Verifying setup..."
echo "  Wrapper script:"
# shellcheck disable=SC2012  # ls -la shows permissions/size, intended for user
ls -la "$TASKER_DIR/mvwifi_portal" 2>&1 | sed 's/^/    /'
echo "  wpa-android:"
# shellcheck disable=SC2012  # ls -la shows permissions/size, intended for user
ls -la "$PREFIX/bin/wpa-android" 2>&1 | sed 's/^/    /'
if su -c 'id' >/dev/null 2>&1; then
    echo "  Termux in battery whitelist:"
    su -c 'dumpsys deviceidle whitelist' 2>/dev/null | grep -i termux | sed 's/^/    /'
    echo "  Phantom process killer (want: false):"
    su -c 'settings get global settings_enable_monitor_phantom_procs' 2>&1 | sed 's/^/    /'
fi
echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Grant RUN_COMMAND permission to Tasker:"
echo "     Android Settings -> Apps -> Tasker -> Permissions"
echo "     -> Additional permissions -> Run commands in Termux environment"
echo ""
echo "  2. Import the Tasker project:"
echo "     From your PC: adb push android/MVwifiAuto-Termux.prj.xml /sdcard/Tasker/projects/"
echo "     In Tasker: long-press bottom nav -> Import Project -> MVwifiAuto-Termux"
echo ""
echo "  3. Test: run the 'MVW_ConnectAndRun' task in Tasker"
