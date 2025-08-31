#!/bin/bash
# Audio Injector Octo health check script
# Based on common issues found in forum discussions

set -e

echo "=== Audio Injector Octo Health Check ==="

# Check if running as root (some checks need it)
if [[ $EUID -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

# 1. Check if Octo modules are loaded
echo "1. Checking kernel modules..."
if lsmod | grep -q "snd_soc_audioinjector_octo"; then
    echo "✓ Audio Injector Octo modules loaded"
else
    echo "✗ Audio Injector Octo modules NOT loaded"
    echo "  Common fix: Run module reload sequence"
fi

# 2. Check for cs42xx8 codec detection
echo "2. Checking codec detection..."
if dmesg | grep -q "cs42xx8.*found device"; then
    echo "✓ CS42xx8 codec detected successfully"
else
    echo "✗ CS42xx8 codec detection issues"
    if dmesg | grep -q "cs42xx8.*failed to get device ID"; then
        echo "  Issue: Failed to get device ID (timing/I2C problem)"
    fi
fi

# 3. Check for I2S sync errors
echo "3. Checking for I2S sync errors..."
if dmesg | grep -q "I2S SYNC error"; then
    echo "✗ I2S SYNC errors detected"
    echo "  This may indicate clock/timing issues"
else
    echo "✓ No I2S SYNC errors found"
fi

# 4. Check ALSA card detection
echo "4. Checking ALSA card detection..."
if aplay -l 2>/dev/null | grep -q "audioinjector"; then
    echo "✓ Audio Injector card detected by ALSA"
    aplay -l | grep "audioinjector"
else
    echo "✗ Audio Injector card NOT detected by ALSA"
fi

# 5. Check modprobe configuration
echo "5. Checking modprobe configuration..."
MODPROBE_CONF="/etc/modprobe.d/audioinjector-octo.conf"
if [[ -f "$MODPROBE_CONF" ]]; then
    echo "✓ Modprobe config exists: $MODPROBE_CONF"
    cat "$MODPROBE_CONF"
else
    echo "✗ Missing modprobe config: $MODPROBE_CONF"
    echo "  Recommended content:"
    echo "  softdep snd_soc_audioinjector_octo_soundcard pre: snd_soc_cs42xx8 snd_soc_cs42xx8_i2c"
fi

# 6. Check device tree overlay
echo "6. Checking device tree configuration..."
if grep -q "audioinjector-addons" /boot/config.txt 2>/dev/null; then
    echo "✓ Audio Injector overlay enabled in /boot/config.txt"
elif grep -q "audioinjector" /boot/firmware/config.txt 2>/dev/null; then
    echo "✓ Audio Injector overlay enabled in /boot/firmware/config.txt"
else
    echo "✗ Audio Injector overlay not found in config.txt"
    echo "  Add: dtoverlay=audioinjector-addons"
fi

# 7. Check for registration failures in dmesg
echo "7. Checking for registration failures..."
if dmesg | grep -q "snd_soc_register_card failed"; then
    echo "✗ Sound card registration failures detected"
    dmesg | grep "snd_soc_register_card failed" | tail -3
else
    echo "✓ No recent registration failures"
fi

# 8. Check I2C tools (if available)
echo "8. Checking I2C bus..."
if command -v i2cdetect >/dev/null 2>&1; then
    echo "I2C devices on bus 1:"
    $SUDO i2cdetect -y 1 2>/dev/null || echo "  Could not scan I2C bus"
else
    echo "  i2c-tools not installed (install with: apt install i2c-tools)"
fi

echo ""
echo "=== Recommendations ==="
echo "If issues found:"
echo "1. Try module reload: sudo modprobe -r snd_soc_audioinjector_octo_soundcard && sudo modprobe snd_soc_audioinjector_octo_soundcard"
echo "2. Check physical connections and power"
echo "3. Verify /boot/config.txt has: dtoverlay=audioinjector-addons"
echo "4. Create modprobe config if missing"
echo "5. Reboot and check again"
