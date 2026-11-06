# IHRA — Referencia técnica

[← Volver al README](../README_es.md) · [English](TECHNICAL.md)

Esta página contiene la descripción precisa, componente por componente, de lo que IHRA implementa y dónde terminan sus garantías. Fue el cuerpo del README hasta el 2026-11-03.

## Qué funciona actualmente

| Componente | Comportamiento existente | Límite comprobado en código |
|---|---|---|
| Descubrimiento | Lee datos de CPU, perfiles ACPI y determinadas señales PCI/NPU/NVIDIA | Tiene supuestos de plataforma y rutas específicas; no es un inventario universal |
| [Sentinel](../core/sentinel.py) | Deriva el riesgo del comando y solo acepta comandos de solo lectura exactos y permitidos; `evaluate_tier1()` clasifica aparte un único cambio consentido | `evaluate_plan()` — la ruta que cualquier llamante desatendido puede alcanzar — deniega todo cambio de estado. No es sandbox de ejecución ni sistema de aprobación autenticado; la ruta Tier-1 existe solo para una persona que teclea en esta TTY |
| [Verificador antifake](../core/verifier.py) | Exige un SHA-256 válido por corrida, throughput finito y un delta explícito de control negativo | Los datos los proporciona el llamante; compara hashes, pero no los recalcula desde resultados crudos ni mide hardware |
| [Controlador de rollback](../core/rollback.py) | `RollbackController` restringe la ejecución a un par restaurar/consultar de perfil de energía, sin shell; `RollbackContract` implementa `capture_prestate` / `execute_rollback` / `verify_restoration` sobre los seis nodos monitoreados del estado CPU | Ya está conectado a ambos ejecutores y el estado se deriva de relecturas, pero la verificación es comparación de observaciones, no autenticación: los recibos se hashan en el manifiesto, no se firman |
| [Ejecutor Tier-1](../core/tier1.py) | Aplica exactamente un `powerprofilesctl set <perfil>` solo después de una confirmación TTY local de un uso ligada al digest del plan **y** al digest de los bytes de la intervención, y luego prueba que el host volvió al prestate capturado | Es la única ruta de IHRA que escribe estado persistente del host y está sujeta a consentimiento, no es autonomía. Si la restauración no se puede verificar, sella una falla de emergencia y bloquea las campañas posteriores |
| [Inspector y estado de falla](../core/recovery.py) | Relee la evidencia sellada contra su manifiesto, re-deriva el estado del rollback en vez de creer la etiqueta guardada y lista o cierra registros de emergencia en solo lectura | Reporta; no repara. Cerrar una falla exige que una persona haya regresado el host, y no existe supervisor de arranque |
| [Ejecutor dry-run](../core/executor.py) | Reproduce dos fixtures sintéticos de evidencia a través del verificador | **No ejecuta workloads, no accede al hardware ni cambia el sistema** |
| [Ejecutor de workload sintético](../core/executor.py) | Ejecuta únicamente el workload CPU fijo `sha256-chain-v1` en Bubblewrap rootless, con montajes del host de solo lectura y temporales aislados | No es un ejecutor de benchmarks o experimentos; no acepta comandos arbitrarios, no lee/controla hardware ni escribe estado persistente del host |
| [Orquestador sintético](../core/experiment.py) | Ejecuta el plan fijo de cinco muestras solo tras una confirmación TTY local de un uso ligada a los digests de propuesta y plan; revalida el vencimiento monotónico del permiso antes de cada muestra y sella `prestate.json`, `poststate.json` y `rollback.json` con la evidencia | El consentimiento TTY no autentica identidad ni protege ante procesos maliciosos del mismo UID; no otorga planificación ni autoridad sobre hardware. Una campaña sin evidencia de rollback coherente es `INCOMPLETE`, no un pase |
| [Adaptador Ada](../adapters/ada_adapter.py) | Compara la función de video NVIDIA con los IDs Ada exactos que conoce el módulo gpu-unlock-ada; lee `DynamicPowerManagement` de `/proc/driver/nvidia/params`, el estado de energía PCI según el kernel, los parámetros del módulo `gpu_unlock` y, opcionalmente, `nvidia-smi` | Todo es estado reportado por software: un parámetro o un módulo cargado **no demuestra** que el unlock esté activo ni que sea seguro |
| [Adaptador REA](../adapters/rea_adapter.py) | Localiza un clon local de REA (con `node`) o un CLI `rea` instalado y ejecuta un comando del CLI de REA (por defecto `inspect-artifact`) | **Ejecuta herramientas externas** y no las aísla. Nunca recurre a `npx` (sin descargas de paquetes); rechaza herramientas solo-MCP como `inspect_malbolge` |
| Wrapper de Node | [bin/ihra.mjs](../bin/ihra.mjs) llama al CLI Python | Node solo ofrece otra entrada; el núcleo del CLI es Python |

