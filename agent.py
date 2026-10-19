"""Agente orquestador con herramientas (patrón orchestrator-workers).

El "loop agéntico" está escrito a mano a propósito, para que veas cada paso:
  1. Enviamos la conversación + la lista de herramientas a Claude.
  2. Si Claude responde con bloques `tool_use`, ejecutamos esas herramientas.
  3. Devolvemos los resultados como `tool_result` y volvemos al paso 1.
  4. Cuando Claude ya no pide herramientas (stop_reason == "end_turn"), terminamos.
"""

import argparse
import json
import sys

import anthropic

from llm import MODELO, MODELO_WORKER, ErrorLLM, crear_mensaje, texto
from tools import DEFINICIONES, WORKSPACE, ErrorHerramienta, ejecutar

MAX_PASOS = 20

SYSTEM = """Eres un agente orquestador. Recibes una tarea y la resuelves coordinando herramientas.

Cómo trabajar:
- Antes de actuar, divide la tarea en subtareas y di brevemente tu plan.
- Explora el workspace con las herramientas en vez de suponer qué contiene.
- Haz todas las cuentas con la herramienta `calcular`.
- Delega a un especialista las subtareas de análisis o redacción que se beneficien de un rol enfocado;
  pásale todo el contexto que necesita, porque no ve esta conversación.
- Si una herramienta devuelve un error, corrige la llamada o cambia de enfoque.
- Al terminar, resume qué hiciste y dónde quedaron los resultados."""


def resumir(valor: str, largo: int = 160) -> str:
    valor = " ".join(str(valor).split())
    return valor if len(valor) <= largo else valor[:largo] + "…"


def ejecutar_herramientas(bloques) -> list[dict]:
    """Ejecuta todas las herramientas pedidas en un turno y devuelve los resultados juntos."""
    resultados = []
    for bloque in bloques:
        print(f"  -> {bloque.name}({resumir(json.dumps(bloque.input, ensure_ascii=False))})")
        try:
            salida, es_error = ejecutar(bloque.name, bloque.input), False
        except (ErrorHerramienta, ErrorLLM, anthropic.APIError, OSError, TypeError) as e:
            salida, es_error = f"Error: {e}", True
        print(f"     {'ERROR' if es_error else 'ok'}: {resumir(salida)}")
        resultados.append({"type": "tool_result", "tool_use_id": bloque.id, "content": salida, "is_error": es_error})
    return resultados


def correr_agente(tarea: str) -> str:
    mensajes = [{"role": "user", "content": tarea}]
    for paso in range(1, MAX_PASOS + 1):
        resp = crear_mensaje(max_tokens=16000, system=SYSTEM, tools=DEFINICIONES, messages=mensajes)

        # Se agrega el contenido completo (incluye thinking y tool_use), no solo el texto
        mensajes.append({"role": "assistant", "content": resp.content})
        pensamiento = texto(resp).strip()
        if pensamiento:
            print(f"\n[paso {paso}] Orquestador: {pensamiento}")

        pedidos = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not pedidos:
            return pensamiento

        if not pensamiento:
            print(f"\n[paso {paso}]")
        mensajes.append({"role": "user", "content": ejecutar_herramientas(pedidos)})

    aviso = f"Se alcanzó el límite de {MAX_PASOS} pasos sin terminar la tarea."
    print(f"\n{aviso}")
    return aviso


def main() -> int:
    parser = argparse.ArgumentParser(description="Agente orquestador que delega subtareas a herramientas.")
    parser.add_argument("tarea", help="Lo que quieres que haga el agente")
    args = parser.parse_args()

    if not WORKSPACE.is_dir():
        print(f"No existe el workspace: {WORKSPACE}")
        return 1
    print(f"Orquestador: {MODELO} | Especialistas: {MODELO_WORKER} | Workspace: {WORKSPACE}")
    try:
        correr_agente(args.tarea)
    except ErrorLLM as e:
        print(f"\nEl agente se detuvo: {e}")
        return 1
    except anthropic.APIError as e:
        print(f"\nError de la API de Claude: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
