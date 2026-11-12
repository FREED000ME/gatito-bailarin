"""Forja3D Cine: arma y renderiza un guion en Blender sin interfaz.

Uso (lo lanza el workflow renderizar-video.yml):
    blender -b --factory-startup -P estudio/forja_cine.py -- --guion guiones/el-faro.json --calidad previa --salida salida

Cada escena se construye en limpio (set, luz, atmósfera, personajes, cámaras) y se renderiza con la
numeración global de fotogramas, así el montaje junta todo en orden. Todo lo que no es esencial va
protegido: si algo falla se avisa con [ForjaCine] y se sigue con un sustituto, para que una vista previa
nunca se caiga por un detalle.
"""
import argparse
import glob
import json
import math
import os
import random
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402
from comun import aviso, error  # noqa: E402
import escenografia as ES  # noqa: E402
import personaje_cuadrupedo as PC  # noqa: E402
import personaje_humano as PHU  # noqa: E402
import polyhaven as PH  # noqa: E402

# ---------------------------------------------------------------- argumentos
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--guion", required=True)
ap.add_argument("--calidad", default="previa", choices=["previa", "final"])
ap.add_argument("--salida", default="salida")
ap.add_argument("--tomas", default="")
ap.add_argument("--raiz", default=os.getcwd())
ARGS = ap.parse_args(argv)
RAIZ = os.path.abspath(ARGS.raiz)
PREVIA = ARGS.calidad == "previa"

G = comun.filtrar_tomas(comun.cargar(ARGS.guion), ARGS.tomas)
FPS = G["fps"]
SALIDA = os.path.abspath(ARGS.salida)
FRAMES = os.path.join(SALIDA, "frames")
os.makedirs(FRAMES, exist_ok=True)
PERSONAJES = {p["id"]: p for p in G.get("personajes") or [] if p.get("id")}
RENDER = G.get("render") or {}
rnd = random.Random(G.get("semilla", 7))
ES.configurar(PREVIA, int(G.get("semilla", 7)))
PARTES = {}   # pid -> {"cabeza": vacío, "nariz": vacío, ...} para apuntar la cámara
CUADRUPEDOS = {}  # pid -> info del esqueleto propio (personaje_cuadrupedo)
PUERTAS = []  # (fotograma, "abre"|"cierra") para el sonido


