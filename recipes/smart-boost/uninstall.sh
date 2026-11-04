#!/bin/bash
# sudo bash uninstall.sh — removes only the three files install.sh added.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run: sudo bash uninstall.sh"; exit 1; }
systemctl disable --quiet --now ihra-smart-boost.service 2>/dev/null || true
rm -f /etc/systemd/system/ihra-smart-boost.service /etc/udev/rules.d/90-ihra-smart-boost.rules /usr/local/sbin/ihra-smart-boost
systemctl daemon-reload; udevadm control --reload
echo "Removed. The boost switch keeps its current value until reboot; your other power tools manage it from now on."
