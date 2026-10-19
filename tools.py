"""Herramientas (workers) que el orquestador puede usar.

Cada herramienta tiene dos partes:
  1. Su definición (nombre, descripción, JSON Schema): es lo que ve Claude para decidir cuándo usarla.
  2. Su implementación en Python: es lo que realmente se ejecuta en tu máquina.

Todas las operaciones con archivos están limitadas a la carpeta WORKSPACE.
"""

import ast
import datetime
import operator
import os
from pathlib import Path

from llm import MODELO_WORKER, crear_mensaje, texto

WORKSPACE = Path(os.environ.get("AGENTE_WORKSPACE", "workspace")).resolve()
CARPETA_SALIDA = "salida"
TAM_PARTE = 2500


class ErrorHerramienta(Exception):
    pass


def ruta_segura(relativa: str) -> Path:
    """Evita que el agente lea o escriba fuera del workspace (por ejemplo con '../../')."""
    ruta = (WORKSPACE / relativa).resolve()
    if not ruta.is_relative_to(WORKSPACE):
        raise ErrorHerramienta(f"Ruta fuera del workspace: {relativa}")
    return ruta


def partir_en_partes(texto_completo: str, tam: int = TAM_PARTE) -> list[str]:
    """Chunking por líneas: junta líneas completas hasta llegar a `tam` caracteres."""
    partes, actual = [], ""
    for linea in texto_completo.splitlines(keepends=True):
        if actual and len(actual) + len(linea) > tam:
            partes.append(actual)
            actual = ""
        actual += linea
    if actual:
        partes.append(actual)
    return partes or [""]


# ---------- Implementaciones ----------

def listar_archivos(subcarpeta: str) -> str:
    carpeta = ruta_segura(subcarpeta)
    if not carpeta.is_dir():
        raise ErrorHerramienta(f"No existe la carpeta: {subcarpeta or '.'}")
    lineas = [
        f"{p.relative_to(WORKSPACE).as_posix()} ({p.stat().st_size} bytes)"
        for p in sorted(carpeta.rglob("*"))
        if p.is_file()
    ]
    return "\n".join(lineas) or "(carpeta vacía)"


def leer_archivo(ruta: str, parte: int) -> str:
    """Los archivos grandes se entregan por partes para no llenar el contexto del modelo de una vez."""
    archivo = ruta_segura(ruta)
    if not archivo.is_file():
        raise ErrorHerramienta(f"No existe el archivo: {ruta}")
    partes = partir_en_partes(archivo.read_text(encoding="utf-8", errors="replace"))
    if not 1 <= parte <= len(partes):
        raise ErrorHerramienta(f"Parte inválida. El archivo tiene {len(partes)} parte(s).")
    aviso = f"\n\n[Quedan {len(partes) - parte} parte(s) más]" if parte < len(partes) else ""
    return f"[{ruta} - parte {parte} de {len(partes)}]\n{partes[parte - 1]}{aviso}"


_OPERADORES = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCIONES = {"round": round, "abs": abs, "min": min, "max": max, "sum": sum}


def _evaluar(nodo):
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        izq, der = _evaluar(nodo.left), _evaluar(nodo.right)
        if isinstance(nodo.op, ast.Pow) and abs(der) > 100:
            raise ErrorHerramienta("Exponente demasiado grande")
        return _OPERADORES[type(nodo.op)](izq, der)
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.operand))
    if isinstance(nodo, (ast.List, ast.Tuple)):
        return [_evaluar(e) for e in nodo.elts]
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id in _FUNCIONES:
        return _FUNCIONES[nodo.func.id](*[_evaluar(a) for a in nodo.args])
    raise ErrorHerramienta("Expresión no permitida: solo números, operadores y round/abs/min/max/sum")


