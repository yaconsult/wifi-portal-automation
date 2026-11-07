#!/bin/bash
# PC-side deployment for MVwifiAuto Android integration.
#
# Run from the repo root with the phone connected via USB:
#   ./scripts/deploy_android.sh
#
# This script:
#   1. Pushes the Tasker XML to the phone
#   2. Creates the Termux wrapper script via adb
#   3. Enables allow-external-apps in termux.properties via adb
#   4. Grants RUN_COMMAND permission to Tasker via adb
#   5. Applies Android power-management protections for Termux
#      (battery whitelist, phantom process killer, self-heal grant)
#   6. Verifies the setup
#
# Requires: adb with the phone connected and USB debugging enabled.
# Requires: root (Magisk) for accessing Termux's private files.

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TASKER_XML="$REPO_DIR/android/MVwifiAuto-Termux.prj.xml"
TERMUX_HOME="/data/data/com.termux/files/home"
TASKER_DIR="$TERMUX_HOME/.termux/tasker"
PROPS_FILE="$TERMUX_HOME/.termux/termux.properties"

# Check adb
if ! command -v adb >/dev/null 2>&1; then
    echo "ERROR: adb not found. Install android-tools or platform-tools."
    exit 1
fi

# Check device connected
if ! adb devices | grep -q "device$"; then
    echo "ERROR: No device connected. Enable USB debugging and authorize the connection."
    adb devices
    exit 1
fi

echo "=== MVwifiAuto Android Deployment ==="
echo ""

# Check root
if ! adb shell "su -c 'id'" 2>/dev/null | grep -q "uid=0"; then
    echo "ERROR: Root not available. Enable shell access in Magisk settings."
    exit 1
fi
echo "Root: OK"
echo ""

# Termux app uid — files pushed via su must be owned by it or Termux
# can't modify them later (read/exec is fine either way, but keep
# ownership consistent with what Termux itself would create).
TERMUX_UID=$(adb shell "su -c 'stat -c %u $TERMUX_HOME'" 2>/dev/null | tr -d ' \r')
echo "Termux uid: $TERMUX_UID"
echo ""

# --- Step 1: Push Tasker XML ---
echo "[1/6] Pushing Tasker XML..."
if [ ! -f "$TASKER_XML" ]; then
    echo "  Tasker XML not found. Generating..."
    cd "$REPO_DIR"
    uv run python -m wifi_portal_automation.tasker_gen --termux -o "$TASKER_XML"
fi
adb shell "mkdir -p /sdcard/Tasker/projects/" 2>/dev/null || true
adb push "$TASKER_XML" /sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml
echo "  Pushed to /sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml"
echo ""

# --- Step 2: Create wrapper scripts ---
echo "[2/6] Creating Termux wrapper scripts..."
adb shell "su -c 'mkdir -p $TASKER_DIR'"
for name in mvwifi_portal costco_portal costco_probe wifi_nudge cmvwifi_nudge shguest_probe; do
    adb push "$REPO_DIR/android/$name" "/sdcard/$name.tmp"
    adb shell "su -c 'cp /sdcard/$name.tmp $TASKER_DIR/$name && chmod 755 $TASKER_DIR/$name && chown $TERMUX_UID:$TERMUX_UID $TASKER_DIR/$name && rm /sdcard/$name.tmp'"
    echo "  Created: $TASKER_DIR/$name"
done
echo ""

# --- Step 3: Enable allow-external-apps ---
echo "[3/6] Configuring termux.properties..."
adb shell "su -c 'mkdir -p $TERMUX_HOME/.termux'"
# Check if already set
ALREADY_SET=$(adb shell "su -c 'grep -c \"^allow-external-apps\" $PROPS_FILE 2>/dev/null'" 2>/dev/null || echo "0")
if [ "$ALREADY_SET" = "0" ]; then
    adb shell "su -c 'echo \"allow-external-apps = true\" >> $PROPS_FILE'"
    echo "  Added allow-external-apps = true"
else
    echo "  allow-external-apps already set"
fi
echo ""

