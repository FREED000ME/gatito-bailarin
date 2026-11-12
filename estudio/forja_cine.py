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
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402
from comun import aviso, error  # noqa: E402

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
    raiz = nuevo_vacio(f"MOD_{nombre}")
    for o in nuevos:
        if o.parent is None:
            o.parent = raiz
    return raiz, nuevos


def medir_altura(objs):
    zs = []
    for o in objs:
        if o.type == "MESH":
            zs += [(o.matrix_world @ Vector(c)).z for c in o.bound_box]
    return (max(zs) - min(zs)) if zs else 0


# ---------------------------------------------------------------- set
def set_exterior(cfg):
    tipo = (cfg.get("suelo") or "pasto").lower()
    colores = {"pasto": (0.12, 0.2, 0.06), "arena": (0.62, 0.52, 0.38), "piedra": (0.32, 0.31, 0.29), "nieve": (0.85, 0.87, 0.9),
               "asfalto": (0.08, 0.08, 0.085), "tierra": (0.25, 0.18, 0.12), "madera": (0.35, 0.22, 0.12), "agua": (0.02, 0.05, 0.07)}
    bpy.ops.mesh.primitive_plane_add(size=float(cfg.get("tamano_m") or 400))
    suelo = bpy.context.object
    suelo.name = "Suelo"
    rug = {"agua": 0.04, "asfalto": 0.75, "nieve": 0.45, "arena": 0.85}.get(tipo, 0.7)
    if (G.get("_lluvia")):
        rug *= 0.35  # superficies mojadas
    asignar(suelo, material(f"Suelo_{tipo}", colores.get(tipo, colores["pasto"]), rug, variacion=0.2, escala=0.8))
    if tipo != "agua":
        # Relieve suave para que el horizonte no sea una línea perfecta
        sub = suelo.modifiers.new("Sub", "SUBSURF")
        sub.subdivision_type = "SIMPLE"
        sub.levels = sub.render_levels = 6 if not PREVIA else 4
        tex = bpy.data.textures.new("Relieve", "CLOUDS")
        tex.noise_scale = 12
        d = suelo.modifiers.new("Relieve", "DISPLACE")
        d.texture = tex
        d.strength = float(cfg.get("relieve", 0.6))
    if tipo == "agua" or cfg.get("mar"):
        intentar("mar", crear_mar, cfg)


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


PROPS_PROCEDURALES = {
    "roca": lambda: (bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.6), (0.35, 0.33, 0.3), 0.85),
    "caja": lambda: (bpy.ops.mesh.primitive_cube_add(size=0.6), (0.45, 0.3, 0.17), 0.7),
    "poste": lambda: (bpy.ops.mesh.primitive_cylinder_add(radius=0.08, depth=3), (0.2, 0.15, 0.1), 0.8),
    "mesa": lambda: (bpy.ops.mesh.primitive_cube_add(size=1), (0.4, 0.25, 0.14), 0.5),
    "barril": lambda: (bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=0.9), (0.35, 0.22, 0.12), 0.65),
    "farol": lambda: (bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=3.2), (0.05, 0.05, 0.05), 0.4),
    "botella": lambda: (bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=0.28), (0.15, 0.35, 0.2), 0.05),
    "faro": lambda: (bpy.ops.mesh.primitive_cone_add(radius1=1.6, radius2=1.1, depth=16, vertices=24), (0.82, 0.8, 0.76), 0.6),
}


