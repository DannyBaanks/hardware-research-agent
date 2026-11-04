#!/bin/bash
# Runs ihra-smart-boost against fake sysfs trees. No root, no real hardware touched.
set -u; HERE="$(cd "$(dirname "$0")" && pwd)"; fail=0
case_(){ # name mains_online usb_online(or -) start_boost expected
  R=$(mktemp -d); mkdir -p $R/sys/devices/system/cpu/cpufreq $R/sys/class/power_supply/ACAD $R/sys/class/power_supply/BAT0
  echo "$4" > $R/sys/devices/system/cpu/cpufreq/boost
  echo Mains > $R/sys/class/power_supply/ACAD/type; echo "$2" > $R/sys/class/power_supply/ACAD/online
  echo Battery > $R/sys/class/power_supply/BAT0/type
  if [ "$3" != - ]; then mkdir -p $R/sys/class/power_supply/usbc; echo USB > $R/sys/class/power_supply/usbc/type; echo "$3" > $R/sys/class/power_supply/usbc/online; fi
  SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost"; got=$(cat $R/sys/devices/system/cpu/cpufreq/boost); rm -rf $R
  if [ "$got" = "$5" ]; then echo "PASS $1"; else echo "FAIL $1: got $got want $5"; fail=1; fi; }
case_ "charger plugged turns boost on"      1 - 0 1
case_ "on battery turns boost off"          0 - 1 0
case_ "usb-c charger only turns boost on"   0 1 0 1
case_ "usb port without power stays off"    0 0 1 0
case_ "already correct is left alone"       1 - 1 1
# Intel: no_turbo is inverted (0 = boost on)
R=$(mktemp -d); mkdir -p $R/sys/devices/system/cpu/intel_pstate $R/sys/class/power_supply/AC; echo 1 > $R/sys/devices/system/cpu/intel_pstate/no_turbo
echo Mains > $R/sys/class/power_supply/AC/type; echo 1 > $R/sys/class/power_supply/AC/online
SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost"; [ "$(cat $R/sys/devices/system/cpu/intel_pstate/no_turbo)" = 0 ] && echo "PASS intel charger sets no_turbo=0" || { echo "FAIL intel charger"; fail=1; }
echo 0 > $R/sys/class/power_supply/AC/online; SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost"; [ "$(cat $R/sys/devices/system/cpu/intel_pstate/no_turbo)" = 1 ] && echo "PASS intel battery sets no_turbo=1" || { echo "FAIL intel battery"; fail=1; }; rm -rf $R
R=$(mktemp -d); SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost" && echo "PASS missing boost knob exits cleanly" || { echo FAIL missing knob; fail=1; }; rm -rf $R
# Desktop: no power_supply entries -> boost left exactly as the user set it (both values).
for start in 0 1; do R=$(mktemp -d); mkdir -p $R/sys/devices/system/cpu/cpufreq $R/sys/class/power_supply; echo $start > $R/sys/devices/system/cpu/cpufreq/boost
SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost"; [ "$(cat $R/sys/devices/system/cpu/cpufreq/boost)" = $start ] && echo "PASS desktop leaves boost=$start alone" || { echo "FAIL desktop boost=$start"; fail=1; }; rm -rf $R; done
# Desktop with a wireless mouse battery (scope=Device) is still a desktop.
R=$(mktemp -d); mkdir -p $R/sys/devices/system/cpu/cpufreq $R/sys/class/power_supply/hidpp_battery_0; echo 1 > $R/sys/devices/system/cpu/cpufreq/boost
echo Battery > $R/sys/class/power_supply/hidpp_battery_0/type; echo Device > $R/sys/class/power_supply/hidpp_battery_0/scope; echo Discharging > $R/sys/class/power_supply/hidpp_battery_0/status
SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost"; [ "$(cat $R/sys/devices/system/cpu/cpufreq/boost)" = 1 ] && echo "PASS mouse battery is not a laptop battery" || { echo "FAIL mouse battery"; fail=1; }; rm -rf $R
# Laptop exposing only its battery: status decides.
for st in Discharging:0 Charging:1; do R=$(mktemp -d); mkdir -p $R/sys/devices/system/cpu/cpufreq $R/sys/class/power_supply/BAT0
echo $((1 - ${st#*:})) > $R/sys/devices/system/cpu/cpufreq/boost; echo Battery > $R/sys/class/power_supply/BAT0/type; echo ${st%:*} > $R/sys/class/power_supply/BAT0/status
SYSFS_ROOT=$R sh "$HERE/ihra-smart-boost"; [ "$(cat $R/sys/devices/system/cpu/cpufreq/boost)" = ${st#*:} ] && echo "PASS battery-only ${st%:*} sets boost=${st#*:}" || { echo "FAIL battery-only ${st%:*}"; fail=1; }; rm -rf $R; done
exit $fail