# ---------------------------------------------------------------- utilidades
def intentar(desc, fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: {desc} falló ({type(e).__name__}: {e}); sigo sin eso")
        return None


def color_hex(h, defecto=(0.6, 0.6, 0.6)):
    try:
        h = str(h).lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except Exception:  # noqa: BLE001
        return defecto


def kelvin_rgb(k):
    """Aproximación de Tanner Helland: temperatura de color → RGB (0-1)."""
    t = max(1000, min(40000, float(k))) / 100
    r = 255 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return tuple(max(0, min(255, c)) / 255 for c in (r, g, b))


def poner(nodo, nombres, valor):
    """Asigna la primera entrada que exista (los nombres cambian entre versiones de Blender)."""
    for n in nombres if isinstance(nombres, (list, tuple)) else [nombres]:
        if n in nodo.inputs:
            nodo.inputs[n].default_value = valor
            return True
    return False


def nodo(nt, tipo, clase, salida=None):
    """Busca un nodo por tipo o lo crea (en Blender 5 los árboles nuevos pueden venir vacíos)."""
    n = next((x for x in nt.nodes if x.type == tipo), None)
    if n is None:
        n = nt.nodes.new(clase)
        if salida:
            out = next((x for x in nt.nodes if x.type == salida[0]), None) or nt.nodes.new(salida[1])
            nt.links.new(n.outputs[0], out.inputs[0])
    return n


def material(nombre, color, rugosidad=0.6, variacion=0.15, escala=4.0, metal=0.0, sss=0.0):
    """Principled con rugosidad y color variables (lo perfecto se ve falso: capítulo 4 del libro)."""
    m = bpy.data.materials.new(nombre)
    intentar("use_nodes", setattr, m, "use_nodes", True)
    nt = m.node_tree
    bsdf = nodo(nt, "BSDF_PRINCIPLED", "ShaderNodeBsdfPrincipled", ("OUTPUT_MATERIAL", "ShaderNodeOutputMaterial"))
    poner(bsdf, "Base Color", (*color, 1))
    poner(bsdf, "Roughness", rugosidad)
    poner(bsdf, "Metallic", metal)
    if sss:
        poner(bsdf, ["Subsurface Weight", "Subsurface"], sss)
    if variacion:
        intentar("variación del material", variar, nt, bsdf, color, rugosidad, variacion, escala)
    return m


def variar(nt, bsdf, color, rugosidad, variacion, escala):
    """Rugosidad, color y relieve con ruido: ninguna superficie real es uniforme."""
    ruido = nt.nodes.new("ShaderNodeTexNoise")
    poner(ruido, "Scale", escala)
    poner(ruido, "Detail", 8.0)
    rampa = nt.nodes.new("ShaderNodeMapRange")
    poner(rampa, "To Min", max(0.0, rugosidad - variacion))
    poner(rampa, "To Max", min(1.0, rugosidad + variacion))
    nt.links.new(ruido.outputs["Fac"], rampa.inputs["Value"])
    nt.links.new(rampa.outputs["Result"], bsdf.inputs["Roughness"])
    intentar("variación de color", mezclar_color, nt, ruido, bsdf, color, variacion)
    bump = nt.nodes.new("ShaderNodeBump")
    poner(bump, "Strength", 0.15)
    nt.links.new(ruido.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


def mezclar_color(nt, ruido, bsdf, color, variacion):
    mezcla = nt.nodes.new("ShaderNodeMix")
    mezcla.data_type = "RGBA"
    mezcla.inputs[6].default_value = (*color, 1)
    mezcla.inputs[7].default_value = tuple(c * (1 - variacion) for c in color) + (1,)
    nt.links.new(ruido.outputs["Fac"], mezcla.inputs[0])
    nt.links.new(mezcla.outputs[2], bsdf.inputs["Base Color"])


def asignar(obj, mat):
    if obj and obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(mat)


def biselar(obj, ancho=0.02):
    if obj and obj.type == "MESH":
        b = obj.modifiers.new("Bisel", "BEVEL")
        b.width = ancho
        b.segments = 2
        intentar("sombreado suave", lambda: bpy.ops.object.shade_smooth())


def nuevo_vacio(nombre, pos=(0, 0, 0)):
    e = bpy.data.objects.new(nombre, None)
    bpy.context.scene.collection.objects.link(e)
    e.location = pos
    return e


# ---------------------------------------------------------------- modelos
EXT = (".glb", ".gltf", ".fbx", ".obj", ".blend")


def buscar_modelo(nombre, preferir=None):
    """Busca assets/modelos/<nombre>/ (como lo guarda guardar_asset) o assets/modelos/<nombre>.<ext>.
    Con 'preferir' (p. ej. "caminar") elige el FBX de ese clip si existe."""
    if not nombre:
        return None
    # assets/modelos/ es la carpeta del estudio; Assets/Forja3D/Modelos/ es donde guardan los proyectos de juego
    bases = [os.path.join(RAIZ, "assets", "modelos"), os.path.join(RAIZ, "Assets", "Forja3D", "Modelos")]
    candidatos = []
    for base in bases:
        for ext in EXT:
            candidatos += glob.glob(os.path.join(base, comun.slug(nombre), f"*{ext}"))
            candidatos += glob.glob(os.path.join(base, f"{comun.slug(nombre)}{ext}"))
    # Meshy deja personaje.fbx (o jefe/modelo) más un FBX por clip (caminar.fbx, correr.fbx…)
    def orden(r):
        b = os.path.basename(r).lower()
        if preferir and preferir in b:
            return (0, r)
        return (1 if any(x in b for x in ("personaje", "jefe", "modelo", "model", "base", "rig")) else 2, r)
    candidatos.sort(key=orden)
    return candidatos[0] if candidatos else None


def importar(ruta, nombre):
    """Importa un archivo y devuelve un vacío que agrupa todo lo importado."""
    antes = set(bpy.data.objects)
    ext = os.path.splitext(ruta)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=ruta)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=ruta, automatic_bone_orientation=True)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=ruta)
    elif ext == ".blend":
        with bpy.data.libraries.load(ruta, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o:
                bpy.context.scene.collection.objects.link(o)
    nuevos = [o for o in bpy.data.objects if o not in antes]
    intentar(f"revisar materiales de {nombre}", reparar_mapas, nuevos, nombre)
    raiz = nuevo_vacio(f"MOD_{nombre}")
    for o in nuevos:
        if o.parent is None:
            o.parent = raiz
    return raiz, nuevos


def reparar_mapas(objs, nombre):
    """Arregla mapas rotos de los modelos exportados por el rigging de Tripo: su FBX declara un mapa de normales
    sin datos y con la MISMA ruta que el color, así que Blender carga la textura de color como normales y el
    modelo sale casi negro con brillos azules. Si el mapa de normales usa la imagen del color (o una vacía),
    se desconecta y el modelo se ve con su textura original de Meshy."""
    hechos = set()
    for o in objs:
        if o.type != "MESH":
            continue
        for m in o.data.materials:
            if not m or not m.node_tree or m.name in hechos:
                continue
            hechos.add(m.name)
            nt = m.node_tree
            color = None
            for n in nt.nodes:
                if n.type == "BSDF_PRINCIPLED" and n.inputs["Base Color"].is_linked:
                    src = n.inputs["Base Color"].links[0].from_node
                    color = src.image if src.type == "TEX_IMAGE" else None
            for n in list(nt.nodes):
                if n.type != "NORMAL_MAP" or not n.inputs["Color"].is_linked:
                    continue
                src = n.inputs["Color"].links[0].from_node
                img = getattr(src, "image", None)
                roto = img is None or (color is not None and (img == color or bpy.path.abspath(img.filepath) == bpy.path.abspath(color.filepath)))
                try:
                    roto = roto or not img.has_data and tuple(img.size) == (0, 0)
                except Exception:  # noqa: BLE001
                    pass
                if roto:
                    for l in list(n.outputs["Normal"].links):
                        nt.links.remove(l)
                    aviso(f"{nombre}: el mapa de normales del material '{m.name}' era la textura de color (FBX del rigging de Tripo); lo quito")


def medir_altura(objs):
    zs = []
    for o in objs:
        if o.type == "MESH":
            zs += [(o.matrix_world @ Vector(c)).z for c in o.bound_box]
    return (max(zs) - min(zs)) if zs else 0


# ---------------------------------------------------------------- set
def ambiente_de(esc):
    cfg = esc.get("set") or {}
    if cfg.get("ambiente"):
        return comun.slug(cfg["ambiente"])
    props = " ".join(comun.slug(p.get("modelo") or p.get("tipo") or "") for p in cfg.get("props") or [])
    titulo = comun.slug(esc.get("titulo") or "")
    suelo = (cfg.get("suelo") or "pasto").lower()
    if "bosque" in titulo or ("arbol" in props and suelo == "tierra"):
        return "bosque"
    if suelo in ("piedra", "asfalto") or "pueblo" in titulo or "calle" in titulo:
        return "pueblo"
    if suelo == "pasto" or "jardin" in titulo:
        return "jardin"
    return "campo"


def puntos_escena(esc):
    """Dónde pasa la acción y desde dónde se mira: para dejar libre el campo visual y dar detalle cerca."""
    accion, cams = [], []
    for t in esc["tomas"]:
        for a in t.get("accion") or []:
            for k in ("desde", "hasta"):
                if a.get(k):
                    accion.append(tuple(a[k][:2]))
        if t.get("mira_a"):
            accion.append(tuple(t["mira_a"][:2]))
        cams.append(tuple((t.get("camara") or (0, -8, 1.6))[:2]))
    if not accion:
        accion = [(0.0, 0.0)]
    c = tuple(float(sum(p[i] for p in accion) / len(accion)) for i in (0, 1))
    return accion, cams, c


def libre(x, y, accion, cams, margen=1.2):
    """True si (x, y) no tapa la acción ni la línea cámara-sujeto."""
    for rx, ry, rr in G.get("_rio_tramos") or []:  # ni árboles ni casas dentro del río
        if math.hypot(x - rx, y - ry) < rr + 0.4:
            return False
    for p in accion:
        if math.hypot(x - p[0], y - p[1]) < margen + 0.6:
            return False
    for cpos in cams:
        for p in accion:
            ax, ay, bx, by = cpos[0], cpos[1], p[0], p[1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy or 1e-6
            u = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
            if math.hypot(x - (ax + u * dx), y - (ay + u * dy)) < margen:
                return False
    return True


SUELOS = {
    "pasto": (["grass", "lawn", "meadow"], (0.12, 0.2, 0.06), ("dry", "artificial", "fake", "sand", "rock", "coast", "beach", "pebble", "gravel")),
    "piedra": (["cobblestone", "cobble", "paving", "street"], (0.32, 0.31, 0.29), ("wall", "rock", "roof", "multicolored", "pebble")),
    "tierra": (["forest", "ground", "leaves", "dirt"], (0.25, 0.18, 0.12), ("wall", "rock", "roof", "sand", "snow")),
    "arena": (["sand", "beach"], (0.62, 0.52, 0.38), ("wall",)),
    "nieve": (["snow"], (0.85, 0.87, 0.9), ("wall",)),
    "asfalto": (["asphalt", "road"], (0.08, 0.08, 0.085), ("wall",)),
    "madera": (["wood", "floor", "planks"], (0.35, 0.22, 0.12), ("wall",)),
}


def set_exterior(cfg, esc):
    tipo = (cfg.get("suelo") or "pasto").lower()
    amb = ambiente_de(esc)
    lluvia = bool((esc.get("atmosfera") or {}).get("lluvia"))
    noche = comun.slug((esc.get("luz") or {}).get("hora") or "") == "noche"
    accion, cams, c = puntos_escena(esc)
    radio = max([math.hypot(p[0] - c[0], p[1] - c[1]) for p in accion + cams] + [3.0]) + 2.0
    terreno = ES.Terreno(c, radio, float(cfg.get("relieve", 0.5)), int(G.get("semilla", 7)))
    G["_terreno"] = terreno
    if tipo == "agua":
        intentar("mar", crear_mar, cfg)
        return
    palabras, color, excl = SUELOS.get(tipo, SUELOS["pasto"])
    mojado = 1.0 if lluvia else (0.5 if noche and amb in ("pueblo", "calle") else 0.0)
    acg_q = {"pasto": "grass", "piedra": "paving stones", "tierra": "ground forest leaves" if amb in ("bosque", "camino") else "ground",
             "arena": "sand", "nieve": "snow", "asfalto": "asphalt", "madera": "wood floor"}.get(tipo)
    m = ES.mat_poly(f"Suelo_{tipo}", palabras, color, 0.8, tam_m=2.5, excluir=excl + ("indoor", "interior", "tiles", "pattern", "carpet"),
                    mojado=mojado, caja=False, requerir=tuple(palabras), acg_q=acg_q)
    ES.suelo(terreno, m)
    rg = np.random.default_rng(int(G.get("semilla", 7)) + len(esc["id"]))
    aviso(f"escena {esc['id']}: ambiente '{amb}', suelo {tipo}{' mojado' if mojado else ''}")
    # el pasto y las hojas nunca tapan la lente ni los charcos
    milo_pts = [tuple(a_.get(k)[:2]) for t in esc["tomas"] for a_ in t.get("accion") or [] for k in ("desde", "hasta")
                if a_.get(k) and PC.es_cuadrupedo(PERSONAJES.get(a_.get("personaje"), {}), {})]
    rio = (esc.get("atmosfera") or {}).get("rio")
    tramos_rio = []
    if rio:  # el pasto y las hojas no crecen dentro del río
        ra, rb = np.array(rio["desde"][:2], float), np.array(rio["hasta"][:2], float)
        tramos_rio = [tuple(ra + (rb - ra) * k / 40) + (float(rio.get("ancho") or 3.0) * 0.7,) for k in range(41)]
    G["_rio_tramos"] = tramos_rio  # (ver también 'rio' más abajo: juncos, peces y árboles de la orilla)
    evitar = tramos_rio + [(cp[0], cp[1], 0.9) for cp in cams] + [(mp[0], mp[1], 0.45) for mp in milo_pts] + [(ch["pos"][0], ch["pos"][1], float(ch.get("radio") or 0.5) + 0.15)
                                                   for ch in ((esc.get("atmosfera") or {}).get("charcos") or [])]
    if amb == "jardin":
        intentar("pasto", ES.pasto, terreno, c, radio + 10, 60000, True, evitar)
        intentar("flores", ES.flores, terreno, c, radio + 6, 700, evitar)
        intentar("hojas", ES.hojas_suelo, terreno, c, radio + 6, 2500, evitar)
        vallas = [p for p in cfg.get("props") or [] if comun.slug(p.get("modelo") or "") in ("reja", "poste", "valla")]
        if vallas:
            y = float(np.mean([p["pos"][1] for p in vallas]))
            xs = [p["pos"][0] for p in vallas]
            reja = next((p for p in vallas if comun.slug(p.get("modelo") or "") == "reja"), None)
            hueco = (reja["pos"][0] - 0.5, reja["pos"][0] + 0.5) if reja else None
            x0v, x1v = min(xs) - 16, max(xs) + 16
            intentar("valla", ES.valla, x0v, x1v, y, 1.0, hueco, float((reja or {}).get("rot_z") or 35) + 20)
            # el jardín cerrado: vallas a los dos lados hasta detrás de la casa (siempre hay valla de fondo)
            for xv in (x0v, x1v):
                intentar("valla lateral", ES.valla_tramo, (xv, y), (xv, y - 22), 1.0)
            if reja and reja.get("abre"):
                intentar("reja que se abre", animar_reja, reja, esc)
            for i in range(14):
                x = min(xs) - 10 + i * 1.6 + rg.uniform(-0.3, 0.3)
                if hueco and hueco[0] - 0.6 < x < hueco[1] + 0.6:
                    continue
                intentar("arbusto", ES.arbusto, (x, y + 0.6, 0), rg.uniform(0.7, 1.2), rg)
            # al otro lado de la valla, un parque: árboles sueltos, arbustos y pasto abierto (ninguna casa)
            for i in range(26):
                x, yy = c[0] + rg.uniform(-22, 22), y + rg.uniform(5, 30)
                if not libre(x, yy, accion, cams, 1.5):
                    continue  # nunca un árbol en el camino del personaje
                intentar("árbol", ES.arbol, (x, yy, float(terreno.z(x, yy))), rg.uniform(6, 11), rg, True)
            for i in range(10):
                x, yy = c[0] + rg.uniform(-14, 14), y + rg.uniform(3, 16)
                if libre(x, yy, accion, cams, 1.0):
                    intentar("arbusto", ES.arbusto, (x, yy, 0), rg.uniform(0.6, 1.1), rg)
        intentar("casa", ES.casa, c[0] - 2, c[1] - 11, 10, 7, 5.5, 0.0, rg, noche, 1)
    elif amb in ("pueblo", "calle"):
        # La calle sigue el camino de los personajes: casas a los dos lados, nunca encima del recorrido
        desp = [(np.array(a_.get("hasta")[:2], float) - np.array(a_.get("desde")[:2], float)) for t in esc["tomas"] for a_ in t.get("accion") or []
                if a_.get("hasta") and a_.get("desde")]
        eje = max(desp, key=lambda d_: float(np.linalg.norm(d_))) if desp else np.array([1.0, 0.0])
        eje = eje / (np.linalg.norm(eje) or 1)
        perp = np.array([-eje[1], eje[0]])
        lat = [float(np.dot(np.array(p_["pos"][:2], float) - np.array(c), perp)) for p_ in cfg.get("props") or []]
        lat_cam = [float(np.dot(np.array(cp, float) - np.array(c), perp)) for cp in cams]
        lado_a = max(max(lat + [0.0]) + 2.2, max(lat_cam + [0.0]) + 1.0)  # detrás de los props y de las cámaras del lado +perp
        lado_b = min(lat + lat_cam + [0.0]) - 3.0  # al otro lado, detrás de las cámaras
        ang = math.atan2(eje[1], eje[0])
        az = math.radians(float((esc.get("luz") or {}).get("azimut") or 135) + 90)
        lado_sol = 1 if float(np.dot(np.array([math.cos(az), math.sin(az)]), perp)) > 0 else -1
        for lado, off in ((1, lado_a), (-1, lado_b)):
            bajo = (lado == lado_sol) and not noche
            if bajo:
                off += lado * 4.0
            u = -22.0
            while u < 26.0:
                ancho = rg.uniform(4.5, 7.5)
                fondo = 7.0
                centro = np.array(c) + eje * (u + ancho / 2) + perp * (off + lado * fondo / 2)
                # la fachada (frente_y local) mira hacia la calle
                alto_c = rg.uniform(3.4, 4.2) if bajo else rg.uniform(5.5, 9)
                intentar("casa", ES.casa, float(centro[0]), float(centro[1]), ancho - 0.25, fondo, alto_c, ang, rg, noche, -lado)
                u += ancho
        for i in range(8):
            u_, v_ = rg.uniform(-12, 14), lado_a - rg.uniform(0.3, 0.9)
            px, py = (np.array(c) + eje * u_ + perp * v_)
            if libre(px, py, accion, cams):
                intentar("caja", ES.caja, (px, py, 0), rg.uniform(0.4, 0.7), rg.uniform(-0.3, 0.3)) if rg.random() < 0.5 else \
                    intentar("arbusto", ES.arbusto, (px, py, 0), rg.uniform(0.5, 0.9), rg)
        if cfg.get("casa_destino"):
            intentar("casa de destino", casa_destino, cfg["casa_destino"], esc, rg, noche)
        if noche:  # faroles a lo largo de la calle: la noche se lee y los personajes no se pierden
            for k, u_ in enumerate(np.arange(-6, 18, 6.5)):
                px, py = np.array(c) + eje * u_ + perp * (lado_a - 0.6 if k % 2 else lado_b + 0.6)
                if libre(px, py, accion, cams, 0.8):
                    intentar("farol", ES.farol, (float(px), float(py), 0), True)
    elif amb in ("bosque", "camino", "campo"):
        otono = comun.slug((esc.get("luz") or {}).get("hora") or "") in ("atardecer",) and amb != "campo"
        intentar("hojas", ES.hojas_suelo, terreno, c, radio + 14, 26000 if amb != "campo" else 4000, evitar)
        intentar("pasto", ES.pasto, terreno, c, radio + 12, 14000 if amb != "campo" else 45000, amb != "campo", evitar)
        if amb in ("campo", "camino"):  # flores silvestres en manchas
            intentar("flores", ES.flores, terreno, c, radio + 8, 2600 if amb == "campo" else 900, evitar)
        if rio:
            ancho_r = float(rio.get("ancho") or 3.0)
            intentar("juncos", ES.juncos, rio["desde"], rio["hasta"], ancho_r, 900, terreno)
            esc_f0, esc_f1 = esc["tomas"][0]["f0"], esc["tomas"][-1]["f1"]
            intentar("peces", ES.peces, rio["desde"], rio["hasta"], ancho_r, esc_f0, esc_f1, FPS, rg, 3, cams[0] if cams else None)
            # sauces y árboles grandes a lo largo de las orillas (detrás del agua, enmarcan el plano)
            ra, rb = np.array(rio["desde"][:2], float), np.array(rio["hasta"][:2], float)
            dr = rb - ra
            nr = np.array([-dr[1], dr[0]]) / (np.linalg.norm(dr) or 1)
            for k in range(10):
                p = ra + dr * rg.uniform(0.25, 0.75) + nr * (ancho_r / 2 + rg.uniform(2.0, 6.0)) * (1 if k % 3 else -1)
                if libre(float(p[0]), float(p[1]), accion, cams, 4.0):
                    intentar("árbol de la orilla", ES.arbol, (float(p[0]), float(p[1]), float(terreno.z(p[0], p[1]))), rg.uniform(8, 13), rg, otono, f"Orilla{k}")
        n_arb = 45 if amb == "bosque" else 26
        puestos = 0
        for i in range(n_arb * 4):
            if puestos >= n_arb:
                break
            a = rg.uniform(0, 2 * math.pi)
            d = rg.uniform(3.5, 38) ** 1.0
            x, y = c[0] + math.cos(a) * d, c[1] + math.sin(a) * d
            if amb == "camino" and abs(x - c[0]) < 2.5:
                continue  # el camino queda libre
            if not libre(x, y, accion, cams, 4.0):  # la copa mide varios metros: que no tape la cámara
                continue
            intentar("árbol", ES.arbol, (x, y, float(terreno.z(x, y))), rg.uniform(7, 15), rg, True, f"Arbol{i}")
            puestos += 1
        for i in range(40):
            x, y = c[0] + rg.uniform(-14, 14), c[1] + rg.uniform(-8, 18)
            if libre(x, y, accion, cams, 0.9):
                intentar("arbusto", ES.arbusto, (x, y, float(terreno.z(x, y))), rg.uniform(0.4, 1.1), rg)
        for i in range(12):
            x, y = c[0] + rg.uniform(-10, 10), c[1] + rg.uniform(-6, 14)
            if libre(x, y, accion, cams, 0.9):
                intentar("roca", ES.roca, (x, y, float(terreno.z(x, y))), rg.uniform(0.2, 0.7), rg)
    G["_ambiente"] = amb


def crear_mar(cfg):
    lado = float(cfg.get("mar_lado", 400))
    bpy.ops.mesh.primitive_plane_add(size=lado, location=cfg.get("mar_pos") or (0, lado / 2 + 20, -0.3))
    mar = bpy.context.object
    mar.name = "Mar"
    m = material("Mar", (0.01, 0.04, 0.06), 0.03, variacion=0.02, escala=0.3)
    poner(nodo(m.node_tree, "BSDF_PRINCIPLED", "ShaderNodeBsdfPrincipled"), ["Specular IOR Level", "Specular"], 0.6)
    asignar(mar, m)
    ola = mar.modifiers.new("Olas", "OCEAN") if not PREVIA else None
    if ola:
        ola.size = 1.0
        ola.spatial_size = 50
        ola.resolution = 12
        ola.time = 1.0
        ola.keyframe_insert("time", frame=1)
        ola.time = 1.0 + G["duracion_s"] * 0.8
        ola.keyframe_insert("time", frame=max(2, G["fotogramas"]))


def set_interior(cfg):
    ancho, fondo, alto = [float(x) for x in (cfg.get("habitacion_m") or (6, 5, 2.7))]
    pared = material("Pared", color_hex(cfg.get("color_paredes"), (0.75, 0.72, 0.66)), 0.85, variacion=0.1, escala=3)
    piso = material("Piso", color_hex(cfg.get("color_piso"), (0.32, 0.2, 0.12)), 0.5, variacion=0.25, escala=2)
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    p = bpy.context.object
    p.scale = (ancho, fondo, 1)
    asignar(p, piso)
    for nombre, loc, rot, esc in (("Fondo", (0, fondo / 2, alto / 2), (math.pi / 2, 0, 0), (ancho, alto, 1)),
                                  ("Izq", (-ancho / 2, 0, alto / 2), (math.pi / 2, 0, math.pi / 2), (fondo, alto, 1)),
                                  ("Der", (ancho / 2, 0, alto / 2), (math.pi / 2, 0, math.pi / 2), (fondo, alto, 1)),
                                  ("Techo", (0, 0, alto), (0, 0, 0), (ancho, fondo, 1))):
        bpy.ops.mesh.primitive_plane_add(size=1, location=loc, rotation=rot)
        o = bpy.context.object
        o.name = nombre
        o.scale = esc
        asignar(o, pared)
    # Ventana: luz de área fría que entra de lado (contraste con las prácticas cálidas)
    luz = bpy.data.lights.new("Ventana", "AREA")
    luz.size = 1.4
    luz.energy = 600
    luz.color = kelvin_rgb(6500)
    v = bpy.data.objects.new("Ventana", luz)
    bpy.context.scene.collection.objects.link(v)
    v.location = (-ancho / 2 + 0.05, 0.5, alto * 0.6)
    v.rotation_euler = (0, math.radians(90), 0)


# Nombre del prop en el guion → palabras para buscarlo en Poly Haven (si no hay constructor propio)
TRADUCCION = {"banco": ["bench"], "maceta": ["pot", "plant"], "bicicleta": ["bicycle"], "silla": ["chair"], "mesa": ["table"],
              "botella": ["bottle"], "linterna": ["lantern"], "tronco": ["log"], "tocon": ["stump"], "carretilla": ["wheelbarrow"],
              "regadera": ["watering can"], "cubo": ["bucket"], "buzon": ["mailbox"], "seta": ["mushroom"], "helecho": ["fern"]}


def linterna(p, pos):
    """Haz de linterna (foco cálido) que apunta a 'apunta'; con 'parpadeo' se mueve como en la mano de alguien."""
    alto = float(p.get("alto_m") or 1.0)
    luz = bpy.data.lights.new("Linterna", "SPOT")
    luz.energy = float(p.get("potencia_w") or 250)
    luz.spot_size = math.radians(float(p.get("apertura") or 32))
    luz.spot_blend = 0.45
    luz.shadow_soft_size = 0.03
    luz.color = (1.0, 0.86, 0.62)
    o = bpy.data.objects.new("Linterna", luz)
    bpy.context.scene.collection.objects.link(o)
    o.location = (pos[0], pos[1], pos[2] + alto)
    destino = Vector(tuple(p.get("apunta") or (pos[0], pos[1] - 5, 0)))
    o.rotation_euler = (destino - o.location).to_track_quat("-Z", "Y").to_euler()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.035, location=o.location)
    foco = bpy.context.object
    asignar(foco, ES.mat_color("FocoLinterna", (1.0, 0.9, 0.7), 0.2, variacion=0, emision=(1.0, 0.85, 0.6), fuerza=60))
    if p.get("en_mano"):
        # se engancha a la mano del personaje después de crearlo (ver enganchar_linternas)
        G.setdefault("_linternas", []).append((o, foco, p["en_mano"], p.get("apunta_a"), p.get("atenuar_tomas") or []))
        return
    if p.get("parpadeo", True):
        r = random.Random(7)
        esc = G.get("_esc") or {}
        f0 = esc["tomas"][0]["f0"] if esc else 1
        f1 = esc["tomas"][-1]["f1"] if esc else 240
        base = o.rotation_euler.copy()
        for f in range(f0, f1 + 1, 8):
            o.rotation_euler = (base.x + r.uniform(-0.05, 0.05), base.y, base.z + r.uniform(-0.08, 0.08))
            o.keyframe_insert("rotation_euler", frame=f)


def enganchar_linternas(objs):
    """La linterna sigue la mano del personaje (COPY_LOCATION al hueso) y apunta siempre a su objetivo (TRACK_TO)."""
    for luz, foco, pid, apunta, atenuar in G.pop("_linternas", []):
        esc = G.get("_esc") or {}
        for t in esc.get("tomas", []):  # p. ej. durante el abrazo la linterna no le da en la cara al perro
            if t["id"] in atenuar:
                e0 = luz.data.energy
                for fr, val, oculto in ((t["f0"] - 1, e0, False), (t["f0"], e0 * 0.08, True), (t["f1"], e0 * 0.08, True), (t["f1"] + 1, e0, False)):
                    luz.data.energy = val
                    luz.data.keyframe_insert("energy", frame=fr)
                    foco.hide_render = oculto
                    foco.keyframe_insert("hide_render", frame=fr)
                luz.data.energy = e0
        if pid not in objs:
            continue
        raiz = objs[pid][0]
        arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
        mano = next((n for n in ("RightHand", "mixamorig:RightHand", "LeftHand", "mixamorig:LeftHand") if arm and n in arm.data.bones), None)
        for o in (luz, foco):
            if arm and mano:
                c = o.constraints.new("COPY_LOCATION")
                c.target = arm
                c.subtarget = mano
                c.head_tail = 1.0
            else:
                c = o.constraints.new("CHILD_OF")
                c.target = raiz
        destino = (PARTES.get(apunta) or {}).get("cuerpo") if apunta else None
        destino = destino or (objs.get(apunta) or (None, None))[1]
        if destino is not None:
            t = luz.constraints.new("TRACK_TO")
            t.target = destino
            t.track_axis = "TRACK_NEGATIVE_Z"
            t.up_axis = "UP_Y"
        aviso(f"linterna en la mano de {pid}" + (f", apuntando a {apunta}" if apunta else ""))


def _fotograma(esc, ref):
    """{'toma': 't02', 's': 6.4} → fotograma absoluto."""
    t = next((t for t in esc["tomas"] if t["id"] == ref.get("toma")), esc["tomas"][-1])
    return int(round(t["f0"] + float(ref.get("s", 0)) * FPS))


def animar_reja(reja, esc):
    """La reja del jardín pasa de entreabierta a abierta (la empuja el personaje) y se queda abierta."""
    bis = bpy.data.objects.get("Reja")
    if not bis:
        return
    ab = reja["abre"]
    a0 = bis.rotation_euler.z
    a1 = math.radians(float(ab.get("a_deg", 100)))
    fa = _fotograma(esc, {"toma": ab.get("toma"), "s": ab.get("desde_s", 0)})
    fb = _fotograma(esc, {"toma": ab.get("toma"), "s": ab.get("hasta_s", ab.get("desde_s", 0) + 0.4)})
    # rebota un poco al final de la apertura (bisagra suelta)
    for fr, ang in ((esc["tomas"][0]["f0"], a0), (fa, a0), (fb, a1), (fb + 5, a1 - math.radians(6)), (fb + 10, a1)):
        bis.rotation_euler.z = ang
        bis.keyframe_insert("rotation_euler", index=2, frame=fr)
    PUERTAS.append((fa, "reja"))
    aviso(f"la reja se abre en los fotogramas {fa}-{fb} y se queda abierta")


def casa_destino(cd, esc, rg, noche):
    """Casa a la que entran los personajes: hueca, con la puerta (con bisagra) en 'puerta' mirando hacia 'hacia'.
    La puerta se abre y se cierra en los momentos del guion (abre/cierra: toma + segundos desde/hasta)."""
    puerta = np.array(cd["puerta"][:2], float)
    hacia = np.array((cd.get("hacia") or (puerta[0], puerta[1] - 5))[:2], float)
    n = hacia - puerta
    n /= np.linalg.norm(n) or 1
    ancho, fondo, alto = float(cd.get("ancho", 6.2)), float(cd.get("fondo", 7.0)), float(cd.get("alto", 5.2))
    rot = math.atan2(n[0], -n[1])  # la fachada (y local = -1) mira hacia 'hacia'
    centro = puerta - n * fondo / 2
    raiz = ES.casa(float(centro[0]), float(centro[1]), ancho, fondo, alto, rot, rg, noche, -1, True)
    piv = bpy.data.objects.get(raiz.get("pivote_puerta", ""))
    if not piv:
        aviso("aviso: la casa de destino no tiene puerta animable")
        return
    f = float(raiz.get("frente", -1))
    abierta = -f * math.radians(float(cd.get("angulo", 100)))  # hacia dentro de la casa
    claves = [(_fotograma(esc, {"toma": esc["tomas"][0]["id"], "s": 0}), 0.0)]
    for nombre, a, b in (("abre", 0.0, abierta), ("cierra", abierta, 0.0)):
        ref = cd.get(nombre)
        if not ref:
            continue
        fa = _fotograma(esc, {"toma": ref.get("toma"), "s": ref.get("desde_s", 0)})
        fb = _fotograma(esc, {"toma": ref.get("toma"), "s": ref.get("hasta_s", ref.get("desde_s", 0) + 0.8)})
        claves += [(fa, a), (fb, b)]
        PUERTAS.append((fa, nombre))
    for fr, ang in claves:
        piv.rotation_euler.z = ang
        piv.keyframe_insert("rotation_euler", index=2, frame=fr)
    try:  # la puerta arranca y frena suave, como empujada por una mano
        for fc in piv.animation_data.action.fcurves:
            for k in fc.keyframe_points:
                k.interpolation = "BEZIER"
                k.easing = "EASE_IN_OUT"
    except Exception:  # noqa: BLE001
        pass  # Blender 5: acciones por capas; se queda con la interpolación por defecto
    aviso(f"casa de destino con la puerta en {puerta.round(2).tolist()}: abre y cierra en {[c[0] for c in claves[1:]]}")


def poner_correa(cfg, objs, esc):
    """Correa de la mano de 'de' al cuello de 'a' (curva con 3 puntos y una ligera caída), visible desde 'desde_toma'."""
    de, a = cfg.get("de"), cfg.get("a")
    if de not in objs or a not in CUADRUPEDOS:
        aviso(f"aviso: correa sin '{de}' o sin cuadrúpedo '{a}'")
        return
    raiz = objs[de][0]
    arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
    mano = next((n for n in ("LeftHand", "mixamorig:LeftHand", "RightHand", "mixamorig:RightHand") if arm and n in arm.data.bones), None)
    if not mano:
        aviso("aviso: la correa no encontró la mano")
        return
    perro = CUADRUPEDOS[a]["arm"]
    def ancla(nombre, objetivo, hueso, cola):
        e = bpy.data.objects.new(nombre, None)
        bpy.context.scene.collection.objects.link(e)
        c = e.constraints.new("COPY_LOCATION")
        c.target, c.subtarget, c.head_tail = objetivo, hueso, cola
        return e
    p0 = ancla("CorreaMano", arm, mano, 1.0)
    p2 = ancla("CorreaCollar", perro, "cuello" if "cuello" in perro.data.bones else perro.data.bones[0].name, 1.0)
    p1 = bpy.data.objects.new("CorreaMedio", None)
    bpy.context.scene.collection.objects.link(p1)
    p1.location = (0, 0, -float(cfg.get("caida_m", 0.25)))
    c = p1.constraints.new("COPY_LOCATION")
    c.target, c.subtarget, c.head_tail, c.use_offset = arm, mano, 1.0, True
    c2 = p1.constraints.new("COPY_LOCATION")
    c2.target, c2.subtarget, c2.head_tail = perro, p2.constraints[0].subtarget, 1.0
    c2.influence = 0.5
    cur = bpy.data.curves.new("Correa", "CURVE")
    cur.dimensions = "3D"
    cur.bevel_depth = 0.006
    cur.bevel_resolution = 2
    sp = cur.splines.new("NURBS")
    sp.points.add(2)
    for pt in sp.points:
        pt.co = (0, 0, 0, 1)
    sp.order_u = 3
    sp.use_endpoint_u = True
    sp.resolution_u = 12
    ob = bpy.data.objects.new("Correa", cur)
    bpy.context.scene.collection.objects.link(ob)
    for i, e in enumerate((p0, p1, p2)):
        h = ob.modifiers.new(f"Gancho{i}", "HOOK")
        h.object = e
        h.vertex_indices_set([i])
    ob.data.materials.append(ES.mat_color("Correa", (0.45, 0.06, 0.04), 0.45, variacion=0.1, escala=30))
    desde_toma = cfg.get("desde_toma") or esc["tomas"][0]["id"]
    if not any(t["id"] == desde_toma for t in esc["tomas"]):
        aviso(f"correa: la toma {desde_toma} no se renderiza ahora; queda oculta")
        ob.hide_render = True
        return
    f_vis = _fotograma(esc, {"toma": desde_toma, "s": cfg.get("desde_s", 0)})
    for fr, oculto in ((esc["tomas"][0]["f0"], f_vis > esc["tomas"][0]["f0"]), (f_vis, False)):
        ob.hide_render = oculto
        ob.keyframe_insert("hide_render", frame=fr)
    aviso(f"correa de {de} ({mano}) al cuello de {a}, visible desde el fotograma {f_vis}")


def poner_lodo(raiz, claves, f0):
    """Ensucia de lodo a un personaje: mezcla un barro con manchas sobre su color, con la intensidad en el tiempo
    (claves [[s, 0..1], …] desde el inicio de la toma). Lo que queda se conserva en las tomas siguientes."""
    for ob in [o for o in raiz.children_recursive if o.type == "MESH"]:
        for m in [m for m in ob.data.materials if m]:
            nt = m.node_tree
            if nt is None:
                continue
            val = nt.nodes.get("Lodo")
            if val is None:
                bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
                if bsdf is None:
                    continue
                val = nt.nodes.new("ShaderNodeValue")
                val.name = "Lodo"
                val.outputs[0].default_value = 0.0
                val.outputs[0].keyframe_insert("default_value", frame=0)
                ruido = nt.nodes.new("ShaderNodeTexNoise")
                poner(ruido, "Scale", 9.0)
                poner(ruido, "Detail", 6.0)
                mancha = nt.nodes.new("ShaderNodeMath")
                mancha.operation = "MULTIPLY"
                mancha.use_clamp = True
                nt.links.new(ruido.outputs["Fac"], mancha.inputs[0])
                nt.links.new(val.outputs[0], mancha.inputs[1])
                fac = nt.nodes.new("ShaderNodeMath")  # las manchas crecen con el valor: a 1 cubre casi todo
                fac.operation = "MULTIPLY"
                fac.use_clamp = True
                nt.links.new(mancha.outputs[0], fac.inputs[0])
                fac.inputs[1].default_value = 2.2
                try:  # Blender 3.4+: nodo Mix en modo color (A=6, B=7, salida 2)
                    mezcla = nt.nodes.new("ShaderNodeMix")
                    mezcla.data_type = "RGBA"
                    e_a, e_b, sal = mezcla.inputs[6], mezcla.inputs[7], mezcla.outputs[2]
                except Exception:  # noqa: BLE001
                    mezcla = nt.nodes.new("ShaderNodeMixRGB")
                    e_a, e_b, sal = mezcla.inputs[1], mezcla.inputs[2], mezcla.outputs[0]
                e_b.default_value = (0.16, 0.1, 0.055, 1.0)
                base = bsdf.inputs["Base Color"]
                if base.links:
                    nt.links.new(base.links[0].from_socket, e_a)
                else:
                    e_a.default_value = tuple(base.default_value)
                nt.links.new(fac.outputs[0], mezcla.inputs[0])
                nt.links.new(sal, base)
                rugo = nt.nodes.new("ShaderNodeMath")  # el barro mojado brilla poco: más rugosidad
                rugo.operation = "MAXIMUM"
                rugo.inputs[0].default_value = float(bsdf.inputs["Roughness"].default_value)
                nt.links.new(fac.outputs[0], rugo.inputs[1])
                if not bsdf.inputs["Roughness"].links:
                    nt.links.new(rugo.outputs[0], bsdf.inputs["Roughness"])
            for s_, v_ in claves:
                val.outputs[0].default_value = float(v_)
                val.outputs[0].keyframe_insert("default_value", frame=int(round(f0 + float(s_) * FPS)))


def salpicar(pos, fa, color, n=12, nombre="gota"):
    """Gotas que saltan de un golpe (lodo al caer, agua) y vuelven a caer."""
    m = ES.mat_color(f"Salpica_{nombre}", color, 0.5, variacion=0.2)
    rg = np.random.default_rng(int(fa) + n)
    for k in range(n):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=float(rg.uniform(0.007, 0.016)), location=(pos.x, pos.y, -3))
        g = bpy.context.object
        asignar(g, m)
        ang, vel, lado = rg.uniform(0, 2 * math.pi), rg.uniform(0.8, 1.6), rg.uniform(0.25, 0.6)
        g.keyframe_insert("location", frame=fa - 1)
        for i in range(int(0.45 * FPS) + 1):
            t = i / FPS
            g.location = (pos.x + math.cos(ang) * lado * t, pos.y + math.sin(ang) * lado * t, max(pos.z + 0.005, pos.z + vel * t - 4.9 * t * t))
            g.keyframe_insert("location", frame=fa + i)
        g.location = (pos.x, pos.y, -3)
        g.keyframe_insert("location", frame=fa + int(0.45 * FPS) + 8)


