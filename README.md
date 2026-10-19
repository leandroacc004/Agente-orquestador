# Agente orquestador

Un agente que recibe una tarea en lenguaje natural, la divide en subtareas y las resuelve coordinando herramientas: lee archivos por partes, hace cuentas exactas, delega análisis a "especialistas" (otros modelos) y guarda resultados. Es un proyecto mediano para aprender el patrón **orchestrator-workers** y cómo funciona un loop agéntico por dentro.

## Cómo funciona

```
                     ┌─────────────────────────────────────────┐
tarea ──► ORQUESTADOR (claude-opus-5-5): planea y decide qué usar │
              │  ▲                                              │
     tool_use │  │ tool_result                                  │
              ▼  │                                              │
   ┌──────────┴──┴─────────────────────────────────────────┐      │
   │ listar_archivos   leer_archivo (por partes)         │      │
   │ calcular          fecha_actual    guardar_archivo   │      │
   │ delegar_a_especialista ──► WORKER (claude-sonnet-5-5)│      │
   └─────────────────────────────────────────────────────┘      │
              └──────────── se repite hasta terminar ───────────┘
```

El loop está en `correr_agente()` (`agent.py`) y está escrito a mano, sin frameworks:

1. Se envía a Claude la conversación y la lista de herramientas (`tools=DEFINICIONES`).
2. Si la respuesta trae bloques `tool_use`, se ejecutan **todas** las herramientas pedidas en ese turno.
3. Los resultados vuelven como `tool_result` en un solo mensaje, con `is_error: true` si algo falló, para que el modelo corrija.
4. Se repite hasta que Claude responde sin pedir herramientas, o hasta llegar a `MAX_PASOS`.

## Conceptos clave

| Concepto | Dónde verlo | Por qué importa |
|---|---|---|
| **Orchestrator-workers** | `delegar_a_especialista` | El orquestador no hace todo: manda subtareas a un worker con un rol enfocado y **solo el contexto necesario**. El worker usa un modelo más barato. |
| **Chunking de resultados** | `leer_archivo(ruta, parte)` | Un archivo grande se entrega en partes de ~2500 caracteres. El agente pide las partes que necesita en vez de llenar su contexto de golpe. |
| **Herramientas deterministas** | `calcular` | Los modelos pueden equivocarse en aritmética. Una herramienta exacta (y segura: usa `ast`, nunca `eval`) es más confiable. |
| **Errores como información** | `ejecutar_herramientas()` | Un error no detiene al agente: se le devuelve y él decide cómo corregir. |
| **Sandbox** | `ruta_segura()` | Todas las rutas se resuelven dentro de `workspace/`. Intentos como `../../etc/passwd` se bloquean. |
| **Límite de pasos** | `MAX_PASOS` | Un agente sin límite puede quedarse en un loop gastando tokens. |
| **Strict tool use** | `"strict": True` | Garantiza que los argumentos que manda Claude cumplen el JSON Schema de cada herramienta. |

## Estructura

| Archivo | Para qué sirve |
|---|---|
| `agent.py` | System prompt del orquestador, loop agéntico y CLI |
| `tools.py` | Definiciones (lo que ve Claude) e implementaciones (lo que se ejecuta) de cada herramienta |
| `llm.py` | Cliente de Claude, modelos del orquestador y de los workers |
| `workspace/` | Archivos de ejemplo: ventas mensuales y una encuesta de clientes |

## Instalación

Necesitas Python 3.10 o superior y una API key de Anthropic ([console.anthropic.com](https://console.anthropic.com)).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY="tu-api-key"   # Windows: set ANTHROPIC_API_KEY=tu-api-key
```

## Uso

```bash
python agent.py "Calcula el ingreso total por trimestre de ventas_2025.csv, indica qué producto creció más entre el primer y el último trimestre, y guarda un resumen en resumen_ventas.md"

python agent.py "Lee la encuesta de satisfacción completa, pide a un especialista en experiencia de cliente los 3 problemas más mencionados con ejemplos, y guarda un plan de acción en plan_mejoras.md"
```

Salida de ejemplo (resumida):

```
Orquestador: claude-opus-5-5 | Especialistas: claude-sonnet-5-5 | Workspace: .../workspace

[paso 1] Orquestador: Voy a leer la encuesta completa, delegar el análisis y guardar el plan.
  -> listar_archivos({"subcarpeta": ""})
     ok: encuesta_satisfaccion.txt (3606 bytes) ventas_2025.csv (1064 bytes)
  -> leer_archivo({"ruta": "encuesta_satisfaccion.txt", "parte": 1})
     ok: [encuesta_satisfaccion.txt - parte 1 de 2] ...

[paso 2]
  -> leer_archivo({"ruta": "encuesta_satisfaccion.txt", "parte": 2})
     ok: ...

[paso 3]
  -> delegar_a_especialista({"rol": "analista de experiencia de cliente", ...})
     ok: 1. Búsqueda lenta e imprecisa (respuestas 4, 9, 15, 20)...

[paso 4]
  -> guardar_archivo({"nombre": "plan_mejoras.md", ...})
     ok: Guardado en salida/plan_mejoras.md

[paso 5] Orquestador: Listo. Leí las 22 respuestas, identifiqué 3 problemas principales...
```

Los resultados quedan en `workspace/salida/`.

## Configuración

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Tu API key (obligatoria) |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Modelo del orquestador |
| `CLAUDE_MODEL_WORKER` | `claude-sonnet-5-5` | Modelo de los especialistas |
| `AGENTE_WORKSPACE` | `workspace` | Carpeta a la que el agente tiene acceso |

## Ideas para seguir aprendiendo

1. **Aprobación humana:** antes de ejecutar `guardar_archivo`, pide confirmación en la terminal y devuelve "el usuario rechazó" si dice que no.
2. **Workers en paralelo:** si Claude pide varias delegaciones en el mismo turno, ejecútalas con `ThreadPoolExecutor`.
3. **Nueva herramienta:** agrega `buscar_en_archivo(ruta, texto)` para que el agente no tenga que leer todo para encontrar algo.
4. **Tool Runner del SDK:** reescribe el loop con `client.beta.messages.tool_runner` y compara cuánto código te ahorras.
5. **Costos:** suma `resp.usage.input_tokens` y `output_tokens` de cada paso y muestra el total al final.