def poner_props(cfg):
    for i, p in enumerate(cfg.get("props") or []):
        nombre = p.get("modelo") or p.get("tipo") or f"prop{i}"
        pos = tuple(p.get("pos") or (0, 0, 0))
        ruta = buscar_modelo(nombre)
        if ruta:
            raiz, _ = importar(ruta, nombre)
        else:
            clave = next((k for k in PROPS_PROCEDURALES if k in comun.slug(nombre)), None)
            if clave:
                _, color, rug = PROPS_PROCEDURALES[clave]()
                raiz = bpy.context.object
                asignar(raiz, material(f"Prop_{clave}", color, rug))
                biselar(raiz, 0.01)
                if clave == "roca":
                    d = raiz.modifiers.new("Forma", "DISPLACE")
                    d.texture = bpy.data.textures.new("RocaTex", "VORONOI")
                    d.strength = 0.25
                if clave == "mesa":
                    raiz.scale = (1.2, 0.7, 0.04)
                    pos = (pos[0], pos[1], pos[2] + 0.75)
                if clave in ("farol", "faro"):
                    alto_luz = raiz.dimensions.z
                    luz = bpy.data.lights.new(f"Luz_{clave}{i}", "POINT")
                    luz.energy = 120 if clave == "farol" else 4000
                    luz.color = kelvin_rgb(2700)
                    lo = bpy.data.objects.new(f"Luz_{clave}{i}", luz)
                    bpy.context.scene.collection.objects.link(lo)
                    lo.location = (pos[0], pos[1], pos[2] + alto_luz * float(p.get("escala") or 1) + 0.3)
            else:
                aviso(f"no hay modelo '{nombre}' en assets/modelos/: uso una caja de sustituto (guárdalo con guardar_asset)")
                bpy.ops.mesh.primitive_cube_add(size=float(p.get("tamano_m") or 1))
                raiz = bpy.context.object
                asignar(raiz, material("Sustituto", (0.5, 0.5, 0.5), 0.6))
        alto = 0 if ruta or raiz.type != "MESH" else raiz.dimensions.z / 2
        raiz.location = (pos[0], pos[1], pos[2] + alto)
        raiz.rotation_euler.z = math.radians(float(p.get("rot_z") or rnd.uniform(-8, 8)))
        e = float(p.get("escala") or 1)
        raiz.scale = tuple(s * e for s in raiz.scale)


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
    if hdri_ruta and os.path.isfile(hdri_ruta):
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(hdri_ruta)
        nt.links.new(env.outputs["Color"], fondo.inputs["Color"])
        poner(fondo, "Strength", float(luz_cfg.get("intensidad_cielo") or 1.0))
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
    sol.energy = float(luz_cfg.get("intensidad_sol") or (0.25 if noche else (2.5 if elev < 10 else 4.0)))
    sol.angle = math.radians(0.6 if not noche else 1.5)
    sol.color = kelvin_rgb(float(luz_cfg.get("temperatura_k") or TEMPERATURA[hora]))
    o = bpy.data.objects.new("Sol", sol)
    bpy.context.scene.collection.objects.link(o)
    o.rotation_euler = (math.radians(90 - (elev if not noche else 35)), 0, math.radians(azim + 180))
    if float(luz_cfg.get("nubes") or 0) > 0.6:
        sol.energy *= 0.35  # cielo cubierto: luz suave
        sol.angle = math.radians(12)


def atmosfera(cfg):
    niebla = float(cfg.get("niebla") or 0)
    if niebla > 0:
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 15))
        caja = bpy.context.object
        caja.name = "Atmosfera"
        caja.scale = (300, 300, 30)
        m = bpy.data.materials.new("Niebla")
        intentar("use_nodes", setattr, m, "use_nodes", True)
        nt = m.node_tree
        for n in list(nt.nodes):
            if n.type == "BSDF_PRINCIPLED":
                nt.nodes.remove(n)
        vol = nt.nodes.new("ShaderNodeVolumePrincipled")
        poner(vol, "Density", 0.004 + 0.03 * niebla)
        salida = nodo(nt, "OUTPUT_MATERIAL", "ShaderNodeOutputMaterial")
        nt.links.new(vol.outputs[0], salida.inputs["Volume"])
        caja.data.materials.append(m)
        caja.visible_shadow = False
    if cfg.get("lluvia"):
        aviso("lluvia: se simula con suelo mojado y bruma (las gotas aún no se generan)")


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


