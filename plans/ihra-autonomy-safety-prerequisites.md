# Plan — IHRA-04: Prerrequisitos de seguridad para autonomía (expiración de autoridad + rollback verificado acoplado a la ejecución)

## Estado
READY — 6 milestones (M0–M5). Baseline verificado 2026-11-05: rama `main`, HEAD `a7ce064`, árbol limpio, `python3 -m unittest discover -s tests` → **143 tests OK** (≈5.9 s). `powerprofilesctl` presente (`/usr/bin/powerprofilesctl`, perfil actual `performance`); `bwrap` presente.

**Avance 2026-11-06:** M0–M5 implementados y comiteados (ver *Registro de ejecución*). Suite: **191 tests OK**; `sentinel lscpu` → AUTHORIZED exit 0; `sentinel 'powerprofilesctl set balanced'` → DENIED exit 1. La documentación dejó de prometer lo que el código no hace: `AF-09`, `AF-02`, `AF-10` y `AF-12` constan como NO demostrados, y `run_boost_matrix.py` aparece en README/TECHNICAL/SAFETY_CONTRACTS como herramienta manual fuera del contrato. Queda pendiente la **ronda Tier-1 real con humano al TTY**, que es la verificación que pidió Danny y no se puede sustituir con fixtures.

## Objetivo

Cerrar la distancia entre lo que `docs/` prometen y lo que `core/` exige, porque sin expiración de autoridad y sin rollback verificado acoplado a la ejecución ningún modo desatendido es defendible. Hoy `core/sentinel.py:105-110` se auto-declare bloqueado: *"State changes are disabled: authenticated human authorization and an execution-coupled verified rollback are not implemented"*.

Éxito observable:

1. Toda autoridad (TTY y Sentinel) lleva `plan_sha256`, `issued_at`, `expires_at` y token; una autoridad caducada o cuyo digest no coincide produce `DENY` **antes** del dispatch, y se revalida dentro del bucle de muestras.
2. `rollback_plan` deja de ser decorativo: un plan sin rollback verificable es rechazado estáticamente por Sentinel.
3. `rollback_status` es observado, no declarado: toda campaña persiste `prestate.json`, `poststate.json` y `rollback.json` cubiertos por el manifiesto SHA-256; un estado no restaurado produce `ABORTED`/`NOT_DEMONSTRATED` más `fault.json`, y un fault sin resolver bloquea la siguiente campaña.
4. Tier-1 (`powerprofilesctl set`) ejecutable **solo con humano al TTY**, con restauración garantizada por señales y verificación releyendo el estado; en modo desatendido sigue `DENIED` por decisión, no por omisión.

Alcance de acción: escritura en sandbox sintético + lecturas reales de solo lectura.

## Evidencia inspeccionada

