# Recipe: smart boost (turbo on with charger, off on battery)

Optional, **you** install it; IHRA never does. Use it only after
[the boost experiment](../../experiments/cpu-boost/README.md) shows boost helps on your machine.

```bash
sudo bash recipes/smart-boost/install.sh      # install
journalctl -t ihra-smart-boost -n 5          # see every switch it made
sudo bash recipes/smart-boost/uninstall.sh    # remove (deletes only the 3 files it added)
```

It adds a udev rule (runs when a charger is plugged or unplugged, including USB-C), a boot-time
systemd oneshot, and a 20-line shell script. Works with `cpufreq/boost` (AMD, generic) and Intel
`intel_pstate/no_turbo`. If another tool (tlp, auto-cpufreq, a tmpfiles rule…) also sets boost, the
installer **warns and lists it but does not touch it**: decide with your agent which one should own it.

Test without root: `bash recipes/smart-boost/test_smart_boost.sh` (fake sysfs, no hardware touched).

**Trade-off:** boost makes the laptop faster but hotter and louder when plugged in. If you prefer it
cool and quiet, don't install this.

---

**Español:** instala con `sudo bash recipes/smart-boost/install.sh` y quítalo con
`sudo bash recipes/smart-boost/uninstall.sh`. Enciende el turbo con cargador y lo apaga en batería.
Si otra herramienta también controla el turbo, el instalador te avisa pero no la toca.