def calcular(expresion: str) -> str:
    """Calculadora segura: interpreta la expresión con `ast` en vez de usar eval()."""
    try:
        arbol = ast.parse(expresion, mode="eval")
        return str(_evaluar(arbol.body))
    except (SyntaxError, ZeroDivisionError, TypeError, ValueError, OverflowError) as e:
        raise ErrorHerramienta(f"No se pudo calcular '{expresion}': {e}")


def fecha_actual() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M (%A)")


def guardar_archivo(nombre: str, contenido: str) -> str:
    destino = ruta_segura(f"{CARPETA_SALIDA}/{nombre}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido, encoding="utf-8")
    return f"Guardado en {destino.relative_to(WORKSPACE).as_posix()} ({len(contenido)} caracteres)"


def delegar_a_especialista(rol: str, tarea: str, contexto: str) -> str:
    """Un worker es otra llamada a Claude, con su propio rol y SOLO el contexto que necesita."""
    resp = crear_mensaje(
        modelo=MODELO_WORKER,
        max_tokens=8000,
        system=f"Eres {rol}. Resuelves una subtarea que te asigna un orquestador. Responde directo y en español.",
        messages=[{"role": "user", "content": f"<contexto>\n{contexto}\n</contexto>\n\nTarea: {tarea}"}],
    )
    return texto(resp)


# ---------- Definiciones que ve Claude ----------

def _schema(propiedades: dict) -> dict:
    return {"type": "object", "properties": propiedades, "required": list(propiedades), "additionalProperties": False}


DEFINICIONES = [
    {
        "name": "listar_archivos",
        "description": "Lista los archivos del workspace con su tamaño. Usa subcarpeta '' para la raíz.",
        "input_schema": _schema({"subcarpeta": {"type": "string"}}),
    },
    {
        "name": "leer_archivo",
        "description": (
            f"Lee un archivo de texto del workspace por partes de ~{TAM_PARTE} caracteres. "
            "Empieza por la parte 1; la respuesta indica cuántas partes quedan."
        ),
        "input_schema": _schema({"ruta": {"type": "string"}, "parte": {"type": "integer"}}),
    },
    {
        "name": "calcular",
        "description": (
            "Evalúa una expresión aritmética exacta, por ejemplo 'sum([120, 340.5, 99])' o 'round(1520 / 12, 2)'. "
            "Úsala para cualquier cuenta en vez de calcular de memoria."
        ),
        "input_schema": _schema({"expresion": {"type": "string"}}),
    },
    {
        "name": "fecha_actual",
        "description": "Devuelve la fecha y hora actuales.",
        "input_schema": _schema({}),
    },
    {
        "name": "guardar_archivo",
        "description": f"Guarda un archivo de texto en la carpeta '{CARPETA_SALIDA}/' del workspace.",
        "input_schema": _schema({"nombre": {"type": "string"}, "contenido": {"type": "string"}}),
    },
    {
        "name": "delegar_a_especialista",
        "description": (
            "Delega una subtarea de análisis o redacción a un especialista (otro modelo). Indica su rol "
            "(por ejemplo 'analista de datos' o 'redactor técnico'), la tarea concreta y todo el contexto "
            "que necesita: el especialista NO ve la conversación ni los archivos."
        ),
        "input_schema": _schema({"rol": {"type": "string"}, "tarea": {"type": "string"}, "contexto": {"type": "string"}}),
    },
]
for _d in DEFINICIONES:
    _d["strict"] = True

IMPLEMENTACIONES = {
    "listar_archivos": listar_archivos,
    "leer_archivo": leer_archivo,
    "calcular": calcular,
    "fecha_actual": fecha_actual,
    "guardar_archivo": guardar_archivo,
    "delegar_a_especialista": delegar_a_especialista,
}


def ejecutar(nombre: str, entrada: dict) -> str:
    if nombre not in IMPLEMENTACIONES:
        raise ErrorHerramienta(f"Herramienta desconocida: {nombre}")
    return IMPLEMENTACIONES[nombre](**entrada)