- `core/sentinel.py:13-20`: `AuthorizationGrant(verdict, experiment_id, risk_tier, reason, allowed_operations)` — sin digest del plan clasificado, sin `issued_at`/`expires_at`, sin token. Nada prueba que el comando ejecutado sea el que Sentinel autorizó.
- `core/sentinel.py:25-41`: allowlist exacta `lscpu`/`lspci`/`sensors`/`powerprofilesctl get`/`nvidia-smi`, regex Tier-1 `powerprofilesctl set (power-saver|balanced|performance)`, patrones prohibidos (flashrom/nvramtool/wrmsr/SMU/`rm -rf`/`> /sys/...`). `:82-94` deriva el tier y rechaza un `risk_tier` mentiroso.
- `core/authorization.py:16-42`: `PlanAuthorization.__slots__ = ("_plan_sha256","_proposal_sha256","_used","_lock")` — un solo uso ✓, atadura por digest con `hmac.compare_digest` ✓, **sin expiración**. Emisión sólo por `authorize_plan_from_tty` (`:76-110`, `/dev/tty`, teclear `AUTHORIZE <plan_sha256>`); `_issuer` centinela en `:11,26-27` impide forjar el objeto. `:46-48` declara ya que el consentimiento TTY no es identidad criptográfica ni protege de un mismo UID.
- `core/rollback.py:31-44`: alcance fijado a `powerprofilesctl get` / `powerprofilesctl set <perfil>`. `capture_state:46-52` **propaga** `OSError`/`CalledProcessError`, mientras `execute_and_verify_rollback:54-93` los convierte en recibo fallido. Importado en `core/cli.py:21` y exportado en `core/__init__.py:4,15`; grep de call sites: **nunca se invoca**.
- `core/experiment.py:404-449`: `run()` exige `type(authorization) is PlanAuthorization`, bytes canónicos de propuesta, y `authorization.consume(plan_digest, proposal.sha256)` en `:430`; devuelve `DENIED/DENY` sin tocar evidencia ante `ValueError`/`AuthorizationDenied`/`SandboxUnavailableError`/`OSError`. Contrato por recibo en `:371-402` con **igualdad de conjunto** de claves (añadir un campo a `WorkloadReceipt` haría que el runner rechace sus propios recibos). Bucle `:522-661`, manifiesto incremental `:258-285`, directorio privado `:201-210`.
- `core/executor.py:84`: `rollback_status="NOT_REQUIRED_NO_STATE_CHANGE"` hardcodeado en el receipt del dry-run.
- `schemas/experiment.schema.json` exige `rollback_plan` (`restoration_command`, `verification_query`, `expected_restored_state`), `hypothesis_id`, `risk_tier`, `controls`, `workload.repetitions>=3`. grep: **cero** referencias Python a `schemas/` — la obligación es decorativa. `jsonschema 4.26.0` está instalado en esta máquina pero no es dependencia declarada (sin `requirements.txt`/`pyproject.toml`; `package.json` sólo envuelve el CLI).
- Acoples de regresión verificados en la suite: `tests/test_antifake_sentinel.py:58-59` exige `TypeError` si `evaluate_plan` recibe un kwarg extra; `:62` aserta el substring `"authenticated human authorization"` de la razón Tier-1; `tests/test_dry_run_executor.py:22` aserta el literal `NOT_REQUIRED_NO_STATE_CHANGE`; `tests/test_synthetic_experiment.py:358` aserta exactamente 10 líneas `sample-` en el manifiesto.
- `core/recovery.py:22,145,264`: allowlists de nombres (`_BASE_FILES` + regex `^sample-(0[1-5])\.(started|receipt|interrupted|error)\.json$`); un artefacto nuevo no listado ⇒ `INCOMPLETE` garantizado. `:71,102` abre con `O_RDONLY`; `:385` clasifica las 8 campañas históricas como `INCOMPLETE` por no tener `checkpoint.json`.
- `core/cpu_power.py:44,55,61,73`: `except: pass` en sondas de lectura; como prestate eso falla **abierto**.
- Agujero real y fuera del contrato: `experiments/cpu-boost/run_boost_matrix.py:192,207` escribe `/sys/devices/system/cpu/cpufreq/boost` corriendo con `sudo` (`:7,167`), sin Sentinel, sin `PlanAuthorization`, sin `RollbackController` y sin manifiesto. Sí relee antes/después de cada job (`check_switch`, `:205,210`) y restaura en señales (`:190-192` + `restore_on_signals:144-149`), pero su `restore()` **imprime** el readback sin compararlo contra `original`. Su evidencia usa nombre con timestamp por segundo (`:185`). `recipes/smart-boost/install.sh:13-17` instala una regla udev y un servicio que siguen escribiendo el mismo knob.
- Docs a satisfacer: `docs/ARCHITECTURE.md` §2.4 items 3–5 (rechazar plan sin `rollback_plan` verificable; exigir humano en Tier-1/2; grant con expiración y token), §2.5 invariants (validar el grant antes del dispatch; **siempre** invocar `RollbackController` al completar o abortar), §2.6 (prestate a archivo aislado, restauración en orden inverso, verificación activa, recibo firmado), `docs/SAFETY_CONTRACTS.md:42-61` (`RollbackContract` + Emergency Fault State), `docs/SAFETY_CONTRACTS.md:78-80` (`RESOURCE_PRESSURE_ABORT`), `docs/ANTIFAKE_TEST_MATRIX.md:19` (AF-09), `docs/TECHNICAL.md:99`.
- Decisiones del propietario (2026-11-05): eje = seguridad; alcance autónomo = sintético + read-only real; Tier-1 real se ejercita con humano al TTY; `run_boost_matrix.py` se documenta y se difiere a IHRA-05.
- Pendiente heredado: IHRA-03 M5 no documentó crash/checkpoint/inspector en `README.md`, `README_es.md` ni `docs/SAFETY_CONTRACTS.md` (grep sin coincidencias).

## Alcance

