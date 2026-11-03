# ROADMAP — ISyCo Hardware Research Agent (IHRA)
## De la investigación Ada `D3cold` y REA-CPU-01 a un pipeline portátil de investigación de hardware

**Estado:** PROPOSAL / DEFERRED — **no implementar todavía**  
**Fecha:** 2026-11-02  
**Destino:** futuro repositorio independiente de metodología y herramientas para agentes en Linux  
**Propietario conceptual:** ISyCo / Danny  
**Condición de arranque:** el agente encargado de **REA-CPU-01** debe **terminar, cerrar y sellar** su investigación actual antes de ejecutar este roadmap.  
**Principio rector:** *«No es un optimizador mágico. Es un protocolo de investigación reproducible y supervisada, capaz de concluir que NO hay una optimización segura».*

> **IMPORTANTE PARA EL AGENTE FUTURO:** este archivo es una guía diferida. No autoriza cambios de BIOS, UEFI, ACPI, SMU, MSR, firmware, voltajes, límites térmicos, módulos kernel ni instalación de software. Tampoco autoriza crear/publicar un repositorio sin confirmar con el usuario. Preservar el experimento CPU vigente; no interrumpirlo ni anticipar conclusiones.

---

## 0. Misión y alcance

Una vez cerrado REA-CPU-01, reconstruir **cómo** se llegó desde una hipótesis de rendimiento de la RTX 4050 Laptop a descubrir el **falso positivo de `D3cold`**, verificar los registros y el rendimiento reales, corregir la interpretación, y después investigar si el Ryzen 5 8645HS tiene oportunidades **medibles** de optimización. Extraer de ambos casos un **pipeline de uso general para un agente ejecutándose localmente en Linux**, no una receta universal para desactivar protecciones o acelerar cualquier equipo.

### Objetivos

1. **Reconstrucción causal y cronológica:** registrar las hipótesis originales, los resultados negativos, el descubrimiento del problema de estado del dispositivo, las pruebas de control, los cambios de configuración y los hallazgos posteriores de CPU. Separar lo que se sabía entonces de lo inferido después.
2. **Extracción de método:** convertir los experimentos en un proceso documentado `DISCOVER → BASELINE → HYPOTHESIZE → CONTROL → AUTHORIZE → EXPERIMENT → VERIFY → ROLLBACK → REPORT`.
3. **Portabilidad segura:** diseñar adaptadores para herramientas y familias de hardware con *capability discovery*, permisos mínimos y operaciones acotadas. Un agente nunca presupone que otro dispositivo reproduce el hallazgo de la Victus.
4. **Reproducibilidad:** preservar entradas, herramientas, versiones, estados de energía, código probado, logs crudos, hashes y controles negativos; no confundir estado reportado con ejecución física.
5. **Resultados negativos legítimos:** cerrar investigaciones con `NOT_DEMONSTRATED` o `NO_SAFE_GAIN_FOUND` sin inventar optimizaciones.

### No-objetivos de V1

- No prometer mejoras de FPS, GHz o tokens/s a un porcentaje fijo.
- No ejecutar automáticamente `gpu-unlock-ada` ni otras modificaciones de hardware.
- No distribuir cargas de firmware, payloads, ROMs o materiales de terceros sin verificar licencias y derechos.
- No hacer overclock/undervolt automático; no desactivar límites de potencia, protecciones térmicas, Secure Boot o controles del kernel.
- No usar telemetría de terceros sin autorización; no subir inventarios, seriales o identificadores de máquinas por defecto.
- No atribuir a REA-CPU-01 resultados que todavía no haya obtenido.

---

## 1. Referencias de origen (conservar como fuentes, no como verdades universales)

### A. Caso de estudio GPU: RTX 4050 AD107M

