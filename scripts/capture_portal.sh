#!/usr/bin/env bash
# capture_portal.sh — capture a captive portal's structure on Linux.
#
# Run at the hotspot while connected to the portal network, BEFORE
# accepting the terms.  Then accept the portal in a browser (watch
# dev tools Network tab for the POST), and run again with --post to
# verify internet access.
#
# Usage:
#   ./scripts/capture_portal.sh [OUTDIR]      # pre-accept capture
#   ./scripts/capture_portal.sh --post [DIR]  # post-accept verify
#
# OUTDIR defaults to portal_capture_<timestamp>.  With --post, DIR
# defaults to the newest portal_capture_* directory.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBE_URL="http://1.1.1.1/"
VERIFY_URL="http://detectportal.firefox.com/success.txt"

usage() {
    sed -n '2,12p' "$0"
    exit "${1:-0}"
}

newest_capture_dir() {
    # Newest portal_capture_* dir in the repo, or empty if none.
    find "$REPO_DIR" -maxdepth 1 -type d -name 'portal_capture_*' \
        -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2- || true
}

run_analyzer() {
    local outdir="$1"
    (cd "$REPO_DIR" && uv run wpa-analyze-portal \
        --probe-url "$PROBE_URL" \
        --save-html \
        --html-path "$outdir/portal.html" \
        --output "$outdir/report.txt")
}

capture_network_state() {
    local outdir="$1"
    nmcli device status > "$outdir/device_status.txt" 2>&1 || true
    nmcli -t -f active,ssid dev wifi 2>/dev/null \
        | grep '^yes' > "$outdir/connected_ssid.txt" || true
    nmcli device wifi list > "$outdir/wifi_scan.txt" 2>&1 || true
}

capture_http() {
    local outdir="$1"
    # Raw probe: headers + body, no redirect follow
    curl -sv --max-redirs 0 "$PROBE_URL" \
        -o "$outdir/probe_body.html" 2> "$outdir/probe_curl.txt" || true
    # Followed: full redirect chain + final portal page
    curl -sLv "$PROBE_URL" \
        -o "$outdir/portal_follow.html" 2> "$outdir/follow_curl.txt" || true
}

do_capture() {
    local outdir="$1"
    mkdir -p "$outdir"
    echo "Capturing portal structure to $outdir ..."
    capture_network_state "$outdir"
    capture_http "$outdir"
    run_analyzer "$outdir" || true
    echo ""
    echo "Capture written to: $outdir"
    echo "Files: report.txt, portal.html, probe_curl.txt,"
    echo "       portal_follow.html, follow_curl.txt, wifi_scan.txt"
    echo ""
    echo "Next: accept the portal in a browser, then run:"
    echo "  $0 --post $outdir"
}

do_post() {
    local outdir="$1"
    echo "Post-accept check (dir: $outdir) ..."
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$VERIFY_URL" || echo 000)"
    echo "Internet verify ($VERIFY_URL): HTTP $code"
    run_analyzer "$outdir" || true
    if [[ "$code" == "200" ]]; then
        echo "Internet is working — portal accepted."
    else
        echo "WARNING: no internet yet (HTTP $code)."
    fi
}

main() {
    local post=0
    local outdir=""
    for arg in "$@"; do
        case "$arg" in
            --post) post=1 ;;
            -h|--help) usage 0 ;;
            -*) echo "Unknown option: $arg" >&2; usage 1 ;;
            *) outdir="$arg" ;;
        esac
    done

    if [[ $post -eq 1 ]]; then
        [[ -z "$outdir" ]] && outdir="$(newest_capture_dir)"
        [[ -z "$outdir" ]] && { echo "No capture dir found" >&2; exit 1; }
        do_post "$outdir"
    else
        [[ -z "$outdir" ]] && outdir="$REPO_DIR/portal_capture_$(date +%Y%m%d_%H%M%S)"
        do_capture "$outdir"
    fi
}

main "$@"
