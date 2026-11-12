"""Forja3D Cine: utilidades compartidas (sin dependencias, sirven dentro y fuera de Blender).

Un guion es un JSON en guiones/<nombre>.json. Este módulo lo carga, lo normaliza y calcula los tiempos de
cada toma en segundos y en fotogramas, para que Blender (forja_cine.py) y el montaje (montar.py) usen
exactamente la misma línea de tiempo.
"""
import json
import math
import os
import re
import sys

FPS = 24
LENTES = {"general": 24, "medio": 40, "americano": 35, "primer_plano": 85, "detalle": 100, "cenital": 28}
MOVIMIENTOS = {"fija", "dolly_in", "dolly_out", "paneo", "grua", "orbita", "en_mano", "seguimiento"}
HORAS = {"amanecer", "manana", "mediodia", "tarde", "atardecer", "noche"}


def aviso(msg):
    """Mensajes con la etiqueta [ForjaCine]: el conector los extrae del log para mostrárselos a Claude."""
    print(f"[ForjaCine] {msg}", flush=True)


def error(msg):
    print(f"[ForjaCine] ERROR: {msg}", flush=True)
    sys.exit(1)


def slug(texto):
    t = str(texto or "").strip().lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9_-]+", "-", t).strip("-") or "sin-nombre"


def cargar(ruta):
    if not os.path.isfile(ruta):
        error(f"no existe el guion {ruta}")
    with open(ruta, encoding="utf-8") as f:
        try:
            g = json.load(f)
        except json.JSONDecodeError as e:
            error(f"el guion {ruta} no es JSON válido: {e}")
    return normalizar(g)


def normalizar(g):
    """Completa valores por defecto y calcula la línea de tiempo (inicio/fin en s y en fotogramas)."""
    g = dict(g)
    g["fps"] = int(g.get("fps") or FPS)
    fps = g["fps"]
    t = 0.0
    for ei, esc in enumerate(g.get("escenas") or []):
        esc.setdefault("id", f"sc{(ei + 1) * 10:03d}")
        esc.setdefault("set", {})
        esc.setdefault("luz", {})
        esc.setdefault("atmosfera", {})
        esc["inicio_s"] = t
        for ti, tom in enumerate(esc.get("tomas") or []):
            tom.setdefault("id", f"t{ti + 1:02d}")
            tom.setdefault("plano", "medio")
            tom.setdefault("movimiento", "fija")
            tom.setdefault("lente_mm", LENTES.get(tom["plano"], 40))
            dur = max(0.5, float(tom.get("duracion_s") or 3))
            tom["duracion_s"] = dur
            tom["inicio_s"] = t
            tom["fin_s"] = t + dur
            # Fotogramas 1-based y contiguos: la toma i va de f0 a f1 inclusive
            tom["f0"] = int(round(t * fps)) + 1
            tom["f1"] = int(round((t + dur) * fps))
            t += dur
        esc["fin_s"] = t
    g["duracion_s"] = t
    g["fotogramas"] = int(round(t * fps))
    return g


def tomas(g):
    for esc in g.get("escenas") or []:
        for tom in esc.get("tomas") or []:
            yield esc, tom


def filtrar_tomas(g, lista):
    """lista: 'sc010/t01,sc020' → conserva solo esas tomas/escenas (vista previa parcial)."""
    if not lista:
        return g
    pedidas = [x.strip() for x in str(lista).split(",") if x.strip()]
    for esc in g["escenas"]:
        esc["tomas"] = [t for t in esc["tomas"] if esc["id"] in pedidas or f"{esc['id']}/{t['id']}" in pedidas]
    g["escenas"] = [e for e in g["escenas"] if e["tomas"]]
    if not g["escenas"]:
        error(f"ninguna toma coincide con '{lista}' (usa escena/toma, p. ej. sc010/t02)")
    for esc in g["escenas"]:
        for tom in esc["tomas"]:
            for k in ("inicio_s", "fin_s", "f0", "f1"):
                tom.pop(k, None)
    return normalizar(g)


def tiempo_srt(s):
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    seg, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{seg:02d},{ms:03d}"


def tiempo_capitulo(s):
    s = int(math.floor(s))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def subtitulos(g):
    """Genera el SRT a partir de los diálogos (tiempos relativos al inicio de cada toma)."""
    lineas, n = [], 0
    for _esc, tom in tomas(g):
        for d in tom.get("dialogo") or []:
            ini = tom["inicio_s"] + float(d.get("desde_s") or 0)
            fin = tom["inicio_s"] + float(d.get("hasta_s") or min(tom["duracion_s"], float(d.get("desde_s") or 0) + 3))
            fin = min(fin, tom["fin_s"])
            if fin <= ini:
                continue
            n += 1
            texto = d.get("texto", "").strip()
            lineas.append(f"{n}\n{tiempo_srt(ini)} --> {tiempo_srt(fin)}\n{texto}\n")
    return "\n".join(lineas)


def capitulos(g):
    """Capítulos de YouTube: solo si hay 3 o más escenas de al menos 10 s (requisitos de YouTube)."""
    escenas = g.get("escenas") or []
    if len(escenas) < 3 or any(e["fin_s"] - e["inicio_s"] < 10 for e in escenas):
        return []
    return [f"{tiempo_capitulo(e['inicio_s'])} {e.get('titulo') or e['id']}" for e in escenas]