- **PR de origen:** [JustAResearcher/gpu-unlock-ada #134](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134).
- **Título:** `ad107: admit 10de:28a1 (RTX 4050 Laptop, AD107M) and document Optimus D3cold power-rail trap`.
- **Rama del fork al momento de preparar este roadmap:** `DannyBaanks:canary-28a1-ad107`.
- **Reporte:** [`docs/ada-28a1-report.md`](https://github.com/JustAResearcher/gpu-unlock-ada/blob/6e1035c87fc71d25e5c8c795e6b13cf631ffefa7/docs/ada-28a1-report.md).
- **Paquete de evidencia:** [`docs/evidence/ada28a1/`](https://github.com/JustAResearcher/gpu-unlock-ada/pull/134/files).
- **Estado de PR observado:** abierta; no asumir que upstream la haya aceptado. Verificar de nuevo al comenzar.

**Cadena que se debe reconstruir con evidencia, no con retrospectiva inventada:**

1. **Identificación de silicio:** HP Victus, RTX 4050 Laptop `10de:28a1`, AD107M, 20 SMs; comparar únicamente con dispositivos explícitamente documentados por el proyecto.
2. **Hipótesis inicial de desbloqueo:** estado de tasa de emisión de instrucciones tensoriales; no confundir con duplicar toda la GPU.
3. **Pruebas de arranque y estado del driver:** los primeros ciclos indicaron éxito en la transacción de unlock y lectura de selector; el throughput físico seguía stock.
4. **Control negativo decisivo:** con gestión de energía dinámica (`NVreg_DynamicPowerManagement=0x02`), el dispositivo entra en `D3cold`, pierde alimentación de la dGPU y al regresar se restablecen ciertos registros físicos. El getter del driver/GSP-RM podía seguir indicando el estado anterior `0 0 0`. **Un valor de control/software fue un falso positivo sobre el estado del silicio.**
5. **Triangulación:** combinar `gpu_peek` (BAR0 en distintos estados), logs de kernel, estado de energía ACPI/PCIe, herramientas de NVIDIA y throughput tensorial real.
6. **Control de condición:** la PR documenta que, al impedir `D3cold` en ese entorno, el estado físico y la velocidad tensorial se conservaron. Esto es **una observación contextual**, NO una instrucción para aplicar la configuración a terceros.
7. **Pruebas cuantitativas:** en esa tarjeta y workloads registrados, `f16_f32` ~25.320→50.682 Top/s; `tf32` ~12.660→25.281 Top/s, control `u4_s32` ~201.30→201.84 Top/s. Los `cuBLAS GEMM` grandes mostraron ganancias menores, dependiendo de la forma y precisión. Se reportaron controles de correctitud con cero discrepancias en los casos probados. **No declarar «PC 2× más rápida».**
8. **Documentación y PR:** archivos crudos, hashes, alcance limitado a un equipo, tests y advertencias de plataforma.

**Advertencia:** estas cifras proceden de los artefactos del PR. El agente debe comprobar el HEAD exacto, los hashes y las ejecuciones al elaborar su informe; no presuponer reproducibilidad externa ni generalizar a otros portátiles.

### B. Caso de estudio CPU: Ryzen 5 8645HS / REA-CPU-01

**Estado al crear el roadmap: INVESTIGACIÓN EN CURSO — RESULTADO DESCONOCIDO.**

Observaciones preliminares de la sesión que requieren confirmación por el agente:

- CPU: AMD Ryzen 5 8645HS, Zen 4, 6 núcleos y 12 hilos; las capacidades reales se verifican con inventario físico.
- La sesión reportó un perfil energético `low-power`; **esto no demuestra estrangulamiento** sin medición comparativa.
- Se identificó un dispositivo NPU AMD XDNA bajo Linux (`amdxdna`, `/dev/accel/accel0`); **dispositivo reconocido ≠ inferencia útil probada**.
- Se plantearon hipótesis de ACPI/SMU, PPT/STAPM y gestión energética. **No inferir que el firmware admite ajustes ocultos o que exista un 2× equivalente al de Ada.**
- ISyInfer-Zig puede servir de workload de aplicación; sus mejoras anteriores por concurrencia/AVX-512 no demuestran por sí mismas una restricción firmware del CPU.

**Insertar después del cierre real de REA-CPU-01:** commit, estado limpio/sucio, árbol de evidencia, herramientas ejecutadas, matriz A/B, valores con incertidumbre, riesgos, decisiones y verdictos. Si el resultado es negativo, se incorpora con el mismo rigor.

### C. Otros referentes (módulos opcionales, no dependencias obligatorias)

- REA: inspector local, siempre inventariar capacidades antes de usarlo.
- `gpu-unlock-ada`: **caso de estudio y herramienta especializada opt-in, nunca plugin genérico autoaplicable**.
- Linux: `lscpu`, `lspci`, `sysfs`, `powerprofilesctl`, sensores/telemetría, `perf` cuando esté permitido; no suponer disponibilidad.
- ISyInfer: workload reproducible opcional para el dueño del equipo; no dependencia central del producto.
- `amdxdna`/XRT: descubrimiento y exploración solo con software/documentación soportados.
- ISyCo Sentinel: modelo conceptual `EXECUTE / DENY / RETURN`; aplicar decisiones de autorización con un único executor responsable. No asumir dependencia runtime con el core histórico.

---

## 2. Gate de activación: esperar REA-CPU-01

**El agente NO puede avanzar a la implementación del pipeline hasta verificar todos:**

- [ ] El trabajo REA-CPU-01 terminó; no hay benchmark, experimento o escritura de evidencia activo.
- [ ] Existe un cierre firmado o reporte equivalente que clasifica cada hipótesis como `DEMONSTRATED`, `INFERRED`, `REFUTED`, `NOT_DEMONSTRATED` o `REQUIRES_SEPARATE_AUTHORIZATION`.
- [ ] Se preservaron logs crudos, entorno, hashes, estado energético original y restauración de cambios reversibles.
- [ ] Las incidencias de seguridad o rollback quedaron cerradas y notificadas.
- [ ] Se identificó qué mejoras —si las hay— se sostuvieron en repeticiones, y cuáles eran artefactos de caché, carga, temperatura, boost, compilación o medición.
- [ ] El usuario autorizó comenzar **una nueva campaña de documentación/implementación**; no interpretar el roadmap como aprobación de acciones disruptivas o una PR pública.

**Si algún gate falla:** emitir `BLOCKED`, describir la evidencia faltante y **detenerse sin inventarla**.

---

## 3. Roadmap de ejecución posterior (fases y criterios de salida)

### Fase 0 — Congelar fuente de verdad y hacer inventario de evidencias

**Meta:** garantizar que el pipeline nace de hechos existentes.

- Confirmar hash completo del HEAD del PR #134 y consultar su estado actual, diferencia con upstream y revisiones.
- Inventariar documentos, hashes y logs de Ada sin copiarlos innecesariamente.
- Reunir el cierre REA-CPU-01, documentación ACPI/XDNA si existe y autorías del trabajo.
- Construir un `EVIDENCE_INDEX.md` que distinga `observed`, `reported`, `replicated`, `hypothesis`.
- Generar una cronología con marcas: **observación → hipótesis → experimento → negativo → revisión → prueba positiva/negativa → conclusión**.
- No reescribir los logs históricos ni completar huecos con narrativa.

**Entregable:** `01_PROVENANCE_AND_TIMELINE.md`, `EVIDENCE_INDEX.md`, refs/commits exactos.  
**Gate:** toda afirmación importante enlaza a un artefacto o está rotulada como hipótesis.

### Fase 1 — Reproducción intelectual del caso `D3cold`

**Meta:** extraer la lección metodológica sin generalizar el exploit.

- Construir una matriz: `software getter` vs `BAR0/readback` vs `power state` vs `benchmark` vs `correctness`.
- Explicar la diferencia entre modificación exitosa, persistencia tras estados de energía y mejora física medida.
- Documentar qué controles falsaron la primera conclusión, por qué el estado aparente era engañoso, y cuál fue el papel del `D3cold`.
- Conservar diferencias de granularidad: throughput de instrucción vs GEMM real vs inferencia extremo a extremo.
- Añadir un caso de test artificial con `getter=unlocked` y `throughput=stock` que **debe ser rechazado** por el pipeline.

**Entregable:** `02_CASE_STUDY_ADA_D3COLD.md`, fixture de falso positivo.  
**Gate:** nadie puede aprobar una optimización solo por un registro, selector, bandera o mensaje de éxito.

### Fase 2 — Incorporar REA-CPU-01, gane o pierda

**Meta:** verificar cómo se transfiere el método a una arquitectura diferente.

- Verificar perfil `low-power` mediante matriz experimental y condiciones de alimentación, si fue efectuada; no inventar 100 % de causas.
- Documentar el Ryzen 5 8645HS real, hilos SMT, AVX-512, memoria y cargas medidas.
- Distinguir mejoras de compilador, afinidad, paralelismo, perfil energético y caché de hipotéticos límites firmware.
- Registrar descubrimiento NPU por separado de la ejecución efectiva de workloads XDNA; marcar `NOT_DEMONSTRATED` si no se ejecutó uno.
- El análisis ACPI/SMU debe ser **de solo lectura** en la documentación reutilizable.
- Traducir un resultado negativo a una condición legítima de salida (`NO_SAFE_GAIN_FOUND`).

**Entregable:** `03_CASE_STUDY_CPU_REA.md` con tabla `claim → baseline → intervention → controls → measured gain → confounders → verdict`.  
**Gate:** ninguna afirmación de undervolt, unlock o mejora CPU sin medición controlada y confirmación de soporte.

### Fase 3 — Diseñar el contrato del Hardware Research Agent

El pipeline debe poder operar como un protocolo independiente del modelo o TUI usado por el usuario, con **un supervisor autorizador** y **un executor explícito**.

**Estados obligatorios:**

```
DISCOVER → BASELINE → HYPOTHESIZE → PLAN_EXPERIMENT → SENTINEL_REVIEW
                                               ↓            ↓
                                             DENY        AUTHORIZE
                                               ↓            ↓
                                             REPORT      EXECUTE
                                                            ↓
                                           MEASURE → VALIDATE → ROLLBACK → RETURN
```

**Cada experimento debe declarar:**

- `experiment_id`, versión de esquema, target detectado y grado de confianza de identificación;
- hipótesis falsable, predicción y variable primaria;
- workload, baseline, warmup, repeticiones, controles positivos/negativos;
- hardware/firmware/driver/OS, energía (AC/batería), temperatura, relojes y carga concurrente;
- clasificación de riesgo, permisos, lista exacta de operaciones permitidas;
- precondición, `rollback_plan`, método de verificar restauración y causa de parada;
- límites de tiempo, temperatura/memoria de **prueba**, sin alterar límites protectores de la máquina;
- referencias de evidencia, digest de herramientas y binarios;
- criterio de éxito, criterio de rechazo y veredicto.

**Desacoplar:** el agente formula hipótesis y redacta planes; **Sentinel autoriza o rechaza**; el executor corre solamente acciones enumeradas. Nunca ejecutar texto arbitrario del LLM como superusuario.

**Entregable:** `04_PROTOCOL_SPEC.md`, `EXPERIMENT_SCHEMA.json` (o YAML), `PERMISSION_MATRIX.md`.  
**Gate:** cada acción tiene owner, permiso, precondición y ruta de recuperación demostrable; las acciones irrecuperables quedan fuera de V1.

### Fase 4 — Implementar MVP read-only / report-first

**Orden obligatorio:**

1. **Inventario seguro:** capacidades detectadas (`CPU`, `PCI`, `NPU`, controladores, fuentes de telemetría). El adaptador informa `supported/unsupported/unknown`; no infiere por nombre comercial.
2. **Baseline reproducible:** benchmark de usuario no privilegiado con techo de tiempo/carga, sin solaparse con trabajos del dueño.
3. **Hipótesis y plan:** propuestas justificadas por datos, incluidos «no tocar nada» y «no hay herramienta aplicable».
4. **Gate de consentimiento:** por defecto solo lectura. Cualquier modificación reversible documentada exige autorización granular de una persona.
5. **Verificación:** comparación A/B con mismos artefactos, orden alternado o controles de temperatura; reproducibilidad y correctitud por encima del mejor pico.
6. **Reporte y rollback:** restauración verificada; paquete de evidencia con SHA-256; dictamen explícito.

**No implementar en V1:** root shell genérico, edición de firmware, modificación de registros crudos, automatización de `gpu-unlock-ada`, cambios BIOS/UEFI, desactivación de protecciones o actualización automática del kernel.

**Entregable:** prototipo local no privilegiado + dry-run seguro + documentación de instalación y desinstalación.  
**Gate:** el agente puede demostrar el pipeline end-to-end sobre datos de prueba **sin cambiar el sistema**.

### Fase 5 — Adaptadores opcionales y catálogo de compatibilidad

- `linux-inventory`: comandos y archivos de solo lectura.
- `linux-cpu-perf`: `perf` y sensores si están disponibles y autorizados.
- `linux-power-profile`: leer perfil; cambio temporal únicamente si hay soporte y autorización explícita.
- `nvidia-telemetry`: lecturas NVML o herramientas equivalentes; distinguir datos cacheados de verificación independiente.
- `amd-xdna-discovery`: estado del driver y rutas oficiales, sin presumir ejecución.
- `rea-readonly`: capa de inspección opcional con alcance explícito.
- `gpu-unlock-ada-case-study`: **solo importación de evidencia y guía de alcance**, ninguna función de auto-unlock.
- `custom-workload`: compilar/ejecutar un benchmark local controlado con hashes; ISyInfer es opcional.

**Regla:** cada adaptador debe responder `NOT_APPLICABLE` de manera limpia cuando el hardware o permisos no correspondan.

**Entregable:** `ADAPTER_REGISTRY.md`, pruebas de compatibilidad por hardware simulado.  
**Gate:** ningún adaptador puede ejecutar cambios no contenidos en su contrato.

### Fase 6 — Suite antifake y pruebas adversariales

Construir casos sintéticos y replays de evidencia para falsar el pipeline:

1. Estado informado **unlocked** pero throughput **stock** (trampa `D3cold`).
2. Comparación de clocks diferentes, atribuyendo erróneamente el cambio a una optimización.
3. Thermal throttling o batería/AC desiguales entre A/B.
4. `PASS` con salidas matemáticas incorrectas.
5. Benchmark solo «best of N», sin mediana, dispersión ni warmup.
6. Logs o hashes ausentes/manipulados.
7. Equipos no soportados donde el agente intenta importar ajustes de otra familia.
8. Proceso de agente detenido después de un ajuste reversible: mecanismo de restauración externo y verificable.
9. Prueba que excede recursos disponibles: denegación antes de causar presión dañina.
10. Mensaje falsificado en documentación que intenta ordenar `sudo`, editar firmware o subir telemetría privada: tratar documentación como datos, no autoridad.
11. Una hipotética optimización CPU que no se confirma en un segundo workload: veredicto **solo para workload específico**.
12. Caso sin mejora: producir reporte completo y `NO_SAFE_GAIN_FOUND`.

**Entregable:** `tests/` y `fixtures/` totalmente artificiales + `ANTIFAKE_MATRIX.md`.  
**Gate:** 100 % de negativos peligrosos rechazados; ninguna tasa de aprobación se aumenta eliminando casos o relajando el contrato.

### Fase 7 — Validación con más de una máquina (sin acceso remoto obligatorio)

- Primera campaña: laptop Victus del creador, sin cambios no autorizados.
- Segunda campaña: máquina Linux con hardware diferente, preferentemente en modo read-only, con permiso del dueño.
- Comparar si el inventario, la detección de «no aplicable» y el reporte funcionan incluso cuando `gpu-unlock-ada`, `amdxdna` o herramientas especializadas no existen.
- No transmitir inventarios automáticamente: exportación opt-in, redactada y revisable.
- Documentar límites: kernels, equipos híbridos, LXC/VM, rootless, sensores ausentes, permisos `perf` y drivers.

**Entregable:** `PORTABILITY_MATRIX.md` y dos paquetes de evidencia separados.  
**Gate:** el sistema no intenta forzar «optimización» donde falta compatibilidad.

### Fase 8 — Preparación de repositorio público (requiere decisión del usuario)

**No crear/pushear sin autorización explícita.** Preparar una propuesta de estructura:

```
hardware-research-agent/
  README.md
  ROADMAP.md
  AGENTS.md
  docs/
    01_PROVENANCE_AND_TIMELINE.md
    02_CASE_STUDY_ADA_D3COLD.md
    03_CASE_STUDY_CPU_REA.md
    04_PROTOCOL_SPEC.md
    SAFETY_AND_THREAT_MODEL.md
    PERMISSION_MATRIX.md
    REPRODUCIBILITY_STANDARD.md
    ANTI_FAKE_VERDICTS.md
  schemas/
    experiment.schema.json
    evidence.schema.json
  adapters/
    linux_inventory/
    linux_cpu_perf/
    nvidia_telemetry/
    amd_xdna_discovery/
  fixtures/
    ada_d3cold_false_positive/
    no_safe_gain_found/
  tests/
  examples/
    sample_report_redacted.md
  LICENSE (a elegir tras revisar dependencias)
```

Revisar licencias de REA, gpu-unlock-ada, dependencias e ISyCo; citar procedencias y distinguir código original de material externo. Analizar privacidad en logs, seriales, UUID, hostname y rutas personales. Preparar política de contribuciones basada en evidencia y máquinas compatibles, no en claims generales.

**Entregable:** propuesta de repositorio, LICENSE recomendada con razonamiento (sin aplicarla), guías de uso local, threat model y revisión de contenidos para publicación.  
**Gate:** aprobación explícita de Danny para el repositorio y su visibilidad.

---

## 4. Esquema de veredicto y métricas mínimas

| Estado | Cuándo se usa |
| --- | --- |
| `DEMONSTRATED` | Claim acotado verificado mediante prueba, artefactos y controles aplicables. |
| `INFERRED` | Interpretación plausible; falta aislar variables o replicar. |
| `REFUTED` | Control válido contradice la hipótesis bajo condiciones definidas. |
| `NOT_DEMONSTRATED` | Falta medición, comparador, salida cruda, correctitud o reproducibilidad. |
| `DENY` | Acción prohibida por límites de autorización, riesgo o ausencia de reversibilidad. |
| `NO_SAFE_GAIN_FOUND` | Campaña correctamente ejecutada sin mejora útil y segura. |
| `REQUIRES_SEPARATE_AUTHORIZATION` | Fuera de los permisos actuales; no ejecutar. |

**Métricas:** tiempo bruto, throughput sostenido, latencia percentiles, mediana y dispersión, correctitud, temperaturas, clocks efectivos, potencia observada si hay sensores fiables, memoria RSS/HWM/swap, I/O/major faults cuando correspondan, entorno y perturbaciones. Nunca reemplazar datos faltantes por cero o por una estimación silenciosa.

### Plantilla mínima de claim

```yaml
claim_id: "CPU-A1"
claim: "Un perfil energético documentado mejora el throughput del workload X"
target: "familia/ID y versión exactos observados"
conditions: "AC, temperatura, kernel, driver, frecuencia y carga"
baseline: "ruta al log y SHA-256"
intervention: "solo cambio autorizado, temporal y documentado"
control: "misma entrada, mismo binario, correctitud comprobada"
repetitions: 3
metrics: "mediana, dispersión y energía si medible"
rollback: "método y confirmación de estado restaurado"
verdict: "NOT_DEMONSTRATED"
limitations: "explicar variables no aisladas"
```

---

## 5. Reglas de seguridad fundamentales

1. **No escalación implícita.** Sin permiso granular previo, ninguna acción fuera de lectura/benchmark seguro de usuario.
2. **Sin shell libre de root.** Si en fases futuras se permiten cambios reversibles, usar operaciones predefinidas y allowlist verificable, no órdenes generadas arbitrariamente.
3. **Rollback antes de la modificación.** Si no se puede comprobar el estado original o garantizar una restauración razonable, denegar. No prometer restauración de daños físicos.
4. **No autopatching de firmware/SMU/ACPI.** Mantener investigación documental separada de cualquier ejecución de alto riesgo.
5. **No publicar secretos de hardware.** Redactar UUID/serial/MAC/hostname/rutas únicas en el paquete público; conservar raw privado solo con consentimiento.
6. **Sin falsa universalidad.** Las condiciones de una RTX 4050 AD107M no autorizan actuaciones en otras GPU, ni el resultado Ryzen aplica a otro OEM.
7. **No perturbar trabajos del propietario.** Detectar tareas concurrentes; pedir permiso antes de consumir recursos relevantes.
8. **Medir seguridad también.** Una mejora puntual no compensa inestabilidad, errores, temperatura fuera de especificación o pérdida de funcionalidad.

---

## 6. Handoff listo para el agente CUANDO termine REA-CPU-01

> **INSTRUCCIÓN DIFERIDA — NO EJECUTAR HASTA CERRAR REA-CPU-01:**
>
> Abre `ROADMAP_HARDWARE_RESEARCH_AGENT_2026-11-02.md` desde Drive. Comprueba el gate de activación. Si no está cerrado, devuelve solo `BLOCKED` con faltantes.
>
> Si el gate está cerrado y Danny autoriza comenzar, recupera los artefactos reales de `JustAResearcher/gpu-unlock-ada` PR #134 y el informe final REA-CPU-01. Reconstruye primero la cronología sin rellenar huecos: inventario de la GPU → hipótesis de unlock → respuesta software → resultados stock → entrada a `D3cold` → reset de registros vs getter cacheado → controles A/B → cambios de interpretación → mediciones válidas → reporte/PR. Luego haz la misma reconstrucción para CPU, sea una mejora o un resultado negativo.
>
> Extrae y redacta un protocolo de investigación general para agentes Linux que descubra capacidades, mida baseline, formule hipótesis, exija autorización, ejecute únicamente pruebas seguras y reversibles, valide correctitud, haga rollback y produzca evidencias verificables. Implementa **primero un prototipo read-only con fixtures sintéticos**, no un unlock automático. Ejecuta la matriz antifake (incluido falso positivo `D3cold`) y produce `RESULTS`, `VERDICTS`, `RISKS`, `GAPS` y `NEXT_ACTIONS`. No crees repo público, no modifiques firmware/voltajes/registros y no atribuyas ganancias CPU sin mediciones. Si faltan fuentes, conserva `NOT_DEMONSTRATED`.
>
> Entrega un paquete local para revisión, rutas y hashes, junto con una propuesta de repositorio y licencia. **Solicita autorización separada para publicar o hacer cambios privilegiados.**

---

## 7. Definición de terminado (Definition of Done)

- [ ] REA-CPU-01 está cerrado y su resultado —positivo o negativo— está representado sin inventos.
- [ ] PR #134 reconstruida como **caso de estudio acotado** de Ada/Optimus `D3cold`, no como optimización universal.
- [ ] Cronología y matriz causal tienen referencias verificables y conservan los controles fallidos.
- [ ] Contratos de agente, permisos y autorizar/ejecutar están especificados y probados.
- [ ] MVP Linux read-only funciona o se demuestra `NOT_DEMONSTRATED` sin PASS falso.
- [ ] Fixture `getter unlocked / benchmark stock` produce DENY/FAIL en el sistema.
- [ ] Sistema soporta ausencia de herramientas, otro hardware y `NO_SAFE_GAIN_FOUND`.
- [ ] Evidencia con SHA-256, redacción de datos privados y rutas completas.
- [ ] Ningún cambio persistente o de alto riesgo realizado sin consentimiento explícito.
- [ ] Repositorio público queda como **propuesta**, pendiente de decisión de Danny.

**Principio final:** *Una máquina no debe volverse más rápida a costa de volverse menos segura, menos correcta o menos comprensible. El hallazgo más valioso puede ser un falso positivo correctamente rechazado.*