# --- Step 4: Grant RUN_COMMAND permission ---
echo "[4/6] Granting RUN_COMMAND permission to Tasker..."
# Try granting via pm (may fail silently if already granted)
adb shell "pm grant net.dinglisch.android.taskerm com.termux.permission.RUN_COMMAND" 2>/dev/null || true
# Verify
PERM_GRANTED=$(adb shell "dumpsys package net.dinglisch.android.taskerm 2>/dev/null | grep 'RUN_COMMAND' | grep -c 'granted=true'" 2>/dev/null || echo "0")
if [ "$PERM_GRANTED" != "0" ]; then
    echo "  Permission granted"
else
    echo "  WARNING: Could not verify permission. Grant manually:"
    echo "    Android Settings -> Apps -> Tasker -> Permissions"
    echo "    -> Additional permissions -> Run commands in Termux environment"
fi
echo ""

# --- Step 5: Apply Android power-management protections ---
echo "[5/6] Applying Android power-management protections..."
# Without these, Android can kill Termux mid-execution and the
# Termux:Tasker plugin result is lost (Tasker reports error code 2).
adb shell "dumpsys deviceidle whitelist +com.termux" >/dev/null 2>&1 \
    && echo "  Termux added to battery optimization whitelist" \
    || echo "  WARNING: could not modify battery whitelist"
adb shell "settings put global settings_enable_monitor_phantom_procs false" \
    && echo "  Phantom process killer disabled" \
    || echo "  WARNING: could not disable phantom process killer"
# Granting WRITE_SECURE_SETTINGS lets the wrapper re-apply the
# phantom-killer setting itself if a future OS update resets it.
if adb shell "pm grant com.termux android.permission.WRITE_SECURE_SETTINGS" 2>/dev/null \
    || adb shell "su -c 'pm grant com.termux android.permission.WRITE_SECURE_SETTINGS'" 2>/dev/null; then
    echo "  WRITE_SECURE_SETTINGS granted to Termux (wrapper self-heal)"
else
    echo "  WARNING: could not grant WRITE_SECURE_SETTINGS (self-heal disabled)"
fi
echo ""

# --- Step 6: Verify ---
echo "[6/6] Verifying deployment..."
echo "  Wrapper scripts:"
adb shell "su -c 'ls -la $TASKER_DIR/mvwifi_portal $TASKER_DIR/costco_portal $TASKER_DIR/costco_probe $TASKER_DIR/wifi_nudge $TASKER_DIR/cmvwifi_nudge $TASKER_DIR/shguest_probe'" 2>&1 | sed 's/^/    /'
echo "  wpa-android:"
adb shell "su -c 'ls -la /data/data/com.termux/files/usr/bin/wpa-android'" 2>&1 | sed 's/^/    /'
echo "  Tasker XML on phone:"
adb shell "ls -la /sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml" 2>&1 | sed 's/^/    /'
echo "  Termux in battery whitelist:"
adb shell "dumpsys deviceidle whitelist | grep -i termux" 2>&1 | sed 's/^/    /'
echo "  Phantom process killer (want: false):"
adb shell "settings get global settings_enable_monitor_phantom_procs" 2>&1 | sed 's/^/    /'
echo ""

echo "=== Deployment complete! ==="
echo ""
echo "Next steps:"
echo "  1. Force-close Termux (Settings -> Apps -> Termux -> Force Stop)"
echo "     and reopen it for termux.properties changes to take effect."
echo "  2. In Tasker: if MVwifiAuto-Termux is already imported, delete"
echo "     it first (long-press its tab -> Delete) AND delete its leftover"
echo "     tasks/profiles — import merges or fails on name collisions."
echo "     Then: long-press bottom nav bar -> Import Project -> select"
echo "     MVwifiAuto-Termux"
echo "  2b. IMPORTANT: exit Tasker gracefully (Back or Exit menu) — the"
echo "      import lives only in memory until Tasker writes its state"
echo "      on a clean exit; force-stop/swipe-kill discards it."
echo "  3. IMPORTANT: open Tasker once after import — imported profile"
echo "     contexts don't register with the monitor until the app opens"
echo "  4. Test: run the 'MVW_ConnectAndRun' task in Tasker"
echo "  5. Enable the 'cmvwifi Auto Connect' profile (green dot in PROFILES tab)"
