# IHRA — deja que un agente de IA te ayude a afinar tu PC Linux, con pruebas y sin humo

[English](README.md) · [Referencia técnica](docs/TECHNICAL_es.md) · [Contratos de seguridad](docs/SAFETY_CONTRACTS.md) · [Casos reales](#la-historia-detrás)

**IHRA (ISyCo Hardware Research Agent)** es un kit pequeño de herramientas más un reglamento para tu agente de IA de programación (Claude Code, Codex, opencode, Gemini CLI, …). Con él, el agente puede revisar tu hardware, medirlo y sugerirte mejoras como lo haría un científico cuidadoso. **Tú** decides cada cambio.

Existe porque muchos "trucos de optimización" de internet están mal, y un agente de IA puede sonar muy seguro con un ajuste que no hace nada o que empeora las cosas. IHRA obliga al agente a **demostrar** una mejora antes de llamarla mejora.

> [!IMPORTANT]
> IHRA es un **prototipo de investigación**, no un "booster" de un clic. Por sí solo únicamente **lee** tu sistema y corre pruebas inofensivas. Nunca hace overclock, nunca flashea firmware y nunca cambia ajustes por su cuenta. "Lo medimos y no hay una mejora segura" es un resultado perfectamente válido.

## La historia detrás

IHRA nació de sesiones reales en las que agentes de IA ayudaron a afinar una laptop HP Victus (Ryzen 5 8645HS + RTX 4050 Laptop):

- **La GPU que mentía.** Un desbloqueo experimental hacía que el driver de NVIDIA *reportara* cómputo tensorial a velocidad completa, pero la velocidad real había vuelto en silencio a la de fábrica. El modo de ahorro de energía de la laptop (`D3cold`) le cortaba la corriente a la GPU y la reiniciaba. Solo se descubrió midiendo el rendimiento real en vez de confiar en lo que decía el software. Una vez encontrada y corregida la causa, una prueba tensorial corrió a cerca de **2× la velocidad de fábrica** en 5 de 5 corridas repetidas, y el cálculo de matrices real (cuBLAS) mejoró entre **+36 % y +50 %**. → [caso completo](docs/HISTORY_ADA_D3COLD.md)
- **El "techo del 5 %" que era un turbo apagado.** Un primer estudio de la CPU concluyó que solo quedaba cerca de +5 % de margen. Al revisarlo de nuevo se encontró que el **turbo estaba apagado** por un archivo de configuración que había puesto otro agente, que el benchmark era Python con hilos atorados en un solo núcleo y que había programas corriendo en segundo plano. Medido bien, el turbo dio **+16 % en un núcleo y +8–9 % en todos** con cargador, y casi nada en batería. Así que el dueño eligió un "turbo inteligente": encendido con cargador y apagado en batería. → [caso completo](docs/HISTORY_REA_CPU_01.md)

Las dos historias enseñan lo mismo: **lo que reporta el sistema no siempre es lo que hace el hardware.** Son resultados de **una sola máquina**, no promesas para la tuya. Lo valioso es el *método*: buscar interruptores escondidos, medir primero, comparar de forma justa y ser honesto con el resultado. Ese método es lo que IHRA empaqueta.

## Cómo usarlo con tu agente de IA

**Necesitas:** Linux, `git` y Python 3 (probado con 3.12). Node.js es opcional.

**1. Descárgalo**

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent
python3 -m unittest discover -s tests   # opcional: debe terminar en "OK"
```

Dos comprobaciones opcionales miran proyectos que viven junto a IHRA en vez de código incluido aquí: REA (forense de binarios) y GPU-Unlock-Ada (raíles de energía de NVIDIA). Señala con `REA_HOME` y `ADA_UNLOCK_HOME` dónde los tengas; `python3 -m core.cli setup` imprime qué ruta usó realmente cada comprobación, y si no defines nada intenta adivinar `~/Development`. Puedes dejar las dos sin definir y todo lo demás sigue funcionando: esas líneas reportan `ABSENT` o `Unavailable`.

**2. Mira qué encuentra en tu PC** (solo lectura, no cambia nada)

```bash
python3 -m core.cli --text discover
```

Verás tu CPU, tu perfil de energía, tu GPU y tu NPU (si tienes). Sin `--text` sale en JSON, que es más fácil de leer para un agente.

**3. Abre tu agente de IA dentro de esta carpeta y pégale este prompt:**

```text
Ayúdame a investigar el rendimiento de mi PC Linux usando IHRA (este repo).
Primero lee AGENTS.md y README_es.md y luego ejecuta: python3 -m core.cli discover
Empieza por los avisos de "Things to check" que imprima (turbo apagado,
batería, carga en segundo plano) antes de medir cualquier cosa.

Reglas:
- Por tu cuenta solo puedes ejecutar comandos de lectura. Pregúntame antes de
  CUALQUIER cambio en mi sistema y dime exactamente cómo deshacerlo antes de hacerlo.
- Nunca flashees firmware o BIOS, no escribas registros MSR/SMU ni desactives
  límites térmicos.
- Mide antes y después al menos 3 veces cada uno, con la misma carga, y comprueba
  que los resultados sigan siendo correctos, no solo más rápidos.
- No te fíes solo de lo que reporta el software; confírmalo con mediciones reales.
- Si no puedes demostrar una mejora, dilo: NOT_DEMONSTRATED o NO_SAFE_GAIN_FOUND.

Mi objetivo: <por ejemplo "modelos de IA locales más rápidos", "ventiladores más silenciosos", "más batería">
```

A partir de ahí el agente te explica tu hardware, te propone un plan y te ayuda a medir cada cambio. Tú decides qué se aplica de verdad.

## Qué hace IHRA y qué no

| ✅ IHRA sí | 🚫 IHRA no |
|---|---|
| Reporta tu CPU, perfil de energía, GPU y NPU (`discover`) | Cambia ajustes por su cuenta |
| Avisa de interruptores escondidos: turbo apagado, batería, perfil de ahorro, carga en segundo plano | Adivina por qué se cambió un ajuste; te dice dónde buscar |
| Revisa si un comando está en su lista de solo lectura (`sentinel`) | Hace overclock, undervolt ni modifica BIOS/firmware |
| Corre una prueba fija e inofensiva de CPU en un sandbox para ver que los resultados se repitan | Promete más FPS ni una PC más rápida |
| Rechaza afirmaciones de "más rápido" sin suficientes corridas, hashes y un control (`AntiFake`) | Deja que un agente presuma una sola corrida con suerte |
| Muestra el estado de NVIDIA Ada y la trampa de energía D3cold (`ada`) | Instala ni ejecuta el desbloqueo experimental de GPU |
| Prueba **un** cambio de perfil de energía mientras tú tecleas `AUTHORIZE` en esta terminal, y después comprueba que lo regresó (`run-tier1`) | Cambia algo sin que estés presente — y si no puede comprobar que regresó el estado, bloquea todas las corridas siguientes hasta que lo arregles |

## Qué significan los resultados

| Resultado | En palabras simples |
|---|---|
| `DEMONSTRATED` | Se midió bien y el efecto es real *en esta máquina* |
| `NOT_DEMONSTRATED` | Todavía no hay suficiente evidencia; no lo cuentes como mejora |
| `NO_SAFE_GAIN_FOUND` | Se probó de forma justa y nada seguro ayudó. ¡También vale saberlo! |
| `REFUTED` / `DENY` | La afirmación era falsa o la acción no está permitida |

## Comandos útiles

| Comando | Qué hace |
|---|---|
| `python3 -m core.cli --text discover` | Resumen de tu hardware |
| `python3 -m core.cli ada` | Estado de NVIDIA serie RTX 40: ajuste de energía del driver, módulo de desbloqueo, estado de energía |
| `python3 -m core.cli sentinel 'lscpu'` | ¿Este comando exacto está en la lista de solo lectura? (no ejecuta nada) |
| `python3 -m core.cli dry-run ada_d3cold_false_positive` | Reproduce el caso de "la GPU que mentía" con datos de ejemplo |
| `python3 -m core.cli run-synthetic sha256-chain-v1` | Prueba de CPU inofensiva en sandbox (necesita `bubblewrap`) |
| `python3 -m core.cli run-synthetic-experiment --proposal <archivo>` | Cinco muestras cronometradas en el sandbox, después de que teclees `AUTHORIZE <sha256>` en esta terminal |
| `python3 -m core.cli inspect-synthetic-campaign <id>` | Lee de nuevo una campaña terminada o interrumpida, hash a hash, sin volver a ejecutar nada |
| `python3 -m core.cli run-tier1 --set balanced` | Prueba un cambio de perfil con un humano mirando: lo aplica, mide, lo regresa y lo comprueba |
| `python3 -m core.cli list-faults` | ¿Queda algún cambio sin restaurar? Sale con error mientras quede uno |
| `python3 -m core.cli resolve-fault <id>` | Comprueba que la máquina volvió a como debe estar y cierra ese registro. En sí no cambia nada |
| `python3 -m core.cli inspect <archivo>` | Analiza un binario con [REA](https://github.com/DannyBaanks/rea), si lo tienes instalado |

## Cuando algo sale mal

**Una campaña se cortó, la mataron o le diste Ctrl-C.** No la vuelvas a correr ni te fíes de la última
línea del log: lee la evidencia.

```bash
python3 -m core.cli inspect-synthetic-campaign <id-de-campana>
```

Revisa el manifiesto archivo por archivo y responde `COMPLETED_VALID`, `INCOMPLETE`,
`NOT_DEMONSTRATED` o `ACTIVE_OR_UNKNOWN`. No reanuda nada y nunca hace ver una campaña vieja mejor de
lo que fue. Cada una de las cinco muestras sella su propio recibo antes de empezar la siguiente, así
que un corte cuesta a lo sumo la muestra que estaba corriendo y la evidencia dice exactamente dónde
paró. **Reanudar automáticamente no está implementado**: el checkpoint te dice dónde quedó y tú decides.

**Un cambio de perfil no regresó.** El runner de Tier-1 lo dice claro, imprime los comandos para
regresarlo y deja un registro de emergencia. Mientras ese registro esté abierto, IHRA no corre ninguna
campaña más — el sandbox incluido. Tú arreglas la máquina a mano y después `resolve-fault <id>`
comprueba que el estado coincide con lo que pide el registro y lo cierra. Así está diseñado:
**IHRA se detiene, no repara.** No hay ningún servicio de fondo que restaure algo al arrancar el PC.

## Experimentos y recetas listos

| Qué | Cómo correrlo | Qué cambia |
|---|---|---|
| **¿El turbo ayuda en mi PC?** Prueba justa con y sin turbo, con watts, relojes, temperatura y hashes | [`experiments/cpu-boost`](experiments/cpu-boost/README.md) | El interruptor del turbo. Lo regresa al terminar y con Ctrl-C — pero **por fuera** del gate de seguridad de IHRA, lee abajo |
| **Turbo inteligente:** encendido con cargador, apagado en batería | [`recipes/smart-boost`](recipes/smart-boost/README.md) | Agrega 3 archivos, uno de los cuales vuelve a prender el interruptor en cada arranque; `uninstall.sh` los quita |

Necesitan `sudo` porque tocan un ajuste del sistema. Tu agente te los explica; **tú** los corres.

> [!WARNING]
> Estos dos son **scripts que tú corres, no capacidades de IHRA**. `run_boost_matrix.py` escribe
> `/sys/devices/system/cpu/cpufreq/boost` con `sudo`, y `smart-boost` instala una regla udev más un
> servicio que siguen escribiendo ese interruptor. Releen el valor y lo regresan cuando termina la corrida
> o cuando los interrumpes, pero lo hacen solos: por fuera del gate de Sentinel, por fuera de la ceremonia
> `AUTHORIZE`, por fuera de `RollbackContract` y por fuera de la evidencia sellada. IHRA no puede
> comprobar ese rollback por ti, y si la máquina se cae a mitad nada regresa el interruptor al arrancar.
> Meterlos en el mismo contrato es el siguiente hito (IHRA-05); mientras tanto trátalos como herramientas
> manuales.

Si tienes Node.js, `node bin/ihra.mjs <comando>` también funciona. La lista completa, con cada límite explicado, está en la [referencia técnica](docs/TECHNICAL_es.md#referencia-de-comandos-y-advertencias).

## Preguntas

**¿Esto hará más rápidos mis juegos o mis modelos de IA?** Puede que sí, puede que no. IHRA te ayuda a *averiguarlo* con honestidad. Muchas veces la respuesta está en un perfil de energía, un ajuste del driver o la refrigeración, y a veces no hay nada seguro que ganar.

**¿Puede dañar mi PC?** IHRA por sí solo solo lee información y corre una prueba en sandbox. Ahora hay
exactamente una cosa que puede cambiar — un perfil de energía — y solo en la misma terminal donde tecleaste
la confirmación, después de lo cual relee la máquina para comprobar que lo regresó. El riesgo viene de los
cambios que **tú** apruebes. Por eso las reglas de arriba exigen una forma de deshacer cada cambio, por eso
un cambio que no se pueda comprobar regresado bloquea las corridas siguientes, y por eso las categorías
peligrosas están prohibidas. Lee lo que te propone el agente antes de decir que sí.

**¿Windows o macOS?** Todavía no. IHRA lee archivos propios de Linux (`/proc`, `/sys`).

**¿Necesito una GPU NVIDIA?** No. Las revisiones de NVIDIA solo se activan si tienes una.

**¿Y el desbloqueo 2× de la GPU?** Es un proyecto experimental aparte, a nivel de kernel: [gpu-unlock-ada](https://github.com/DannyBaanks/gpu-unlock-ada). Cubre tarjetas RTX serie 40 concretas, solo está verificado en una laptop y tiene riesgos reales. IHRA nunca lo instala.

## Para desarrolladores

- [Referencia técnica](docs/TECHNICAL_es.md): qué está implementado exactamente, hasta dónde llega cada garantía y los huecos conocidos
- [Pipeline de investigación](docs/COMMON_RESEARCH_PIPELINE.md) · [Contratos de seguridad](docs/SAFETY_CONTRACTS.md) · [Arquitectura](docs/ARCHITECTURE.md) · [Roadmap](ROADMAP.md)
- Tests: `python3 -m unittest discover -s tests -v`

Por favor no publiques hostnames, números de serie, UUIDs de GPU ni tokens en issues o evidencia.

**Licencia:** [MIT](LICENSE) · Hecho por [DannyBaanks](https://github.com/DannyBaanks), junto con los agentes de IA que ayudaron a afinar la laptop donde nació IHRA.
