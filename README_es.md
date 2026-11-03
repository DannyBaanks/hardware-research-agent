# ISyCo Hardware Research Agent (IHRA)

**Protocolo de investigación de hardware en Linux: reproducible, supervisado y orientado a evidencia.**

[English](README.md) · [Pipeline científico](docs/COMMON_RESEARCH_PIPELINE.md) · [Contratos de seguridad](docs/SAFETY_CONTRACTS.md) · [Resultados IHRA-01](docs/SYNTHETIC_EXPERIMENT_CONTROLS.md) · [Roadmap](ROADMAP.md) · [Casos de investigación](#origen-de-la-investigación)

> [!IMPORTANT]
> **Prototipo público de investigación, no un optimizador autónomo de hardware.** IHRA ya permite iniciar explícitamente una campaña sintética fija de cinco muestras, además de descubrir capacidades y reproducir evidencia. **No autoriza ni ejecuta cambios de hardware/sistema ni ofrece recuperación durable para experimentos generales.** No le otorgues privilegios por asumir que toda la seguridad descrita en los documentos ya está implementada.

**Principio:** concluir **«no se encontró una mejora segura»** también es un éxito científico. Este repositorio no promete más FPS, más tokens por segundo ni desbloqueos universales.

## Qué funciona actualmente

| Componente | Comportamiento existente | Límite comprobado en código |
|---|---|---|
| Descubrimiento | Lee datos de CPU, perfiles ACPI y determinadas señales PCI/NPU/NVIDIA | Tiene supuestos de plataforma y rutas específicas; no es un inventario universal |
| [Sentinel](core/sentinel.py) | Deriva el riesgo del comando y solo acepta comandos de solo lectura exactos y permitidos | Deniega cambios de estado; no es sandbox, autenticación de aprobación ni ejecutor |
| [Verificador antifake](core/verifier.py) | Exige un SHA-256 válido por corrida, throughput finito y un delta explícito de control negativo | Los datos los proporciona el llamante; compara hashes, pero no los recalcula desde resultados crudos ni mide hardware |
| [Controlador de rollback](core/rollback.py) | Restringe la ejecución a restaurar y consultar perfiles de energía, sin shell | Es una **pieza independiente**, no está conectada a un ejecutor de experimentos y no demuestra rollback extremo a extremo |
| [Ejecutor dry-run](core/executor.py) | Reproduce dos fixtures sintéticos de evidencia a través del verificador | **No ejecuta workloads, no accede al hardware ni cambia el sistema** |
| [Ejecutor de workload sintético](core/executor.py) | Ejecuta únicamente el workload CPU fijo `sha256-chain-v1` en Bubblewrap rootless, con montajes del host de solo lectura y temporales aislados | No es un ejecutor de benchmarks o experimentos; no acepta comandos arbitrarios, no lee/controla hardware ni escribe estado persistente del host |
| [Orquestador sintético](core/experiment.py) | Ejecuta el plan fijo de cinco muestras solo tras una confirmación TTY local de un uso ligada a los digests de propuesta y plan | El consentimiento TTY no autentica identidad ni protege ante procesos maliciosos del mismo UID; no otorga planificación ni autoridad sobre hardware |
| [Adaptador Ada](adapters/ada_adapter.py) | Detecta ciertos PCI IDs NVIDIA y consulta el parámetro de energía y `nvidia-smi` | Un parámetro del driver **no demuestra** que el unlock esté activo ni que sea seguro |
| [Adaptador REA](adapters/rea_adapter.py) | Localiza herramientas y puede invocar análisis mediante Node o npx | **Ejecuta herramientas externas**; no las aísla ni garantiza ausencia de descargas |
| Wrapper de Node | [bin/ihra.mjs](bin/ihra.mjs) llama al CLI Python | Node solo ofrece otra entrada; el núcleo del CLI es Python |

La [especificación arquitectónica](docs/ARCHITECTURE.md) describe un futuro ejecutor autónomo, un historial de hipótesis y un grabador de evidencia; esos módulos de producción **no están implementados**. `DryRunExecutor` solo reproduce fixtures locales fijos y nunca ejecuta workloads. Los contratos y esquemas expresan objetivos de diseño; no ejecutan esas garantías por existir.

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
| `python3 -m core.cli inspect /ruta/al/archivo --tool inspect_malbolge` | Delega el análisis a REA | Puede ejecutar Node o `npx`; verifica dependencias antes de usarlo |

Sentinel ya no acepta `--human-approved` ni un nivel de riesgo del llamante como autoridad. Un veredicto para una operación de solo lectura **tampoco autoriza su ejecución**. La revisión de propuestas es de solo lectura; estas no pueden incluir comandos ejecutables ni alterar el plan fijo. La campaña exige una confirmación TTY local, nueva y de un solo uso, ligada a los digests canónicos de propuesta y plan. Es una ceremonia de consentimiento, no autenticación de identidad, y no protege ante procesos maliciosos con el mismo usuario. La campaña fija no está conectada a Sentinel, adaptadores de hardware, planes de experimento arbitrarios ni rollback. La evidencia se escribe atómicamente con permisos privados en `$XDG_STATE_HOME/ihra/synthetic-experiments/` (o `~/.local/state/ihra/synthetic-experiments/`); un fallo detiene la secuencia y conserva recibos completados.

## Método científico y trazabilidad

El [pipeline de diez etapas](docs/COMMON_RESEARCH_PIPELINE.md) especifica el **proceso que se desea construir**, no un servicio autónomo ya terminado:

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
- Los cambios de nivel 1/2 siguen deshabilitados: la confirmación TTY solo sirve para la campaña sintética fija; no autentica identidad ni existe un ejecutor de experimentos conectado a rollback obligatorio. El helper independiente de rollback solo admite restaurar y consultar un perfil de energía.

Consulta los [contratos de seguridad](docs/SAFETY_CONTRACTS.md), la [matriz de 12 escenarios adversariales propuestos](docs/ANTIFAKE_TEST_MATRIX.md) y el [plan de implementación](docs/IMPLEMENTATION_PLAN.md) para conocer el diseño de seguridad previsto.

## Origen de la investigación

Estos son **casos documentados que inspiraron IHRA**, no optimizaciones que el agente aplique automáticamente:

**A. RTX 4050 Laptop y la trampa `D3cold` de Optimus.** El desbloqueo al arranque parecía correcto según el controlador, pero una transición de energía restauraba registros volátiles mientras el getter seguía informando velocidad completa. El control determinante fue comparar estado BAR0 y throughput físico. En una laptop, la mejor microprueba tensorial se acercó a **2×**, mientras la ganancia de GEMM cuBLAS fue menor y dependiente del workload. Véase el [caso IHRA](docs/HISTORY_ADA_D3COLD.md), la [evidencia original](https://github.com/DannyBaanks/gpu-unlock-ada/tree/main/docs/evidence/ada28a1) y la [PR upstream #134](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134).

**B. Ryzen 5 8645HS y REA-CPU-01.** Una matriz contrabalanceada de perfiles de energía reportó cerca de **+12.6 % de frecuencia sostenida**, pero **+5.3 % de throughput** en una microprueba CPU determinada de una HP Victus. Se detectó la NPU XDNA sin demostrar inferencia útil mediante su stack de usuario, y no se identificó una ruta de escritura segura y soportada para los controles SMU estudiados. Véase el [caso CPU](docs/HISTORY_REA_CPU_01.md). **No implica un incremento universal de CPU.**

### Herramientas relacionadas

- [REA](https://github.com/DannyBaanks/rea): herramientas externas de ingeniería inversa con requisitos y riesgos propios.
- [gpu-unlock-ada](https://github.com/DannyBaanks/gpu-unlock-ada): investigación separada sobre kernel y firmware; IHRA **no la instala ni ejecuta automáticamente**.

## Pruebas y alcance de la evidencia

```bash
python3 -m unittest discover -s tests -v
```

Las pruebas `unittest` cubren adaptadores simulados, denegaciones de Sentinel, rollback restringido, límites del verificador, hashes requeridos, replay de fixtures, el executor fijo y diez controles adversariales sintéticos de campaña. El [informe de controles IHRA-01](docs/SYNTHETIC_EXPERIMENT_CONTROLS.md) separa las simulaciones de las comprobaciones reales de procesos en Linux y registra la evidencia verificada de campaña y timeout. La campaña de cinco muestras solo demuestra repetibilidad, no rendimiento ni optimización del hardware. No se implementan planificación autónoma, autorización general, cambios de hardware ni recuperación general ante fallos.

Ver [fixtures](fixtures/), [esquema de experimento](schemas/experiment.schema.json) y [esquema de evidencia](schemas/evidence.schema.json). Las simulaciones **no son logs crudos de una máquina real**.

## Seguridad, procedencia y publicación

- La investigación de solo lectura es la **política de diseño**, no garantía de aislamiento por el sistema operativo.
- Los contratos prohíben firmware flashing, escritura directa SMU/MSR y la desactivación de protecciones. La implementación actual basada en texto **no sustituye un ejecutor con denegación por defecto a nivel del sistema**.
- IHRA no incluye ni recomienda overclock, undervolt, cambios de BIOS o desbloqueo de protecciones térmicas automáticos.
- La publicación de trazas exige revisar nombres de equipos, seriales, UUID, tokens y otros identificadores privados.

**Licencia: MIT.** El archivo raíz [`LICENSE`](LICENSE) contiene la concesión MIT y `package.json` declara el mismo identificador. DannyBaanks eligió MIT; la propuesta anterior de Apache-2.0 queda reemplazada.

**Estado actual:** **prototipo público HRA-00**. El [ROADMAP.md](ROADMAP.md) es la propuesta histórica de fases; las menciones antiguas a un repositorio aún no publicado no describen el estado actual.
