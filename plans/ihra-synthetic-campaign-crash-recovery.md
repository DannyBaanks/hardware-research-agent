# Plan — IHRA-03: Recuperación segura ante interrupciones de campañas sintéticas

## Estado
READY — 6 milestones (M0–M5).

## Objetivo

Hacer que una campaña sintética interrumpida abruptamente conserve checkpoints verificables y nunca parezca completada por ausencia del proceso. Añadir inspección de solo lectura que valide lo persistido y clasifique evidencia incompleta como `NOT_DEMONSTRATED`. No reanudar campañas ni volver a ejecutar muestras automáticamente. El éxito observable es que una terminación abrupta durante una muestra, tras un recibo o durante una actualización de evidencia no produce `COMPLETED/DEMONSTRATED`, no pierde recibos ya confirmados y puede diagnosticarse sin modificar la evidencia.

## Evidencia inspeccionada

- `core/experiment.py:152-259`: `_CampaignEvidence` crea el directorio privado, escribe cada archivo mediante temporal + `fsync` + `os.replace` + `fsync` del directorio y acumula digests en memoria; `write_manifest()` se invoca explícitamente al finalizar y el digest del propio manifiesto se añade al acumulador después de escribirlo.
- `core/experiment.py:428-461`: la campaña crea `campaign.json` con `status=RUNNING`; registra `plan.json` y `proposal.json` antes del preflight.
- `core/experiment.py:514-536`: se persiste `sample-NN.started.json` antes del dispatch y `KeyboardInterrupt` cooperativo produce un marcador de interrupción.
- `core/experiment.py:639-670`: el estado final y el manifiesto se escriben al completar el bucle; el handler exterior de interrupción devuelve `INTERRUPTED` pero conserva el último estado ya escrito en disco.
- `core/executor.py:228-255,299-305`: Bubblewrap usa `--die-with-parent` y `--new-session`; timeout e interrupción cooperativa terminan el grupo de procesos. No se halló prueba que mate abruptamente al padre real mientras Bubblewrap está activo.
- `tests/test_synthetic_experiment.py:444-453`: NC-10 inyecta `KeyboardInterrupt` en un executor falso; comprueba marcador y no-completado, no `SIGKILL`, muerte del proceso padre ni lectura posterior de evidencia.
- `core/cli.py:204-228`: el CLI ejecuta la campaña tras consentimiento TTY; no se encontró comando de inspección, reconciliación ni reanudación de campañas.
- `docs/SYNTHETIC_EXPERIMENT_CONTROLS.md:48-64`: el informe declara que `SIGKILL`, pérdida de energía y recuperación durable permanecen sin demostrar.
- `README.md:122`: la capacidad actual excluye recuperación general ante crash.
- `AGENTS.md:1-21`: seguridad read-only por defecto, no Tier 3 y suite obligatoria `python3 -m unittest discover -s tests -v`.
- `git status --short --branch` y `git log -3 --oneline`: árbol limpio en `main`, HEAD `1d40890`; rama local siete commits adelante de `origin/main`, sin autorización de push.
- Baseline ejecutado para este plan: `python3 -m unittest discover -s tests -q` → 78 tests OK.
- Búsqueda en `core`, `tests`, README y docs: no se encontró API/CLI de recuperación o reanudación; ausencia confirmada en las rutas inspeccionadas.

## Alcance

- Especificar un protocolo crash-consistent para estado de campaña, inicio de muestra, recibo validado y manifiesto incremental.
- Hacer explícita la diferencia entre estado activo observado, campaña incompleta y campaña completada con toda la evidencia verificable; ausencia de estado final nunca equivale a éxito.
- Añadir una operación de inspección de campaña de solo lectura, con validación de ruta, archivos regulares, esquema, referencias y digests.
- Preservar sin alteraciones los recibos válidos anteriores a la interrupción; exponer incompletitud o corrupción como `NOT_DEMONSTRATED`.
- Añadir pruebas de proceso real para interrupciones abruptas con workload fijo y sandbox, además de simulaciones de cortes entre escrituras.
- Documentar qué se probó, qué quedó simulado y que `SIGKILL` no demuestra recuperación tras pérdida de energía.

## No objetivos