def lanzar_piedra(cfg, objs, esc):
    """Piedra que lanza un personaje: va en su mano derecha hasta 'suelta_s' y luego vuela en arco hasta 'cae'
    (en el río salpica y deja ondas). cfg: {de, toma, suelta_s, cae: [x, y], alto_m}."""
    de = cfg.get("de")
    if de not in objs:
        return
    raiz = objs[de][0]
    arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
    mano = next((n for n in ("RightHand", "mixamorig:RightHand") if arm and n in arm.data.bones), None)
    tom = next((t for t in esc["tomas"] if t["id"] == cfg.get("toma")), None)
    if not mano or not tom:
        aviso("aviso: la piedra no encontró la mano o la toma")
        return
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.035, location=(0, 0, 0))
    pie = bpy.context.object
    pie.name = "PiedraLanzada"
    pie.scale = (1.0, 0.8, 0.6)
    asignar(pie, ES.mat_color("PiedraLanzada", (0.32, 0.3, 0.27), 0.8, variacion=0.3, escala=30))
    c = pie.constraints.new("COPY_LOCATION")
    c.target, c.subtarget, c.head_tail = arm, mano, 1.0
    fs = int(round(tom["f0"] + float(cfg.get("suelta_s", 1.5)) * FPS))
    for fr, inf in ((tom["f0"] - 1, 1.0), (fs, 1.0), (fs + 1, 0.0)):
        c.influence = inf
        c.keyframe_insert("influence", frame=fr)
    # vuelo: desde un punto delante del hombro (donde la mano suelta) hasta el agua, en parábola
    acc = next((x for x in tom.get("accion") or [] if x.get("personaje") == de), {})
    r = math.radians(float(acc.get("mira_z") or 0))
    adelante = Vector((-math.sin(r), math.cos(r), 0))
    base = Vector((*(acc.get("hasta") or acc.get("desde") or (0, 0))[:2], 0.0))
    ini = base + adelante * 0.45 + Vector((0, 0, float(cfg.get("alto_m") or 1.6)))
    fin = Vector((*cfg["cae"][:2], 0.0))
    dur = max(8, int(FPS * float(cfg.get("vuelo_s") or 0.9)))
    sube = float(cfg.get("arco_m") or 1.2)
    for i in range(dur + 1):
        u = i / dur
        p = ini.lerp(fin, u) + Vector((0, 0, sube * 4 * u * (1 - u)))
        pie.location = p
        pie.rotation_euler = (u * 9, u * 5, 0)
        pie.keyframe_insert("location", frame=fs + 1 + i)
        pie.keyframe_insert("rotation_euler", frame=fs + 1 + i)
    pie.location = (fin.x, fin.y, -3)
    pie.keyframe_insert("location", frame=fs + dur + 2)
    # salpicadura: gotas que suben y caen + ondas que se abren
    fa = fs + dur + 1
    m_gota = ES.mat_color("Gota", (0.7, 0.78, 0.8), 0.05, variacion=0)
    rg = np.random.default_rng(int(fs))
    for k in range(10):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.012, location=(fin.x, fin.y, -3))
        g = bpy.context.object
        asignar(g, m_gota)
        ang, vel = rg.uniform(0, 2 * math.pi), rg.uniform(0.6, 1.4)
        g.keyframe_insert("location", frame=fa - 1)
        for i in range(int(0.5 * FPS) + 1):
            t = i / FPS
            g.location = (fin.x + math.cos(ang) * 0.4 * t, fin.y + math.sin(ang) * 0.4 * t, max(-3.0, vel * t - 4.9 * t * t))
            g.keyframe_insert("location", frame=fa + i)
        g.location = (fin.x, fin.y, -3)
        g.keyframe_insert("location", frame=fa + int(0.5 * FPS) + 1)
    m_onda = ES.mat_color("OndaPiedra", (0.75, 0.8, 0.82), 0.05, variacion=0)
    for k in range(3):
        bpy.ops.mesh.primitive_torus_add(major_radius=0.1, minor_radius=0.005, location=(fin.x, fin.y, -3))
        o = bpy.context.object
        asignar(o, m_onda)
        o.keyframe_insert("location", frame=fa + k * 6 - 1)
        o.location = (fin.x, fin.y, 0.012)
        for fr, e in ((fa + k * 6, 0.3), (fa + k * 6 + int(2 * FPS), 6.0 - k)):
            o.scale = (e, e, 1)
            o.keyframe_insert("scale", frame=fr)
            o.keyframe_insert("location", frame=fr)
    PUERTAS.append((fa, "chapoteo"))
    aviso(f"{de} lanza una piedra en el fotograma {fs}; cae al agua en el {fa}")