La [especificación arquitectónica](ARCHITECTURE.md) describe un futuro ejecutor autónomo, un historial de hipótesis y un grabador de evidencia; esos módulos de producción **no están implementados**. `DryRunExecutor` solo reproduce fixtures locales fijos y nunca ejecuta workloads. Los contratos y esquemas expresan objetivos de diseño; no ejecutan esas garantías por existir.

## Inicio rápido responsable (Linux)

**Requisitos:** Python 3 y su biblioteca estándar. El comando de workload fijo también requiere `/usr/bin/bwrap` y `/usr/bin/python3` confiables (propiedad de root) y user namespaces rootless habilitados. Node.js es opcional; algunas consultas necesitan herramientas del sistema instaladas.

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent

# Consultar la interfaz y ejecutar las pruebas disponibles
python3 -m core.cli --help
python3 -m unittest discover -s tests -v

# Consultar inventario y telemetría seleccionada
python3 -m core.cli discover

# Reproducir evidencia sintética (no ejecuta workloads)
python3 -m core.cli dry-run ada_d3cold_false_positive

# Ejecutar el único workload CPU fijo en sandbox rootless
python3 -m core.cli run-synthetic sha256-chain-v1

# Consultar el plan y exportar una propuesta estricta que no es ejecutable
python3 -m core.cli show-synthetic-plan
python3 -m core.cli show-synthetic-proposal > /tmp/ihra-proposal.json
python3 -m core.cli review-synthetic-proposal /tmp/ihra-proposal.json

# Revisa la propuesta exacta y autoriza interactivamente desde la TTY controladora
python3 -m core.cli run-synthetic-experiment --proposal /tmp/ihra-proposal.json

