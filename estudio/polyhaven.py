"""Forja3D Cine · cliente mínimo de Poly Haven (HDRIs, texturas y modelos CC0) con caché local.

Powered by Poly Haven (https://polyhaven.com) — los recursos son CC0; el uso de la API pide citar "Poly Haven".
Solo biblioteca estándar. Si no hay red o el recurso no existe, devuelve None y quien llama usa un sustituto.
"""
import json
import os
import random
import urllib.request

API = "https://api.polyhaven.com"
UA = {"User-Agent": "ForjaCine/1.0 (Forja3D; cortos animados en Blender)"}
CACHE = os.environ.get("FORJA_CACHE") or os.path.join(os.path.expanduser("~"), ".forja3d_cache", "polyhaven")
USADOS = []  # ids usados en este render (para los créditos)
_listas = {}
_sin_red = []  # si la API falla una vez, no se reintenta en todo el render


def _get(url, destino=None, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        datos = r.read()
    if destino:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        tmp = destino + ".part"
        with open(tmp, "wb") as f:
            f.write(datos)
        os.replace(tmp, destino)
        return destino
    return json.loads(datos.decode("utf-8"))


def _json_cache(nombre, url, dias=7):
    ruta = os.path.join(CACHE, "_api", nombre + ".json")
    import time
    if os.path.isfile(ruta) and time.time() - os.path.getmtime(ruta) < dias * 86400:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    datos = _get(url)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f)
    return datos


def lista(tipo):
    """tipo: hdris | textures | models → {id: info}"""
    if _sin_red:
        raise ConnectionError("Poly Haven no disponible en este render")
    if tipo not in _listas:
        try:
            _listas[tipo] = _json_cache(f"lista_{tipo}", f"{API}/assets?t={tipo}")
        except Exception:
            _sin_red.append(1)
            raise
    return _listas[tipo]


def buscar(tipo, incluir, excluir=(), semilla=0, top=6, requerir=()):
    """Busca por palabras en id, nombre, etiquetas y categorías. Elige al azar (determinista) entre los mejores.
    'requerir': al menos una de esas palabras debe aparecer como palabra completa (evita 'pine' = madera de pino)."""
    puntos = []
    for aid, info in lista(tipo).items():
        partes = [aid.replace("_", " "), str(info.get("name", ""))] + list(info.get("tags") or []) + list(info.get("categories") or [])
        texto = " ".join(partes).lower()
        palabras = set(texto.replace("/", " ").replace("-", " ").split())
        if any(e in texto for e in excluir):
            continue
        if requerir and not any((r in palabras) or (" " in r and r in texto) for r in requerir):
            continue
        p = sum(3 if (w in palabras or (" " in w and w in texto)) else (1 if w in texto else 0) for w in incluir)
        if p:
            puntos.append((p, info.get("download_count", 0), aid))
    if not puntos:
        return None
    puntos.sort(reverse=True)
    mejores = [a for _, _, a in puntos[:top]]
    return random.Random(semilla).choice(mejores)


def archivos(aid):
    return _json_cache(f"files_{aid}", f"{API}/files/{aid}", dias=30)


def _bajar(url, rel):
    destino = os.path.join(CACHE, rel)
    if not os.path.isfile(destino):
        _get(url, destino, timeout=300)
    return destino


def hdri(aid, res="1k"):
    f = archivos(aid).get("hdri") or {}
    r = f.get(res) or f.get("1k") or next(iter(f.values()), {})
    d = r.get("hdr") or r.get("exr")
    if not d:
        return None
    USADOS.append(aid)
    return _bajar(d["url"], os.path.join(aid, os.path.basename(d["url"])))


def textura(aid, res="1k"):
    """Devuelve {mapa: ruta} con Diffuse, nor_gl, Rough, Displacement (los que haya)."""
    f = archivos(aid)
    salida = {}
    for mapa in ("Diffuse", "nor_gl", "Rough", "Displacement", "AO"):
        m = f.get(mapa) or {}
        r = m.get(res) or m.get("1k") or (next(iter(m.values())) if m else {})
        d = r.get("jpg") or r.get("png") or r.get("exr")
        if d:
            salida[mapa] = _bajar(d["url"], os.path.join(aid, os.path.basename(d["url"])))
    if salida:
        USADOS.append(aid)
    return salida or None


def modelo(aid, res="1k"):
    """Descarga el glTF del modelo con sus texturas y devuelve la ruta del .gltf."""
    f = archivos(aid).get("gltf") or {}
    r = f.get(res) or f.get("1k") or (next(iter(f.values())) if f else {})
    g = r.get("gltf")
    if not g:
        return None
    base = os.path.join(aid, res)
    ruta = _bajar(g["url"], os.path.join(base, os.path.basename(g["url"])))
    for rel, inc in (g.get("include") or {}).items():
        _bajar(inc["url"], os.path.join(base, rel))
    USADOS.append(aid)
    return ruta


def info(tipo, aid):
    return lista(tipo).get(aid) or {}
