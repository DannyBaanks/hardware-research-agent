# Plan — IHRA-02: Propuestas de experimento y autorización humana por TTY

## Estado

COMPLETED WITH LIMITATION — 6 milestones (M0–M5). El repositorio se creó después del freeze del 2026-10-30 y queda fuera de los 101 repositorios públicos ya existentes incluidos en ese freeze; verificación por GitHub API: `created_at=2026-11-03T01:44:29Z`. La ruta de autorización TTY fue probada mediante PTY automatizada; que una persona escriba la confirmación en una sesión real no fue demostrado. TTY no es autenticación criptográfica de identidad.

## Objetivo

Permitir que IHRA reciba y revise propuestas de planes como datos estrictamente tipados, sin darles capacidad de ejecución, y que un operador autorice interactivamente el digest exacto del plan ejecutable en una TTY local. El único plan que podrá alcanzar el ejecutor en esta etapa seguirá siendo `sha256-chain-v1`, con sus cinco muestras y límites fijos. Sin TTY, ante cancelación, cambio del plan o cualquier duda, el resultado será DENY y no habrá ejecución.

La autorización TTY es una ceremonia de consentimiento local, no una prueba criptográfica de identidad humana. No protege contra procesos maliciosos que compartan el UID del operador y puedan controlar o inyectar la TTY; esa limitación debe mostrarse en CLI y documentación.

## Evidencia inspeccionada

- `core/experiment.py:26-67`: `SyntheticExperimentPlan` fija el workload, cinco muestras, límites, políticas y digest de resultado; sus valores no son configurables por el llamante.
- `core/experiment.py:264-375`: el runner valida el plan y los recibos; actualmente recibe el digest autorizado como argumento de su API.
- `core/experiment.py:375-413`: `run()` considera la igualdad entre el digest recibido y el plan como autorización suficiente; esta interfaz debe dejar de ser el gate de consentimiento.
- `core/cli.py:135-152,246-254`: el CLI muestra el digest y acepta `--authorize-plan-sha256` como argumento; no requiere TTY ni interacción humana.
- `tests/test_synthetic_experiment.py:199-205,258-273`: se comprueban el digest exacto y el rechazo de un plan mutado durante una campaña.
- `docs/SYNTHETIC_EXPERIMENT_CONTROLS.md:50-61`: la campaña demuestra repetibilidad del workload fijo; identidad humana, planificación autónoma, autorización general y recuperación durable permanecen fuera de lo demostrado.
- `AGENTS.md:1-21`: seguridad read-only por defecto, no acciones Tier 3 y suite obligatoria `python3 -m unittest discover -s tests -v`.
- `git log -3 --oneline`: `671e324` (`feat: add bounded synthetic experiment orchestration`) es el HEAD; el árbol estaba limpio y `main` estaba cinco commits adelante de `origin/main`.
- `python3 -m unittest discover -s tests -v`: última ejecución registrada, 57/57 pruebas OK. `python3 -m core.cli show-synthetic-plan` emitió el plan fijo y el digest `135907eb2bf31e57a60ffe0a414a6fcb7b0a8cd55c744ae9984e683e61efa010`.
- M0 ejecutado el 2026-11-03: `python3 -m unittest discover -s tests -v` pasó 57/57; `git diff --check` pasó; `plan_tool.py verify` aceptó este roadmap. La suite final pasó 78/78; la campaña `9cb0377f48a440e3a516e2f6447488ed` completó 5/5 bajo Bubblewrap mediante PTY automatizada, sin demostrar consentimiento humano.
- Bridge, anuncio `#10031`: el freeze del 2026-10-30 se aplica a los 101 repositorios públicos existentes entonces. `gh api repos/DannyBaanks/hardware-research-agent --jq '{created_at, pushed_at, visibility, default_branch}'` confirmó `created_at=2026-11-03T01:44:29Z`, posterior al freeze; IHRA no pertenece a ese conjunto congelado.

## Alcance

- Definir un formato versionado y estricto de propuestas de plan; las propuestas son datos, no comandos ni código ejecutable.
- Revisar propuestas contra una política allowlist y contra la capacidad real del executor; en esta fase solo el plan fijo `sha256-chain-v1` puede ser elegible.
- Requerir que la confirmación ocurra desde una TTY local y muestre todos los campos efectivos junto con los digests canónicos de la propuesta y del plan ejecutable.
- Vincular el consentimiento a esa propuesta y plan exactos, usarlo una sola vez en el mismo flujo de ejecución y fallar cerrado si el contenido cambia.
- Eliminar el digest de CLI como autoridad suficiente y cubrir bypasses, denegaciones, TTY ausente, cancelación y mutaciones con pruebas.
- Actualizar README inglés/español, contratos y resultados de pruebas para describir correctamente el límite de confianza del TTY.