# Alternativa usando Node.js
node bin/ihra.mjs discover
```

> [!NOTE]
> El descubrimiento no busca modificar la configuración persistente, pero herramientas como `nvidia-smi` pueden despertar o inicializar la GPU dedicada en ciertos sistemas. El inventario es una **pista de capacidades**, no una prueba independiente de rendimiento.

### Referencia de comandos y advertencias

| Comando | Función | Precaución |
|---|---|---|
| `python3 -m core.cli discover` | Inventario y lectura de adaptadores | Parte de la detección usa rutas o identificadores específicos |
| `python3 -m core.cli setup` | Informa ubicaciones de proyectos relacionados | Comprueba **rutas de desarrollo fijadas en el código**; no constituye verificación universal de instalación |
| `python3 -m core.cli ada` | Lee estado NVIDIA y su parámetro de gestión de energía | Informa `NOT_DEMONSTRATED`; el parámetro no prueba estado físico persistente ni seguridad del unlock |
| `python3 -m core.cli sentinel 'powerprofilesctl get'` | Comprueba un comando exacto de solo lectura contra una lista permitida | Solo clasifica; no ejecuta. Deniega cambios de estado y comandos no incluidos |
| `python3 -m core.cli dry-run <fixture>` | Reproduce uno de dos fixtures sintéticos incluidos usando el verificador | Solo usa datos fixture; no ejecuta workloads/comandos, no lee ni modifica hardware y no hace rollback |
| `python3 -m core.cli run-synthetic sha256-chain-v1` | Ejecuta una cadena fija de 20.000 pasos SHA-256 en un sandbox rootless de Bubblewrap | Requiere Bubblewrap y Python del sistema confiables; no acepta comandos o scripts, no accede a telemetría/control de hardware ni persiste cambios en el host |
| `python3 -m core.cli show-synthetic-proposal` | Emite la propuesta JSON estricta del único plan sintético elegible | La propuesta es dato inerte; no concede autoridad de ejecución |
| `python3 -m core.cli review-synthetic-proposal <archivo>` | Valida y muestra una propuesta sin ejecutarla ni autorizarla | Rechaza campos desconocidos, comandos, workloads o límites modificados |
| `python3 -m core.cli run-synthetic-experiment --proposal <archivo>` | Muestra la propuesta exacta, exige escribir `AUTHORIZE <digest-del-plan>` en la TTY controladora y ejecuta cinco muestras | Sin TTY, EOF, cancelación o digest distinto, deniega; la confirmación TTY no autentica identidad ni protege frente a procesos del mismo UID |
| `python3 -m core.cli inspect-synthetic-campaign <id-de-campaña>` | Lee la evidencia y comprueba manifiesto, plan, propuesta, checkpoint, recibos y la terna de rollback | Solo lectura; no reanuda ni ejecuta. Re-deriva el estado del rollback desde los estados sellados. Las campañas históricas sin los enlaces actuales siguen `NOT_DEMONSTRATED` |
| `python3 -m core.cli run-tier1 --set <perfil>` | Construye el plan de un cambio de perfil, lee del host cómo volver, pide `AUTHORIZE <digest-del-plan>` en esta TTY, aplica, mide, regresa y comprueba | Sale 0 solo con `DEMONSTRATED`; 1 si lo niegan o la restauración falla; 130 si interrumpes el prompt; 2 por argumentos inválidos. Escribe estado del host **solo** con un humano confirmando estos bytes exactos. `--plan` acepta un plan JSON estricto en su lugar y ambas opciones son mutuamente exclusivas |
| `python3 -m core.cli list-faults` | Imprime los registros de emergencia sin cerrar | Solo lectura; si queda alguno sale 1, y esa misma falla sin resolver deniega la campaña siguiente |
| `python3 -m core.cli resolve-fault <id>` | Relee el host, lo compara con lo que el registro exige y solo entonces lo cierra | Nunca cambia el host. Exige un identificador de 32 hex; rechaza rutas arbitrarias |
| `python3 -m core.cli inspect /ruta/al/archivo [--tool inspect-with-objdump]` | Delega el análisis a un comando del CLI de REA (por defecto `inspect-artifact`) | Arranca el runtime Node local de REA; sale con código distinto de cero si REA falla |

Sentinel ya no acepta `--human-approved` ni un nivel de riesgo del llamante como autoridad. Un veredicto para una operación de solo lectura **tampoco autoriza su ejecución**. La revisión de propuestas es de solo lectura; estas no pueden incluir comandos ejecutables ni alterar el plan fijo. La campaña exige una confirmación TTY local, nueva y de un solo uso, ligada a los digests canónicos de propuesta y plan —el aviso se escribe sólo después de descartar la entrada ya encolada en el terminal, así que una línea tecleada antes de ver la propuesta no puede responderlo—, con un vencimiento monotónico cuyo tope de 120 segundos está fijado en el código: el llamante puede acortar la vida del permiso, nunca alargarlo, y ni una bandera ni una variable de entorno lo modifican; se revalida antes de despachar y antes de cada muestra. Es una ceremonia de consentimiento, no autenticación de identidad, y no protege ante procesos maliciosos con el mismo usuario. La campaña fija no está conectada a Sentinel, a **controles** de hardware ni a planes de experimento arbitrarios; desde IHRA-04 sí lee la superficie monitoreada de energía CPU y sella un resultado de rollback, pero no declara ninguna intervención, así que su evidencia prueba que esa superficie no se movió. La evidencia se escribe atómicamente con permisos privados en `$XDG_STATE_HOME/ihra/synthetic-experiments/` (o `~/.local/state/ihra/synthetic-experiments/`); un fallo detiene la secuencia y conserva recibos completados.

El inspector de campañas es de solo lectura y no infiere abandono por antigüedad o ausencia de PID. Devuelve `ACTIVE_OR_UNKNOWN` para una campaña persistida como `RUNNING`, `INCOMPLETE` / `NOT_DEMONSTRATED` si faltan evidencias o hay inconsistencias, y `COMPLETED_VALID` solo cuando coinciden propuesta, plan, checkpoint terminal, cinco recibos, la terna `prestate`/`poststate`/`rollback` y el manifiesto — y además el estado de rollback re-derivado de los dos estados sellados coincide con la etiqueta registrada. La evidencia histórica sigue siendo legible, pero no se eleva a `COMPLETED_VALID` si carece de los enlaces actuales de propuesta/checkpoint o de los artefactos de rollback.

También se probaron en Linux dos interrupciones reales con `SIGKILL` al proceso padre: mientras el workload fijo estaba activo dentro de Bubblewrap y después de persistir un recibo, antes de la siguiente muestra. En ambos casos terminan los descendientes del sandbox identificados, se conserva la evidencia previa verificable y la inspección no declara éxito. Son corridas aisladas de harness con evidencia temporal; no producen un paquete independiente ni prueban autorización humana. En la ruta Tier-1, `SIGINT`, `SIGTERM`, `SIGHUP` y `SIGQUIT` sí están cubiertos: la señal se atiende dentro de la ventana protegida, se restaura el perfil y se relee antes de reportar. `SIGKILL` sigue siendo inalcanzable para cualquier guarda, y la pérdida de energía, los fallos de almacenamiento y la recuperación/reanudación automática siguen sin probarse o no están disponibles.

## Método científico y trazabilidad

El [pipeline de diez etapas](COMMON_RESEARCH_PIPELINE.md) especifica el **proceso que se desea construir**, no un servicio autónomo ya terminado:

```text
DISCOVER → BASELINE → HYPOTHESIZE → CONTROL → RISK REVIEW
  → AUTHORIZE → EXPERIMENT → VERIFY → RESTORE → EVIDENCE / RETURN
