#!/bin/bash
# Install WiFi Portal Automation as a user systemd service

set -e

echo "=== WiFi Portal Automation Installer ==="

# Resolve the repo root (directory containing this script)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Repo directory: $REPO_DIR"

# Check prerequisites
echo "Checking prerequisites..."

# Check for NetworkManager
if ! command -v nmcli &> /dev/null; then
    echo "ERROR: NetworkManager (nmcli) not found. Install with: sudo dnf install NetworkManager"
    exit 1
fi

# Check for python3-dbus
if ! python3 -c "import dbus" 2>/dev/null; then
    echo "ERROR: python3-dbus not found. Install with: sudo dnf install python3-dbus"
    exit 1
fi

# Check for uv
if ! command -v uv &> /dev/null; then
    echo "ERROR: uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "Prerequisites OK"

# Setup virtual environment with system site packages
echo "Setting up virtual environment..."
cd "$REPO_DIR"
if [ ! -d ".venv" ]; then
    uv venv --system-site-packages
fi

# Install dependencies and package
echo "Installing dependencies..."
uv sync

# Install the package in editable mode
echo "Installing wpa-auto..."
uv pip install -e .

# Check if installation worked
if ! uv run wpa-auto --help &>/dev/null; then
    echo "ERROR: Installation failed"
    exit 1
fi

echo "Package installed successfully"

# Install systemd user services
echo "Installing systemd user services..."
mkdir -p ~/.config/systemd/user
cp systemd/wpa-auto.service ~/.config/systemd/user/
cp systemd/wpa-auto-resume.service ~/.config/systemd/user/

# Get the actual path for ExecStart and update service file
BIN_PATH="$HOME/.local/bin"
mkdir -p "$BIN_PATH"

# Create wrapper script (uses repo directory, not a hardcoded path)
WRAPPER="$BIN_PATH/wpa-auto"
cat > "$WRAPPER" << EOF
#!/bin/bash
# Wrapper script to run wpa-auto
cd "$REPO_DIR"
export PYTHONPATH="$REPO_DIR/src"
exec python3 -m wifi_portal_automation.cli "\$@"
EOF
chmod +x "$WRAPPER"

# Update service files to use the correct path
sed -i "s|%h/.local/bin/wpa-auto|$WRAPPER|g" ~/.config/systemd/user/wpa-auto.service
sed -i "s|%h/.local/bin/wpa-auto|$WRAPPER|g" ~/.config/systemd/user/wpa-auto-resume.service

# Reload systemd
systemctl --user daemon-reload

echo ""
echo "=== Installation Complete ==="
echo ""
echo "To start the service:"
echo "  systemctl --user start wpa-auto"
echo ""
echo "To enable auto-start on login:"
echo "  systemctl --user enable wpa-auto"
echo ""
echo "To enable resume check (after suspend/hibernate):"
echo "  systemctl --user enable wpa-auto-resume"
echo ""
echo "To check status:"
echo "  systemctl --user status wpa-auto"
echo "  journalctl --user -u wpa-auto -f"
echo ""
echo "To run once manually (for testing):"
echo "  wpa-auto --once --verbose"
echo ""