- No reanudar una campaña, repetir muestras ni conceder nueva autoridad después de una interrupción.
- No inferir que una campaña está abandonada por edad, PID ausente o timeout; los datos de proceso no prueban por sí solos que un proceso no siga activo.
- No modificar ni borrar campañas, recibos, manifiestos o evidencia histórica durante la inspección.
- No ejecutar workload, shell, comandos externos ni acciones de hardware desde el comando de inspección.
- No convertir un SHA-256 local en prueba de autoría o resistencia a un atacante con el mismo UID; no añadir firma, servicio, daemon o base de datos en esta fase.
- No afirmar resistencia a pérdida de energía, corrupción de almacenamiento o interrupción física sin ensayos independientes específicos.
- No cambiar el workload fijo, el sandbox, los límites, la ceremonia TTY ni el comportamiento de Sentinel.
- No hacer push.

## Milestones

### M0 — Congelar contrato crash-consistent y baseline

- **Objetivo:** Definir estados, invariantes de persistencia, límites de confianza y resultados que la inspección puede afirmar.
- **Rutas previstas:** `core/experiment.py`, `core/cli.py`, `tests/test_synthetic_experiment.py`, `tests/test_campaign_recovery.py` (nuevo), `docs/SYNTHETIC_EXPERIMENT_CONTROLS.md`, `README.md`, `README_es.md`.
- **Pasos:**
  1. Registrar el baseline de 78 tests y preservar intacta la evidencia de campañas previas.
  2. Definir que solo `COMPLETED` con cinco recibos verificados, plan/propuesta enlazados y manifiesto consistente puede clasificarse `DEMONSTRATED`.
  3. Definir que `RUNNING` persistido, una muestra con solo marcador `started`, manifiesto ausente o inconsistente, o cualquier campo requerido faltante produce `NOT_DEMONSTRATED`; la inspección no debe cambiar el registro a un estado terminal.
  4. Fijar que la inspección no deduce si la campaña sigue ejecutándose ni determina que el propietario murió.
- **Aceptación:** El contrato distingue `ACTIVE/UNKNOWN`, `INCOMPLETE` y `COMPLETED_VALID` sin convertir antigüedad o ausencia de PID en evidencia de terminación; ningún estado no verificado puede ser éxito.
- **Prueba:** `python3 -m unittest discover -s tests -q`
- **Rollback:** Descartar solo la especificación M0 si contradice el contrato existente; conservar recibos y ejecutar la suite baseline.

### M1 — Diseñar checkpoints durables y manifiesto incremental

- **Objetivo:** Persistir suficiente evidencia en cada frontera para que un corte abrupto no borre el historial ya confirmado.
- **Rutas previstas:** `core/experiment.py`, `tests/test_campaign_recovery.py` (nuevo).
- **Pasos:**
  1. Reemplazar escrituras dispersas por transiciones explícitas y ordenadas: campaña iniciada → muestra iniciada → recibo validado → checkpoint/manifiesto actualizado → siguiente muestra.
  2. Actualizar atómicamente el checkpoint después de cada transición, incluyendo número de muestra, resultado observado y estado; ningún checkpoint declara completada una muestra antes de que su recibo se haya persistido.
  3. Escribir un manifiesto incremental que enumere cada artefacto previo durable, excepto a sí mismo; no incluir un digest viejo del propio manifiesto al reescribirlo.
  4. Mantener la escritura temporal, `fsync`, reemplazo atómico, permisos privados y protección contra symlink; probar los límites exactos en los que un fallo puede dejar un temporal huérfano sin invalidar los artefactos previamente confirmados.
  5. Al reiniciar el programa, no mutar automáticamente directorios ni continuar el plan; la campaña que quedó en `RUNNING` sigue incompleta.
- **Aceptación:** Cortes simulados antes y después de cada transición conservan los recibos anteriores, no producen manifiestos autorreferenciales ni permiten clasificar como completada una muestra sin recibo durable.
- **Prueba:** `python3 -m unittest tests.test_campaign_recovery tests.test_synthetic_experiment -v`
- **Rollback:** Revertir únicamente el cambio de protocolo y nuevos tests; mantener el formato histórico de evidencia legible y no reescribir campañas existentes.

### M2 — Añadir inspección offline, read-only y fail-closed

