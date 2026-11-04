# IHRA — deja que un agente de IA te ayude a afinar tu PC Linux, con pruebas y sin humo

[English](README.md) · [Referencia técnica](docs/TECHNICAL_es.md) · [Contratos de seguridad](docs/SAFETY_CONTRACTS.md) · [Casos reales](#la-historia-detrás)

**IHRA (ISyCo Hardware Research Agent)** es un kit pequeño de herramientas más un reglamento para tu agente de IA de programación (Claude Code, Codex, opencode, Gemini CLI, …). Con él, el agente puede revisar tu hardware, medirlo y sugerirte mejoras como lo haría un científico cuidadoso. **Tú** decides cada cambio.

Existe porque muchos "trucos de optimización" de internet están mal, y un agente de IA puede sonar muy seguro con un ajuste que no hace nada o que empeora las cosas. IHRA obliga al agente a **demostrar** una mejora antes de llamarla mejora.

> [!IMPORTANT]
> IHRA es un **prototipo de investigación**, no un "booster" de un clic. Por sí solo únicamente **lee** tu sistema y corre pruebas inofensivas. Nunca hace overclock, nunca flashea firmware y nunca cambia ajustes por su cuenta. "Lo medimos y no hay una mejora segura" es un resultado perfectamente válido.

## La historia detrás

IHRA nació de sesiones reales en las que agentes de IA ayudaron a afinar una laptop HP Victus (Ryzen 5 8645HS + RTX 4050 Laptop):

- **La GPU que mentía.** Un desbloqueo experimental hacía que el driver de NVIDIA *reportara* cómputo tensorial a velocidad completa, pero la velocidad real había vuelto en silencio a la de fábrica. El modo de ahorro de energía de la laptop (`D3cold`) le cortaba la corriente a la GPU y la reiniciaba. Solo se descubrió midiendo el rendimiento real en vez de confiar en lo que decía el software. Una vez encontrada y corregida la causa, una prueba tensorial corrió a cerca de **2× la velocidad de fábrica**, y el cálculo de matrices real (cuBLAS) mejoró entre **+36 % y +48 %** (mediana, matrices de 4096×4096). → [caso completo](docs/HISTORY_ADA_D3COLD.md)
- **El perfil de CPU que parecía mejor de lo que era.** Pasar del perfil `power-saver` a `performance` subió la frecuencia sostenida cerca de un **12.6 %**, pero un benchmark real de CPU solo mejoró un **5.3 %**. Fiarse solo de la frecuencia habría exagerado el resultado. → [caso completo](docs/HISTORY_REA_CPU_01.md)

Son resultados de **una sola máquina**, no promesas para la tuya. Lo valioso es el *método*: medir primero, comparar de forma justa y ser honesto con el resultado. Ese método es lo que IHRA empaqueta.

## Cómo usarlo con tu agente de IA

**Necesitas:** Linux, `git` y Python 3 (probado con 3.12). Node.js es opcional.

**1. Descárgalo**

```bash
git clone https://github.com/DannyBaanks/hardware-research-agent.git
cd hardware-research-agent
python3 -m unittest discover -s tests   # opcional: debe terminar en "OK"
```

**2. Mira qué encuentra en tu PC** (solo lectura, no cambia nada)

```bash
python3 -m core.cli --text discover
```

Verás tu CPU, tu perfil de energía, tu GPU y tu NPU (si tienes). Sin `--text` sale en JSON, que es más fácil de leer para un agente.

**3. Abre tu agente de IA dentro de esta carpeta y pégale este prompt:**

```text
Ayúdame a investigar el rendimiento de mi PC Linux usando IHRA (este repo).
Primero lee AGENTS.md y README_es.md y luego ejecuta: python3 -m core.cli discover

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
| Revisa si un comando está en su lista de solo lectura (`sentinel`) | Hace overclock, undervolt ni modifica BIOS/firmware |
| Corre una prueba fija e inofensiva de CPU en un sandbox para ver que los resultados se repitan | Promete más FPS ni una PC más rápida |
| Rechaza afirmaciones de "más rápido" sin suficientes corridas, hashes y un control (`AntiFake`) | Deja que un agente presuma una sola corrida con suerte |
| Muestra el estado de NVIDIA Ada y la trampa de energía D3cold (`ada`) | Instala ni ejecuta el desbloqueo experimental de GPU |

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
| `python3 -m core.cli inspect <archivo>` | Analiza un binario con [REA](https://github.com/DannyBaanks/rea), si lo tienes instalado |

Si tienes Node.js, `node bin/ihra.mjs <comando>` también funciona. La lista completa, con cada límite explicado, está en la [referencia técnica](docs/TECHNICAL_es.md#referencia-de-comandos-y-advertencias).

## Preguntas

**¿Esto hará más rápidos mis juegos o mis modelos de IA?** Puede que sí, puede que no. IHRA te ayuda a *averiguarlo* con honestidad. Muchas veces la respuesta está en un perfil de energía, un ajuste del driver o la refrigeración, y a veces no hay nada seguro que ganar.

**¿Puede dañar mi PC?** IHRA por sí solo solo lee información y corre una prueba en sandbox. El riesgo viene de los cambios que **tú** apruebes. Por eso las reglas de arriba exigen una forma de deshacer cada cambio y prohíben las categorías peligrosas. Lee lo que te propone el agente antes de decir que sí.

**¿Windows o macOS?** Todavía no. IHRA lee archivos propios de Linux (`/proc`, `/sys`).

**¿Necesito una GPU NVIDIA?** No. Las revisiones de NVIDIA solo se activan si tienes una.

**¿Y el desbloqueo 2× de la GPU?** Es un proyecto experimental aparte, a nivel de kernel: [gpu-unlock-ada](https://github.com/DannyBaanks/gpu-unlock-ada). Cubre tarjetas RTX serie 40 concretas, solo está verificado en una laptop y tiene riesgos reales. IHRA nunca lo instala.

## Para desarrolladores

- [Referencia técnica](docs/TECHNICAL_es.md): qué está implementado exactamente, hasta dónde llega cada garantía y los huecos conocidos
- [Pipeline de investigación](docs/COMMON_RESEARCH_PIPELINE.md) · [Contratos de seguridad](docs/SAFETY_CONTRACTS.md) · [Arquitectura](docs/ARCHITECTURE.md) · [Roadmap](ROADMAP.md)
- Tests: `python3 -m unittest discover -s tests -v`

Por favor no publiques hostnames, números de serie, UUIDs de GPU ni tokens en issues o evidencia.

**Licencia:** [MIT](LICENSE) · Hecho por [DannyBaanks](https://github.com/DannyBaanks), junto con los agentes de IA que ayudaron a afinar la laptop donde nació IHRA.
