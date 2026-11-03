# ISyCo Hardware Research Agent (IHRA)

**Protocolo de investigación de hardware en Linux: reproducible, supervisado y orientado a evidencia.**

[English](README.md) · [Pipeline científico](docs/COMMON_RESEARCH_PIPELINE.md) · [Contratos de seguridad](docs/SAFETY_CONTRACTS.md) · [Roadmap](ROADMAP.md) · [Casos de investigación](#origen-de-la-investigación)

> [!IMPORTANT]
> **Prototipo público de investigación (HRA-00), no un optimizador autónomo listo para producción.** IHRA puede descubrir ciertas capacidades, evaluar cadenas de comandos, consultar telemetría seleccionada y contrastar datos de benchmarks. **Todavía no implementa ni obliga a cumplir un ciclo completo, aislado y autónomo de autorización, ejecución y restauración.** No le otorgues privilegios por asumir que toda la seguridad descrita en los documentos ya está implementada.

**Principio:** concluir **«no se encontró una mejora segura»** también es un éxito científico. Este repositorio no promete más FPS, más tokens por segundo ni desbloqueos universales.

## Qué funciona actualmente

| Componente | Comportamiento existente | Límite comprobado en código |
|---|---|---|
| Descubrimiento | Lee datos de CPU, perfiles ACPI y determinadas señales PCI/NPU/NVIDIA | Tiene supuestos de plataforma y rutas específicas; no es un inventario universal |
| [Sentinel](core/sentinel.py) | Examina una cadena de comando y un nivel de riesgo proporcionado; bloquea patrones peligrosos y nivel 3 | **Clasificador consultivo basado en expresiones regulares**, no sandbox ni autenticación |
| [Verificador antifake](core/verifier.py) | Compara medianas, hashes proporcionados, variación de control negativo y contradicciones de estado | No recopila por sí mismo mediciones físicas ni demuestra significación estadística formal |
| [Controlador de rollback](core/rollback.py) | Ejecuta una restauración indicada y compara el estado devuelto por otra consulta | Es una **pieza independiente**, no una restauración automática conectada a un ejecutor de experimentos |
| [Adaptador Ada](adapters/ada_adapter.py) | Detecta ciertos PCI IDs NVIDIA y consulta el parámetro de energía y `nvidia-smi` | Un parámetro del driver **no demuestra** que el unlock esté activo ni que sea seguro |
| [Adaptador REA](adapters/rea_adapter.py) | Localiza herramientas y puede invocar análisis mediante Node o npx | **Ejecuta herramientas externas**; no las aísla ni garantiza ausencia de descargas |
| Wrapper de Node | [bin/ihra.mjs](bin/ihra.mjs) llama al CLI Python | Node solo ofrece otra entrada; el núcleo del CLI es Python |

El [diseño arquitectónico](docs/ARCHITECTURE.md) también describe un ejecutor aislado, un historial de hipótesis y un grabador de evidencia. **No todos esos módulos existen todavía en `core/`.** Los contratos y esquemas expresan objetivos de diseño; no ejecutan esas garantías por existir.

## Inicio rápido responsable (Linux)

**Requisitos:** Python 3 y su biblioteca estándar. Node.js es opcional; algunas consultas necesitan herramientas del sistema instaladas.

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent

# Consultar la interfaz y ejecutar las pruebas disponibles
python3 -m core.cli --help
python3 -m unittest discover -s tests -v

# Consultar inventario y telemetría seleccionada
python3 -m core.cli discover

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
| `python3 -m core.cli ada` | Lee estado NVIDIA y heurísticas de `D3cold` | Su texto de recomendación actual puede **exagerar** seguridad y mejora GEMM; exige verificación física |
| `python3 -m core.cli sentinel 'wrmsr 0x199 0x2000'` | Demostración de rechazo de una cadena peligrosa | **No ejecuta** el comando mostrado; solamente clasifica texto |
| `python3 -m core.cli inspect /ruta/al/archivo --tool inspect_malbolge` | Delega el análisis a REA | Puede ejecutar Node o `npx`; verifica dependencias antes de usarlo |

**`--human-approved` no es una autenticación real:** es un booleano proporcionado por quien llama al programa. Del mismo modo, un veredicto `AUTHORIZED` **no equivale a permiso para ejecutar ese comando**, ni inicia automáticamente un ejecutor protegido o un rollback verificado.

## Método científico y trazabilidad

El [pipeline de diez etapas](docs/COMMON_RESEARCH_PIPELINE.md) especifica el **proceso que se desea construir**, no un servicio autónomo ya terminado:

```text
DISCOVER → BASELINE → HYPOTHESIZE → CONTROL → RISK REVIEW
  → AUTHORIZE → EXPERIMENT → VERIFY → RESTORE → EVIDENCE / RETURN
```

Antes de declarar una mejora se deben comparar condiciones equivalentes, usar controles negativos, ejecutar un workload reproducible, revisar correctitud matemática y obtener evidencia física independiente cuando sea posible. Después debe documentarse la restauración del sistema. Los resultados `REFUTED`, `NOT_DEMONSTRATED`, `NO_SAFE_GAIN_FOUND` o `DENY` no se maquillan como éxitos.

### Limitaciones actuales del verificador y Sentinel

- El verificador exige **al menos tres muestras por grupo** y compara **medianas**. Eso no constituye una prueba de significación estadística; sus valores fijos de confianza tampoco son intervalos de confianza.
- Compara hashes **entregados por el llamante**, pero actualmente **no exige hashes presentes y no vacíos en todas las muestras**. Esa ausencia puede pasar inadvertida.
- El control negativo usa un valor predeterminado de variación igual a cero si no se proporciona uno: **no demuestra que se ejecutó un control**.
- La regla para el falso positivo de `D3cold` rechaza una declaración de desbloqueo cuando el throughput permanece en stock. El programa **no lee BAR0 directamente** para demostrar la causa.
- La clasificación por expresiones regulares y el nivel de riesgo indicado por el llamante **no forman una barrera segura** frente a órdenes arbitrarias generadas por un agente.
- El rollback no está unido automáticamente al resultado del Sentinel. El controlador usa actualmente `shell=True`; no debe recibir comandos arbitrarios o no revisados.

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

El repositorio contiene **22 métodos `unittest` distribuidos en seis archivos**, contados directamente en el código versionado. Cubren adaptadores simulados, filtros de Sentinel, resultados de rollback, hashes y falsos positivos sintéticos de `D3cold`. **No se afirma aquí haber ejecutado y aprobado personalmente la suite ni una validación física extremo a extremo.** El árbol inspeccionado no contiene un workflow de GitHub Actions bajo `.github/workflows/`.

Ver [fixtures](fixtures/), [esquema de experimento](schemas/experiment.schema.json) y [esquema de evidencia](schemas/evidence.schema.json). Las simulaciones **no son logs crudos de una máquina real**.

## Seguridad, procedencia y publicación

- La investigación de solo lectura es la **política de diseño**, no garantía de aislamiento por el sistema operativo.
- Los contratos prohíben firmware flashing, escritura directa SMU/MSR y la desactivación de protecciones. La implementación actual basada en texto **no sustituye un ejecutor con denegación por defecto a nivel del sistema**.
- IHRA no incluye ni recomienda overclock, undervolt, cambios de BIOS o desbloqueo de protecciones térmicas automáticos.
- La publicación de trazas exige revisar nombres de equipos, seriales, UUID, tokens y otros identificadores privados.

**Licencia pendiente de unificación:** `package.json` declara `MIT`, el README anterior proponía `Apache-2.0` y el árbol raíz revisado **no contiene un archivo `LICENSE`**. Este documento no presupone que una licencia definitiva esté formalizada. Antes de una distribución estable, elige la licencia, incorpora su texto y alinea la documentación y metadatos.

**Estado actual:** **prototipo público HRA-00**. El [ROADMAP.md](ROADMAP.md) es la propuesta histórica de fases; las menciones antiguas a un repositorio aún no publicado no describen el estado actual.
