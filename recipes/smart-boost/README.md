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

On a **desktop** (no charger or system battery reported) it does nothing and leaves boost as you set it.
Wireless mice and controllers report their own batteries; those are ignored. It writes a journal line
only when it actually flips the switch.

**Before running [the boost experiment](../../experiments/cpu-boost/README.md)**, uninstall this recipe
(or stop it): the experiment refuses to run while something else can flip boost mid-test.

Test without root: `bash recipes/smart-boost/test_smart_boost.sh` (fake sysfs, no hardware touched).

**Trade-off:** boost makes the laptop faster but hotter and louder when plugged in. If you prefer it
cool and quiet, don't install this.

---

**Español:** instala con `sudo bash recipes/smart-boost/install.sh` y quítalo con
`sudo bash recipes/smart-boost/uninstall.sh`. Enciende el turbo con cargador y lo apaga en batería.
Si otra herramienta también controla el turbo, el instalador te avisa pero no la toca.
En una PC de escritorio (sin cargador ni batería del sistema) no hace nada. Ignora las baterías de
mouses y controles inalámbricos, y solo escribe en el journal cuando de verdad cambia el turbo.
Desinstálalo antes de correr el experimento de boost.