- Expiración con reloj inyectable y revalidación por muestra; token HMAC-SHA256 por proceso.
- Atadura digest→grant→ejecución: sólo puede ejecutarse el comando cuyo digest clasificó Sentinel.
- Validador stdlib-only del plan que evalúa Sentinel, con igualdad probada contra `schemas/experiment.schema.json`.
- `RollbackContract` invocado por el runner, con artefactos persistidos dentro del manifiesto y clasificación en la inspección.
- Tier-1 condicionado a autoridad humana sin consumir + rollback validado + prestate/poststate; Emergency Fault State bloqueante.
- Corrección documental de `ARCHITECTURE.md`, `SAFETY_CONTRACTS.md`, `TECHNICAL.md`, `ANTIFAKE_TEST_MATRIX.md`, READMEs y encabezado de `ROADMAP.md`.

## No objetivos

- No habilitar Tier-1/Tier-2 desatendido; no tocar `run_boost_matrix.py` ni `recipes/smart-boost` (sólo documentar su estado).
- No implementar polling térmico/memoria ni `RESOURCE_PRESSURE_ABORT`; no firmar `RollbackReceipt` (no hay material clave ni verificador); no implementar hooks de restauración en orden inverso multi-intervención.
- No crear `core/hypothesis.py` ni `core/recorder.py`; no redactar evidencia.
- No añadir orquestador, daemon, bucle multi-experimento ni `resume` (IHRA-05/06).
- No añadir dependencias de terceros; no introducir `jsonschema` aunque esté instalado.
- No modificar ni reescribir campañas, recibos ni manifiestos existentes; no asumir resistencia a pérdida de energía.
- No permitir TTL, tier o allowlist configurables por el llamante, por CLI o por entorno.
- No hacer push.

## Milestones

### M0 — Congelar contrato de autoridad y baseline

- **Objetivo:** Fijar por escrito la semántica de expiración y del token antes de tocar `core/`, y registrar el baseline y el trabajo diferido.
- **Rutas previstas:** `docs/SAFETY_CONTRACTS.md` (nueva §6), `docs/ARCHITECTURE.md` §2.4/§2.5, `plans/ihra-autonomy-safety-prerequisites.md` (este archivo).
- **Pasos:**
  1. Registrar baseline: 143 tests OK, HEAD `a7ce064`, rama `main`.
  2. Definir expiración como **deadline monotónico** (`time.monotonic`), con `issued_at`/`expires_at` en ISO sólo para lectura humana de la evidencia; la decisión de autoridad nunca depende del wall-clock.
  3. Definir el token como HMAC-SHA256 sobre `(plan_sha256, proposal_sha256, issued_at)` con clave `secrets.token_bytes(32)` generada **por proceso**: es una atadura anti-replay intra-proceso, **no** identidad; un proceso del mismo UID sigue fuera de la protección.
  4. Fijar `DEFAULT_TTL_SECONDS = 120` en código, invariante `TTL ≥ plan.max_total_duration` (60 s), y prohibir TTL por parámetro del llamante, CLI o entorno.
  5. Dejar constancia explícita de que `run_boost_matrix.py` escribe hardware real fuera de este contrato y de que cerrar eso es IHRA-05.
- **Aceptación:** El contrato no deja ningún campo de autoridad dependiente del llamante; la diferencia entre deadline monotónico y timestamp de evidencia queda escrita; nada de esto se afirma como ya implementado.
- **Prueba:** `python3 -m unittest discover -s tests -v` (sigue 143 OK).
- **Rollback:** Descartar sólo el diff documental de M0; no toca código.
- **No hace:** Cambiar una línea de `core/`.

### M1 — Expiración y vinculación digest→grant

