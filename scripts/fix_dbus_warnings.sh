#!/bin/bash
# Fix D-Bus warnings on Raspberry Pi
# Run this script to suppress D-Bus session bus warnings

echo "🔧 Fixing D-Bus warnings for Bullen Audio Router..."

# Create systemd user directory if it doesn't exist
sudo mkdir -p /run/user/$(id -u)
sudo chown $(id -u):$(id -g) /run/user/$(id -u)

# Set proper permissions
sudo chmod 755 /run/user/$(id -u)

# Create a dummy D-Bus session for headless operation
export DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"

# Add to bashrc for permanent fix
if ! grep -q "DBUS_SESSION_BUS_ADDRESS" ~/.bashrc; then
    echo 'export DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"' >> ~/.bashrc
    echo "✅ Added D-Bus environment variable to ~/.bashrc"
fi

# Update systemd service to include D-Bus fix
if [ -f "/etc/systemd/system/bullen.service" ]; then
    sudo sed -i '/\[Service\]/a Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null"' /etc/systemd/system/bullen.service
    sudo systemctl daemon-reload
    echo "✅ Updated systemd service with D-Bus fix"
fi

echo "🎉 D-Bus warnings should now be suppressed"
echo "💡 Restart Bullen service: sudo systemctl restart bullen"
