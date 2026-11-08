#!/bin/bash
# PC-side verification of the Android-side MVwifiAuto prerequisites.
#
# Run from anywhere with the phone connected via USB:
#   ./scripts/verify_android.sh [--json|--markdown]
#
# Checks every setting the system depends on, so when the auto-connect
# stops working after an OS update or config change, this shows what
# regressed. Exit code: 0 = all required checks pass, 1 = failures,
# 2 = cannot run checks (adb missing or no device).

set -u

FORMAT="text"
case "${1:-}" in
    --json) FORMAT="json" ;;
    --markdown) FORMAT="markdown" ;;
    "") ;;
    *)
        echo "Usage: $0 [--json|--markdown]" >&2
        exit 2
        ;;
esac

TERMUX_HOME="/data/data/com.termux/files/home"
TASKER_DIR="$TERMUX_HOME/.termux/tasker"
PROPS_FILE="$TERMUX_HOME/.termux/termux.properties"

# Results accumulate as: STATUS<TAB>check name<TAB>detail
RESULTS=()

record() {
    RESULTS+=("$1"$'\t'"$2"$'\t'"$3")
}

adb_sh() {
    adb shell "$@" 2>/dev/null
}

adb_su() {
    adb shell "su -c '$*'" 2>/dev/null
}

# --- Prerequisite gates ---------------------------------------------------

if ! command -v adb >/dev/null 2>&1; then
    record FAIL "adb installed" "adb not found in PATH"
elif ! adb devices | grep -q "device$"; then
    record FAIL "device connected" "no device; enable USB debugging"
fi

if [ "${#RESULTS[@]}" -gt 0 ]; then
    # Cannot run any device checks; emit report and bail out.
    FAILURES=1
else

# --- Device checks --------------------------------------------------------

HAVE_ROOT=0
if adb_su "id" | grep -q "uid=0"; then
    HAVE_ROOT=1
    record PASS "root access" "su works"
else
    record WARN "root access" "no su; file/permission checks skipped"
fi

if adb_sh "pm path com.termux" | grep -q "package:"; then
    VER=$(adb_sh "dumpsys package com.termux | grep versionName" | head -1 | tr -d ' \r')
    record PASS "Termux installed" "${VER:-unknown version}"
else
    record FAIL "Termux installed" "package com.termux not found"
fi

if adb_sh "pm path com.termux.tasker" | grep -q "package:"; then
    record PASS "Termux:Tasker installed" ""
else
    record FAIL "Termux:Tasker installed" "package com.termux.tasker not found"
fi

if adb_sh "pm path net.dinglisch.android.taskerm" | grep -q "package:"; then
    record PASS "Tasker installed" ""
else
    record FAIL "Tasker installed" "package net.dinglisch.android.taskerm not found"
fi

if adb_sh "dumpsys deviceidle whitelist" | grep -qi "com.termux"; then
    record PASS "Termux battery exempt" "in deviceidle whitelist"
else
    record FAIL "Termux battery exempt" \
        "not whitelisted; fix: adb shell dumpsys deviceidle whitelist +com.termux"
fi

PHANTOM=$(adb_sh "settings get global settings_enable_monitor_phantom_procs" | tr -d '\r')
if [ "$PHANTOM" = "false" ]; then
    record PASS "phantom process killer" "disabled"
else
    record FAIL "phantom process killer" \
        "enabled ($PHANTOM); fix: adb shell settings put global settings_enable_monitor_phantom_procs false"
fi

if adb_sh "dumpsys package com.termux" \
        | grep -q "WRITE_SECURE_SETTINGS: granted=true"; then
    record PASS "Termux WRITE_SECURE_SETTINGS" "granted (wrapper can self-heal)"
else
    record WARN "Termux WRITE_SECURE_SETTINGS" \
        "not granted; fix: adb shell pm grant com.termux android.permission.WRITE_SECURE_SETTINGS"
fi

if adb_sh "dumpsys package net.dinglisch.android.taskerm | grep RUN_COMMAND" \
        | grep -q "granted=true"; then
    record PASS "Tasker RUN_COMMAND" "granted"
else
    record FAIL "Tasker RUN_COMMAND" \
        "not granted; Settings -> Apps -> Tasker -> Additional permissions"
fi