- **Objetivo:** Que la autoridad venza y que sólo pueda ejecutarse el comando cuyo digest clasificó Sentinel. *Invariante: autoridad caducada o digest no coincidente ⇒ `DENY` antes del dispatch.*
- **Rutas previstas:** `core/authorization.py:16-42,76-110`, `core/sentinel.py:13-51,96-112`, `core/experiment.py:404-431,522-564`, `core/cli.py:228-253`, `tests/test_grant_expiry.py` (nuevo).
- **Pasos:**
  1. `AuthorizationExpired(AuthorizationDenied)` para que los `except AuthorizationDenied` existentes (`core/cli.py:236`, `core/experiment.py:438`) sigan valiendo sin tocarlos.
  2. `PlanAuthorization.__slots__` + `_deadline`, `_issued_at`, `_expires_at`, `_clock`; `authorize_plan_from_tty(..., ttl_seconds=DEFAULT_TTL_SECONDS, clock=time.monotonic)`.
  3. En `consume()`: comprobar expiración **antes** de fijar `_used` — un grant caducado no quema la ceremonia TTY.
  4. `remaining_seconds()` legible tras consumir, para revalidar antes de cada muestra en el bucle de `core/experiment.py:522`.
  5. `AuthorizationGrant` con `plan_sha256`, `issued_at`, `expires_at`, `grant_token`; verificación separada `SentinelGate.verify_grant(grant, plan)` que recalcula el digest y rechaza caducados.
  6. **Restricción dura:** la firma `evaluate_plan(self, plan)` no cambia — `tests/test_antifake_sentinel.py:58-59` exige `TypeError` ante un kwarg extra.
- **Aceptación:** Con reloj falso en `t=11` y TTL 10 ⇒ `AuthorizationExpired`, cero evidencia escrita; expirar en la muestra 3 detiene la campaña preservando los recibos 1–2; ningún test dormita.
- **Prueba:** `python3 -m unittest tests.test_grant_expiry tests.test_tty_authorization tests.test_synthetic_experiment -v`
- **Rollback:** Revertir M1; `consume()` vuelve a dos argumentos y `AuthorizationGrant` a cinco campos.
- **No hace:** Tokens persistentes, TTL configurable, Tier-1 habilitado.

### M2 — Sentinel fail-closed sobre `rollback_plan`

- **Objetivo:** Hacer operativa la promesa de `docs/ARCHITECTURE.md` §2.4 item 3. *Invariante: plan sin rollback verificable ⇒ `DENY`; ninguna verificación se salta en silencio.*
- **Rutas previstas:** `core/sentinel.py:53-112`, `core/rollback.py:31-44`, `schemas/experiment.schema.json`, `core/cli.py:130-147`, `tests/test_sentinel_plan_validation.py` (nuevo).
- **Pasos:**
  1. Validador stdlib del plan que evalúa Sentinel: igualdad de conjunto sobre `required`, tipos estrictos, `risk_tier ∈ {0,1,2,3}`, `workload.repetitions >= 3`, siguiendo el estilo de `core/planning.py:68-76,95-107`.
  2. Decidir "unverifiable" **en estático**: que `_validate_restore`/`_validate_query` (`core/rollback.py:31-44`) rechazarían esos tres campos, sin ejecutar nada.
  3. Plan sin `rollback_plan`, o con campos no-`str`, ⇒ `DENY` tier 3.
  4. Un test lee `schemas/experiment.schema.json` con `json` y afirma que el conjunto `required` del validador es igual al del schema: la coherencia se prueba, no se delega a una librería instalada.
- **Aceptación:** `cmd_sentinel` sigue sin ejecutar comandos; un plan sin `rollback_plan` es rechazado aunque el comando sea allowlisted; Tier-1 con rollback válido sigue `DENIED`, ahora por política de autoridad humana y no por falta de verificación estática.
- **Prueba:** `python3 -m unittest tests.test_sentinel_plan_validation tests.test_antifake_sentinel -v`
- **Rollback:** Retirar el validador; `evaluate_plan` recupera la conducta actual.
- **No hace:** Añadir `jsonschema` como dependencia; ejecutar restauraciones para comprobarlas.

### M3 — Rollback acoplado a la ejecución con evidencia persistida