def poner_props(cfg):
    noche = G.get("_noche", False)
    terreno = G.get("_terreno")
    rg = np.random.default_rng(int(G.get("semilla", 7)) + 3)
    for i, p in enumerate(cfg.get("props") or []):
        nombre = comun.slug(p.get("modelo") or p.get("tipo") or f"prop{i}")
        x, y = (p.get("pos") or (0, 0, 0))[:2]
        z = float(terreno.z(x, y)) if terreno else 0.0
        e = float(p.get("escala") or 1)
        rot = math.radians(float(p.get("rot_z") or rnd.uniform(-8, 8)))
        if nombre in ("reja", "poste", "valla") and G.get("_ambiente") == "jardin":
            continue  # la valla del jardín ya la construyó set_exterior
        ruta = buscar_modelo(nombre)
        if ruta:
            raiz, _ = importar(ruta, nombre)
            raiz.location = (x, y, z)
            raiz.rotation_euler.z = rot
            raiz.scale = (e, e, e)
            continue
        if "arbol" in nombre:
            ES.arbol((x, y, z), 9 * e, rg, True)
        elif "roca" in nombre or "piedra" in nombre:
            ES.roca((x, y, z), 0.6 * e, rg)
        elif "barril" in nombre:
            ES.barril((x, y, z), rot)
        elif "farol" in nombre or "faro" == nombre:
            ES.farol((x, y, z), noche or G.get("_tarde", False), 3.2 * e)
        elif "caja" in nombre:
            ES.caja((x, y, z), 0.6 * e, rot)
        elif "arbusto" in nombre:
            ES.arbusto((x, y, z), 1.0 * e, rg)
        elif nombre == "linterna":
            intentar("linterna", linterna, p, (x, y, z))
        elif nombre in ("reja", "poste", "valla"):
            ES.valla(x - 1.5, x + 1.5, y, 1.0 * e, (x - 0.5, x + 0.5) if nombre == "reja" else None)
        else:
            palabras = TRADUCCION.get(nombre, [nombre])
            info = ES.modelo_ph(nombre, palabras)
            if info:
                ES.instancia(info, (x, y, z), rot, e, nombre)
            else:
                aviso(f"no hay modelo '{nombre}' (ni en assets/modelos/ ni en Poly Haven): uso una caja de sustituto")
                bpy.ops.mesh.primitive_cube_add(size=float(p.get("tamano_m") or 1), location=(x, y, z + 0.5))
                asignar(bpy.context.object, material("Sustituto", (0.5, 0.5, 0.5), 0.6))