def crear_personajes(esc):
    objs = {}
    usados = {a.get("personaje") for t in esc["tomas"] for a in t.get("accion") or []} | {t.get("mira") for t in esc["tomas"]}
    for pid, cfg in PERSONAJES.items():
        if pid not in usados:
            continue  # solo se importan los personajes que salen en esta escena
        camina = any(a.get("personaje") == pid and a.get("hasta") and a.get("hasta") != a.get("desde")
                     for t in esc["tomas"] for a in t.get("accion") or [])
        ruta = buscar_modelo(cfg.get("modelo") or pid, "caminar" if camina else None)
        if ruta:
            raiz, nuevos = importar(ruta, pid)
            alto = medir_altura(nuevos)
            if alto > 0.1 and cfg.get("altura_m"):
                f = float(cfg["altura_m"]) / alto
                raiz.scale = (f, f, f)
            raiz["clips"] = [a.name for a in bpy.data.actions]
        else:
            if not PREVIA:
                aviso(f"el personaje '{pid}' no tiene modelo en assets/modelos/ (guárdalo con guardar_asset); en el render final se verá como figura")
            raiz = figura(pid, cfg)
        cabeza = nuevo_vacio(f"CAB_{pid}", (0, 0, float(cfg.get("altura_m") or 1.75) * 0.9))
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
        # [x, y] en el suelo o [x, y, z] para lo que vuela (pájaros de Forja Animación)
        d3 = list(acc.get("desde", (0, 0))) + [0, 0, 0]
        h3 = list(acc.get("hasta") or acc.get("desde") or (0, 0)) + [0, 0, 0]
        desde = Vector((float(d3[0]), float(d3[1]), float(d3[2]) if len(acc.get("desde", (0, 0))) > 2 else 0.0))
        hasta = Vector((float(h3[0]), float(h3[1]), float(h3[2]) if len(acc.get("hasta") or acc.get("desde") or (0, 0)) > 2 else 0.0))
        f0, f1 = tom["f0"], tom["f1"]
        if not raiz.get("animado"):
            raiz.location = Vector((0, 0, -1000))  # fuera de cuadro antes de su primera toma
            raiz.keyframe_insert("location", frame=0)
            raiz["animado"] = 1
            raiz["finales"] = [0]
        dirv = hasta - desde
        dirv = Vector((dirv.x, dirv.y, 0.0)) if dirv.z else dirv
        raiz.rotation_euler.z = math.atan2(dirv.y, dirv.x) - math.pi / 2 if dirv.length > 0.05 else math.radians(float(acc.get("mira_z") or 0))
        raiz.keyframe_insert("rotation_euler", frame=f0)
        clips = list(raiz.get("clips") or [])
        pasos = max(1, int((f1 - f0) / 6)) if (dirv.length > 0.05 and not clips) else 1
        for i in range(pasos + 1):
            # Figura de vista previa: leve balanceo al caminar
            raiz.location = desde.lerp(hasta, i / pasos) + Vector((0, 0, 0.03 if (pasos > 1 and i % 2) else 0))
            raiz.keyframe_insert("location", frame=int(round(f0 + (f1 - f0) * i / pasos)))
        raiz["finales"] = list(raiz["finales"]) + [f1]
        if clips:
            intentar("clip del personaje", poner_clip, raiz, acc.get("clip"), clips, f0, f1)
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


def poner_clip(raiz, clip, clips, f0, f1):
    arm = next((o for o in raiz.children_recursive if o.type == "ARMATURE"), None)
    if not arm:
        return
    nombre = next((c for c in clips if clip and clip.lower() in c.lower()), clips[0])
    accion = bpy.data.actions.get(nombre)
    arm.animation_data_create()
    pista = arm.animation_data.nla_tracks.new()
    largo = max(1.0, accion.frame_range[1] - accion.frame_range[0])
    tira = pista.strips.new(nombre, int(f0), accion)
    tira.repeat = max(1.0, (f1 - f0) / largo)


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
    if mira in objs:
        objetivo = objs[mira][1]
        alto = float(PERSONAJES.get(mira, {}).get("altura_m") or 1.75) * 0.9
        ini = Vector((*((accion or {}).get("desde") or (0, 0))[:2], alto))
        fin = Vector((*((accion or {}).get("hasta") or (accion or {}).get("desde") or (0, 0))[:2], alto))
    else:
        objetivo = nuevo_vacio(f"MIRA_{tom['id']}", tuple(tom.get("mira_a") or (0, 0, 1.4)))
        ini = fin = Vector(objetivo.location)
    c = cam.constraints.new("TRACK_TO")
    c.target = objetivo
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    datos.dof.focus_object = objetivo
    mover(cam, objetivo, pos, ini, fin, tom)
    return cam