- **Objetivo:** Cumplir el mandato `AGENTS.md` §0.3 y `docs/SAFETY_CONTRACTS.md` §4 en el eje sintético + read-only. *Invariante: `rollback_status` proviene de observaciones, nunca de una declaración.*
- **Rutas previstas:** `core/rollback.py`, `core/experiment.py:258-285,451-696`, `core/executor.py:84`, `core/recovery.py:22,145,264,370-385`, `core/cpu_power.py:44-73`, `tests/test_campaign_rollback.py` (nuevo), `tests/test_campaign_recovery.py:60-145`.
- **Pasos:**
  1. `capture_prestate()` / `execute_rollback(prestate)` / `verify_restoration(prestate)` con la forma exacta de `docs/SAFETY_CONTRACTS.md:46-59`. Prestate = lecturas Tier-0 (`platform_profile`, knob de boost, `/sys/class/power_supply`) y `powerprofilesctl get`, cada una sólo bajo comandos de la allowlist.
  2. El runner escribe `prestate.json` **antes** de la primera muestra y `poststate.json` + `rollback.json` al cerrar, con `write_bytes` (heredan el manifiesto incremental de `core/experiment.py:258-285`). Sin `subprocess` por muestra: prestate y poststate una sola vez por campaña, para no rozar el techo de 60 s.
  3. `rollback_status` derivado: `VERIFIED_UNCHANGED` / `RESTORED_VERIFIED` / `FAILED_STATE_DIFF`; en `DryRunExecutor` se deriva de `commands_executed == 0 and not system_state_changed`, conservando el literal actual para no romper `tests/test_dry_run_executor.py:22`.
  4. Nombres **sin** prefijo `sample-` (no romper `tests/test_synthetic_experiment.py:358`) y extensión **en el mismo commit** de las allowlists de `core/recovery.py:22,145,264`, con estados `ROLLED_BACK_VALID` y `FAULT_STATE`; `COMPLETED_VALID` pasa a exigir los tres artefactos.
  5. Envolver `RollbackController.capture_state` (`core/rollback.py:46-52`): hoy propaga excepciones que se fugarían por el brazo `except ValueError` de `core/experiment.py:432`. Fallar la lectura del prestate **niega** la campaña; registrarla como `None` está prohibido (corregir también el `except: pass` de `core/cpu_power.py` en el camino del prestate).
  6. **No** añadir campos a `WorkloadReceipt`: la igualdad de conjunto de `core/experiment.py:371-385` rechazaría el recibo propio.
- **Aceptación:** Campaña completa = tres artefactos nuevos dentro del manifiesto y sus SHA-256 verificables por inspección; `poststate != prestate` con `verify_restoration()` falso ⇒ `ABORTED`/`NOT_DEMONSTRATED` + `fault.json` con comandos de remediación; campaña sin artefactos ⇒ `INCOMPLETE`; las 8 campañas históricas siguen `INCOMPLETE` sin ser tocadas.
- **Prueba:** `python3 -m unittest tests.test_campaign_rollback tests.test_rollback_controller tests.test_campaign_recovery tests.test_synthetic_experiment -v`
- **Rollback:** Revertir M3 **junto con** las allowlists de `recovery.py`, dejando lectores que clasifiquen campañas con o sin artefactos como `NOT_DEMONSTRATED`. Nunca migrar, borrar ni reescribir directorios de evidencia.
- **No hace:** Restauración automática al arrancar (AF-09); firma de recibos; polling de sensores.

### M4 — Tier-1 sólo con humano al TTY + Emergency Fault State

- **Objetivo:** Ejercitar la autoridad humana sobre hardware reversible sin abrir el modo autónomo, y bloquear experimentar sobre un fault sin resolver.
- **Rutas previstas:** `core/sentinel.py:105-112`, `core/experiment.py:404-459`, `core/cli.py:228-253`, `core/rollback.py`, `tests/test_antifake_sentinel.py:62`, `tests/test_tier1_human_authorization.py` (nuevo).
- **Pasos:**
  1. Método **nuevo** `SentinelGate.evaluate_tier1(plan, authorization)` (no un kwarg de `evaluate_plan`, ver M1): autoriza exactamente un `powerprofilesctl set <perfil>` de la regex existente sólo con (a) `PlanAuthorization` TTY sin consumir sobre el digest canónico del plan, (b) `rollback_plan` validado en M2, (c) prestate capturado, (d) verificación tras restaurar. Sin grant humano ⇒ `DENIED`.
  2. Restauración garantizada reutilizando el patrón probado del repo: `restore_on_signals` + `RESTORE_SIGNALS` (`experiments/cpu-boost/run_boost_matrix.py:144-149`), no un diseño nuevo.
  3. `verify_restoration()` falso ⇒ **Emergency Fault State**: `fault.json` con la discrepancia y los comandos manuales de remediación; `find_unresolved_faults()` de solo lectura con la misma disciplina de descriptores (`core/recovery.py:71,102`) consultada por `run()` **antes** de abrir el directorio de evidencia ⇒ `DENIED`/`DENY`.
  4. Reescribir la razón de `core/sentinel.py:109` para que siga siendo veraz tras M1–M3: la expiración y el rollback acoplado ya existen; lo que sigue ausente es un camino Tier-1 desatendido, prohibido **por decisión**. Actualizar `tests/test_antifake_sentinel.py:62` en el mismo commit, con el motivo escrito: el texto nuevo es más restrictivo, no menos.