## No objetivos

- No integrar ni delegar planificación o ejecución a un LLM, agente remoto, MCP o proceso externo.
- No aceptar shell, comandos, scripts, rutas de ejecutables, parámetros arbitrarios, nuevos workloads ni cambios de límites desde una propuesta.
- No autorizar cambios de CPU, GPU, BIOS, firmware, drivers, voltajes, energía o estado persistente del host.
- No presentar TTY como identidad autenticada ni como protección frente a malware o procesos del mismo UID.
- No crear permisos reutilizables, tokens bearer, grants persistentes, autorización por variables de entorno, flags `--yes` o consentimiento recibido por stdin redirigido.
- No implementar recuperación durable general, ejecución desatendida, aprobación remota ni firma criptográfica.
- No hacer push ni publicar afirmaciones de autonomía más allá de la capacidad realmente verificada.

## Milestones

### M0 — Congelar contrato de propuesta y amenaza TTY

- **Objetivo:** Fijar el modelo de datos, la política de allowlist, la semántica del consentimiento TTY y sus exclusiones antes de cambiar comportamiento.
- **Rutas previstas:** `docs/SAFETY_CONTRACTS.md`, `docs/ARCHITECTURE.md`, `plans/ihra-agent-planning-tty-authorization.md`, `tests/`.
- **Pasos:**
  1. Confirmar contra el anuncio `#10031` y la fecha de creación de GitHub que el repositorio está fuera del freeze.
  2. Repetir y guardar el resultado de la suite baseline.
  3. Especificar campos permitidos, canonicalización, mensajes de aprobación/cancelación y el límite de procesos del mismo UID.
  4. Definir que cualquier propuesta no representable exactamente por la política actual permanece no ejecutable.
- **Aceptación:** La fecha de creación demuestra que el repositorio queda fuera del freeze; la suite baseline pasa; el contrato documenta que el TTY requiere presencia interactiva local, no acredita identidad criptográfica, y que solo el plan sintético ya implementado es elegible.
- **Estado observado:** Completado; el modelo elegido es TTY local con advertencia explícita sobre procesos del mismo UID.
- **Prueba:** `python3 -m unittest discover -s tests -v`
- **Rollback:** Revertir únicamente el commit de M0 si sus documentos generan contradicciones; preservar cambios ajenos y volver a ejecutar la suite baseline.

### M1 — Añadir el modelo estricto de propuestas

- **Objetivo:** Representar y canonicalizar propuestas sin conferirles autoridad de ejecución.
- **Rutas previstas:** `core/planning.py` (nuevo), `tests/test_plan_proposals.py` (nuevo), `core/experiment.py`.
- **Pasos:**
  1. Definir una estructura versionada con campos explícitos y límites de longitud/tipo.
  2. Rechazar claves desconocidas, valores fuera de allowlist, recursos mutables, rutas, comandos y campos ejecutables.
  3. Canonicalizar determinísticamente la propuesta y vincularla al digest del único plan ejecutable permitido.
  4. Asegurar que parsear, mostrar o validar una propuesta no invoca al executor ni escribe fuera de un área de evidencia autorizada.
- **Aceptación:** Las pruebas muestran que propuestas válidas solo describen el plan fijo y que campos desconocidos, alterados o con autoridad ejecutable se rechazan antes de cualquier invocación.
- **Estado observado:** Implementado en `core/planning.py`; pruebas cubren esquema cerrado, claves duplicadas, límites, identificadores y parsing estricto.
- **Prueba:** `python3 -m unittest tests.test_plan_proposals tests.test_synthetic_experiment -v`
- **Rollback:** Revertir el commit de M1 con sus rutas exactas; confirmar que `core.experiment` y el comando de campaña previo siguen pasando sus pruebas.

### M2 — Implementar revisión de propuestas sin efectos

- **Objetivo:** Exponer una vista revisable y un veredicto de elegibilidad sin iniciar muestras.
- **Rutas previstas:** `core/planning.py`, `core/cli.py`, `tests/test_plan_proposals.py`.
- **Pasos:**
  1. Añadir una operación CLI de inspección/validación que presente campos efectivos, digest de propuesta, digest del plan y límites.
  2. Emitir `DENY` o `NOT_DEMONSTRATED` para propuestas no ejecutables; no normalizar silenciosamente ni ignorar campos.
  3. Probar que la revisión no llama al executor ni crea recibos de campaña.