# ---------------------------------------------------------------- luz y atmósfera
ELEVACION = {"amanecer": 3, "manana": 28, "mediodia": 65, "tarde": 38, "atardecer": 4, "noche": 25}
TEMPERATURA = {"amanecer": 3200, "manana": 5200, "mediodia": 5800, "tarde": 5000, "atardecer": 2900, "noche": 7500}


def mundo(luz_cfg, set_cfg):
    hora = comun.slug(luz_cfg.get("hora") or "tarde").replace("-", "")
    if hora not in ELEVACION:
        aviso(f"hora '{hora}' desconocida; uso tarde")
        hora = "tarde"
    elev = float(luz_cfg.get("elevacion") or ELEVACION[hora])
    azim = float(luz_cfg.get("azimut") or 135)
    noche = hora == "noche"
    w = bpy.data.worlds.new("Mundo")
    bpy.context.scene.world = w
    intentar("use_nodes", setattr, w, "use_nodes", True)
    nt = w.node_tree
    fondo = nodo(nt, "BACKGROUND", "ShaderNodeBackground", ("OUTPUT_WORLD", "ShaderNodeOutputWorld"))
    hdri = set_cfg.get("hdri")
    hdri_ruta = os.path.join(RAIZ, hdri) if hdri else None
    if not (hdri_ruta and os.path.isfile(hdri_ruta)) and set_cfg.get("hdri") is not False:
        r = intentar("HDRI de Poly Haven", ES.elegir_hdri, hora, float(luz_cfg.get("nubes") or 0), G.get("_amb_previo", "campo"))
        if r and r[0]:
            hdri_ruta = r[0]
            aviso(f"cielo: HDRI {r[1]} (Poly Haven)")
    if hdri_ruta and os.path.isfile(hdri_ruta):
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(hdri_ruta, check_existing=True)
        mapeo = nt.nodes.new("ShaderNodeMapping")
        coord = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(coord.outputs["Generated"], mapeo.inputs["Vector"])
        nt.links.new(mapeo.outputs["Vector"], env.inputs["Vector"])
        # Gira el HDRI para que su sol quede en el azimut del guion (sombras coherentes con la lámpara de sol)
        r_sol = intentar("dirección del sol del HDRI", ES.direccion_sol, env.image)
        if r_sol:
            theta, _ = r_sol
            beta = math.pi - theta
            alfa = math.radians(azim + 90)
            mapeo.inputs["Rotation"].default_value[2] = -(alfa - beta)
        nt.links.new(env.outputs["Color"], fondo.inputs["Color"])
        poner(fondo, "Strength", float(luz_cfg.get("intensidad_cielo") or (0.25 if noche else (0.7 if elev < 10 else 1.0))))
        G["_hdri"] = True
    else:
        if hdri:
            aviso(f"no encuentro el HDRI {hdri}; uso cielo físico")
        cielo = nt.nodes.new("ShaderNodeTexSky")
        for modelo in ("NISHITA", "MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "HOSEK_WILKIE"):
            try:
                cielo.sky_type = modelo
                break
            except Exception:  # noqa: BLE001  (los modelos de cielo cambian entre versiones)
                continue
        for attr, val in (("sun_elevation", math.radians(-8 if noche else elev)), ("sun_rotation", math.radians(azim)),
                          ("air_density", 1.0), ("dust_density", 1.0 + 3 * float(luz_cfg.get("bruma", 0.3))),
                          ("sun_disc", True)):
            if hasattr(cielo, attr):
                setattr(cielo, attr, val)
        nt.links.new(cielo.outputs["Color"], fondo.inputs["Color"])
        poner(fondo, "Strength", 0.02 if noche else float(luz_cfg.get("intensidad_cielo") or 0.35))
    # Sol (o luna) direccional: sombras de contacto nítidas y temperatura según la hora
    sol = bpy.data.lights.new("Sol", "SUN")
    sol.energy = float(luz_cfg.get("intensidad_sol") or (0.3 if noche else (2.0 if elev < 10 else 3.2)))
    if G.get("_hdri") and not noche:
        sol.energy *= 0.6  # el HDRI ya trae parte de la luz del sol
    if noche:
        sol.color = (0.55, 0.65, 1.0)  # luz de luna fría
    sol.angle = math.radians(0.6 if not noche else 1.5)
    sol.color = kelvin_rgb(float(luz_cfg.get("temperatura_k") or TEMPERATURA[hora]))
    o = bpy.data.objects.new("Sol", sol)
    bpy.context.scene.collection.objects.link(o)
    o.rotation_euler = (math.radians(90 - (elev if not noche else 35)), 0, math.radians(azim + 180))
    if float(luz_cfg.get("nubes") or 0) > 0.6:
        sol.energy *= 0.35  # cielo cubierto: luz suave
        sol.angle = math.radians(12)
    exp = float(luz_cfg.get("exposicion") if luz_cfg.get("exposicion") is not None else
                (0.9 if float(luz_cfg.get("nubes") or 0) > 0.6 else (0.4 if hora in ("amanecer", "atardecer") else (0.8 if noche else 0.0))))
    intentar("exposición", setattr, bpy.context.scene.view_settings, "exposure", exp)


def atmosfera(cfg):
    niebla = float(cfg.get("niebla") or 0)
    if niebla > 0:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 15))
        caja = bpy.context.object
        caja.name = "Atmosfera"
        caja.scale = (700, 700, 60)
        caja.location = (0, 0, 25)
        m = bpy.data.materials.new("Niebla")
        intentar("use_nodes", setattr, m, "use_nodes", True)
        nt = m.node_tree
        for n in list(nt.nodes):
            if n.type == "BSDF_PRINCIPLED":
                nt.nodes.remove(n)
        vol = nt.nodes.new("ShaderNodeVolumePrincipled")
        poner(vol, "Density", 0.002 + 0.012 * niebla)
        salida = nodo(nt, "OUTPUT_MATERIAL", "ShaderNodeOutputMaterial")
        nt.links.new(vol.outputs[0], salida.inputs["Volume"])
        caja.data.materials.append(m)
        caja.visible_shadow = False
    if cfg.get("lluvia"):
        esc = G.get("_esc") or {}
        _, cams, c = puntos_escena(esc) if esc else ([], [], (0, 0))
        f0 = esc["tomas"][0]["f0"] if esc else 1
        f1 = esc["tomas"][-1]["f1"] if esc else 240
        if cfg.get("tormenta"):
            rel = intentar("relámpagos", ES.relampagos, f0, f1, FPS, np.random.default_rng(int(G.get("semilla", 7)) + 9), int(cfg.get("relampagos") or 3))
            for fd in (list(rel["destellos"]) if rel else []):
                PUERTAS.append((int(fd) + int(0.5 * FPS), "trueno"))  # el trueno llega medio segundo después
        for cpos in cams or [c]:
            intentar("lluvia", ES.lluvia, ((cpos[0] + c[0]) / 2, (cpos[1] + c[1]) / 2), 9.0, FPS, f0, f1,
                     float(cfg.get("intensidad_lluvia") or (2.4 if cfg.get("tormenta") else 1.3)), float(cfg.get("viento") or (0.35 if cfg.get("tormenta") else 0.0)))
            break
    for ch in cfg.get("charcos") or []:
        intentar("charco", ES.charco, tuple(ch.get("pos") or (0, 0, 0)), float(ch.get("radio") or 0.5), FPS, bool(cfg.get("lluvia")))
    rio = cfg.get("rio")
    if rio:
        intentar("río", ES.rio, rio["desde"], rio["hasta"], float(rio.get("ancho") or 3.0), np.random.default_rng(int(G.get("semilla", 7)) + 5), FPS,
                 float(rio.get("corriente") or 0.35))


# ---------------------------------------------------------------- personajes
def figura(pid, cfg):
    """Sustituto de personaje (vista previa) con proporciones humanas: cuerpo, cabeza y sombra."""
    alto = float(cfg.get("altura_m") or 1.75)
    color = color_hex(cfg.get("color"), (0.35, 0.3, 0.28))
    raiz = nuevo_vacio(f"PJ_{pid}")
    bpy.ops.mesh.primitive_cylinder_add(radius=alto * 0.13, depth=alto * 0.62, location=(0, 0, alto * 0.48))
    cuerpo = bpy.context.object
    asignar(cuerpo, material(f"Ropa_{pid}", color, 0.8, variacion=0.1, escala=10))
    biselar(cuerpo, 0.04)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=alto * 0.075, location=(0, 0, alto * 0.88))
    cabeza = bpy.context.object
    asignar(cabeza, material(f"Piel_{pid}", color_hex(cfg.get("piel"), (0.75, 0.55, 0.45)), 0.5, variacion=0.1, sss=0.15))
    intentar("suavizar cabeza", lambda: bpy.ops.object.shade_smooth())
    for o in (cuerpo, cabeza):
        o.parent = raiz
    return raiz


def toma_cercana(t, pid):
    """Primeros planos y detalles, o plano medio con el personaje quieto: ahí el pelo se nota (y se paga)."""
    if t.get("plano") in ("detalle", "primer_plano"):
        return True
    quieto = all(not a.get("hasta") or a.get("hasta") == a.get("desde") for a in t.get("accion") or [] if a.get("personaje") == pid)
    return t.get("plano") == "medio" and quieto


def pelo_por_tomas(esc):
    """Con render.pelo = 'cercanos', el pelo solo se renderiza en las tomas cercanas (se enciende y apaga por toma)."""
    if RENDER.get("pelo", "cercanos" if not PREVIA else False) != "cercanos":
        return
    for pid, info in CUADRUPEDOS.items():
        mods = [m for m in info["malla"].modifiers if m.type == "PARTICLE_SYSTEM"]
        if not mods:
            continue
        for t in esc["tomas"]:
            on = toma_cercana(t, pid)
            for m in mods:
                m.show_render = on
                m.keyframe_insert("show_render", frame=t["f0"])
        G.setdefault("_pelo_tomas", []).extend((t["f0"], t["f1"]) for t in esc["tomas"] if toma_cercana(t, pid))
        aviso(f"{pid}: pelo solo en {sum(toma_cercana(t, pid) for t in esc['tomas'])} de {len(esc['tomas'])} tomas de {esc['id']} (las cercanas)")