- **Aceptación:** Tier-1 desatendido ⇒ `DENIED` exit 1; Tier-1 con autoridad humana restaura y verifica, y persiste el recibo; `SIGTERM` a mitad deja el perfil == prestate; una campaña intentada sobre un `fault.json` sin resolver ⇒ `DENIED`.
- **Prueba:** `python3 -m unittest tests.test_tier1_human_authorization tests.test_antifake_sentinel -v` + ronda real descrita en Verificación.
- **Rollback:** Revertir el método y la razón; el fault-state queda como artefacto leído pero no consultado, y Tier-1 vuelve a `DENIED` incondicional.
- **No hace:** Tier-2; governor writes; cualquier escritura a `/sys`; autonomía desatendida de estado.

### M5 — Docs que dejen de mentir y cierre del pendiente IHRA-03 M5

- **Objetivo:** Que ningún documento afirme lo diferido y que la recuperación sintética esté documentada para un humano.
- **Rutas previstas:** `docs/ARCHITECTURE.md:85-96,106-112`, `docs/SAFETY_CONTRACTS.md` §4-5, `docs/TECHNICAL.md:99`, `docs/ANTIFAKE_TEST_MATRIX.md:19`, `README.md:63-101`, `README_es.md`, `ROADMAP.md:4`.
- **Pasos:**
  1. Quitar las notas "not implemented" **sólo** donde M1–M4 las vuelvan falsas; conservarlas en §2.5 (executor de planes arbitrarios) y §2.6 (recibo firmado, orden inverso).
  2. `RESOURCE_PRESSURE_ABORT` (`SAFETY_CONTRACTS.md:78-80`) queda explícitamente no implementado.
  3. `ANTIFAKE_TEST_MATRIX.md:19` (AF-09) se marca `NOT_DEMONSTRATED`: M4 produce fault-state, no reparación automática al arrancar.
  4. Documentar crash/checkpoint/inspector en `README.md` y `README_es.md` (IHRA-03 M5), incluyendo que la inspección no reanada ni repara y que `SIGKILL` probado no equivale a seguridad ante pérdida de energía.
  5. Advertir en ambos README que `experiments/cpu-boost/run_boost_matrix.py` escribe hardware real con `sudo` fuera del gate de Sentinel/autorización/rollback, hasta que IHRA-05 lo incorpore.
  6. Encabezado de `ROADMAP.md:4`: dice "PROPOSAL / DEFERRED — no implementar todavía" y "repositorio público queda como propuesta" cuando el código está implementado y publicado; se marca `[HISTORICAL]` preservando el texto y añadiendo la fecha de verificación, sin borrar nada.
- **Aceptación:** Cada afirmación de capacidad tiene respaldo en código o está rotulada como diferida; ninguna ruta prometida del roadmap sigue descrita como pendiente sin datación.
- **Prueba:** `python3 -m unittest discover -s tests -v && python3 -m compileall -q core tests && git diff --check`
- **Rollback:** Revertir sólo el diff documental.
- **No hace:** Borrar historia ni evidencia documental; reescribir `ROADMAP.md` desde cero.

## Verificación

```bash
cd "$HOME/Development/ISyCo Git/hardware-research-agent"
python3 -m unittest discover -s tests -v                        # 143 baseline + ~25 nuevos, 0 failures
python3 -m unittest tests.test_grant_expiry tests.test_campaign_rollback -v
python3 -m compileall -q core tests && git diff --check
python3 -m core.cli sentinel lscpu                              # AUTHORIZED, exit 0
python3 -m core.cli sentinel 'powerprofilesctl set balanced'    # desatendido ⇒ DENIED, exit 1
python3 /home/danny/Development/ISyCo/.agents/skills/openisy-planner/scripts/plan_tool.py verify plans/ihra-autonomy-safety-prerequisites.md
```

Ronda Tier-1 real (M4, con el propietario al TTY): lanzar el comando Tier-1 en un PTY, teclear `AUTHORIZE <plan_sha256>`, y comprobar que `powerprofilesctl get` vuelve a `performance`, que `rollback.json` con `verified_state` está cubierto por el manifiesto, que una ronda cortada con `SIGTERM` restaura el perfil, y que la campaña siguiente sobre un `fault.json` sin resolver devuelve `DENIED`. Único write real: un perfil energético reversible por DBus; nada toca `/sys`.