- **Objetivo:** Permitir que un operador inspeccione una campaña sin ejecutar workload ni cambiar sus archivos.
- **Rutas previstas:** `core/experiment.py` o módulo de lectura acotado, `core/cli.py`, `tests/test_campaign_recovery.py`.
- **Pasos:**
  1. Añadir `inspect-synthetic-campaign` con identificador validado y resolución confinada al directorio XDG de campañas.
  2. Abrir la ruta sin seguir symlinks; aceptar únicamente directorios/archivos regulares esperados y rechazar escapes, nombres duplicados o entradas inesperadas que afecten la interpretación.
  3. Validar hashes del manifiesto, esquema y enlaces entre campaña, plan, propuesta, muestra y recibo; no aceptar un `COMPLETED` aislado como prueba suficiente.
  4. Emitir una clasificación y lista de faltantes/inconsistencias sin editar ni “reparar” evidencia. Si no puede comprobar el estado, responder `NOT_DEMONSTRATED`.
  5. Asegurar que la ruta de inspección no construye un executor, abre TTY, lanza procesos ni requiere autorización de ejecución.
- **Aceptación:** Las pruebas muestran cero cambios byte por byte y cero llamadas a executor para campañas completas, incompletas, symlinked, corruptas y con artefactos faltantes.
- **Prueba:** `python3 -m unittest tests.test_campaign_recovery -v`
- **Rollback:** Retirar solo el comando y lector nuevo; las campañas y el flujo normal de ejecución siguen intactos.

### M3 — Añadir controles de cortes entre escrituras

- **Objetivo:** Falsar la consistencia del protocolo ante interrupción en cada punto persistente sin depender únicamente de excepciones cooperativas.
- **Rutas previstas:** `tests/test_campaign_recovery.py` (nuevo), helpers de pruebas existentes solo si son necesarios.
- **Pasos:**
  1. Inyectar fallos antes/después de `started`, ejecución del executor, recibo, checkpoint, manifiesto y cierre final.
  2. Reconstruir el lector desde el directorio de evidencia únicamente, como lo haría un proceso nuevo.
  3. Confirmar que temporales incompletos no se interpretan como recibos, y que un marcador `RUNNING` nunca se transforma en éxito.
  4. Confirmar que la inspección repetida es determinista y no modifica mtime, contenido, permisos ni manifiesto.
- **Aceptación:** Cada punto de corte produce una clasificación conservadora y preserva todos los artefactos ya confirmados; una única anomalía invalida el estado exitoso.
- **Prueba:** `python3 -m unittest tests.test_campaign_recovery -v`
- **Rollback:** Retirar solamente los escenarios de inyección que no correspondan al protocolo final; mantener controles que revelen falsos éxitos.

### M4 — Probar interrupción de proceso real bajo Linux/Bubblewrap

- **Objetivo:** Verificar el comportamiento observable del padre, Bubblewrap y la evidencia al matar el proceso durante una campaña de prueba controlada.
- **Rutas previstas:** `tests/test_campaign_recovery.py`, `docs/SYNTHETIC_EXPERIMENT_CONTROLS.md`.
- **Pasos:**
  1. Crear una campaña temporal aislada con el único workload fijo, propuesta fija y PTY automatizada de prueba; identificar explícitamente esta autorización como harness, no consentimiento humano.
  2. Interrumpir solo el PID de prueba controlado en fases distintas, incluyendo durante una muestra y después de un recibo confirmado; no usar señales por nombre ni afectar otros procesos.
  3. Comprobar si `--die-with-parent` termina el descendiente Bubblewrap en esta topología; registrar procesos supervivientes, exit status, estado persistido y manifiesto.
  4. Ejecutar el inspector como proceso nuevo y exigir resultado no exitoso para cada ejecución interrumpida.
  5. Limitar la afirmación a los cortes Linux/señales realmente ejecutados; dejar pérdida de energía y corrupción del dispositivo como `NOT_DEMONSTRATED`.
- **Aceptación:** Ningún caso interrumpido aparece `COMPLETED/DEMONSTRATED`; ningún proceso sandbox hijo queda vivo; recibos previos siguen verificables; los recibos parciales no se promueven.
- **Prueba:** `python3 -m unittest tests.test_campaign_recovery -v`
- **Rollback:** Detener solo el proceso de prueba identificado; conservar evidencia adversarial. Si el control deja procesos vivos, bloquear el milestone y no añadir cleanup amplio ni comandos de proceso por nombre.

### M5 — Documentar límites y cerrar regresiones