def json_modelo(ruta):
    try:
        with open(os.path.join(os.path.dirname(ruta), "forja3d.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def orientar_humanoide(raiz, nuevos):
    """Gira el modelo para que mire hacia +Y (la convención del estudio), usando pie → dedos."""
    arm = next((o for o in nuevos if o.type == "ARMATURE"), None)
    if not arm:
        return
    bpy.context.view_layer.update()
    b = arm.data.bones
    pie = next((b[n] for n in ("LeftFoot", "mixamorig:LeftFoot", "RightFoot") if n in b), None)
    dedo = next((b[n] for n in ("LeftToeBase", "mixamorig:LeftToeBase", "RightToeBase", "LeftToe_End") if n in b), None)
    if not (pie and dedo):
        return
    d = (arm.matrix_world @ dedo.head_local) - (arm.matrix_world @ pie.head_local)
    if d.xy.length < 1e-4:
        return
    giro = math.pi / 2 - math.atan2(d.y, d.x)
    ori = nuevo_vacio(f"ORI_{raiz.name}")
    ori.parent = raiz
    for o in nuevos:
        if o.parent == raiz:
            o.parent = ori
    ori.rotation_euler.z = giro
    aviso(f"{raiz.name}: girado {math.degrees(giro):.0f}° para mirar hacia +Y")


def crear_personajes(esc):
    objs = {}
    usados = {a.get("personaje") for t in esc["tomas"] for a in t.get("accion") or []} | {t.get("mira") for t in esc["tomas"]}
    for pid, cfg in PERSONAJES.items():
        if pid not in usados:
            continue  # solo se importan los personajes que salen en esta escena
        camina = any(a.get("personaje") == pid and a.get("hasta") and a.get("hasta") != a.get("desde")
                     for t in esc["tomas"] for a in t.get("accion") or [])
        ruta = buscar_modelo(cfg.get("modelo") or pid, "caminar" if camina else None)
        cab_alto = float(cfg.get("altura_m") or 1.75) * 0.9
        cabeza = None
        if ruta:
            antes = set(bpy.data.actions)
            raiz, nuevos = importar(ruta, pid)
            if PC.es_cuadrupedo(cfg, json_modelo(ruta)):
                # El pelo se recalcula en la CPU en cada fotograma (~70 % del costo): solo en el final y si la escena
                # tiene planos cercanos del personaje (detalle, primer plano o medio). Se puede forzar con render.pelo
                modo_pelo = RENDER.get("pelo", "cercanos" if not PREVIA else False)
                con_pelo = bool(modo_pelo) and (modo_pelo is True or any(toma_cercana(t, pid) for t in esc["tomas"]))
                info = intentar(f"esqueleto propio de {pid}", PC.preparar, raiz, nuevos, cfg.get("altura_m"), pid, FPS,
                                int(G.get("semilla", 7)), bool(con_pelo), cfg.get("pelaje"), cfg.get("pelo"))
                if info:
                    CUADRUPEDOS[pid] = info
                    PARTES[pid] = info["partes"]
                    cabeza = info["partes"].get("cabeza")
            if pid not in CUADRUPEDOS:
                alto = medir_altura(nuevos)
                if alto > 0.1 and cfg.get("altura_m"):
                    f = float(cfg["altura_m"]) / alto
                    raiz.scale = (f, f, f)
                intentar(f"orientar {pid}", orientar_humanoide, raiz, nuevos)
                if not cfg.get("altura_m") and alto > 0.01:
                    # animales sin altura en el guion (un pájaro): la cámara mira a su cuerpo, no a 1.6 m de altura
                    cfg["altura_m"] = alto
                    cab_alto = alto * 0.6
                raiz["clips"] = [a.name for a in bpy.data.actions if a not in antes] or [a.name for a in bpy.data.actions]
        else:
            if not PREVIA:
                aviso(f"el personaje '{pid}' no tiene modelo en assets/modelos/ (guárdalo con guardar_asset); en el render final se verá como figura")
            raiz = figura(pid, cfg)
        if cabeza is None:
            cabeza = nuevo_vacio(f"CAB_{pid}", (0, 0, cab_alto))
            cabeza.parent = raiz
        raiz.location = (0, 0, -1000)  # fuera de cuadro hasta que una toma lo use
        objs[pid] = (raiz, cabeza)
    return objs


def animar_accion(objs, tom):
    """Mueve a cada personaje de 'desde' a 'hasta' durante la toma. Entre tomas salta (sin deslizarse)."""
    for acc in tom.get("accion") or []:
        pid = acc.get("personaje")
        if pid not in objs:
            aviso(f"toma {tom['id']}: personaje '{pid}' no está en 'personajes'")
            continue
        raiz, _ = objs[pid]
        def _p(p):  # [x, y] o [x, y, z] (lo que vuela lleva altura)
            p = list(p or (0, 0))
            return Vector((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
        desde = _p(acc.get("desde"))
        hasta = _p(acc.get("hasta") or acc.get("desde"))
        f0, f1 = tom["f0"], tom["f1"]
        if not raiz.get("animado"):
            raiz.location = Vector((0, 0, -1000))  # fuera de cuadro antes de su primera toma
            raiz.keyframe_insert("location", frame=0)
            raiz["animado"] = 1
            raiz["finales"] = [0]
        dirv = hasta - desde
        dirv = Vector((dirv.x, dirv.y, 0.0))  # el rumbo lo da el desplazamiento en el suelo (subir o bajar no gira al personaje)
        raiz.rotation_euler.z = math.atan2(dirv.y, dirv.x) - math.pi / 2 if dirv.length > 0.05 else math.radians(float(acc.get("mira_z") or 0))
        raiz.keyframe_insert("rotation_euler", frame=f0)
        raiz.keyframe_insert("rotation_euler", frame=f1)  # sin esto gira sobre sí mismo hasta la toma siguiente
        clips = list(raiz.get("clips") or [])
        pasos = max(1, int((f1 - f0) / 6)) if (dirv.length > 0.05 and not clips and pid not in CUADRUPEDOS) else 1
        if acc.get("ruta"):
            # trayecto con puntos intermedios [[s, x, y, z], …]: una caída con rachas de viento, un vuelo con curvas
            for pt in acc["ruta"]:
                raiz.location = _p(pt[1:])
                raiz.keyframe_insert("location", frame=int(round(f0 + float(pt[0]) * FPS)))
        else:
            for i in range(pasos + 1):
                # Figura de vista previa: leve balanceo al caminar
                raiz.location = desde.lerp(hasta, i / pasos) + Vector((0, 0, 0.03 if (pasos > 1 and i % 2) else 0))
                raiz.keyframe_insert("location", frame=int(round(f0 + (f1 - f0) * i / pasos)))
        if acc.get("lodo"):
            intentar(f"lodo de {pid}", poner_lodo, raiz, acc["lodo"], f0)
        if acc.get("salpica_s") is not None:
            pos_s = _p((acc.get("ruta") or [[0, *hasta]])[-1][1:])
            intentar("salpicadura de lodo", salpicar, pos_s, int(round(f0 + float(acc["salpica_s"]) * FPS)), (0.2, 0.13, 0.07), 14, "lodo")
        raiz["finales"] = list(raiz["finales"]) + [f1]
        if pid in CUADRUPEDOS:
            intentar(f"animación de {pid}", PC.animar_toma, CUADRUPEDOS[pid], pid, acc, f0, f1, FPS, tom.get("nota", ""))
        elif clips:
            # un clip pedido en el guion se reproduce aunque el personaje no se desplace (un pájaro posado respira)
            intentar("clip del personaje", poner_clip, raiz, acc.get("clip"), clips, f0, f1, dirv.length > 0.05 or bool(acc.get("clip")))
        pose = acc.get("pose")  # explícita en el guion (p. ej. "abrazo"); solo personajes humanos
        if pose and pid not in CUADRUPEDOS:
            intentar(f"pose '{pose}' de {pid}", poner_pose, raiz, pose, f0, f1, objs, tom)
        elif acc.get("poses") and pid not in CUADRUPEDOS:
            intentar(f"poses de {pid}", poner_secuencia, raiz, acc["poses"], f0, f1)
        elif pid not in CUADRUPEDOS and dirv.length <= 0.05 and not acc.get("clip"):
            # quieta sin clip: de pie y respirando (sin esto se queda con los brazos abiertos del modelo)
            intentar(f"reposo de {pid}", poner_secuencia, raiz, [{"s": 0, "pose": "de_pie"}], f0, f1)
    for raiz, _ in objs.values():
        if raiz.get("animado"):
            intentar("interpolación", interpolar, raiz, set(int(x) for x in raiz["finales"]))


def curvas(obj):
    ad = obj.animation_data
    acc = ad.action if ad else None
    if acc is None:
        return []
    lista = list(getattr(acc, "fcurves", None) or [])
    if not lista:  # Blender 5: acciones por capas y ranuras
        for capa in getattr(acc, "layers", []):
            for tira in capa.strips:
                for bolsa in getattr(tira, "channelbags", []):
                    lista += list(bolsa.fcurves)
    return lista


def interpolar(obj, finales):
    """Dentro de la toma: ritmo constante (LINEAR). Al final de cada toma: CONSTANT, para saltar al
    punto de partida de la siguiente en lugar de deslizarse."""
    for c in curvas(obj):
        for k in c.keyframe_points:
            k.interpolation = "CONSTANT" if int(round(k.co.x)) in finales else "LINEAR"


def poner_pose(raiz, pose, f0, f1, objs, tom):
    arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
    if not arm:
        return
    B = PHU.huesos_de(arm)
    if pose == "abrazo":
        # se mece despacio de lado a lado (~0.35 Hz) y la mano derecha acaricia el lomo
        claves, f = [], f0 + 10
        i = 0
        while f <= f1:
            claves.append((f, *PHU.pose_abrazo(B, mecer=0.18 * (1 if i % 2 else -1), caricia=(i % 3) / 2.0)))
            f += int(FPS * 1.4)
            i += 1
        if claves[-1][0] < f1:
            claves.append((f1, *claves[-1][1:]))
        PHU.aplicar_poses(arm, claves, f0 + 10, f1, 20, pose)
        aviso(f"{raiz.name}: abrazo con {len(claves)} poses (se mece y acaricia)")
        return
    aviso(f"pose '{pose}' desconocida")
    aviso(f"{raiz.name}: pose '{pose}' en {f0 + 10}-{f1} (entrada suave de 20 fotogramas)")


def poner_secuencia(raiz, secuencia, f0, f1):
    """Poses con nombre en el tiempo de la toma: [{"s": 0.3, "pose": "agacharse", "hasta_s": 1.8}, ...].
    Entre una y otra se pasa con suavidad; con 'hasta_s' la pose se mantiene (respirando) hasta ese segundo."""
    arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
    if not arm:
        return
    B = PHU.huesos_de(arm)
    if "Hips" not in B and "mixamorig:Hips" not in B:
        return  # no es un humano (un pájaro sin clip, por ejemplo)
    claves = []
    sec = sorted(secuencia, key=lambda p: float(p.get("s", 0)))
    for k, p in enumerate(sec):
        fa = int(round(f0 + float(p.get("s", 0)) * FPS))
        fin = float(p["hasta_s"]) if p.get("hasta_s") is not None else (float(sec[k + 1].get("s", 0)) - 0.35 if k + 1 < len(sec) else (f1 - f0) / FPS)
        fb = int(round(f0 + fin * FPS))
        i = 0
        f = fa
        while True:  # mantiene la pose con una respiración lenta (claves cada ~1.2 s)
            claves.append((min(f, fb), *PHU.pose_nombrada(B, p["pose"], respiro=(1 if i % 2 else -1) * 0.6)))
            if f >= fb:
                break
            f += int(FPS * 1.2)
            i += 1
    claves = sorted({c[0]: c for c in claves}.values(), key=lambda c: c[0])
    if claves[-1][0] < f1:
        claves.append((f1, *claves[-1][1:]))
    PHU.aplicar_poses(arm, claves, f0, f1, 6, "poses")
    aviso(f"{raiz.name}: {len(sec)} poses ({', '.join(p['pose'] for p in sec)}) en {len(claves)} claves")


UNA_VEZ = {"sacudirse", "despegar", "aterrizar", "caer", "ataque", "golpeado", "muerte"}


def poner_clip(raiz, clip, clips, f0, f1, se_mueve=True):
    arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
    if not arm:
        return
    nombre = next((c for c in clips if clip and clip.lower() in c.lower()), clips[0])
    accion = bpy.data.actions.get(nombre)
    arm.animation_data_create()
    arm.animation_data.action = None  # solo NLA: cada toma tiene su tira
    pista = arm.animation_data.nla_tracks.new()
    largo = max(1.0, accion.frame_range[1] - accion.frame_range[0])
    tira = pista.strips.new(f"{nombre}_{f0}", int(f0), accion)
    tira.extrapolation = "HOLD_FORWARD"
    if clip and comun.slug(clip) in UNA_VEZ:
        pass  # despegar, aterrizar, sacudirse…: se reproduce una vez y se queda en su última pose
    elif se_mueve:
        tira.repeat = max(1.0, (f1 - f0) / largo)
    else:
        # quieto: se congela una pose natural del clip (no la pose en T del modelo)
        a0 = accion.frame_range[0]
        tira.action_frame_start = a0
        tira.action_frame_end = a0 + 1
        tira.scale = max(1.0, float(f1 - f0))


# ---------------------------------------------------------------- cámaras
def camara(tom, objs, idx):
    datos = bpy.data.cameras.new(f"CAM_{tom['id']}")
    datos.lens = float(tom["lente_mm"])
    datos.sensor_width = 36
    datos.dof.use_dof = not PREVIA or bool(RENDER.get("dof_en_previa"))
    datos.dof.aperture_fstop = float(tom.get("diafragma") or (2.0 if tom["plano"] in ("primer_plano", "detalle") else 4.0))
    cam = bpy.data.objects.new(f"CAM_{idx:03d}_{tom['id']}", datos)
    bpy.context.scene.collection.objects.link(cam)
    pos = Vector(tom.get("camara") or (0, -8, 1.6))
    mira = tom.get("mira")
    # Dónde está el sujeto: un personaje (su cabeza) o un punto fijo
    accion = next((a for a in tom.get("accion") or [] if a.get("personaje") == mira), None)
    parte = (tom.get("mira_parte") or ("nariz" if tom.get("plano") == "detalle" and "nariz" in (tom.get("nota") or "").lower() else "cabeza"))
    if mira in objs:
        objetivo = (objetivo_suave(mira, parte, tom, objs) or (PARTES.get(mira) or {}).get(parte) or objs[mira][1])
        alto = float(PERSONAJES.get(mira, {}).get("altura_m") or 1.75) * 0.9
        ini = Vector((*((accion or {}).get("desde") or (0, 0))[:2], alto))
        fin = Vector((*((accion or {}).get("hasta") or (accion or {}).get("desde") or (0, 0))[:2], alto))
    else:
        objetivo = nuevo_vacio(f"MIRA_{tom['id']}", tuple(tom.get("mira_a") or (0, 0, 1.4)))
        ini = fin = Vector(objetivo.location)
    acompana = tom.get("movimiento") == "acompanar" or bool(tom.get("acompanar"))
    if acompana and accion:
        # vuela con el sujeto: 'camara' es la posición respecto a él; las mismas claves que su raíz → va pegada
        _ = lambda p: Vector((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0))
        pts = accion.get("ruta") or [[0, *(accion.get("desde") or (0, 0))], [(tom["f1"] - tom["f0"]) / FPS, *(accion.get("hasta") or accion.get("desde") or (0, 0))]]
        for pt in pts:
            cam.location = _(pt[1:]) + pos
            cam.keyframe_insert("location", frame=int(round(tom["f0"] + float(pt[0]) * FPS)))
        if tom.get("vibracion"):
            mover(cam, objetivo, cam.location, ini, fin, dict(tom, movimiento="ninguno"))
    if tom.get("mira_desde"):
        # empieza mirando un punto (donde se fue el pájaro) y gira hacia el sujeto en 'mira_transicion_s'
        previo = nuevo_vacio(f"MIRA0_{tom['id']}", tuple(tom["mira_desde"]))
        c0 = cam.constraints.new("TRACK_TO")
        c0.target, c0.track_axis, c0.up_axis = previo, "TRACK_NEGATIVE_Z", "UP_Y"
    c = cam.constraints.new("TRACK_TO")
    c.target = objetivo
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    if tom.get("mira_desde"):
        ft = tom["f0"] + max(1, int(float(tom.get("mira_transicion_s") or 1.2) * FPS))
        for fr, inf in ((tom["f0"], 0.0), (tom["f0"] + int(0.25 * FPS), 0.0), (ft, 1.0)):
            c.influence = inf
            c.keyframe_insert("influence", frame=fr)
    datos.dof.focus_object = objetivo
    if not (acompana and accion):
        mover(cam, objetivo, pos, ini, fin, tom)
    return cam


def objetivo_suave(pid, parte, tom, objs):
    """Punto al que mira la cámara: la parte del personaje con un filtro suave, como un camarógrafo que la sigue
    sin dar tirones con cada movimiento de la cabeza o la cola. Quieto: casi fijo; caminando: sigue el cuerpo."""
    f0, f1 = int(tom["f0"]), int(tom["f1"])
    raiz = objs[pid][0]
    if pid in CUADRUPEDOS:
        tray = (CUADRUPEDOS[pid].get("tray") or {}).get(parte) or {}
        fs = [f for f in range(f0, f1 + 1) if f in tray]
        if len(fs) < 2:
            return None
        P = np.array([tray[f] for f in fs])
    else:
        alto = float(PERSONAJES.get(pid, {}).get("altura_m") or 1.75) * 0.9
        fs = list(range(f0, f1 + 1))
        P = np.tile([0.0, 0.0, alto], (len(fs), 1))
    quieto = all(not a.get("hasta") or a.get("hasta") == a.get("desde") for a in tom.get("accion") or [] if a.get("personaje") == pid)
    ventana = int(FPS * (1.6 if quieto else 0.7))
    k = np.ones(2 * ventana + 1)
    Pp = np.pad(P, ((ventana, ventana), (0, 0)), mode="edge")
    S = np.stack([np.convolve(Pp[:, i], k / k.sum(), mode="valid") for i in range(3)], 1)
    if quieto:  # encuadre estable: 70 % fijo en el punto medio, 30 % sigue el movimiento suavizado
        S = 0.7 * P.mean(0) + 0.3 * S
    e = bpy.data.objects.new(f"MIRA_{tom['id']}_{pid}", None)
    bpy.context.scene.collection.objects.link(e)
    e.parent = raiz  # el personaje se desplaza con su raíz; aquí solo va la parte suavizada en sus ejes
    paso = max(1, int(bpy.context.scene.frame_step))
    for i, f in enumerate(fs):
        if (f - f0) % paso and f != f1:
            continue
        e.location = tuple(float(x) for x in S[i])
        e.keyframe_insert("location", frame=f)
    return e


def mover(cam, objetivo, pos, ini, fin, tom):
    """Movimientos de cámara reales. Las claves Bezier dan aceleración y frenado suaves (inercia)."""
    f0, f1 = tom["f0"], tom["f1"]
    mov = tom.get("movimiento", "fija")
    k = float(tom.get("intensidad") or 1.0)
    if mov != "ninguno":
        cam.location = pos
        cam.keyframe_insert("location", frame=f0)
    if mov in ("dolly_in", "dolly_out"):
        d = ini - pos
        paso = d.normalized() * min(d.length * 0.35, float(tom.get("distancia_m") or 3.0)) * k
        cam.location = pos + paso if mov == "dolly_in" else pos - paso
        cam.keyframe_insert("location", frame=f1)
    elif mov == "grua":
        cam.location = pos + Vector((0, 0, float(tom.get("altura_m") or 2.5) * k))
        cam.keyframe_insert("location", frame=f1)
    elif mov == "orbita":
        ang = math.radians(float(tom.get("angulo") or 30) * k)
        rel = pos - ini
        for i in range(1, 7):
            a = ang * i / 6
            cam.location = ini + Vector((rel.x * math.cos(a) - rel.y * math.sin(a), rel.x * math.sin(a) + rel.y * math.cos(a), rel.z))
            cam.keyframe_insert("location", frame=int(round(f0 + (f1 - f0) * i / 6)))
    elif mov == "seguimiento":
        cam.location = pos + (fin - ini)
        cam.keyframe_insert("location", frame=f1)
    elif mov == "paneo" and objetivo.parent is None:
        lateral = float(tom.get("paneo_m") or 2.5) * k
        base = Vector(objetivo.location)
        objetivo.location = base - Vector((lateral / 2, 0, 0))
        objetivo.keyframe_insert("location", frame=f0)
        objetivo.location = base + Vector((lateral / 2, 0, 0))
        objetivo.keyframe_insert("location", frame=f1)
    if mov == "en_mano" or tom.get("vibracion"):
        # Vibración de cámara en mano en un canal aparte (delta), compatible con cualquier movimiento
        r = random.Random(sum(map(ord, tom["id"])) * 31 + idx_semilla())
        amp = 0.012 * k
        for f in range(f0, f1 + 1, 5):
            cam.delta_location = (r.uniform(-amp, amp), r.uniform(-amp, amp) * 0.5, r.uniform(-amp, amp))
            cam.keyframe_insert("delta_location", frame=f)


def luces_toma(tom, objs):
    """Luces de cine solo para esta toma (clave suave, contraluz, relleno): como en un set real, se encienden
    al empezar la toma y se apagan al terminar. luces: [{tipo, pos, mira (personaje o punto), energia, color, tamano}]."""
    esc = bpy.context.scene
    for k, l in enumerate(tom["luces"]):
        tipo = str(l.get("tipo") or "AREA").upper()
        d = bpy.data.lights.new(f"Luz_{tom['id']}_{k}", tipo if tipo in ("AREA", "POINT", "SPOT") else "AREA")
        d.energy = float(l.get("energia") or 50)
        d.color = tuple(l.get("color") or (1.0, 0.8, 0.6))
        if d.type == "AREA":
            d.shape = "DISK"
            d.size = float(l.get("tamano") or 1.0)  # grande = sombra suave, piel favorecida
        else:
            d.shadow_soft_size = float(l.get("tamano") or 0.3)
        o = bpy.data.objects.new(d.name, d)
        esc.collection.objects.link(o)
        o.location = tuple(l.get("pos") or (1, -1, 1.5))
        mira = l.get("mira")
        destino = (PARTES.get(mira) or {}).get(l.get("parte") or "cuerpo") if isinstance(mira, str) else None
        if destino is None and isinstance(mira, str) and mira in objs:
            destino = objs[mira][1]
        if destino is None:
            destino = nuevo_vacio(f"MIRA_LUZ_{tom['id']}_{k}", tuple(mira) if isinstance(mira, (list, tuple)) else (0, 0, 0.4))
        c = o.constraints.new("TRACK_TO")
        c.target = destino
        c.track_axis = "TRACK_NEGATIVE_Z"
        c.up_axis = "UP_Y"
        for fr, oculto in ((esc.frame_start, True), (tom["f0"], False), (tom["f1"] + 1, True)):
            o.hide_render = oculto
            o.keyframe_insert("hide_render", frame=max(0, fr))
    aviso(f"{tom['id']}: {len(tom['luces'])} luces de la toma")


def idx_semilla():
    return int(G.get("semilla", 7))


# ---------------------------------------------------------------- render
def activar_gpu(escena):
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception:  # noqa: BLE001
        return False
    for tipo in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = tipo
            prefs.get_devices()
            devs = [d for d in prefs.devices if d.type == tipo]
            if devs:
                for d in prefs.devices:
                    d.use = d.type == tipo
                escena.cycles.device = "GPU"
                G["_gpu"] = tipo
                aviso(f"render en GPU ({tipo}: {', '.join(d.name for d in devs)})")
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def ajustes(escena):
    r = escena.render
    r.engine = "CYCLES"
    c = escena.cycles
    gpu = activar_gpu(escena)
    if not gpu:
        c.device = "CPU"
        aviso("render en CPU" + (" (normal en la nube de GitHub)" if PREVIA else ": el render final será lento; usa equipo mi_pc con GPU"))
    if PREVIA:
        r.resolution_x, r.resolution_y = tuple(RENDER.get("resolucion_previa") or (854, 480))
        c.samples = int(RENDER.get("muestras_previa") or 12)
        c.max_bounces = 4
        if hasattr(c, "volume_step_rate"):
            c.volume_step_rate = 4.0
        escena.frame_step = int(RENDER.get("paso_previa") or 2)  # 12 fps; el montaje los repite a 24
        intentar("GI rápida", setattr, c, "use_fast_gi", True)  # iluminación indirecta aproximada (vista previa)
        for attr, val in (("diffuse_bounces", 2), ("glossy_bounces", 2), ("transmission_bounces", 4), ("volume_bounces", 0)):
            intentar(attr, setattr, c, attr, val)
        intentar("pelo en cinta", setattr, escena.cycles_curves, "shape", "RIBBONS")
        r.use_motion_blur = False
    else:
        res = str(RENDER.get("resolucion") or "1440p")
        r.resolution_x, r.resolution_y = {"1080p": (1920, 1080), "1440p": (2560, 1440), "4k": (3840, 2160), "2160p": (3840, 2160)}.get(res.lower(), (2560, 1440))
        c.samples = int(RENDER.get("muestras") or 256)
        c.max_bounces = 12
        escena.frame_step = 1
        r.use_motion_blur = True
        r.motion_blur_shutter = 0.5  # obturador de 180°
    r.resolution_percentage = 100
    intentar("muestreo adaptativo", setattr, c, "use_adaptive_sampling", True)
    # hojas con silueta (transparencia) superpuestas: sin suficientes rebotes de transparencia salen manchas negras
    intentar("rebotes de transparencia", setattr, c, "transparent_max_bounces", 16 if PREVIA else 32)
    intentar("umbral adaptativo", setattr, c, "adaptive_threshold", 0.05 if PREVIA else 0.015)
    intentar("denoiser", setattr, c, "use_denoising", True)
    if gpu and G.get("_gpu") == "OPTIX":
        intentar("denoiser OptiX", setattr, c, "denoiser", "OPTIX")  # en la GPU, mucho más rápido que OIDN en CPU
    intentar("clamp indirecto", setattr, c, "sample_clamp_indirect", 12.0)
    escena.render.fps = FPS
    intentar("vista AgX", setattr, escena.view_settings, "view_transform", "AgX")
    for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
        if intentar("look AgX", setattr, escena.view_settings, "look", look) is None and escena.view_settings.look == look:
            break
    img = r.image_settings
    intentar("formato PNG", setattr, img, "file_format", "PNG")
    intentar("profundidad", setattr, img, "color_depth", "8" if PREVIA else "16")
    r.use_persistent_data = True


def construir_y_renderizar(esc, objs_previos=None):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    escena = bpy.context.scene
    ajustes(escena)
    set_cfg = esc.get("set") or {}
    G["_lluvia"] = bool((esc.get("atmosfera") or {}).get("lluvia"))
    G["_esc"] = esc
    G["_hdri"] = False
    G["_terreno"] = None
    G["_noche"] = comun.slug((esc.get("luz") or {}).get("hora") or "") == "noche"
    G["_tarde"] = comun.slug((esc.get("luz") or {}).get("hora") or "") in ("atardecer", "noche")
    G["_amb_previo"] = ambiente_de(esc)
    ES.configurar(PREVIA, int(G.get("semilla", 7)) + len(esc["id"]))
    PARTES.clear()
    CUADRUPEDOS.clear()
    intentar("cielo y sol", mundo, esc.get("luz") or {}, set_cfg)
    if set_cfg.get("tipo") == "interior":
        intentar("set interior", set_interior, set_cfg)
    else:
        intentar("set exterior", set_exterior, set_cfg, esc)
    intentar("props", poner_props, set_cfg)
    intentar("atmósfera", atmosfera, esc.get("atmosfera") or {})
    objs = intentar("personajes", crear_personajes, esc) or {}
    intentar("linternas en la mano", enganchar_linternas, objs)
    if set_cfg.get("piedra"):
        intentar("piedra lanzada", lanzar_piedra, set_cfg["piedra"], objs, esc)
    if set_cfg.get("correa"):
        intentar("correa", poner_correa, set_cfg["correa"], objs, esc)
    G["_pelo_tomas"] = []
    intentar("pelo por tomas", pelo_por_tomas, esc)
    marcas = []
    for i, tom in enumerate(esc["tomas"]):
        intentar(f"acción de {tom['id']}", animar_accion, objs, tom)
        cam = camara(tom, objs, i)
        if tom.get("luces"):
            intentar(f"luces de {tom['id']}", luces_toma, tom, objs)
        m = escena.timeline_markers.new(tom["id"], frame=tom["f0"])
        m.camera = cam
        marcas.append(cam)
    escena.camera = marcas[0]
    escena.frame_start = esc["tomas"][0]["f0"]
    escena.frame_end = esc["tomas"][-1]["f1"]
    escena.render.filepath = os.path.join(FRAMES, "f_####")
    if PERFIL:
        perfilar(escena, esc)
    t0 = time.time()
    n = len(range(escena.frame_start, escena.frame_end + 1, escena.frame_step))
    aviso(f"escena {esc['id']}: {len(esc['tomas'])} tomas, fotogramas {escena.frame_start}-{escena.frame_end} ({n} a renderizar)")
    intentar("hornear física del pelo", PC.hornear_dinamica_pelo, escena)
    if os.environ.get("FORJA_GUARDAR_BLEND"):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(SALIDA, f"{esc['id']}.blend"))
    bpy.ops.render.render(animation=True)
    seg = time.time() - t0
    aviso(f"escena {esc['id']} lista en {seg / 60:.1f} min ({seg / max(1, n):.1f} s por fotograma)")
    return n, seg


PERFIL = os.path.isfile(os.path.join(RAIZ, "estudio", "perfil.txt"))  # modo diagnóstico (lo activa Claude)


def perfilar(escena, esc):
    """Renderiza un fotograma de la escena con todo y luego quitando cada elemento, y dice cuánto cuesta cada uno."""
    import tempfile
    f = esc["tomas"][0]["f0"] + 2
    escena.frame_set(f)
    grupos = {
        "pelo": [m for o in escena.objects if o.type == "MESH" for m in o.modifiers if m.type == "PARTICLE_SYSTEM"],
        "hojas de árboles": [o for o in bpy.data.objects if o.name.startswith("ArbolVar") and o.name.endswith("_hojas")],
        "pasto": [o for o in escena.objects if o.name.startswith("Pasto")],
        "hojarasca": [o for o in escena.objects if o.name.startswith("Hojarasca")],
        "niebla": [o for o in escena.objects if o.name.startswith("Atmosfera")],
        "lluvia": [o for o in escena.objects if o.name.startswith("Lluvia")],
        "árboles enteros": [o for o in escena.objects if o.instance_type == "COLLECTION"],
    }
    ruta = escena.render.filepath
    escena.render.filepath = os.path.join(tempfile.gettempdir(), "perfil_")
    def medir():
        t = time.time()
        bpy.ops.render.render(write_still=False)
        return time.time() - t
    medir()  # calentamiento (compila shaders y construye la escena)
    base = medir()
    aviso(f"PERFIL {esc['id']}: fotograma completo {base:.2f} s")
    for nombre, cosas in grupos.items():
        if not cosas:
            continue
        for c in cosas:
            if hasattr(c, "show_render"):
                c.show_render = False
            else:
                c.hide_render = True
        t = medir()
        aviso(f"PERFIL {esc['id']}: sin {nombre}: {t:.2f} s  → {nombre} cuesta {max(0.0, base - t):.2f} s ({max(0.0, base - t) / max(base, 1e-6) * 100:.0f}%)")
        for c in cosas:
            if hasattr(c, "show_render"):
                c.show_render = True
            else:
                c.hide_render = False
    escena.render.filepath = ruta


PROGRESO = {"hecho": 0, "total": 0, "escena": "", "t0": time.time()}


def progreso(texto=None):
    """Escribe salida/progreso.txt (el workflow lo publica como estado del commit cada 20 s)."""
    p = PROGRESO
    if texto is None:
        dt = time.time() - p["t0"]
        spf = dt / max(1, p["hecho"])
        resto = (p["total"] - p["hecho"]) * spf / 60
        texto = f"renderizando {p['hecho']}/{p['total']} · {p['escena']} · {spf:.1f} s/fot · faltan ~{resto:.0f} min"
    import unicodedata
    texto = unicodedata.normalize("NFKD", texto.replace("·", "-")).encode("ascii", "ignore").decode()[:130]
    try:
        with open(os.path.join(SALIDA, "progreso.txt"), "w", encoding="utf-8") as f:
            f.write(texto)
    except OSError:
        pass


MAX_SPF = float(os.environ.get("FORJA_MAX_SPF") or 0)  # freno de costo en máquinas alquiladas (0 = sin límite)


@bpy.app.handlers.persistent
def _fotograma_escrito(*_):
    PROGRESO["hecho"] += 1
    progreso()
    p = PROGRESO
    if p["hecho"] == p.get("vigilar_desde", 0):
        p["t_escena"] = time.time()
    if MAX_SPF and p.get("t_escena") and p["hecho"] == p.get("vigilar_desde", 0) + 10:
        spf = (time.time() - p["t_escena"]) / 10
        limite = MAX_SPF * (7 if G.get("_pelo_tomas") else 1)  # el pelo es caro a propósito en los primeros planos
        if spf > limite:
            progreso(f"detenido por costo: {spf:.1f} s/fot (limite {limite:.1f})")
            print(f"::error::[ForjaCine] ERROR: render detenido para no gastar de más: va a {spf:.1f} s por fotograma y el límite es "
                  f"{limite:.1f} (optimiza la escena o sube la variable GPU_MAX_SPF_PREVIA / GPU_MAX_SPF_FINAL)", flush=True)
            os._exit(3)  # dentro de un manejador de Blender una excepción no detiene el render: salida inmediata


def main():
    aviso(f"Blender {bpy.app.version_string} · guion '{G.get('titulo', '?')}' · {G['duracion_s']:.1f} s · calidad {ARGS.calidad}")
    if not G.get("escenas"):
        error("el guion no tiene escenas")
    total_n, total_s = 0, 0.0
    paso = int(RENDER.get("paso_previa") or 2) if PREVIA else 1
    PROGRESO["total"] = sum(len(range(e["tomas"][0]["f0"], e["tomas"][-1]["f1"] + 1, paso)) for e in G["escenas"])
    bpy.app.handlers.render_write.append(_fotograma_escrito)
    for k, esc in enumerate(G["escenas"], 1):
        PROGRESO["escena"] = f"escena {k}/{len(G['escenas'])} ({esc['id']})"
        progreso(f"construyendo {PROGRESO['escena']} · {PROGRESO['hecho']}/{PROGRESO['total']} fotogramas listos")
        PROGRESO["vigilar_desde"] = PROGRESO["hecho"] + 2  # los 2 primeros de cada escena compilan shaders
        PROGRESO["t_escena"] = None
        n, s = construir_y_renderizar(esc)
        total_n += n
        total_s += s
    stats = {"calidad": ARGS.calidad, "fotogramas_renderizados": total_n, "segundos": round(total_s, 1),
             "segundos_por_fotograma": round(total_s / max(1, total_n), 2), "blender": bpy.app.version_string,
             "fps": FPS, "paso": int(RENDER.get("paso_previa") or 2) if PREVIA else 1, "duracion_s": G["duracion_s"],
             "guion": os.path.relpath(os.path.abspath(ARGS.guion), RAIZ).replace(os.sep, "/"), "parcial": bool(ARGS.tomas)}
    with open(os.path.join(SALIDA, "render_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    with open(os.path.join(SALIDA, "eventos.json"), "w", encoding="utf-8") as f:
        json.dump({"fps": FPS, "pasos": PC.EVENTOS, "puertas": PUERTAS, "polyhaven": sorted(set(PH.USADOS)), "ambientcg": sorted(set(ES.acg.USADOS))}, f)
    if PH.USADOS:
        aviso(f"recursos CC0 de Poly Haven usados: {', '.join(sorted(set(PH.USADOS)))}")
    aviso(f"render terminado: {total_n} fotogramas en {total_s / 60:.1f} min")
    progreso(f"render terminado: {total_n} fotogramas · montando video y sonido")


main()