- **Aceptación:** Una propuesta válida se puede inspeccionar sin efectos; propuesta inválida o no soportada termina con código no cero y ninguna ejecución.
- **Estado observado:** Implementado con `show-synthetic-proposal` y `review-synthetic-proposal`; el test comprueba que la revisión no llama al runner.
- **Prueba:** `python3 -m unittest tests.test_plan_proposals -v`
- **Rollback:** Revertir el commit de M2 limitado a `core/planning.py`, `core/cli.py` y las pruebas añadidas; verificar `python3 -m core.cli --help`.

### M3 — Añadir consentimiento interactivo ligado al digest

- **Objetivo:** Solicitar confirmación explícita desde el terminal controlador, después de mostrar la propuesta exacta que se autorizaría.
- **Rutas previstas:** `core/authorization.py` (nuevo), `tests/test_tty_authorization.py` (nuevo).
- **Pasos:**
  1. Exigir TTY real y leer consentimiento desde `/dev/tty`, no desde argumentos, entorno ni stdin redirigido.
  2. Mostrar el resumen completo del plan fijo, su digest, límites, y la advertencia del modelo de confianza TTY.
  3. Requerir una frase de confirmación que incluya el digest canónico exacto; EOF, rechazo o digest distinto producen DENY.
  4. Crear un consentimiento no serializable y de un solo uso, acotado al proceso y al digest exacto de propuesta/plan.
- **Aceptación:** La autorización solo se emite tras confirmación interactiva coincidente; ausencia de TTY, cancelación, error de lectura y cambio de digest nunca producen un consentimiento utilizable.
- **Estado observado:** Implementado con `/dev/tty`, confirmación exacta del digest, advertencia de confianza y grant de un solo uso.
- **Prueba:** `python3 -m unittest tests.test_tty_authorization -v`
- **Rollback:** Revertir solo el commit de M3 y sus pruebas; preservar el modelo de propuestas no ejecutable de M1-M2.

### M4 — Enlazar autorización, CLI y executor existente

- **Objetivo:** Exigir el consentimiento TTY en la frontera de campaña y eliminar el digest como autorización suficiente sin cambiar el sandbox ni el workload.
- **Rutas previstas:** `core/authorization.py`, `core/experiment.py`, `core/cli.py`, `tests/test_synthetic_experiment.py`, `tests/test_plan_proposals.py`.
- **Pasos:**
  1. Cambiar la interfaz de campaña para consumir el consentimiento de un solo uso y verificar de nuevo ambos digests inmediatamente antes de ejecutar.
  2. Sustituir `--authorize-plan-sha256` por una secuencia CLI de revisión y consentimiento interactivo; denegar siempre el modo no interactivo.
  3. Mantener el recorrido de ejecución exclusivamente a través de `SyntheticWorkloadExecutor`; no introducir rutas `subprocess` o comandos libres en planificación/autorización.
  4. Añadir regresiones que prueben que invocaciones directas de la API, consentimiento reutilizado y cambios entre revisión y ejecución no autorizan la campaña.
- **Aceptación:** Sin consentimiento TTY válido no se ejecuta muestra alguna; consentimiento válido solo permite una campaña del digest exacto, y las validaciones del ejecutor actual siguen intactas.
- **Estado observado:** Implementado; el runner revalida propuesta y plan canónicos, guarda ambos artefactos y deniega sin grant o con digest alterado.
- **Prueba:** `python3 -m unittest tests.test_plan_proposals tests.test_tty_authorization tests.test_synthetic_experiment tests.test_synthetic_workload_executor -v`
- **Rollback:** Revertir el commit de M4 con sus rutas explícitas; preservar M1-M3 si siguen siendo no ejecutables, y repetir sus pruebas junto con `python3 -m core.cli show-synthetic-plan`.

### M5 — Pruebas adversariales, documentación y cierre