- **Objetivo:** Publicar con precisión qué recuperación quedó implementada y qué tipo de crash sigue fuera de evidencia.
- **Rutas previstas:** `README.md`, `README_es.md`, `docs/ARCHITECTURE.md`, `docs/SAFETY_CONTRACTS.md`, `docs/SYNTHETIC_EXPERIMENT_CONTROLS.md`, `core/cli.py`, tests.
- **Pasos:**
  1. Documentar el comando read-only, el formato de estados, el significado de incompleto y que no existe resume/retry automático.
  2. Añadir controles con condición, resultado esperado/observado, evidencia y modo (simulado o proceso real).
  3. Ejecutar suite completa, compileall y diff check; verificar que las campañas históricas siguen legibles y no se reescribieron.
  4. Reportar por separado prueba `SIGKILL`/señales, prueba `SIGTERM` y las limitaciones de pérdida de energía/almacenamiento.
- **Aceptación:** Documentación y outputs nunca equiparan `RUNNING`, manifiesto ausente o recibos parciales a éxito; pruebas y verificación de artefactos pasan.
- **Prueba:** `python3 -m unittest discover -s tests -v && python3 -m compileall -q core tests && git diff --check`
- **Rollback:** Revertir solo los commits de este plan si la evidencia o las garantías fallan; preservar intactos todos los recibos y resultados históricos.

## Dependencias

- `M0` → `M1` → `M2` → `M3` → `M4` → `M5`.
- M1 requiere mantener compatibilidad de lectura con campañas históricas sin reescribirlas; cualquier incompatibilidad bloquea el avance y exige una estrategia de lectura, no migración destructiva.
- M4 requiere Linux, Bubblewrap rootless y una prueba de proceso aislada. El PTY de harness se etiqueta como automatizado y nunca como consentimiento humano.
- No se añade daemon, base de datos, dependencia externa ni servicio en segundo plano.

## Riesgos

- **Riesgo:** Un `COMPLETED` parcial o un manifiesto final ausente se confunde con éxito.
  - **Mitigación:** El único estado exitoso requiere validar registro terminal, cinco recibos, enlaces de digests y manifiesto coherente; cualquier incertidumbre es `NOT_DEMONSTRATED`.
- **Riesgo:** Reescribir manifiestos incrementales incluye el hash de una versión anterior del mismo manifiesto y produce una falsa verificación.
  - **Mitigación:** Definir que el manifiesto nunca se incluye a sí mismo y probar varias actualizaciones consecutivas del mismo directorio.
- **Riesgo:** Una señal mata al padre pero deja vivo el workload sandboxed.
  - **Mitigación:** Verificar en proceso real la topología Bubblewrap, limitar la prueba a un PID de test conocido y no afirmar limpieza hasta comprobar ausencia del descendiente identificado.
- **Riesgo:** Inspeccionar evidencia concurrentemente durante una campaña activa aparenta corrupción o abandono.
  - **Mitigación:** Clasificar el último checkpoint sin decidir actividad/abandono; repetir inspección no escribe ni finaliza la campaña.
- **Riesgo:** El contenido del estado local puede ser manipulado por procesos del mismo UID.
  - **Mitigación:** Describir SHA-256 como control de integridad interna del paquete, no como autenticidad contra un atacante con permisos de escritura al estado.
- **Riesgo:** Pruebas de `SIGKILL` se extrapolan a apagado o fallo del almacenamiento.
  - **Mitigación:** Afirmaciones limitadas a señales y sistema probados; pérdida de energía, controlador y filesystem permanecen `NOT_DEMONSTRATED`.

## Rollback global

1. Detener la implementación de recuperación; no borrar, mover, renombrar ni reparar recursivamente directorios de evidencia.
2. Inspeccionar `git status --short` y `git diff --name-status`; limitar reversión a rutas y commits de este plan.
3. Si hace falta revertir, inspeccionar cada commit del milestone y usar `git revert --no-commit <sha>` solo para ese commit; nunca usar `git reset --hard`, `git clean` ni restauración global.
4. Mantener lectores capaces de clasificar campañas nuevas o históricas conservadoramente como `NOT_DEMONSTRATED` si se revierte el escritor.
5. Ejecutar `python3 -m unittest discover -s tests -v`, `python3 -m compileall -q core tests` y `git diff --check`; conservar toda evidencia fuera de Git sin alteración.

## Próximo paso

- **Primer paso:** Ejecutar y archivar el baseline de IHRA y revisar el contrato de interrupción no cooperativa antes de modificar el formato de evidencia.
- **Comando inicial:** `cd "$HOME/Development/ISyCo Git/hardware-research-agent" && git status --short --branch && python3 -m unittest discover -s tests -v`