## Dependencias

`M0 → M1 → M2 → M3 → M4 → M5`. M3 necesita M1 (deadline) y M2 (rollback comprobable en estático). M4 necesita M2 y M3. IHRA-05 (polling de sensores, cerrar `run_boost_matrix.py`, hooks inversos) depende de este plan. IHRA-06 (orquestador desatendido, `resume`) depende de IHRA-05. Linux; sólo las pruebas de campaña real requieren Bubblewrap. Sin dependencias nuevas de terceros.

## Riesgos

1. **Un TTL mayor que la campaña otorga autoridad de sobra.** Mitigación: 120 s fijos, revalidación por muestra y test del invariante `TTL ≥ max_total_duration`.
2. **Extender `recovery.py` tarde convierte cada campaña nueva en `INCOMPLETE`** y nadie lo nota. Mitigación: escritor y allowlists cambian en el mismo commit, con test explícito de ausencia.
3. **Marcar como usado un grant caducado quema la ceremonia TTY.** Mitigación: orden expiración→`_used`, probado por `test_expired_grant_is_not_consumed`.
4. **Habilitar Tier-1 aunque sea con humano ensancha la superficie de escritura.** Mitigación: un solo comando de la allowlist, sin strings derivados del plan; `DENIED` si falta el grant; fault-state bloqueante; la verificación real la presencia el propietario.
5. **Cambiar la razón de Sentinel puede leerse como suavizar el gate.** Mitigación: el test se actualiza en el mismo commit con el motivo escrito y el texto nuevo es más restrictivo.
6. **Parchear strings de tests puede ocultar regresiones.** Mitigación: sólo se tocan las dos aserciones conscientemente listadas; cualquier otra rotura se trata como defecto de código, no de test.
7. **`run_boost_matrix.py` sigue escribiendo hardware fuera del contrato.** Aceptado y documentado por decisión del propietario; cierra en IHRA-05.

## Rollback global

1. Detener campañas; **no borrar, mover ni "reparar" evidencia**. Los ocho directorios bajo `~/.local/state/ihra/synthetic-experiments` se conservan tal cual.
2. Inspeccionar `git status --short` y `git diff --name-status`; acotar el revert a las rutas de este plan.
3. Revertir por milestone con `git revert --no-commit <sha>`; nunca `reset --hard`, `git clean` ni restauración global.
4. Si se revierte el escritor de artefactos, mantener lectores que clasifiquen campañas con o sin ellos como `NOT_DEMONSTRATED`.
5. Re-verificar: suite completa, `compileall`, `git diff --check`. Sin push.

## Próximo paso

- **Primer paso:** Registrar el baseline y escribir la §6 de `docs/SAFETY_CONTRACTS.md` con el contrato de expiración y del límite del token, sin tocar `core/` (M0).
- **Comando inicial:** `cd "$HOME/Development/ISyCo Git/hardware-research-agent" && python3 -m unittest discover -s tests -v && git log -1 --oneline`

## Registro de ejecución

Ejecutado 2026-11-05 → 2026-11-06. Un commit por milestone, sin push.

| Milestone | Commit | Evidencia |
|---|---|---|
| M0 | `099b524` | baseline 143 OK + contrato de expiración/rollback en `docs/` |
| M1 | `29629cc` | `tests/test_grant_expiry.py` (expiry, orden expiración→`_used`, token) |
| M2 | `9ec0d11` | `tests/test_sentinel_plan_validation.py` (validador stdlib ≡ schema) |
| M3 | `d6bc601` | `tests/test_campaign_rollback.py` (9) + allowlists de `recovery.py` |
| M4 | `a6fb916` | `tests/test_tier1_safety.py` (9); suite completa 191 OK |
| M5 | `1467970` | docs: `ARCHITECTURE.md`, `SAFETY_CONTRACTS.md`, `TECHNICAL.md`, `TECHNICAL_es.md`, `ANTIFAKE_TEST_MATRIX.md`, `README.md`, `README_es.md`, `ROADMAP.md`, `AGENTS.md` |

Desviaciones respecto al plan (todas más estrictas o de alcance menor, nunca más permisivas):