if [ "$HAVE_ROOT" -eq 1 ]; then
    if adb_su "grep -q '^allow-external-apps = true' $PROPS_FILE"; then
        record PASS "allow-external-apps" "set in termux.properties"
    else
        record FAIL "allow-external-apps" \
            "missing; add 'allow-external-apps = true' to $PROPS_FILE"
    fi

    if adb_su "test -x $TASKER_DIR/mvwifi_portal"; then
        if adb_su "grep -q mvwifi_history.log $TASKER_DIR/mvwifi_portal"; then
            record PASS "wrapper script" \
                "present, executable, hardened + history logging"
        elif adb_su "grep -q termux-wake-lock $TASKER_DIR/mvwifi_portal"; then
            record WARN "wrapper script" \
                "wake-lock version but no history logging; re-run deploy_android.sh"
        else
            record WARN "wrapper script" \
                "present but old version (no wake lock); re-run deploy_android.sh"
        fi
    else
        record FAIL "wrapper script" "missing or not executable at $TASKER_DIR/mvwifi_portal"
    fi

    for wrapper in costco_portal costco_probe wifi_nudge cmvwifi_nudge shguest_probe; do
        if adb_su "test -x $TASKER_DIR/$wrapper"; then
            record PASS "$wrapper wrapper" "present and executable"
        else
            record WARN "$wrapper wrapper" \
                "missing; re-run deploy_android.sh"
        fi
    done

    if adb_su "test -f /data/data/com.termux/files/usr/bin/wpa-android"; then
        record PASS "wpa-android installed" "in Termux bin"
    else
        record FAIL "wpa-android installed" "not in Termux bin; run termux_setup.sh"
    fi

    for module in costco_portal costco_probe portal_probe wifi_nudge root_shell; do
        if adb_su "test -f $TERMUX_HOME/MVwifiAuto/src/wifi_portal_automation/$module.py"; then
            record PASS "$module module" "in ~/wifi-portal-automation clone"
        else
            record WARN "$module module" \
                "not in ~/wifi-portal-automation clone; run 'git pull' in Termux"
        fi
    done
fi

if adb_sh "test -f /sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml"; then
    record PASS "Tasker XML pushed" "/sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml"
else
    record WARN "Tasker XML pushed" "not found in /sdcard/Tasker/projects/"
fi

fi

# --- Output ----------------------------------------------------------------

FAILURES=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^FAIL" || true)
WARNINGS=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^WARN" || true)

emit_text() {
    local r status name detail
    for r in "${RESULTS[@]}"; do
        status="${r%%	*}"
        r="${r#*	}"
        name="${r%%	*}"
        detail="${r#*	}"
        if [ -n "$detail" ]; then
            printf "%-5s %s: %s\n" "$status" "$name" "$detail"
        else
            printf "%-5s %s\n" "$status" "$name"
        fi
    done
    echo ""
    echo "Result: $(( ${#RESULTS[@]} - FAILURES - WARNINGS )) passed," \
        "$FAILURES failed, $WARNINGS warnings"
}

emit_json() {
    local r status name detail first=1
    printf '{"checks":['
    for r in "${RESULTS[@]}"; do
        status="${r%%	*}"
        r="${r#*	}"
        name="${r%%	*}"
        detail="${r#*	}"
        detail="${detail//\"/\\\"}"
        [ "$first" -eq 0 ] && printf ','
        first=0
        printf '{"name":"%s","status":"%s","detail":"%s"}' \
            "$name" "$status" "$detail"
    done
    printf '],"failures":%s,"warnings":%s}\n' "$FAILURES" "$WARNINGS"
}

emit_markdown() {
    local r status name detail
    echo "| Check | Status | Detail |"
    echo "|---|---|---|"
    for r in "${RESULTS[@]}"; do
        status="${r%%	*}"
        r="${r#*	}"
        name="${r%%	*}"
        detail="${r#*	}"
        printf "| %s | %s | %s |\n" "$name" "$status" "$detail"
    done
    echo ""
    echo "**Failures: $FAILURES, warnings: $WARNINGS**"
}

case "$FORMAT" in
    json) emit_json ;;
    markdown) emit_markdown ;;
    *) emit_text ;;
esac

[ "$FAILURES" -eq 0 ] && exit 0 || exit 1