- **Objetivo:** Demostrar las rutas autorizadas y denegadas, aclarar el alcance de TTY y dejar el estado de autonomía correctamente clasificado.
- **Rutas previstas:** `tests/test_tty_authorization.py`, `tests/test_plan_proposals.py`, `tests/test_synthetic_experiment.py`, `README.md`, `README_es.md`, `docs/SAFETY_CONTRACTS.md`, `docs/ARCHITECTURE.md`, `docs/SYNTHETIC_EXPERIMENT_CONTROLS.md`.
- **Pasos:**
  1. Añadir pruebas PTY en Linux para consentimiento correcto, rechazo, EOF, TTY ausente, digest cambiado e intento de reutilización.
  2. Repetir la batería existente de diez controles y ejecutar la suite completa.
  3. Verificar la CLI y campaña bajo un PTY real con un controlador de prueba automatizado; conservar el manifiesto y no atribuir a ese input una aprobación humana.
  4. Documentar la limitación de procesos con el mismo UID y las capacidades que siguen `NOT_DEMONSTRATED`.
- **Aceptación:** Todas las pruebas pasan; el recibo enlaza los digests aprobados; ninguna prueba o documento afirma identidad autenticada, planificación científica autónoma ni autonomía de hardware.
- **Estado observado:** Implementado y validado. La prueba usa input automatizado de PTY; una persona escribiendo el digest en una sesión real queda `NOT_DEMONSTRATED`.
- **Prueba:** `python3 -m unittest discover -s tests -v && python3 -m compileall -q core tests && git diff --check`
- **Rollback:** Revertir únicamente commits del milestone afectado; no borrar evidencia ni revertir archivos de terceros; reejecutar la suite completa y verificar el diff residual.

## Dependencias

- `M0` → `M1` → `M2` → `M3` → `M4` → `M5`.
- El repositorio se creó el 2026-11-03, después del freeze del 2026-10-30 de los 101 repositorios públicos existentes; no requiere un anuncio `FREEZE-LIFTED` para ese conjunto.
- Plataforma de pruebas: Linux con Python 3; M3-M5 requieren TTY/PTY. M4 conserva el requisito existente de Bubblewrap rootless para una ejecución real.
- No se añade dependencia Python ni servicio externo; el consentimiento reside en memoria y dura una sola invocación.

## Riesgos

- **Riesgo:** Una TTY no demuestra identidad humana frente a malware o procesos del mismo UID.
  - **Mitigación:** No llamarla autenticación; exigir terminal controlador interactivo, mostrar esta limitación en cada ceremonia y excluir explícitamente procesos del mismo UID del modelo de protección.
- **Riesgo:** Un digest puede corresponder al plan validado mientras la ejecución termina usando otro objeto mutado.
  - **Mitigación:** Canonicalizar, mantener la propuesta inmutable y revalidar digests inmediatamente antes del dispatch; añadir pruebas de mutación entre revisión y uso.
- **Riesgo:** Una propuesta de agente introduce campos ejecutables inadvertidos.
  - **Mitigación:** Esquema cerrado con rechazo de campos desconocidos y sin aceptar comandos, rutas, argumentos ni parámetros de workload.
- **Riesgo:** Cambiar el gate en el runner deja un camino alternativo de autorización directa.
  - **Mitigación:** Revisar todos los call sites de `SyntheticExperimentRunner.run`, eliminar el digest como token suficiente y probar rutas CLI y API.
- **Riesgo:** La interpretación del alcance temporal del freeze puede ser incorrecta.
  - **Mitigación:** Se comprobó el timestamp de creación en la API de GitHub contra el anuncio `#10031`; detener cambios si aparece evidencia de que IHRA pertenecía a los 101 repositorios congelados.

## Rollback global

1. Detener la ejecución de campañas; no eliminar ni sobrescribir recibos existentes.
2. Revisar `git status --short` y `git diff --name-status`; aislar los commits y rutas exactas de IHRA de este roadmap.
3. Si hay que revertir, usar `git revert --no-commit <sha-del-commit-del-milestone>` solo después de inspeccionar ese commit y comprobar que no incluye trabajo ajeno; no usar `reset --hard`, `git clean` ni restauraciones globales.
4. Ejecutar `python3 -m unittest discover -s tests -v`, `python3 -m compileall -q core tests` y `git diff --check`; conservar cualquier evidencia de campañas tal como está.

## Próximo paso

- **Primer paso:** Si se requiere demostrar consentimiento de una persona, ejecutar la campaña desde una terminal local interactiva y escribir allí la frase mostrada; la campaña PTY automatizada no demuestra aprobación humana.
- **Comando inicial:** `cd "$HOME/Development/ISyCo Git/hardware-research-agent" && python3 -m core.cli show-synthetic-proposal > /tmp/ihra-proposal.json && python3 -m core.cli review-synthetic-proposal /tmp/ihra-proposal.json && python3 -m core.cli run-synthetic-experiment --proposal /tmp/ihra-proposal.json`