1. **M2** — La obligación de plan completo y verificable se aplica al tier **derivado** 1, y la denegación se registra en ese tier derivado, no en el solicitado por el llamante.
2. **M2** — `core/executor.py` conserva su `rollback_status` fijo: pertenece a `DryRunReceipt` (`simulation_only=True`, `hardware_accessed=False`). El estado observado por una campaña real vive en la evidencia de M3.
3. **M3** — Los tres artefactos nuevos se nombran sin prefijo `sample-` y el allowlist de `core/recovery.py` se extendió en el mismo commit. `tests/test_campaign_recovery.py` sólo los emite con `modern=True` para que las ocho campañas históricas sigan `INCOMPLETE` sin reescribir evidencia.
4. **M4** — `evaluate_tier1()` es un método **nuevo**: `evaluate_plan(self, plan)` conserva su firma exacta (`tests/test_antifake_sentinel.py` exige `TypeError` ante kwargs) y sigue negando Tier-1, así que el modo desatendido no se ensancha.
5. **M4** — Tier-1 no tiene artefacto `proposal`, así que la ceremonia liga el digest del plan también en el slot del proposal y añade un tercer digest, `intervention_sha256`, derivado de los bytes de la intervención declarada en el plan.
6. **M4** — La evidencia Tier-1 se escribe en `state/ihra/tier1-experiments` y los fallos en `state/ihra/tier1-faults` (árboles hermanos del árbol sintético), de modo que las allowlists de M3 y los manifiestos históricos quedan intactos. Consecuencia: `inspect-synthetic-campaign` no inspecciona bundles Tier-1, así que AF-09 sigue `NOT_DEMONSTRATED` y M5 lo documenta.
7. **M4** — Sólo las campañas Tier-1 escriben registros de estado de fallo. Una deriva no declarada observada por una campaña sintética se registra pero no bloquea la siguiente: no hay evidencia de que la campaña la causara. Ambos runners sí consultan `find_unresolved_faults()` antes de ejecutar.
8. **M4** — `PlanAuthorization.binds()` exige ahora que el slot de intervención coincida también en presencia (antes un grant ligado a una intervención pasaba un `binds()` genérico). `consume()` ya lo exigía.
9. **M4** — `SentinelGate.verify_grant()` sigue siendo la revalidación del tier 0: un grant Tier-1 no la pasa y falla cerrado. La autoridad Tier-1 se revalida consumiendo el grant contra los tres digest.
10. **M4** — Cambió el texto de la razón de Sentinel para Tier-1 (dos aserciones actualizadas a propósito en el mismo commit). El texto viejo afirmaba que expiry y rollback no existían, lo cual es falso desde M1/M3; el nuevo niega el mismo camino y añade que el bloqueo es una decisión.
11. **Operativo** — Con un estado de fallo sin resolver quedan denegadas todas las campañas, incluidas las de la suite de pruebas, hasta que una persona ejecute la remediación y `resolve-fault`.
12. **M5** — `docs/TECHNICAL_es.md` no estaba en el plan pero replicaba los mismos enunciados falsos («pieza independiente», «los cambios de nivel 1/2 siguen deshabilitados», «la campaña fija no está conectada a rollback»). Se corrigió en paralelo: los dos idiomas afirman lo mismo o el inglés pasa por el canónico y el español sigue mintiendo.
13. **M5** — `docs/ANTIFAKE_TEST_MATRIX.md` no tenía columna de estado; el plan sólo pedía dejar AF-09 como NO demostrado. Se añadió una sección *Status against the code on disk* con las doce filas, porque AF-02 (normalización por GHz), AF-10 (`RESOURCE_PRESSURE_ABORT`) y AF-12 tampoco se cumplen, y una matriz sin estado se lee como un checklist pasado.
14. **M5** — `AGENTS.md` ganó una regla de comportamiento (Tier-1 lo arranca el humano en su terminal; `list-faults` vacío antes de proponer otra campaña) y la lista de módulos de `core/`. No estaba en el plan, no toca código y hace más estrecha la conducta del agente, no más ancha.
15. **M5** — En `docs/ARCHITECTURE.md` se anotaron también el diagrama ASCII y la máquina de estados: el polling térmico/de memoria con abort no existe, y el token del grant es un HMAC por proceso que liga digests, no una firma de evidencia. Sin esas notas el diagrama seguía describiendo componentes imaginarios por su cuenta.