def mover(cam, objetivo, pos, ini, fin, tom):
    """Movimientos de cámara reales. Las claves Bezier dan aceleración y frenado suaves (inercia)."""
    f0, f1 = tom["f0"], tom["f1"]
    mov = tom.get("movimiento", "fija")
    k = float(tom.get("intensidad") or 1.0)
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
        r.resolution_x, r.resolution_y = (854, 480)
        c.samples = int(RENDER.get("muestras_previa") or 12)
        c.max_bounces = 4
        if hasattr(c, "volume_step_rate"):
            c.volume_step_rate = 4.0
        escena.frame_step = 2  # 12 fps; el montaje los repite a 24
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
    intentar("umbral adaptativo", setattr, c, "adaptive_threshold", 0.05 if PREVIA else 0.015)
    intentar("denoiser", setattr, c, "use_denoising", True)
    intentar("clamp indirecto", setattr, c, "sample_clamp_indirect", 12.0)
    escena.render.fps = FPS
    intentar("vista AgX", setattr, escena.view_settings, "view_transform", "AgX")
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
    intentar("cielo y sol", mundo, esc.get("luz") or {}, set_cfg)
    if set_cfg.get("tipo") == "interior":
        intentar("set interior", set_interior, set_cfg)
    else:
        intentar("set exterior", set_exterior, set_cfg)
    intentar("props", poner_props, set_cfg)
    intentar("atmósfera", atmosfera, esc.get("atmosfera") or {})
    objs = intentar("personajes", crear_personajes, esc) or {}
    marcas = []
    for i, tom in enumerate(esc["tomas"]):
        intentar(f"acción de {tom['id']}", animar_accion, objs, tom)
        cam = camara(tom, objs, i)
        m = escena.timeline_markers.new(tom["id"], frame=tom["f0"])
        m.camera = cam
        marcas.append(cam)
    escena.camera = marcas[0]
    escena.frame_start = esc["tomas"][0]["f0"]
    escena.frame_end = esc["tomas"][-1]["f1"]
    escena.render.filepath = os.path.join(FRAMES, "f_####")
    t0 = time.time()
    n = len(range(escena.frame_start, escena.frame_end + 1, escena.frame_step))
    aviso(f"escena {esc['id']}: {len(esc['tomas'])} tomas, fotogramas {escena.frame_start}-{escena.frame_end} ({n} a renderizar)")
    if os.environ.get("FORJA_GUARDAR_BLEND"):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(SALIDA, f"{esc['id']}.blend"))
    bpy.ops.render.render(animation=True)
    seg = time.time() - t0
    aviso(f"escena {esc['id']} lista en {seg / 60:.1f} min ({seg / max(1, n):.1f} s por fotograma)")
    return n, seg


def main():
    aviso(f"Blender {bpy.app.version_string} · guion '{G.get('titulo', '?')}' · {G['duracion_s']:.1f} s · calidad {ARGS.calidad}")
    if not G.get("escenas"):
        error("el guion no tiene escenas")
    total_n, total_s = 0, 0.0
    for esc in G["escenas"]:
        n, s = construir_y_renderizar(esc)
        total_n += n
        total_s += s
    stats = {"calidad": ARGS.calidad, "fotogramas_renderizados": total_n, "segundos": round(total_s, 1),
             "segundos_por_fotograma": round(total_s / max(1, total_n), 2), "blender": bpy.app.version_string,
             "fps": FPS, "paso": 2 if PREVIA else 1, "duracion_s": G["duracion_s"],
             "guion": os.path.relpath(os.path.abspath(ARGS.guion), RAIZ).replace(os.sep, "/"), "parcial": bool(ARGS.tomas)}
    with open(os.path.join(SALIDA, "render_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    aviso(f"render terminado: {total_n} fotogramas en {total_s / 60:.1f} min")


main()
