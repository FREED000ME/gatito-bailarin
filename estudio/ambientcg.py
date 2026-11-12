"""Forja3D Cine · cliente mínimo de ambientCG (materiales PBR de fotogrametría, CC0) con caché local.

Recursos CC0 de ambientCG.com: sin atribución obligatoria (se agradece en los créditos).
Solo biblioteca estándar. Si no hay red o no encuentra nada, devuelve None y quien llama usa otra fuente.
"""
import io
import json
import os
import random
import urllib.parse
import urllib.request
import zipfile

API = "https://ambientcg.com/api/v2/full_json"
UA = {"User-Agent": "ForjaCine/1.0 (Forja3D; cortos animados en Blender)"}
CACHE = os.environ.get("FORJA_CACHE_ACG") or os.path.join(os.path.expanduser("~"), ".forja3d_cache", "ambientcg")
USADOS = []
_sin_red = []
MAPAS = {"Color": "Diffuse", "NormalGL": "nor_gl", "Roughness": "Rough", "Displacement": "Displacement", "AmbientOcclusion": "AO"}


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _descargas(nodo):
    """Recorre el JSON y devuelve las entradas de descarga ({'attribute': '1K-JPG', 'downloadLink': ...})."""
    if isinstance(nodo, dict):
        if "attribute" in nodo and ("downloadLink" in nodo or "rawLink" in nodo):
            yield nodo
        for v in nodo.values():
            yield from _descargas(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from _descargas(v)


def buscar(q, semilla=0, excluir=(), top=6):
    if _sin_red:
        raise ConnectionError("ambientCG no disponible en este render")
    url = API + "?" + urllib.parse.urlencode({"type": "Material", "q": q, "sort": "Popular", "limit": 30,
                                               "include": "downloadData,tagData"})
    ruta = os.path.join(CACHE, "_api", "q_" + "".join(c if c.isalnum() else "_" for c in q) + ".json")
    try:
        if os.path.isfile(ruta):
            datos = json.load(open(ruta, encoding="utf-8"))
        else:
            datos = json.loads(_get(url).decode("utf-8"))
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            json.dump(datos, open(ruta, "w", encoding="utf-8"))
    except Exception:
        _sin_red.append(1)
        raise
    activos = [a for a in datos.get("foundAssets") or [] if not any(e in (a.get("assetId", "") + " " + " ".join(a.get("tags") or [])).lower() for e in excluir)]
    if not activos:
        return None
    return random.Random(semilla).choice(activos[:top])


def material(q, res="1K", semilla=0, excluir=()):
    """Descarga (con caché) el material más popular que coincida y devuelve {mapa: ruta} + 'id'."""
    a = buscar(q, semilla, excluir)
    if not a:
        return None
    aid = a["assetId"]
    carpeta = os.path.join(CACHE, aid, res)
    if not os.path.isdir(carpeta) or not os.listdir(carpeta):
        d = next((x for x in _descargas(a) if x.get("attribute", "").upper() == f"{res}-JPG"), None) or \
            next((x for x in _descargas(a) if x.get("attribute", "").upper().endswith("-JPG")), None)
        if not d:
            return None
        datos = _get(d.get("downloadLink") or d["rawLink"], timeout=300)
        os.makedirs(carpeta, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            for n in z.namelist():
                if n.lower().endswith((".jpg", ".png")) and "/" not in n.strip("/"):
                    with open(os.path.join(carpeta, os.path.basename(n)), "wb") as f:
                        f.write(z.read(n))
    salida = {}
    for f in os.listdir(carpeta):
        for clave, mapa in MAPAS.items():
            if f"_{clave}." in f:
                salida[mapa] = os.path.join(carpeta, f)
    if "Diffuse" not in salida:
        return None
    USADOS.append(aid)
    salida["id"] = aid
    return salida