```

Antes de declarar una mejora se deben comparar condiciones equivalentes, usar controles negativos, ejecutar un workload reproducible, revisar correctitud matemática y obtener evidencia física independiente cuando sea posible. Después debe documentarse la restauración del sistema. Los resultados `REFUTED`, `NOT_DEMONSTRATED`, `NO_SAFE_GAIN_FOUND` o `DENY` no se maquillan como éxitos.

### Limitaciones actuales del verificador y Sentinel

- El verificador exige **al menos tres muestras por grupo**, hashes SHA-256 válidos en **todas** las corridas, throughput finito y un delta explícito del control negativo; compara **medianas**. No es una prueba formal de significación y la evidencia del control sigue siendo proporcionada por el llamante.
- Compara hashes **proporcionados**; no los recalcula desde artefactos crudos.
- La regla para el falso positivo de `D3cold` rechaza una declaración de desbloqueo cuando el throughput permanece en stock. El programa **no lee BAR0 directamente** para demostrar la causa.
- Sentinel deriva el riesgo y solo permite comandos exactos de solo lectura incluidos en una lista; deniega el resto, incluidos benchmarks arbitrarios. Es un clasificador conservador, no un sandbox impuesto por el sistema operativo.
- El nivel 1 solo es ejecutable con un humano en esta TTY, y el nivel 2 no existe como ruta de código. `run-tier1` puede fijar un perfil `powerprofilesctl` permitido después de que la confirmación ate el digest del plan y el digest de la intervención, y se niega a dar por terminada la corrida si la restauración no se releyó y verificó. `evaluate_plan()` — todo lo que un llamante desatendido puede alcanzar — sigue denegando cualquier cambio de estado, así que no existe autoridad autónoma sobre el hardware.
- El nivel 1 se ejercitó cinco veces sobre el hardware real el 2026-11-06, con el operador tecleando cada confirmación en su propia terminal. La primera: `run-tier1 --set performance` cerró como `RESTORED_VERIFIED` en 0,84 s; `powerprofilesctl get` y todos los demás campos monitorizados (governor, EPP, boost, driver) volvieron al prestate capturado, los siete artefactos verificaron contra el manifiesto de la campaña y el ledger de fallos quedó vacío. Un defecto que esta ronda dejó ver —el plan generado declaraba `workload.repetitions = 3` porque el validador exige al menos tres, mientras el ejecutor Tier-1 registraba **una** observación tras el cambio y reportaba `samples_completed: 1`— está cerrado: el ejecutor Tier-1 ahora observa la superficie monitorizada una vez por repetición declarada, reporta ese recuento observado y rechaza cualquier plan que declare más de `MAX_TIER1_OBSERVATIONS` (16), para que una sola campaña autorizada no pueda convertirse en un bucle de lectura sin límite. Las observaciones son lecturas de archivos, así que los comandos con autorización de escritura siguen siendo exactamente dos: el cambio y la restauración. La segunda ronda probó el camino de interrupción: `SIGTERM` llegó después de que el cambio autorizado ya estaba en el host pero antes de sellar el recibo de intervención; el handler restauró el perfil y la campaña cerró `ABORTED`/`REFUTED` con `RESTORED_VERIFIED`, `differences` vacío y sin registro de fault. Su bundle trae cinco artefactos y ningún `measurement.json`, que es lo que honestamente significa una señal tan temprana. Dos rondas más cerraron lo que aquella dejó en los tests: los bundles `2ac69773d17b43f5be4e11fb3b656bb5` y `f1308fed1c4c4074b4f50d0b9e178054` sellan cada uno `repetitions_declared: 3` con `samples_recorded: 3`, tres observaciones de la superficie monitorizada tras el cambio, `RESTORED_VERIFIED` y siete artefactos que verifican contra su manifiesto, así que el recuento de repeticiones está ahora demostrado sobre hardware y no sólo por `tests/test_tier1_safety.py`. Las tres muestras caen dentro de un mismo segundo, de modo que esas rondas prueban que el recuento declarado se honra, no la varianza ni la estabilidad temporal. Una quinta invocación del mismo comando fue rechazada antes de tocar el host por un registro de emergencia sin resolver — `status DENIED`, `samples_completed: 0`, sin abrir carpeta de campaña — y el registro se cerró después con `resolve-fault`, que relee el host y limpia el ledger sólo cuando coinciden; ese control usó un registro de fault **inducido** a través de `core.recovery.write_fault_record`, porque en esta máquina no falló ninguna restauración, así que demuestra el gate y la resolución verificada, no una recuperación ante una deriva real. La quinta ronda, bundle `27a6f34f2c0b4a3c9ff8665b6ebda44c` a las 12:48Z, corrió ya corregidos los dos hallazgos de esa sesión y sirve de control de no regresión del drenado de cola del TTY: una autorización tecleada **después** del aviso sigue completando, con `RESTORED_VERIFIED`, `samples_recorded: 3` dentro de un mismo segundo y siete artefactos que verifican contra su manifiesto. Esa ronda además obligó a retirar una afirmación que se había hecho sobre la ceremonia: el bloque pegado en varias líneas lo consumió el editor de línea de la shell, nunca la lectura de confirmación, así que F2 era un camino latente del código demostrado en pseudo-terminal, y no un esquive observado en vivo. Ver `plans/ihra-autonomy-safety-prerequisites.md`, *Ronda Tier-1 real consentida* y *Ronda Tier-1 completa y gate de fault en el host real*.
- El helper de rollback ya no es una pieza aparte: `RollbackContract` corre dentro de ambos ejecutores y el estado registrado se deriva de lecturas antes/después. Lo que sigue sin ofrecer es autenticación (los recibos se hashan, no se firman), un supervisor que repare o restaure el host al siguiente arranque, y cualquier rollback para una caída fuera de la ventana protegida.

Consulta los [contratos de seguridad](SAFETY_CONTRACTS.md), la [matriz de 12 escenarios adversariales propuestos](ANTIFAKE_TEST_MATRIX.md) y el [plan de implementación](IMPLEMENTATION_PLAN.md) para conocer el diseño de seguridad previsto.

## Origen de la investigación

Estos son **casos documentados que inspiraron IHRA**, no optimizaciones que el agente aplique automáticamente:

**A. RTX 4050 Laptop y la trampa `D3cold` de Optimus.** El desbloqueo al arranque parecía correcto según el controlador, pero una transición de energía restauraba registros volátiles mientras el getter seguía informando velocidad completa. El control determinante fue comparar estado BAR0 y throughput físico. En una laptop, la mejor microprueba tensorial se acercó a **2×**, mientras la ganancia de GEMM cuBLAS fue menor y dependiente del workload. Véase el [caso IHRA](HISTORY_ADA_D3COLD.md), la [evidencia original](https://github.com/DannyBaanks/gpu-unlock-ada/tree/main/docs/evidence/ada28a1) y la [PR upstream #134](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134).

**B. Ryzen 5 8645HS y REA-CPU-01.** Una matriz contrabalanceada de perfiles de energía reportó cerca de **+12.6 % de frecuencia sostenida**, pero **+5.3 % de throughput** en una microprueba CPU determinada de una HP Victus. Se detectó la NPU XDNA sin demostrar inferencia útil mediante su stack de usuario, y no se identificó una ruta de escritura segura y soportada para los controles SMU estudiados. Véase el [caso CPU](HISTORY_REA_CPU_01.md). **No implica un incremento universal de CPU.**

### Herramientas relacionadas

- [REA](https://github.com/DannyBaanks/rea): herramientas externas de ingeniería inversa con requisitos y riesgos propios.
- [gpu-unlock-ada](https://github.com/DannyBaanks/gpu-unlock-ada): investigación separada sobre kernel y firmware; IHRA **no la instala ni ejecuta automáticamente**.

## Pruebas y alcance de la evidencia

```bash
python3 -m unittest discover -s tests -v
```

Las pruebas `unittest` cubren adaptadores simulados, denegaciones de Sentinel, rollback restringido, límites del verificador, hashes requeridos, replay de fixtures, el executor fijo, diez controles adversariales sintéticos de campaña, inspección read-only, cortes simulados en fronteras de persistencia y dos interrupciones reales con `SIGKILL` al padre en Linux/Bubblewrap. IHRA-04 sumó cuatro suites: 14 pruebas de vencimiento y ligadura del permiso, 16 de validación de planes en Sentinel —que además anclan el schema contra el validador—, 9 de que una campaña selle evidencia de rollback coherente y 9 de seguridad Tier-1 que recorren el ciclo completo de consentimiento, intervención, señal y falla contra un host `powerprofilesctl` simulado, incluida la ruta desatendida denegada sin ejecutar un solo subprocess. La suite son 191 pruebas. El [informe de controles IHRA-01](SYNTHETIC_EXPERIMENT_CONTROLS.md) separa simulaciones y procesos reales. La campaña de cinco muestras solo demuestra repetibilidad, no rendimiento ni optimización del hardware. Se demostraron checkpoints, inspección read-only y esos dos cortes específicos; la reanudación automática, pérdida de energía o fallos de almacenamiento, la planificación autónoma, la autorización general y los cambios de hardware desatendidos no están demostrados ni disponibles.

Ver [fixtures](../fixtures/), [esquema de experimento](../schemas/experiment.schema.json) y [esquema de evidencia](../schemas/evidence.schema.json). Las simulaciones **no son logs crudos de una máquina real**.

## Seguridad, procedencia y publicación

- La investigación de solo lectura es la **política de diseño**, no garantía de aislamiento por el sistema operativo.
- Los contratos prohíben firmware flashing, escritura directa SMU/MSR y la desactivación de protecciones. La implementación actual basada en texto **no sustituye un ejecutor con denegación por defecto a nivel del sistema**.
- IHRA no incluye ni recomienda overclock, undervolt, cambios de BIOS o desbloqueo de protecciones térmicas automáticos.
- **Fuera de los contratos de arriba:** `experiments/cpu-boost/run_boost_matrix.py` escribe `/sys/devices/system/cpu/cpufreq/boost` con `sudo`, y `recipes/smart-boost/install.sh` instala una regla udev y un servicio que siguen escribiendo ese mismo interruptor. Los dos releen el nodo y lo restauran alrededor de cada trabajo y ante señales, pero lo hacen **por fuera** del gate de Sentinel, de `PlanAuthorization`, de `RollbackController`, de `RollbackContract` y del manifiesto de campaña; su manejador de señal imprime la relectura en lugar de compararla con el valor capturado. Cerrar esa brecha es trabajo de IHRA-05; por ahora este documento no describe ese script como una capacidad.
- La publicación de trazas exige revisar nombres de equipos, seriales, UUID, tokens y otros identificadores privados.

**Licencia: MIT.** El archivo raíz [`LICENSE`](../LICENSE) contiene la concesión MIT y `package.json` declara el mismo identificador. DannyBaanks eligió MIT; la propuesta anterior de Apache-2.0 queda reemplazada.

**Estado actual:** **prototipo público HRA-00**. El [ROADMAP.md](../ROADMAP.md) es la propuesta histórica de fases; las menciones antiguas a un repositorio aún no publicado no describen el estado actual.
