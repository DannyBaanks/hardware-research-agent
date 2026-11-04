#!/bin/bash
# sudo bash install.sh   — undo with: sudo bash uninstall.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run: sudo bash install.sh"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
# Other tools that also set boost would fight this one. Report them; never edit them.
others=$(grep -lsE 'cpufreq/boost|no_turbo' /etc/tmpfiles.d/* /etc/systemd/system/*.service /etc/rc.local 2>/dev/null | grep -v ihra-smart-boost || true)
for t in tlp auto-cpufreq tuned system76-power; do systemctl is-active --quiet "$t" 2>/dev/null && others="$others $t.service(active)"; done
if [ -n "$others" ]; then
  echo "WARNING: these also control CPU boost and may override smart boost:"; for o in $others; do echo "  - $o"; done
  echo "Disable or remove them yourself if you want smart boost to be the only owner (ask your agent first)."
fi
install -m 0755 "$HERE/ihra-smart-boost" /usr/local/sbin/ihra-smart-boost
install -m 0644 "$HERE/90-ihra-smart-boost.rules" /etc/udev/rules.d/90-ihra-smart-boost.rules
install -m 0644 "$HERE/ihra-smart-boost.service" /etc/systemd/system/ihra-smart-boost.service
systemctl daemon-reload; udevadm control --reload
systemctl enable --quiet --now ihra-smart-boost.service
journalctl -t ihra-smart-boost -n 1 --no-pager -o cat 2>/dev/null || true
echo "DONE. Test: unplug the charger, wait 2 s, run: journalctl -t ihra-smart-boost -n 3"
