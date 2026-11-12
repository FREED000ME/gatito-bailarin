"""Forja3D Cine · escenografía: suelos con textura real, cielo HDRI, árboles, casas, vallas, faroles,
barriles, pasto, hojas de otoño, lluvia y charcos.

Primero intenta recursos CC0 de Poly Haven (polyhaven.py); si no hay red o no encuentra nada,
construye un sustituto procedural decente. Todo va protegido: un fallo aquí nunca tumba el render.
"""
import math
import random

import bpy
import numpy as np
from mathutils import Vector

import ambientcg as acg
import polyhaven as ph
from comun import aviso

PREVIA = True
SEMILLA = 7
_mats = {}
_modelos = {}


def configurar(previa, semilla):
    global PREVIA, SEMILLA
    PREVIA, SEMILLA = previa, semilla
    _mats.clear()
    _modelos.clear()


def _try(desc, fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: {desc} falló ({type(e).__name__}: {e}); uso un sustituto")
        return None


def _res():
    return "1k" if PREVIA else "2k"


def _entrada(nodo, nombres):
    for n in nombres if isinstance(nombres, (list, tuple)) else [nombres]:
        if n in nodo.inputs:
            return nodo.inputs[n]
    return None


def _poner(nodo, nombres, valor):
    e = _entrada(nodo, nombres)
    if e is not None:
        e.default_value = valor


def _mat_base(nombre):
    m = bpy.data.materials.new(nombre)
    try:
        m.use_nodes = True
    except Exception:  # noqa: BLE001
        pass
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None) or nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
    if not out.inputs["Surface"].is_linked:
        nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return m, nt, bsdf, out


def mat_color(nombre, color, rug=0.6, variacion=0.15, escala=3.0, emision=None, fuerza=0.0, alfa=1.0, transmision=0.0, sss=0.0, metal=0.0):
    clave = ("c", nombre)
    if clave in _mats:
        return _mats[clave]
    m, nt, bsdf, _ = _mat_base(nombre)
    _poner(bsdf, "Base Color", (*color, 1))
    _poner(bsdf, "Roughness", rug)
    _poner(bsdf, "Metallic", metal)
    if transmision:
        _poner(bsdf, ["Transmission Weight", "Transmission"], transmision)
    if sss:
        _poner(bsdf, ["Subsurface Weight", "Subsurface"], sss)
    if alfa < 1:
        _poner(bsdf, "Alpha", alfa)
    if emision:
        _poner(bsdf, ["Emission Color", "Emission"], (*emision, 1))
        _poner(bsdf, "Emission Strength", fuerza)
    if variacion:
        ruido = nt.nodes.new("ShaderNodeTexNoise")
        _poner(ruido, "Scale", escala)
        _poner(ruido, "Detail", 6.0)
        mezcla = nt.nodes.new("ShaderNodeMix")
        mezcla.data_type = "RGBA"
        mezcla.inputs[6].default_value = (*color, 1)
        mezcla.inputs[7].default_value = tuple(max(0, c * (1 - variacion * 2)) for c in color) + (1,)
        nt.links.new(ruido.outputs["Fac"], mezcla.inputs[0])
        nt.links.new(mezcla.outputs[2], bsdf.inputs["Base Color"])
        bump = nt.nodes.new("ShaderNodeBump")
        _poner(bump, "Strength", 0.12)
        nt.links.new(ruido.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    _mats[clave] = m
    return m


def mat_vertices(nombre, rug=0.55, sss=0.0, transmision=0.0):
    """Material que toma el color del atributo de vértice 'col' (pasto, hojas): variación sin texturas."""
    clave = ("v", nombre)
    if clave in _mats:
        return _mats[clave]
    m, nt, bsdf, _ = _mat_base(nombre)
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "col"
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    _poner(bsdf, "Roughness", rug)
    if sss:
        _poner(bsdf, ["Subsurface Weight", "Subsurface"], sss)
    if transmision:
        _poner(bsdf, ["Transmission Weight", "Transmission"], transmision)
    _mats[clave] = m
    return m


def mat_poly(nombre, palabras, color, rug=0.7, tam_m=2.0, excluir=(), mojado=0.0, caja=True, requerir=(), acg_q=None):
    """Material PBR con una textura de Poly Haven buscada por palabras; si falla, color con ruido."""
    clave = ("p", nombre, mojado)
    if clave in _mats:
        return _mats[clave]
    mapas = None
    aid = None
    if acg_q:  # ambientCG primero: fotogrametría de suelos de muy buena calidad
        mapas = _try(f"ambientCG {acg_q}", acg.material, acg_q, "1K" if PREVIA else "2K", SEMILLA, excluir)
        if mapas:
            aid = mapas.pop("id") + " (ambientCG)"
    if not mapas:
        aid = _try(f"buscar textura {nombre}", ph.buscar, "textures", palabras, excluir, SEMILLA, 6, requerir or palabras[:1])
        if aid:
            mapas = _try(f"descargar textura {aid}", ph.textura, aid, _res())
            aid = f"{aid} (Poly Haven)"
    if not mapas or "Diffuse" not in mapas:
        m = mat_color(nombre, color, rug, variacion=0.25, escala=1.5 / tam_m)
        _mats[clave] = m
        return m
    aviso(f"textura '{nombre}': {aid}")
    m, nt, bsdf, _ = _mat_base(nombre)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapeo = nt.nodes.new("ShaderNodeMapping")
    mapeo.inputs["Scale"].default_value = (1 / tam_m, 1 / tam_m, 1 / tam_m)
    nt.links.new(coord.outputs["Object"], mapeo.inputs["Vector"])

    def imagen(ruta, color_datos):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(ruta, check_existing=True)
        if not color_datos:
            try:
                tex.image.colorspace_settings.name = "Non-Color"
            except Exception:  # noqa: BLE001
                pass
        if caja:
            tex.projection = "BOX"
            tex.projection_blend = 0.25
        nt.links.new(mapeo.outputs["Vector"], tex.inputs["Vector"])
        return tex

    dif = imagen(mapas["Diffuse"], True)
    color_sal = dif.outputs["Color"]
    if not caja:
        # anti-repetición: segunda muestra a 2.3x de escala y girada 37°, mezclada por un ruido grande,
        # más una variación lenta de tono (así no se ven baldosas repetidas desde lejos)
        mapeo2 = nt.nodes.new("ShaderNodeMapping")
        mapeo2.inputs["Scale"].default_value = (1 / (tam_m * 2.3),) * 3
        mapeo2.inputs["Rotation"].default_value[2] = 0.65
        nt.links.new(coord.outputs["Object"], mapeo2.inputs["Vector"])
        dif2 = nt.nodes.new("ShaderNodeTexImage")
        dif2.image = dif.image
        nt.links.new(mapeo2.outputs["Vector"], dif2.inputs["Vector"])
        mascara = nt.nodes.new("ShaderNodeTexNoise")
        _poner(mascara, "Scale", 0.08)
        _poner(mascara, "Detail", 2.0)
        nt.links.new(coord.outputs["Object"], mascara.inputs["Vector"])
        mm = nt.nodes.new("ShaderNodeMapRange")
        _poner(mm, "From Min", 0.4)
        _poner(mm, "From Max", 0.6)
        nt.links.new(mascara.outputs["Fac"], mm.inputs["Value"])
        mz = nt.nodes.new("ShaderNodeMix")
        mz.data_type = "RGBA"
        nt.links.new(mm.outputs["Result"], mz.inputs[0])
        nt.links.new(color_sal, mz.inputs[6])
        nt.links.new(dif2.outputs["Color"], mz.inputs[7])
        tono = nt.nodes.new("ShaderNodeTexNoise")
        _poner(tono, "Scale", 0.03)
        nt.links.new(coord.outputs["Object"], tono.inputs["Vector"])
        tr = nt.nodes.new("ShaderNodeMapRange")
        _poner(tr, "To Min", 0.8)
        _poner(tr, "To Max", 1.15)
        nt.links.new(tono.outputs["Fac"], tr.inputs["Value"])
        mul = nt.nodes.new("ShaderNodeMix")
        mul.data_type = "RGBA"
        mul.blend_type = "MULTIPLY"
        mul.inputs[0].default_value = 1.0
        nt.links.new(mz.outputs[2], mul.inputs[6])
        nt.links.new(tr.outputs["Result"], mul.inputs[7])
        color_sal = mul.outputs[2]
    rug_sal = None
    if "Rough" in mapas:
        rug_sal = imagen(mapas["Rough"], False).outputs["Color"]
    if mojado > 0:
        # Charcos y suelo mojado: más oscuro y brillante donde un ruido grande lo indica
        ruido = nt.nodes.new("ShaderNodeTexNoise")
        _poner(ruido, "Scale", 0.35)
        _poner(ruido, "Detail", 3.0)
        nt.links.new(coord.outputs["Object"], ruido.inputs["Vector"])
        rampa = nt.nodes.new("ShaderNodeMapRange")
        _poner(rampa, "From Min", 0.52)
        _poner(rampa, "From Max", 0.6)
        nt.links.new(ruido.outputs["Fac"], rampa.inputs["Value"])
        oscuro = nt.nodes.new("ShaderNodeMix")
        oscuro.data_type = "RGBA"
        oscuro.blend_type = "MULTIPLY"
        oscuro.inputs[0].default_value = 0.45 * mojado
        nt.links.new(color_sal, oscuro.inputs[6])
        oscuro.inputs[7].default_value = (0.35, 0.35, 0.35, 1)
        color_sal = oscuro.outputs[2]
        base_r = nt.nodes.new("ShaderNodeMath")
        base_r.operation = "MULTIPLY"
        base_r.inputs[1].default_value = 1 - 0.5 * mojado
        if rug_sal is not None:
            nt.links.new(rug_sal, base_r.inputs[0])
        else:
            base_r.inputs[0].default_value = rug
        charco = nt.nodes.new("ShaderNodeMix")
        charco.data_type = "FLOAT"
        nt.links.new(rampa.outputs["Result"], charco.inputs[0])
        nt.links.new(base_r.outputs[0], charco.inputs[2])
        charco.inputs[3].default_value = 0.03
        rug_sal = charco.outputs[0]
    nt.links.new(color_sal, bsdf.inputs["Base Color"])
    if rug_sal is not None:
        nt.links.new(rug_sal, bsdf.inputs["Roughness"])
    else:
        _poner(bsdf, "Roughness", rug)
    if "nor_gl" in mapas:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(imagen(mapas["nor_gl"], False).outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    elif "Displacement" in mapas:
        bump = nt.nodes.new("ShaderNodeBump")
        _poner(bump, "Strength", 0.4)
        nt.links.new(imagen(mapas["Displacement"], False).outputs["Color"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    _mats[clave] = m
    return m


def asignar(obj, m):
    if obj is not None and obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(m)


def malla(nombre, verts, caras, colores=None, coleccion=None):
    me = bpy.data.meshes.new(nombre)
    me.from_pydata(np.asarray(verts, float).tolist(), [], [list(map(int, c)) for c in caras])
    me.update()
    if colores is not None:
        try:
            a = me.color_attributes.new("col", "FLOAT_COLOR", "POINT")
            c = np.ones((len(verts), 4), np.float32)
            c[:, :3] = colores
            a.data.foreach_set("color", c.ravel())
        except Exception as e:  # noqa: BLE001
            aviso(f"aviso: colores de vértice ({e})")
    o = bpy.data.objects.new(nombre, me)
    (coleccion or bpy.context.scene.collection).objects.link(o)
    return o


def suavizar(o):
    try:
        for p in o.data.polygons:
            p.use_smooth = True
    except Exception:  # noqa: BLE001
        pass


def curvas_de(idb):
    """F-curves de un objeto o árbol de nodos (Blender 4 y Blender 5 con acciones por capas)."""
    ad = getattr(idb, "animation_data", None)
    acc = ad.action if ad else None
    if acc is None:
        return []
    lista = list(getattr(acc, "fcurves", None) or [])
    if not lista:
        for capa in getattr(acc, "layers", []):
            for tira in capa.strips:
                for bolsa in getattr(tira, "channelbags", []):
                    lista += list(bolsa.fcurves)
    return lista


# ------------------------------------------------------------------ terreno
class Terreno:
    """Altura del suelo: plano donde actúan los personajes (z=0) y relieve suave lejos."""

    def __init__(self, centro, radio_plano, relieve, semilla):
        self.c = np.asarray(centro, float)
        self.r = radio_plano
        self.rel = relieve
        rg = np.random.default_rng(semilla)
        self.k = rg.uniform(0.03, 0.12, (6, 2)) * rg.choice([-1, 1], (6, 2))
        self.p = rg.uniform(0, 2 * np.pi, 6)
        self.a = rg.uniform(0.4, 1.0, 6)

    def z(self, x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        n = sum(self.a[i] * np.sin(self.k[i, 0] * x + self.k[i, 1] * y + self.p[i]) for i in range(6)) / 3
        d = np.hypot(x - self.c[0], y - self.c[1])
        t = np.clip((d - self.r) / 10.0, 0, 1)
        return self.rel * 1.5 * n * t * t * (3 - 2 * t)


def suelo(terreno, mat, tam=90.0, paso=None):
    paso = paso or (0.5 if PREVIA else 0.3)
    n = int(tam / paso)
    xs = np.linspace(-tam / 2, tam / 2, n + 1) + terreno.c[0]
    ys = np.linspace(-tam / 2, tam / 2, n + 1) + terreno.c[1]
    X, Y = np.meshgrid(xs, ys)
    Z = terreno.z(X, Y)
    verts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
    i = np.arange(n)
    a = (i[:, None] * (n + 1) + i[None, :]).ravel()
    caras = np.stack([a, a + 1, a + n + 2, a + n + 1], 1)
    o = malla("Suelo", verts, caras)
    suavizar(o)
    asignar(o, mat)
    # horizonte: plano enorme un poco más bajo, mismo material
    bpy.ops.mesh.primitive_plane_add(size=1500, location=(terreno.c[0], terreno.c[1], -0.25))
    h = bpy.context.object
    h.name = "Horizonte"
    asignar(h, mat)
    return o


# ------------------------------------------------------------------ dispersión (pasto, hojas)
def _puntos(rg, n, centro, radio, evitar=()):
    ang = rg.uniform(0, 2 * np.pi, n * 2)
    r = radio * np.sqrt(rg.uniform(0, 1, n * 2)) ** 1.3  # más denso cerca del centro
    P = np.stack([centro[0] + r * np.cos(ang), centro[1] + r * np.sin(ang)], 1)
    for (ex, ey, er) in evitar:
        P = P[np.hypot(P[:, 0] - ex, P[:, 1] - ey) > er]
    return P[:n]


def pasto(terreno, centro, radio, densidad, otono=True, evitar=()):
    rg = np.random.default_rng(SEMILLA + 11)
    n = int(densidad * (0.35 if PREVIA else 1.0))
    P = _puntos(rg, n, centro, radio, evitar)
    n = len(P)
    hojas = 5
    verts, caras, cols = [], [], []
    alto = rg.uniform(0.035, 0.09, (n, hojas)) * (1 + 0.6 * (rg.random((n, 1)) > 0.9))
    ang = rg.uniform(0, 2 * np.pi, (n, hojas))
    incl = rg.uniform(0.1, 0.6, (n, hojas))
    base_col = np.array([0.09, 0.16, 0.035]) if not otono else np.array([0.12, 0.15, 0.04])
    seco = np.array([0.32, 0.26, 0.1])
    z0 = terreno.z(P[:, 0], P[:, 1])
    k = 0
    for j in range(hojas):
        dx, dy = np.cos(ang[:, j]), np.sin(ang[:, j])
        px, py = -dy, dx
        ox = P[:, 0] + rg.uniform(-0.03, 0.03, n)
        oy = P[:, 1] + rg.uniform(-0.03, 0.03, n)
        w = 0.0025 + 0.002 * rg.random(n)
        h = alto[:, j]
        b = incl[:, j]
        v = np.zeros((n, 5, 3))
        v[:, 0] = np.stack([ox - px * w, oy - py * w, z0], 1)
        v[:, 1] = np.stack([ox + px * w, oy + py * w, z0], 1)
        v[:, 2] = np.stack([ox + dx * h * b * 0.4 - px * w * 0.7, oy + dy * h * b * 0.4 - py * w * 0.7, z0 + h * 0.55], 1)
        v[:, 3] = np.stack([ox + dx * h * b * 0.4 + px * w * 0.7, oy + dy * h * b * 0.4 + py * w * 0.7, z0 + h * 0.55], 1)
        v[:, 4] = np.stack([ox + dx * h * b, oy + dy * h * b, z0 + h * (1 - 0.3 * b)], 1)
        verts.append(v.reshape(-1, 3))
        base = (k + np.arange(n) * 5)[:, None]
        caras += (base + np.array([0, 1, 3, 2])).tolist() + (base + np.array([2, 3, 4, 4])).tolist()
        t = rg.random((n, 1)) ** 2
        c = base_col * (1 - t) + seco * t
        c = c * rg.uniform(0.7, 1.25, (n, 1))
        cols.append(np.repeat(c, 5, 0) * np.tile(np.array([0.55, 0.55, 0.85, 0.85, 1.15])[:, None], (n, 1)))
        k += n * 5
    caras = [c if c[2] != c[3] else c[:3] for c in caras]
    o = malla("Pasto", np.concatenate(verts), caras, np.concatenate(cols))
    asignar(o, mat_vertices("Pasto", rug=0.5))
    aviso(f"pasto: {n} matas")
    return o


def hojas_suelo(terreno, centro, radio, n, evitar=()):
    rg = np.random.default_rng(SEMILLA + 23)
    n = int(n * (0.4 if PREVIA else 1.0))
    P = _puntos(rg, n, centro, radio, evitar)
    n = len(P)
    paleta = np.array([[0.45, 0.12, 0.02], [0.6, 0.28, 0.03], [0.35, 0.2, 0.06], [0.55, 0.42, 0.08], [0.2, 0.1, 0.04]])
    tam = rg.uniform(0.03, 0.07, n)
    a = rg.uniform(0, 2 * np.pi, n)
    z = terreno.z(P[:, 0], P[:, 1]) + 0.004 + rg.uniform(0, 0.01, n)
    forma = np.array([[0, 1.0], [0.45, 0.2], [0, -0.7], [-0.45, 0.2]])
    v = np.zeros((n, 4, 3))
    for i in range(4):
        fx, fy = forma[i]
        v[:, i, 0] = P[:, 0] + tam * (fx * np.cos(a) - fy * np.sin(a))
        v[:, i, 1] = P[:, 1] + tam * (fx * np.sin(a) + fy * np.cos(a))
        v[:, i, 2] = z + (0.006 if i == 1 else 0)
    caras = (np.arange(n)[:, None] * 4 + np.array([0, 1, 2, 3])).tolist()
    c = paleta[rg.integers(0, len(paleta), n)] * rg.uniform(0.7, 1.2, (n, 1))
    o = malla("Hojarasca", v.reshape(-1, 3), caras, np.repeat(c, 4, 0))
    asignar(o, mat_vertices("Hojas", rug=0.6))
    return o


# ------------------------------------------------------------------ modelos de Poly Haven
def _importar_gltf(ruta, nombre):
    antes = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=ruta)
    nuevos = [o for o in bpy.data.objects if o not in antes]
    col = bpy.data.collections.new(f"PH_{nombre}")
    for o in nuevos:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        col.objects.link(o)
    return col, nuevos


def modelo_ph(clave, palabras, excluir=(), requerir=(), alto_min=0.05):
    """Importa (una vez) un modelo de Poly Haven y lo deja como colección para instanciar."""
    if clave in _modelos:
        return _modelos[clave]
    _modelos[clave] = None
    aid = _try(f"buscar modelo {clave}", ph.buscar, "models", palabras, excluir, SEMILLA, 6, requerir)
    if not aid:
        return None
    ruta = _try(f"descargar modelo {aid}", ph.modelo, aid, _res())
    if not ruta:
        return None
    r = _try(f"importar {aid}", _importar_gltf, ruta, clave)
    if not r:
        return None
    col, nuevos = r
    zs, xs, ys = [], [], []
    for o in nuevos:
        if o.type == "MESH":
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                xs.append(w.x)
                ys.append(w.y)
                zs.append(w.z)
    if not zs:
        return None
    if max(zs) - min(zs) < alto_min:
        aviso(f"modelo '{clave}': {aid} es demasiado plano; uso un sustituto")
        return None
    info = {"col": col, "alto": max(zs) - min(zs), "ancho": max(max(xs) - min(xs), max(ys) - min(ys)), "z0": min(zs), "id": aid}
    _modelos[clave] = info
    aviso(f"modelo '{clave}': {aid} (Poly Haven, {info['alto']:.1f} m)")
    return info


def instancia(info, pos, rot_z=0.0, escala=1.0, nombre="Inst"):
    e = bpy.data.objects.new(nombre, None)
    e.instance_type = "COLLECTION"
    e.instance_collection = info["col"]
    bpy.context.scene.collection.objects.link(e)
    e.location = (pos[0], pos[1], pos[2] - info["z0"] * escala)
    e.rotation_euler.z = rot_z
    e.scale = (escala, escala, escala)
    return e


# ------------------------------------------------------------------ sustitutos procedurales
def arbol_procedural(pos, alto, rg, otono=True, nombre="Arbol"):
    raiz = bpy.data.objects.new(nombre, None)
    bpy.context.scene.collection.objects.link(raiz)
    raiz.location = pos
    corteza = mat_poly("Corteza", ["bark"], (0.16, 0.11, 0.07), 0.85, tam_m=1.5)
    r0 = alto * rg.uniform(0.025, 0.04)
    bpy.ops.mesh.primitive_cone_add(vertices=12, radius1=r0, radius2=r0 * 0.35, depth=alto * 0.75, location=(0, 0, alto * 0.37))
    tronco = bpy.context.object
    tronco.parent = raiz
    asignar(tronco, corteza)
    suavizar(tronco)
    paleta = [(0.55, 0.16, 0.02), (0.72, 0.36, 0.03), (0.42, 0.3, 0.05), (0.6, 0.48, 0.08), (0.2, 0.28, 0.05)] if otono else [(0.1, 0.2, 0.04)]
    for i in range(rg.integers(3, 6)):
        a = rg.uniform(0, 2 * np.pi)
        h = alto * rg.uniform(0.45, 0.7)
        lr = alto * rg.uniform(0.18, 0.3)
        bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=r0 * 0.35, radius2=r0 * 0.1, depth=lr,
                                        location=(math.cos(a) * lr * 0.4, math.sin(a) * lr * 0.4, h + lr * 0.3),
                                        rotation=(0, math.radians(55), a))
        rama = bpy.context.object
        rama.parent = raiz
        asignar(rama, corteza)
    for i in range(rg.integers(6, 11)):
        a = rg.uniform(0, 2 * np.pi)
        d = alto * rg.uniform(0.0, 0.22)
        r = alto * rg.uniform(0.14, 0.24)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=r, location=(math.cos(a) * d, math.sin(a) * d, alto * rg.uniform(0.62, 0.95)))
        copa = bpy.context.object
        copa.parent = raiz
        copa.scale = (1, 1, rg.uniform(0.7, 0.95))
        dm = copa.modifiers.new("Hojas", "DISPLACE")
        t = bpy.data.textures.get("CopaTex") or bpy.data.textures.new("CopaTex", "CLOUDS")
        t.noise_scale = 0.35
        dm.texture = t
        dm.strength = r * 0.45
        c = paleta[rg.integers(0, len(paleta))]
        asignar(copa, mat_color(f"Copa_{c}", c, 0.7, variacion=0.35, escala=8))
        suavizar(copa)
    return raiz


def roca_procedural(pos, tam, rg):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=tam, location=(pos[0], pos[1], pos[2] + tam * 0.3))
    o = bpy.context.object
    o.name = "Roca"
    o.scale = (1, rg.uniform(0.7, 1.1), rg.uniform(0.45, 0.75))
    o.rotation_euler.z = rg.uniform(0, 6.3)
    d = o.modifiers.new("Forma", "DISPLACE")
    t = bpy.data.textures.get("RocaTex") or bpy.data.textures.new("RocaTex", "VORONOI")
    d.texture = t
    d.strength = tam * 0.35
    asignar(o, mat_poly("Roca", ["rock", "stone"], (0.33, 0.32, 0.3), 0.85, tam_m=1.0, excluir=("floor", "wall", "tiles", "brick", "path", "road")))
    suavizar(o)
    return o


def barril(pos, rot=0.0):
    madera = mat_poly("MaderaBarril", ["wood", "planks"], (0.36, 0.22, 0.11), 0.6, tam_m=0.8, excluir=("floor",))
    hierro = mat_color("Hierro", (0.07, 0.07, 0.07), 0.45, variacion=0.2, metal=0.9)
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.3, depth=0.9, location=(pos[0], pos[1], pos[2] + 0.45))
    b = bpy.context.object
    b.name = "Barril"
    for v in b.data.vertices:  # panza del barril
        v.co.x *= 1.0
    sub = b.modifiers.new("Liso", "SUBSURF")
    sub.levels = sub.render_levels = 1
    asignar(b, madera)
    suavizar(b)
    for z in (0.12, 0.78):
        bpy.ops.mesh.primitive_torus_add(major_radius=0.305, minor_radius=0.012, location=(pos[0], pos[1], pos[2] + z))
        aro = bpy.context.object
        asignar(aro, hierro)
        aro.parent = b
        aro.matrix_parent_inverse = b.matrix_world.inverted()
    b.rotation_euler.z = rot
    return b


def caja(pos, tam=0.6, rot=0.0):
    bpy.ops.mesh.primitive_cube_add(size=tam, location=(pos[0], pos[1], pos[2] + tam / 2))
    c = bpy.context.object
    c.name = "Caja"
    c.rotation_euler.z = rot
    bv = c.modifiers.new("Bisel", "BEVEL")
    bv.width = 0.01
    asignar(c, mat_poly("MaderaCaja", ["wood", "planks", "crate"], (0.42, 0.29, 0.16), 0.7, tam_m=0.6, excluir=("floor",)))
    return c


def farol(pos, noche=True, alto=3.2):
    hierro = mat_color("HierroFarol", (0.03, 0.03, 0.03), 0.4, variacion=0.1, metal=0.8)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.05, depth=alto, location=(pos[0], pos[1], pos[2] + alto / 2))
    poste = bpy.context.object
    poste.name = "Farol"
    asignar(poste, hierro)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.11, depth=0.12, location=(pos[0], pos[1], pos[2] + 0.06))
    asignar(bpy.context.object, hierro)
    bpy.ops.mesh.primitive_cylinder_add(vertices=4, radius=0.2, depth=0.38, location=(pos[0], pos[1], pos[2] + alto + 0.2))
    vidrio = bpy.context.object
    asignar(vidrio, mat_color("VidrioFarol", (1.0, 0.75, 0.4), 0.2, variacion=0, emision=(1.0, 0.62, 0.28), fuerza=12.0 if noche else 0.6))
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.27, radius2=0.02, depth=0.2, location=(pos[0], pos[1], pos[2] + alto + 0.48))
    asignar(bpy.context.object, hierro)
    luz = bpy.data.lights.new("LuzFarol", "POINT")
    luz.energy = 260 if noche else 15
    luz.shadow_soft_size = 0.12
    luz.color = (1.0, 0.7, 0.42)
    lo = bpy.data.objects.new("LuzFarol", luz)
    bpy.context.scene.collection.objects.link(lo)
    lo.location = (pos[0], pos[1], pos[2] + alto + 0.2)
    return poste


def valla(x0, x1, y, alto=1.0, hueco=None, abierta_deg=35):
    """Valla de tablas blancas de x0 a x1 en y; 'hueco' = (xa, xb) para la reja (puerta) abierta."""
    pintura = mat_color("ValllaPintada", (0.78, 0.76, 0.7), 0.6, variacion=0.12, escala=6)
    objs = []
    x = x0
    while x <= x1:
        if not (hueco and hueco[0] - 0.05 < x < hueco[1] + 0.05):
            bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, alto * 0.48))
            t = bpy.context.object
            t.scale = (0.08, 0.025, alto * 0.96)
            asignar(t, pintura)
            objs.append(t)
        x += 0.13
    for z in (alto * 0.25, alto * 0.75):
        tramos = [(x0, hueco[0]), (hueco[1], x1)] if hueco else [(x0, x1)]
        for a, b in tramos:
            bpy.ops.mesh.primitive_cube_add(size=1, location=((a + b) / 2, y - 0.03, z))
            r = bpy.context.object
            r.scale = (b - a, 0.03, 0.07)
            asignar(r, pintura)
    if hueco:
        ancho = hueco[1] - hueco[0]
        bisagra = bpy.data.objects.new("Reja", None)
        bpy.context.scene.collection.objects.link(bisagra)
        bisagra.location = (hueco[0], y, 0)
        n = int(ancho / 0.13)
        for i in range(n):
            bpy.ops.mesh.primitive_cube_add(size=1, location=(hueco[0] + 0.07 + i * 0.13, y, alto * 0.46))
            t = bpy.context.object
            t.scale = (0.08, 0.025, alto * 0.9)
            asignar(t, pintura)
            t.parent = bisagra
            t.matrix_parent_inverse = bisagra.matrix_world.inverted()
        bisagra.rotation_euler.z = math.radians(abierta_deg)
        for xp in hueco:
            bpy.ops.mesh.primitive_cube_add(size=1, location=(xp, y, alto * 0.6))
            p = bpy.context.object
            p.scale = (0.1, 0.1, alto * 1.2)
            asignar(p, pintura)
    return objs


def _caja(nombre, centro, tam, mat, padre, bisel=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=centro)
    o = bpy.context.object
    o.name = nombre
    o.scale = tam
    if bisel:
        bv = o.modifiers.new("Bisel", "BEVEL")
        bv.width = bisel
        bv.segments = 2
    asignar(o, mat)
    o.parent = padre
    return o


def mat_mugre(base_m, nombre):
    """Variante del material de pared con suciedad que sube desde el suelo (humedad y salpicaduras)."""
    clave = ("mugre", nombre)
    if clave in _mats:
        return _mats[clave]
    m = base_m.copy()
    m.name = nombre
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf and bsdf.inputs["Base Color"].is_linked:
        src = bsdf.inputs["Base Color"].links[0].from_socket
        coord = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        nt.links.new(coord.outputs["Object"], sep.inputs[0])
        ruido = nt.nodes.new("ShaderNodeTexNoise")
        _poner(ruido, "Scale", 3.0)
        nt.links.new(coord.outputs["Object"], ruido.inputs["Vector"])
        sube = nt.nodes.new("ShaderNodeMapRange")
        _poner(sube, "From Min", -0.5)
        _poner(sube, "From Max", -0.2)
        _poner(sube, "To Min", 0.55)
        _poner(sube, "To Max", 0.0)
        nt.links.new(sep.outputs[2], sube.inputs["Value"])
        suma = nt.nodes.new("ShaderNodeMath")
        suma.operation = "MULTIPLY_ADD"
        nt.links.new(ruido.outputs["Fac"], suma.inputs[0])
        suma.inputs[1].default_value = 0.35
        nt.links.new(sube.outputs["Result"], suma.inputs[2])
        mez = nt.nodes.new("ShaderNodeMix")
        mez.data_type = "RGBA"
        mez.blend_type = "MULTIPLY"
        nt.links.new(suma.outputs[0], mez.inputs[0])
        nt.links.new(src, mez.inputs[6])
        mez.inputs[7].default_value = (0.32, 0.29, 0.25, 1)
        nt.links.new(mez.outputs[2], bsdf.inputs["Base Color"])
    _mats[clave] = m
    return m


def casa(x, y, ancho, fondo, alto, rot, rg, noche=False, frente_y=-1, puerta_abierta=False):
    """Casa con fachada detallada: zócalo, cornisas, ventanas hundidas con marco, alféizar y contraventanas,
    puerta con escalón y dintel, canalón, chimenea y suciedad que sube del suelo. La fachada mira a 'frente_y'
    en ejes locales (el giro 'rot' la orienta hacia la calle).
    puerta_abierta=True: casa hueca (muros con grosor), puerta en el centro con bisagra que se puede animar
    (raiz["pivote_puerta"]) y luz cálida dentro: los personajes pueden entrar."""
    pared = rg.choice(["plaster", "brick", "stone wall"])
    colores = {"plaster": (0.72, 0.62, 0.5), "brick": (0.45, 0.22, 0.14), "stone wall": (0.45, 0.43, 0.4)}
    m_pared = mat_poly(f"Pared_{pared}", [pared, "wall"] if pared != "stone wall" else ["stone", "wall"], colores[pared], 0.85, tam_m=2.0,
                       excluir=("floor", "ground", "road", "rock"), acg_q={"plaster": "plaster", "brick": "bricks", "stone wall": "stone wall"}[pared])
    m_pared = mat_mugre(m_pared, f"Pared_{pared}_mugre") or m_pared
    m_piedra = mat_poly("Zocalo", ["stone", "wall"], (0.35, 0.34, 0.32), 0.85, tam_m=1.2, excluir=("floor", "ground"), acg_q="stone wall")
    m_techo = mat_poly("Tejado", ["roof", "tiles"], (0.32, 0.12, 0.07), 0.7, tam_m=1.5, acg_q="roofing tiles")
    tono = [(0.2, 0.28, 0.22), (0.35, 0.18, 0.12), (0.18, 0.22, 0.32), (0.32, 0.3, 0.26)][int(rg.integers(0, 4))]
    m_madera = mat_color(f"Madera_{tono}", tono, 0.55, variacion=0.2, escala=8)
    m_moldura = mat_color("Moldura", (0.78, 0.75, 0.68), 0.7, variacion=0.1, escala=4)
    m_vidrio_luz = mat_color("VentanaLuz", (0.05, 0.06, 0.07), 0.05, variacion=0, emision=(1.0, 0.6, 0.3), fuerza=1.4)  # cálida sin quemarse
    m_vidrio = mat_color("Vidrio", (0.02, 0.025, 0.03), 0.04, variacion=0, metal=0.0)
    m_metal = mat_color("Canalon", (0.25, 0.26, 0.27), 0.35, variacion=0.15, metal=0.85)
    raiz = bpy.data.objects.new("Casa", None)
    bpy.context.scene.collection.objects.link(raiz)
    raiz.location = (x, y, 0)
    raiz.rotation_euler.z = rot
    f = frente_y
    fy = f * fondo / 2
    if puerta_abierta:
        g = 0.25  # grosor de los muros
        xa, xb = -0.53, 0.53  # vano de la puerta (centrada)
        for nombre, cx_, ancho_ in (("MuroIzq", (-ancho / 2 + xa) / 2, xa + ancho / 2), ("MuroDer", (ancho / 2 + xb) / 2, ancho / 2 - xb)):
            _caja(nombre, (cx_, fy - f * g / 2, alto / 2), (ancho_, g, alto), m_pared, raiz)
            _caja("Zocalo", (cx_, fy - f * (g / 2 - 0.04), 0.35), (ancho_, g + 0.08, 0.7), m_piedra, raiz, 0.02)
        _caja("MuroSobrePuerta", (0, fy - f * g / 2, (2.25 + alto) / 2), (xb - xa, g, alto - 2.25), m_pared, raiz)
        _caja("MuroFondo", (0, -fy + f * g / 2, alto / 2), (ancho, g, alto), m_pared, raiz)
        for lx in (-1, 1):
            _caja("MuroLado", (lx * (ancho / 2 - g / 2), 0, alto / 2), (g, fondo, alto), m_pared, raiz)
        _caja("Techo", (0, 0, alto - 0.1), (ancho, fondo, 0.2), m_pared, raiz)
        # interior distinto de la fachada: suelo de madera, paredes pintadas de color cálido con zócalo de
        # madera, una alfombra, un cuadro y un perchero junto a la puerta (al abrirse se lee "entran a casa")
        m_dentro = mat_poly("SueloMadera", ["wood", "floor"], (0.32, 0.2, 0.11), 0.5, tam_m=1.5, excluir=("wall",), acg_q="wood floor")
        _caja("SueloCasa", (0, 0, 0.012), (ancho - 2 * g, fondo - 2 * g, 0.024), m_dentro, raiz)
        m_pintura = mat_color("ParedInterior", (0.62, 0.42, 0.26), 0.85, variacion=0.08, escala=3)
        m_friso = mat_color("FrisoMadera", (0.22, 0.12, 0.06), 0.45, variacion=0.15, escala=10)
        ai, fi = ancho - 2 * g - 0.02, fondo - 2 * g - 0.02
        yb = -fy + f * (g + 0.01)  # cara interior del muro del fondo
        _caja("InteriorFondo", (0, yb, alto / 2), (ai, 0.02, alto - 0.2), m_pintura, raiz)
        _caja("FrisoFondo", (0, yb - f * 0.015, 0.45), (ai, 0.03, 0.9), m_friso, raiz)
        for lx in (-1, 1):
            xi = lx * (ancho / 2 - g - 0.01)
            _caja("InteriorLado", (xi, 0, alto / 2), (0.02, fi, alto - 0.2), m_pintura, raiz)
            _caja("FrisoLado", (xi - lx * 0.015, 0, 0.45), (0.03, fi, 0.9), m_friso, raiz)
        yf = fy - f * (g + 0.01)  # cara interior de la fachada
        for nombre_, cx_, an_ in (("InteriorFrenteIzq", (-ancho / 2 + g - 0.53) / 2, ancho / 2 - g - 0.53), ("InteriorFrenteDer", (ancho / 2 - g + 0.53) / 2, ancho / 2 - g - 0.53)):
            _caja(nombre_, (cx_, yf, alto / 2), (an_, 0.02, alto - 0.2), m_pintura, raiz)
        m_alfombra = mat_color("Alfombra", (0.45, 0.08, 0.06), 0.9, variacion=0.25, escala=14)
        _caja("Alfombra", (0, fy - f * 1.6, 0.03), (1.3, 1.9, 0.012), m_alfombra, raiz)
        m_marco = mat_color("MarcoCuadro", (0.55, 0.42, 0.18), 0.4, variacion=0.1, metal=0.6)
        _caja("Cuadro", (0.2, yb - f * 0.03, 1.55), (0.9, 0.04, 0.65), m_marco, raiz)
        _caja("Lienzo", (0.2, yb - f * 0.055, 1.55), (0.78, 0.01, 0.53), mat_color("Lienzo", (0.35, 0.45, 0.3), 0.8, variacion=0.4, escala=5), raiz)
        _caja("Perchero", (-ancho / 2 + g + 0.35, fy - f * 0.6, 0.9), (0.05, 0.05, 1.8), m_friso, raiz)
        _caja("Abrigo", (-ancho / 2 + g + 0.35, fy - f * 0.6, 1.35), (0.32, 0.2, 0.6), mat_color("Abrigo", (0.15, 0.2, 0.32), 0.9, variacion=0.15), raiz)
        luz = bpy.data.lights.new("LuzCasa", "POINT")
        luz.energy = 160
        luz.color = (1.0, 0.66, 0.36)
        luz.shadow_soft_size = 0.4
        lo = bpy.data.objects.new("LuzCasa", luz)
        bpy.context.scene.collection.objects.link(lo)
        lo.parent = raiz
        lo.location = (0.3, fy - f * 1.6, 2.2)
    else:
        _caja("Muro", (0, 0, alto / 2), (ancho, fondo, alto), m_pared, raiz)
        _caja("Zocalo", (0, 0, 0.35), (ancho + 0.08, fondo + 0.08, 0.7), m_piedra, raiz, 0.02)
    pisos = max(1, int(alto / 2.8))
    for p in range(1, pisos + 1):  # cornisas
        _caja("Cornisa", (0, fy + f * 0.06, min(alto - 0.1, p * 2.8)), (ancho + 0.12, 0.14, 0.14), m_moldura, raiz, 0.02)
    # tejado a dos aguas con alero
    h = ancho * 0.32
    verts = [(-ancho / 2 - 0.35, -fondo / 2 - 0.45, alto), (ancho / 2 + 0.35, -fondo / 2 - 0.45, alto), (0, -fondo / 2 - 0.45, alto + h),
             (-ancho / 2 - 0.35, fondo / 2 + 0.45, alto), (ancho / 2 + 0.35, fondo / 2 + 0.45, alto), (0, fondo / 2 + 0.45, alto + h)]
    techo = malla("Tejado", verts, [(0, 2, 5, 3), (1, 4, 5, 2), (0, 1, 2), (3, 5, 4)])
    sol = techo.modifiers.new("Grosor", "SOLIDIFY")
    sol.thickness = 0.12
    asignar(techo, m_techo)
    techo.parent = raiz
    _caja("Chimenea", (ancho * rg.uniform(-0.3, 0.3), fondo * 0.15, alto + h * 0.7), (0.55, 0.55, h * 0.9), m_piedra, raiz)
    _caja("Canalon", (0, fy + f * 0.42, alto - 0.05), (ancho + 0.7, 0.12, 0.1), m_metal, raiz)
    _caja("Bajante", (ancho / 2 - 0.1, fy + f * 0.1, alto / 2), (0.08, 0.08, alto), m_metal, raiz)
    cols = max(1, int(ancho / 1.7))
    puerta_c = int(rg.integers(0, cols))
    if puerta_abierta:  # la puerta va en el centro: columnas impares
        cols = cols if cols % 2 else cols + 1
        puerta_c = cols // 2
    for p in range(pisos):
        for c in range(cols):
            cx = -ancho / 2 + ancho * (c + 0.5) / cols
            if p == 0 and c == puerta_c and puerta_abierta:
                # puerta con bisagra a la izquierda: el pivote gira en Z y la hoja abre hacia dentro
                pivote = bpy.data.objects.new("BisagraPuerta", None)
                bpy.context.scene.collection.objects.link(pivote)
                pivote.parent = raiz
                pivote.location = (cx - 0.46, fy - f * 0.12, 0.02)
                hoja = _caja("Puerta", (0, 0, 0), (0.92, 0.06, 2.12), m_madera, None, 0.01)
                hoja.parent = pivote
                hoja.location = (0.46, 0, 1.06)
                pomo = _caja("Pomo", (0, 0, 0), (0.05, 0.06, 0.05), m_metal, None)
                pomo.parent = pivote
                pomo.location = (0.82, f * 0.05, 1.0)  # por fuera
                for dx in (-0.5, 0.5):
                    _caja("MarcoPuerta", (cx + dx, fy + f * 0.02, 1.12), (0.08, 0.1, 2.26), m_moldura, raiz)
                _caja("Dintel", (cx, fy + f * 0.04, 2.27), (1.3, 0.12, 0.16), m_moldura, raiz, 0.01)
                _caja("Escalon", (cx, fy + f * 0.25, 0.03), (1.4, 0.5, 0.06), m_piedra, raiz, 0.01)
                raiz["pivote_puerta"] = pivote.name
                raiz["frente"] = float(f)
                continue
            if p == 0 and c == puerta_c:
                # puerta hundida con marco, dintel y escalón
                _caja("HuecoPuerta", (cx, fy - f * 0.08, 1.1), (1.05, 0.2, 2.2), m_vidrio, raiz)
                _caja("Puerta", (cx, fy - f * 0.04, 1.08), (0.92, 0.06, 2.12), m_madera, raiz, 0.01)
                _caja("Dintel", (cx, fy + f * 0.04, 2.27), (1.3, 0.12, 0.16), m_moldura, raiz, 0.01)
                _caja("Escalon", (cx, fy + f * 0.25, 0.08), (1.4, 0.5, 0.16), m_piedra, raiz, 0.02)
                continue
            cz = 1.55 + p * 2.8
            # ventana hundida: el vidrio queda 12 cm dentro del muro (sombra real en el vano)
            _caja("Vano", (cx, fy - f * 0.06, cz), (0.85, 0.14, 1.2), m_vidrio_luz if (noche and rg.random() < 0.55) else m_vidrio, raiz)
            for dx in (-0.47, 0.47):
                _caja("Marco", (cx + dx, fy + f * 0.01, cz), (0.08, 0.08, 1.3), m_moldura, raiz)
            _caja("MarcoSup", (cx, fy + f * 0.01, cz + 0.64), (1.02, 0.08, 0.08), m_moldura, raiz)
            _caja("Alfeizar", (cx, fy + f * 0.07, cz - 0.64), (1.1, 0.16, 0.06), m_moldura, raiz, 0.01)
            _caja("Parteluz", (cx, fy - f * 0.01, cz), (0.04, 0.03, 1.2), m_moldura, raiz)
            if rg.random() < 0.6:  # contraventanas abiertas
                for dx in (-0.75, 0.75):
                    _caja("Contraventana", (cx + dx, fy + f * 0.05, cz), (0.42, 0.04, 1.25), m_madera, raiz, 0.005)
            if p >= 1 and rg.random() < 0.3:  # jardinera con flores
                _caja("Jardinera", (cx, fy + f * 0.18, cz - 0.72), (0.9, 0.22, 0.18), m_madera, raiz)
                for k in range(5):
                    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.09, location=(cx - 0.36 + k * 0.18, fy + f * 0.18, cz - 0.55))
                    flor = bpy.context.object
                    flor.parent = raiz
                    asignar(flor, mat_color(f"Flor{k % 3}", [(0.7, 0.08, 0.1), (0.85, 0.6, 0.1), (0.6, 0.15, 0.45)][k % 3], 0.6, variacion=0.2))
    return raiz


def arbusto(pos, tam, rg, otono=True):
    info = modelo_ph("arbusto", ["shrub", "bush", "hedge", "fern"], ("dead", "potted", "succulent", "cactus", "aloe", "furniture"),
                     requerir=("shrub", "bush", "hedge", "fern", "plant"), alto_min=0.25)
    if info:
        return instancia(info, pos, rg.uniform(0, 6.3), min(1.4, tam / max(0.3, info["alto"])), "Arbusto")
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=tam / 2, location=(pos[0], pos[1], pos[2] + tam * 0.35))
    o = bpy.context.object
    o.name = "Arbusto"
    o.scale = (1, rg.uniform(0.8, 1.2), 0.75)
    d = o.modifiers.new("Hojas", "DISPLACE")
    t = bpy.data.textures.get("CopaTex") or bpy.data.textures.new("CopaTex", "CLOUDS")
    d.texture = t
    d.strength = tam * 0.3
    c = (0.16, 0.22, 0.05) if rg.random() < 0.6 or not otono else (0.5, 0.2, 0.03)
    asignar(o, mat_color(f"Arbusto_{c}", c, 0.7, variacion=0.35, escala=10))
    suavizar(o)
    return o


def _tubo(puntos, radios, lados=8):
    """Malla de un tubo que sigue 'puntos' (N,3) con radio variable: troncos y ramas."""
    P = np.asarray(puntos, float)
    n = len(P)
    t = np.gradient(P, axis=0)
    t /= np.maximum(1e-9, np.linalg.norm(t, axis=1, keepdims=True))
    a = np.where(np.abs(t[:, 2:3]) < 0.9, np.array([[0, 0, 1.0]]), np.array([[1.0, 0, 0]]))
    u = np.cross(t, a)
    u /= np.maximum(1e-9, np.linalg.norm(u, axis=1, keepdims=True))
    w = np.cross(t, u)
    ang = np.linspace(0, 2 * np.pi, lados, endpoint=False)
    V = (P[:, None, :] + np.asarray(radios)[:, None, None] * (np.cos(ang)[None, :, None] * u[:, None, :] + np.sin(ang)[None, :, None] * w[:, None, :]))
    caras = []
    for i in range(n - 1):
        for k in range(lados):
            caras.append((i * lados + k, i * lados + (k + 1) % lados, (i + 1) * lados + (k + 1) % lados, (i + 1) * lados + k))
    return V.reshape(-1, 3), caras


def _rama(rg, base, direccion, largo, r0, segs=6, gravedad=0.0, torsion=0.25):
    d = np.asarray(direccion, float)
    d /= np.linalg.norm(d)
    P = [np.asarray(base, float)]
    for i in range(segs):
        d = d + rg.normal(0, torsion, 3) * 0.3 + np.array([0, 0, -gravedad * (i / segs)])
        d /= np.linalg.norm(d)
        P.append(P[-1] + d * largo / segs)
    radios = np.linspace(r0, r0 * 0.25, len(P))
    return np.array(P), radios


def _mat_hojas():
    clave = ("hojas_tarjeta",)
    if clave in _mats:
        return _mats[clave]
    m, nt, bsdf, out = _mat_base("HojasArbol")
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "col"
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    _poner(bsdf, "Roughness", 0.55)
    # luz que atraviesa la hoja a contraluz: BSDF translúcido (barato) en vez de dispersión subsuperficial
    trans = nt.nodes.new("ShaderNodeBsdfTranslucent")
    nt.links.new(attr.outputs["Color"], trans.inputs["Color"])
    mez = nt.nodes.new("ShaderNodeMixShader")
    mez.inputs[0].default_value = 0.3
    nt.links.new(bsdf.outputs[0], mez.inputs[1])
    nt.links.new(trans.outputs[0], mez.inputs[2])
    nt.links.new(mez.outputs[0], out.inputs["Surface"])
    _mats[clave] = m
    return m


def arbol_hojas(alto, rg, otono=True, nombre="ArbolVar"):
    """Árbol con tronco y ramas reales y miles de hojas individuales (tarjetas con silueta de hoja).
    Se construye en una colección aparte y se instancia: muchos árboles cuestan casi como uno."""
    col = bpy.data.collections.new(nombre)
    corteza = mat_poly("Corteza", ["bark"], (0.16, 0.11, 0.07), 0.85, tam_m=1.2)
    r0 = alto * rg.uniform(0.022, 0.032)
    tronco, rt = _rama(rg, (0, 0, 0), (rg.normal(0, 0.05), rg.normal(0, 0.05), 1), alto * 0.62, r0, 8, 0.0, 0.12)
    tubos = [(tronco, rt)]
    puntas = []
    n_ramas = int(rg.integers(7, 12))
    for i in range(n_ramas):
        k = int(rg.integers(3, len(tronco) - 1))
        a = rg.uniform(0, 2 * np.pi)
        dir_ = (np.cos(a), np.sin(a), rg.uniform(0.3, 1.0))
        largo = alto * rg.uniform(0.22, 0.38) * (1.15 - k / len(tronco) * 0.5)
        P, r = _rama(rg, tronco[k], dir_, largo, rt[k] * 0.55, 6, rg.uniform(0.0, 0.35))
        tubos.append((P, r))
        for j in range(int(rg.integers(2, 4))):  # ramitas
            kk = int(rg.integers(2, len(P)))
            a2 = a + rg.uniform(-1.2, 1.2)
            P2, r2 = _rama(rg, P[kk - 1], (np.cos(a2), np.sin(a2), rg.uniform(0.2, 0.9)), largo * rg.uniform(0.35, 0.6), r[kk - 1] * 0.5, 4, 0.2)
            tubos.append((P2, r2))
            puntas.append(P2)
        puntas.append(P)
    V, C, off = [], [], 0
    for P, r in tubos:
        v, c = _tubo(P, r, 8)
        V.append(v)
        C += [tuple(x + off for x in f) for f in c]
        off += len(v)
    t = malla(f"{nombre}_madera", np.concatenate(V), C, coleccion=col)
    asignar(t, corteza)
    suavizar(t)
    # hojas alrededor de la parte exterior de las ramas
    paleta = np.array([[0.55, 0.16, 0.02], [0.72, 0.36, 0.03], [0.62, 0.48, 0.06], [0.42, 0.27, 0.04], [0.25, 0.32, 0.06]]) if otono \
        else np.array([[0.12, 0.25, 0.04], [0.18, 0.3, 0.06], [0.1, 0.2, 0.05]])
    base_col = paleta[rg.integers(0, len(paleta))]
    n_h = int((8000 if PREVIA else 20000) * alto / 10)
    pts = np.concatenate([P[len(P) // 3:] for P in puntas])
    centro = pts[rg.integers(0, len(pts), n_h)] + rg.normal(0, alto * 0.05, (n_h, 3))
    tam = alto * rg.uniform(0.008, 0.014, n_h)
    a1, a2 = rg.uniform(0, 2 * np.pi, n_h), rg.uniform(-0.9, 0.9, n_h)
    ex = np.stack([np.cos(a1), np.sin(a1), np.zeros(n_h)], 1)
    ey = np.stack([-np.sin(a1) * np.cos(a2), np.cos(a1) * np.cos(a2), np.sin(a2)], 1)
    # silueta de hoja como polígono opaco (6 vértices: base, dos hombros, punta, dos lados): sin transparencias,
    # que con miles de hojas multiplican el tiempo de render
    forma = np.array([[0.0, -0.5], [0.32, -0.2], [0.28, 0.18], [0.0, 0.5], [-0.28, 0.18], [-0.32, -0.2]])
    curva = rg.uniform(-0.25, 0.25, n_h)
    nrm = np.cross(ex, ey)
    Vh = (centro[:, None, :] + tam[:, None, None] * (forma[None, :, 0:1] * ex[:, None, :] + forma[None, :, 1:2] * ey[:, None, :] * 1.6)
          + (tam * curva)[:, None, None] * (np.abs(forma[None, :, 0:1]) * 0.6) * nrm[:, None, :])
    caras = (np.arange(n_h)[:, None] * 6 + np.arange(6)).tolist()
    var = rg.uniform(0.75, 1.2, (n_h, 1))
    mezcla = rg.random((n_h, 1)) < 0.3
    cols = np.where(mezcla, paleta[rg.integers(0, len(paleta), n_h)], base_col) * var
    h = malla(f"{nombre}_hojas", Vh.reshape(-1, 3), caras, np.repeat(cols, 6, 0), coleccion=col)
    asignar(h, _mat_hojas())
    return {"col": col, "alto": alto, "ancho": alto * 0.6, "z0": 0.0, "id": "procedural"}


def arbol(pos, alto, rg, otono=True, nombre="Arbol"):
    # variantes de árbol con hojas reales, construidas una vez por escena e instanciadas
    if "arbol_vars" not in _modelos:
        _modelos["arbol_vars"] = [v for v in (_try("árbol con hojas", arbol_hojas, 10.0, np.random.default_rng(SEMILLA + 101 + i), otono, f"ArbolVar{i}")
                                              for i in range(4)) if v]
    vars_ = _modelos["arbol_vars"]
    if vars_:
        info = vars_[int(rg.integers(0, len(vars_)))]
        return instancia(info, pos, rg.uniform(0, 6.3), alto / info["alto"], nombre)
    info = modelo_ph("arbol", ["tree", "oak", "maple", "birch", "beech", "pine", "fir"],
                     ("dead", "stump", "log", "trunk", "branch", "palm", "quiver", "aloe", "cactus", "succulent", "baobab", "bonsai", "potted",
                      "shelf", "shelves", "furniture", "table", "chair", "plank", "crate"), requerir=("tree", "trees"), alto_min=2.0)
    if info:
        esc = alto / max(1.0, info["alto"])
        return instancia(info, pos, rg.uniform(0, 6.3), esc, nombre)
    return arbol_procedural(pos, alto, rg, otono, nombre)


def roca(pos, tam, rg):
    info = modelo_ph("roca", ["rock", "boulder", "stone"], ("wall", "cliff", "namaqualand", "desert", "furniture", "statue"),
                     requerir=("rock", "boulder", "rocks"), alto_min=0.1)
    if info:
        return instancia(info, pos, rg.uniform(0, 6.3), tam / max(0.1, info["ancho"]), "Roca")
    return roca_procedural(pos, tam, rg)


# ------------------------------------------------------------------ lluvia y charcos
def lluvia(centro, radio=14.0, fps=24, f0=1, f1=240, intensidad=1.0, viento=0.0):
    """Lluvia de rayas que caen en bucle. intensidad 1 = lluvia normal; 2-3 = tormenta. viento inclina las gotas."""
    rg = np.random.default_rng(SEMILLA + 5)
    alto = 12.0
    n = int((16000 if not PREVIA else 9000) * intensidad)
    P = np.stack([rg.uniform(-radio, radio, n) + centro[0], rg.uniform(-radio, radio, n) + centro[1], rg.uniform(0, alto, n)], 1)
    # más gotas cerca del centro (delante de la cámara): son las que se leen como lluvia
    k = n // 3
    P[:k, :2] = centro[:2] + rg.normal(0, radio * 0.25, (k, 2))
    largo = (0.45 if PREVIA else 0.25) * (1 + 0.25 * (intensidad - 1))
    P = np.concatenate([P, P + np.array([0, 0, alto])])  # dos periodos iguales: el bucle no se nota
    w = 0.0035
    inc = viento * largo  # la raya se inclina con el viento
    v = np.zeros((len(P), 4, 3))
    v[:, 0] = P + [-w, 0, 0]
    v[:, 1] = P + [w, 0, 0]
    v[:, 2] = P + [w + inc, 0, largo]
    v[:, 3] = P + [-w + inc, 0, largo]
    caras = (np.arange(len(P))[:, None] * 4 + np.array([0, 1, 2, 3])).tolist()
    o = malla("Lluvia", v.reshape(-1, 3), caras)
    asignar(o, mat_color("Gota", (0.85, 0.9, 0.95), 0.05, variacion=0, alfa=0.5, transmision=0.5))
    try:
        o.visible_shadow = False
    except Exception:  # noqa: BLE001
        pass
    periodo = alto / 9.0 * fps
    o.location = (0, 0, 0)
    o.keyframe_insert("location", index=2, frame=0)
    if viento:
        o.keyframe_insert("location", index=0, frame=0)
    o.location = (-viento * alto, 0, -alto)
    o.keyframe_insert("location", index=2, frame=periodo)
    if viento:
        o.keyframe_insert("location", index=0, frame=periodo)
    for c in curvas_de(o):
        for k in c.keyframe_points:
            k.interpolation = "LINEAR"
        c.modifiers.new("CYCLES")
    return o


def relampagos(f0, f1, fps, rg, n=3):
    """Destellos de tormenta: una luz de área enorme muy alta que se enciende dos veces seguidas, muy rápido."""
    luz = bpy.data.lights.new("Relampago", "AREA")
    luz.size = 60
    luz.color = (0.8, 0.86, 1.0)
    o = bpy.data.objects.new("Relampago", luz)
    bpy.context.scene.collection.objects.link(o)
    o.location = (rg.uniform(-30, 30), rg.uniform(20, 50), 60)
    luz.energy = 0
    luz.keyframe_insert("energy", frame=f0)
    o["destellos"] = []
    for _ in range(n):
        f = int(rg.uniform(f0 + fps, max(f0 + fps + 1, f1 - fps)))
        o["destellos"] = list(o["destellos"]) + [f]
        for df, e in ((0, 0), (1, 9e5), (2, 1e5), (4, 7e5), (6, 0)):
            luz.energy = e
            luz.keyframe_insert("energy", frame=f + df)
    return o


def charco(pos, radio, fps=24, gotas=True):
    bpy.ops.mesh.primitive_circle_add(vertices=48, radius=radio, fill_type="NGON", location=(pos[0], pos[1], pos[2] + 0.004))
    o = bpy.context.object
    o.name = "Charco"
    d = o.modifiers.new("Borde", "DISPLACE")
    t = bpy.data.textures.get("BordeCharco") or bpy.data.textures.new("BordeCharco", "CLOUDS")
    t.noise_scale = radio * 0.6
    d.texture = t
    d.direction = "X"
    d.strength = radio * 0.25
    m, nt, bsdf, _ = _mat_base("Agua")
    _poner(bsdf, "Base Color", (0.015, 0.018, 0.02, 1))
    _poner(bsdf, "Roughness", 0.015)
    _poner(bsdf, ["Specular IOR Level", "Specular"], 0.7)
    if gotas:
        # ondas de gotas: anillos que se expanden en celdas de Voronoi con fase aleatoria
        coord = nt.nodes.new("ShaderNodeTexCoord")
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        _poner(vor, "Scale", 9.0)
        nt.links.new(coord.outputs["Object"], vor.inputs["Vector"])
        tiempo = nt.nodes.new("ShaderNodeValue")
        tiempo.outputs[0].default_value = 0
        tiempo.outputs[0].keyframe_insert("default_value", frame=0)
        tiempo.outputs[0].default_value = 4000 / fps
        tiempo.outputs[0].keyframe_insert("default_value", frame=4000)
        fase = nt.nodes.new("ShaderNodeSeparateColor") if "ShaderNodeSeparateColor" in dir(bpy.types) else nt.nodes.new("ShaderNodeSeparateRGB")
        nt.links.new(vor.outputs["Color"], fase.inputs[0])
        t1 = nt.nodes.new("ShaderNodeMath")
        t1.operation = "ADD"
        nt.links.new(tiempo.outputs[0], t1.inputs[0])
        nt.links.new(fase.outputs[0], t1.inputs[1])
        fr = nt.nodes.new("ShaderNodeMath")
        fr.operation = "FRACT"
        nt.links.new(t1.outputs[0], fr.inputs[0])
        radio_n = nt.nodes.new("ShaderNodeMath")
        radio_n.operation = "MULTIPLY"
        radio_n.inputs[1].default_value = 0.5
        nt.links.new(fr.outputs[0], radio_n.inputs[0])
        dif = nt.nodes.new("ShaderNodeMath")
        dif.operation = "SUBTRACT"
        nt.links.new(vor.outputs["Distance"], dif.inputs[0])
        nt.links.new(radio_n.outputs[0], dif.inputs[1])
        onda = nt.nodes.new("ShaderNodeMath")
        onda.operation = "MULTIPLY"
        onda.inputs[1].default_value = 60.0
        nt.links.new(dif.outputs[0], onda.inputs[0])
        sen = nt.nodes.new("ShaderNodeMath")
        sen.operation = "SINE"
        nt.links.new(onda.outputs[0], sen.inputs[0])
        cerca = nt.nodes.new("ShaderNodeMath")
        cerca.operation = "LESS_THAN"
        cerca.inputs[1].default_value = 0.08
        absd = nt.nodes.new("ShaderNodeMath")
        absd.operation = "ABSOLUTE"
        nt.links.new(dif.outputs[0], absd.inputs[0])
        nt.links.new(absd.outputs[0], cerca.inputs[0])
        apaga = nt.nodes.new("ShaderNodeMath")
        apaga.operation = "SUBTRACT"
        apaga.inputs[0].default_value = 1.0
        nt.links.new(fr.outputs[0], apaga.inputs[1])
        m1 = nt.nodes.new("ShaderNodeMath")
        m1.operation = "MULTIPLY"
        nt.links.new(sen.outputs[0], m1.inputs[0])
        nt.links.new(cerca.outputs[0], m1.inputs[1])
        m2 = nt.nodes.new("ShaderNodeMath")
        m2.operation = "MULTIPLY"
        nt.links.new(m1.outputs[0], m2.inputs[0])
        nt.links.new(apaga.outputs[0], m2.inputs[1])
        bump = nt.nodes.new("ShaderNodeBump")
        _poner(bump, "Strength", 0.35)
        _poner(bump, "Distance", 0.02)
        nt.links.new(m2.outputs[0], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        for c in curvas_de(nt):
            for k in c.keyframe_points:
                k.interpolation = "LINEAR"
    asignar(o, m)
    return o


# ------------------------------------------------------------------ cielo
def elegir_hdri(hora, nubes, ambiente):
    if hora == "noche":
        palabras, excluir = ["night", "moon"], ("indoor", "interior", "studio")
    elif hora in ("amanecer", "atardecer"):
        palabras, excluir = ["sunrise", "sunset", "golden", "dawn", "dusk"], ("indoor", "interior", "studio", "night", "urban")
    elif nubes > 0.6:
        palabras, excluir = ["overcast", "cloudy"], ("indoor", "interior", "studio", "night")
    else:
        palabras, excluir = ["clear", "sunny", "partly cloudy", "skies"], ("indoor", "interior", "studio", "night", "sunset", "sunrise")
    if ambiente == "bosque":
        palabras = palabras + ["forest", "nature"]
    elif ambiente in ("pueblo", "calle"):
        palabras = palabras + ["urban", "street", "village", "town"]
        excluir = tuple(excluir) + ("skidpan", "track", "parking", "airport", "highway", "industrial", "stadium")
    else:
        palabras = palabras + ["nature", "outdoor", "field", "meadow", "garden"]
        excluir = tuple(excluir) + ("skidpan", "track", "desert", "beach", "industrial")
    aid = _try("buscar HDRI", ph.buscar, "hdris", palabras, excluir, SEMILLA + len(hora))
    if not aid:
        return None, None
    ruta = _try(f"descargar HDRI {aid}", ph.hdri, aid, "1k" if PREVIA else "2k")
    return ruta, aid


def direccion_sol(imagen):
    """Ángulo horizontal (rad) del píxel más brillante de un HDRI equirectangular."""
    w, h = imagen.size
    px = np.empty(w * h * 4, np.float32)
    imagen.pixels.foreach_get(px)
    lum = px.reshape(h, w, 4)[:, :, :3].sum(2)
    lum[: h // 2 - 1] *= 0.0  # solo la mitad de arriba (cielo); en Blender la fila 0 es la de abajo
    fila, col = np.unravel_index(np.argmax(lum), lum.shape)
    u = (col + 0.5) / w
    v = (fila + 0.5) / h
    return 2 * math.pi * (u - 0.5), math.pi * (v - 0.5)


def rio(a, b, ancho, rg, fps=24, corriente=0.35):
    """Río: franja de agua de a a b (orillas algo irregulares) que corre en esa dirección, con piedras en las orillas.
    El agua es un espejo oscuro con ondas de ruido que avanzan con la corriente (el reflejo del cielo hace el resto)."""
    a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
    d = b - a
    L = float(np.linalg.norm(d)) or 1.0
    u = d / L
    n = np.array([-u[1], u[0]])
    N = max(8, int(L / 0.5))
    verts, caras = [], []
    for i in range(N + 1):
        p = a + d * i / N
        w0 = ancho / 2 * (1 + 0.18 * math.sin(i * 1.7) + 0.1 * rg.uniform(-1, 1))
        w1 = ancho / 2 * (1 + 0.18 * math.cos(i * 1.3) + 0.1 * rg.uniform(-1, 1))
        verts += [(*(p + n * w0), 0.006), (*(p - n * w1), 0.006)]
        if i:
            k = 2 * i
            caras.append((k - 2, k - 1, k + 1, k))
    o = malla("Rio", verts, caras)
    m, nt, bsdf, _ = _mat_base("AguaRio")
    _poner(bsdf, "Base Color", (0.012, 0.03, 0.032, 1))
    _poner(bsdf, "Roughness", 0.03)
    _poner(bsdf, ["Specular IOR Level", "Specular"], 0.8)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapa = nt.nodes.new("ShaderNodeMapping")
    nt.links.new(coord.outputs["Object"], mapa.inputs["Vector"])
    # la textura avanza con la corriente: la ubicación del mapeo se anima a lo largo del río
    loc = mapa.inputs["Location"]
    for fr, s in ((0, 0.0), (4000, -corriente * 4000 / fps)):
        loc.default_value = (s * u[0], s * u[1], 0)
        loc.keyframe_insert("default_value", frame=fr)
    ruido = nt.nodes.new("ShaderNodeTexNoise")
    _poner(ruido, "Scale", 3.5)
    _poner(ruido, "Detail", 6.0)
    nt.links.new(mapa.outputs["Vector"], ruido.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")
    _poner(bump, "Strength", 0.25)
    nt.links.new(ruido.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    asignar(o, m)
    # piedras en las dos orillas
    for i in range(int(L / 1.2)):
        t = rg.uniform(0, 1)
        lado = 1 if rg.random() < 0.5 else -1
        p = a + d * t + n * lado * (ancho / 2 + rg.uniform(0.0, 0.35))
        roca_procedural((float(p[0]), float(p[1]), 0.0), rg.uniform(0.08, 0.22), rg)
    return o


# ------------------------------------------------------------------ flores, juncos y peces (una malla por tipo: barato)
def flores(terreno, centro, radio, n, evitar=(), otono=False):
    """Flores silvestres en manchas (como crecen de verdad): margaritas blancas, botones de oro y tréboles morados.
    Cada flor = tallo (2 triángulos) + corola de 6 pétalos en abanico; todo en UNA malla con color por vértice."""
    rg = np.random.default_rng(SEMILLA + 31)
    n = int(n * (0.4 if PREVIA else 1.0))
    # manchas: centros al azar y flores alrededor de cada uno
    manchas = _puntos(rg, max(6, n // 60), centro, radio, evitar)
    if not len(manchas):
        return None
    idx = rg.integers(0, len(manchas), n)
    P = manchas[idx] + rg.normal(0, 0.45, (n, 2))
    for (ex, ey, er) in evitar:
        P = P[np.hypot(P[:, 0] - ex, P[:, 1] - ey) > er]
    n = len(P)
    if not n:
        return None
    paleta = np.array([[0.85, 0.85, 0.8], [0.95, 0.75, 0.08], [0.55, 0.25, 0.6], [0.9, 0.9, 0.85]]) * (0.8 if otono else 1.0)
    tipo = (idx[:n] + rg.integers(0, 2, n)) % len(paleta)
    alto = rg.uniform(0.07, 0.2, n)
    r = rg.uniform(0.012, 0.024, n) * np.where(tipo == 1, 0.8, 1.0)
    z0 = terreno.z(P[:, 0], P[:, 1])
    lean = rg.normal(0, 0.15, (n, 2)) * alto[:, None]
    tope = np.stack([P[:, 0] + lean[:, 0], P[:, 1] + lean[:, 1], z0 + alto], 1)
    V, C, F = [], [], []
    base = 0
    tallo = np.array([0.1, 0.22, 0.05])
    # tallo
    w = 0.0018
    v = np.stack([np.stack([P[:, 0] - w, P[:, 1], z0], 1), np.stack([P[:, 0] + w, P[:, 1], z0], 1), tope], 1)
    V.append(v.reshape(-1, 3)); C.append(np.tile(tallo, (n * 3, 1)))
    F += (np.arange(n)[:, None] * 3 + np.array([0, 1, 2])).tolist()
    base += n * 3
    # corola: centro + 6 puntas (pétalos como abanico), algo inclinada hacia el sol
    k = 6
    ang = np.linspace(0, 2 * np.pi, k, endpoint=False)[None, :] + rg.uniform(0, 1, (n, 1))
    pts = np.zeros((n, k + 1, 3))
    pts[:, 0] = tope + np.array([0, 0, 0.003])
    pts[:, 1:, 0] = tope[:, None, 0] + r[:, None] * np.cos(ang)
    pts[:, 1:, 1] = tope[:, None, 1] + r[:, None] * np.sin(ang)
    pts[:, 1:, 2] = tope[:, None, 2] - 0.15 * r[:, None]
    V.append(pts.reshape(-1, 3))
    col = paleta[tipo] * rg.uniform(0.85, 1.1, (n, 1))
    cc = np.repeat(col[:, None, :], k + 1, 1)
    cc[:, 0] = np.where((tipo == 0)[:, None] | (tipo == 3)[:, None], [0.95, 0.7, 0.05], col * 0.7)  # botón amarillo
    C.append(cc.reshape(-1, 3))
    for j in range(k):
        F += (base + np.arange(n)[:, None] * (k + 1) + np.array([0, 1 + j, 1 + (j + 1) % k])).tolist()
    o = malla("Flores", np.concatenate(V), F, np.concatenate(C))
    asignar(o, mat_vertices("Flores", rug=0.55, sss=0.15))
    aviso(f"flores: {n} en {len(manchas)} manchas")
    return o


def juncos(a, b, ancho, n, terreno=None):
    """Juncos y espadañas en las dos orillas del río: hojas altas y finas, algunas con la espiga marrón."""
    rg = np.random.default_rng(SEMILLA + 37)
    n = int(n * (0.5 if PREVIA else 1.0))
    a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
    d = b - a
    L = float(np.linalg.norm(d)) or 1.0
    u, nn = d / L, np.array([-d[1], d[0]]) / L
    t = rg.uniform(0, 1, n)
    lado = np.where(rg.random(n) < 0.5, 1, -1)
    P = a + d * t[:, None] + nn * (lado * (ancho / 2 + rg.uniform(-0.15, 0.45, n)))[:, None]
    z0 = terreno.z(P[:, 0], P[:, 1]) if terreno is not None else np.zeros(n)
    h = rg.uniform(0.5, 1.2, n)
    ang = rg.uniform(0, 2 * np.pi, n)
    incl = rg.uniform(0.05, 0.3, n)
    w = 0.006
    dx, dy = np.cos(ang), np.sin(ang)
    v = np.zeros((n, 3, 3))
    v[:, 0] = np.stack([P[:, 0] - dy * w, P[:, 1] + dx * w, z0], 1)
    v[:, 1] = np.stack([P[:, 0] + dy * w, P[:, 1] - dx * w, z0], 1)
    v[:, 2] = np.stack([P[:, 0] + dx * h * incl, P[:, 1] + dy * h * incl, z0 + h], 1)
    col = np.array([0.16, 0.24, 0.07]) * rg.uniform(0.7, 1.3, (n, 1))
    V, C = [v.reshape(-1, 3)], [np.repeat(col, 3, 0)]
    F = (np.arange(n)[:, None] * 3 + np.array([0, 1, 2])).tolist()
    # espigas (1 de cada 4): un rombo marrón alargado cerca de la punta
    m = np.nonzero(rg.random(n) < 0.25)[0]
    base = n * 3
    e = np.zeros((len(m), 4, 3))
    top = v[m, 2]
    mid = top - np.array([0, 0, 0.12])
    e[:, 0] = mid + np.array([0.012, 0, 0]); e[:, 1] = top - np.array([0, 0, 0.02]); e[:, 2] = mid - np.array([0.012, 0, 0]); e[:, 3] = mid - np.array([0, 0, 0.1])
    V.append(e.reshape(-1, 3)); C.append(np.tile([0.28, 0.14, 0.05], (len(m) * 4, 1)))
    F += (base + np.arange(len(m))[:, None] * 4 + np.array([0, 1, 2, 3])).tolist()
    o = malla("Juncos", np.concatenate(V), F, np.concatenate(C))
    asignar(o, mat_vertices("Juncos", rug=0.6, sss=0.1))
    return o


def peces(a, b, ancho, f0, f1, fps, rg, n=3, cerca=None):
    """Peces que saltan del río de vez en cuando (arco de 0.5 s) y dejan una onda que se abre en el agua.
    Cada pez es una malla pequeña que solo existe en el aire (fuera del salto queda oculto bajo el agua)."""
    a, b = np.asarray(a[:2], float), np.asarray(b[:2], float)
    d = b - a
    L = float(np.linalg.norm(d)) or 1.0
    u, nn = d / L, np.array([-d[1], d[0]]) / L
    m_pez = mat_color("Pez", (0.42, 0.45, 0.42), 0.25, variacion=0.15, escala=40, metal=0.35)
    m_onda = mat_color("OndaPez", (0.7, 0.75, 0.78), 0.05, variacion=0)
    saltos = 0
    for k in range(n):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, radius=0.05, location=(0, 0, -5))
        pez = bpy.context.object
        pez.name = f"Pez{k}"
        pez.scale = (0.45, 1.6, 0.55)
        asignar(pez, m_pez)
        bpy.ops.mesh.primitive_torus_add(major_radius=0.12, minor_radius=0.006, location=(0, 0, -5))
        onda = bpy.context.object
        onda.name = f"Onda{k}"
        asignar(onda, m_onda)
        f = int(f0 + rg.uniform(0.5, 3.0) * fps)
        while f < f1 - fps:
            t = rg.uniform(0.15, 0.85)
            if cerca is not None:  # cerca de donde mira la cámara, para que se vean
                t = float(np.clip(((np.asarray(cerca[:2]) - a) @ u) / L + rg.uniform(-0.12, 0.12), 0.02, 0.98))
            p = a + d * t + nn * rg.uniform(-0.3, 0.3) * ancho
            dirx = u * (1 if rg.random() < 0.5 else -1)
            dur = int(0.5 * fps)
            alto = rg.uniform(0.25, 0.45)
            for i in range(dur + 1):
                s = i / dur
                x = p + dirx * (s - 0.5) * 0.6
                z = alto * 4 * s * (1 - s)
                pez.location = (float(x[0]), float(x[1]), float(z) - 0.02)
                pez.rotation_euler = (math.atan2(alto * 4 * (1 - 2 * s), 0.6) * -1, 0, math.atan2(dirx[1], dirx[0]) - math.pi / 2)
                pez.keyframe_insert("location", frame=f + i)
                pez.keyframe_insert("rotation_euler", frame=f + i)
            pez.location = (0, 0, -5)
            pez.keyframe_insert("location", frame=f + dur + 1)
            pez.keyframe_insert("location", frame=f - 1)
            # onda donde vuelve a entrar al agua
            ox = p + dirx * 0.3
            onda.location = (float(ox[0]), float(ox[1]), 0.012)
            for fr, esc in ((f + dur, 0.2), (f + dur + int(1.2 * fps), 3.0)):
                onda.scale = (esc, esc, 1)
                onda.keyframe_insert("scale", frame=fr)
                onda.keyframe_insert("location", frame=fr)
            onda.location = (0, 0, -5)
            onda.keyframe_insert("location", frame=f + dur + int(1.2 * fps) + 1)
            onda.keyframe_insert("location", frame=f + dur - 1)
            saltos += 1
            f += int(rg.uniform(3.5, 7.0) * fps)
    aviso(f"peces: {saltos} saltos en el río")


def valla_tramo(p0, p1, alto=1.0):
    """Tramo de valla de tablas entre dos puntos cualesquiera (para cerrar el jardín por los lados)."""
    pintura = mat_color("ValllaPintada", (0.78, 0.76, 0.7), 0.6, variacion=0.12, escala=6)
    p0, p1 = np.asarray(p0[:2], float), np.asarray(p1[:2], float)
    d = p1 - p0
    L = float(np.linalg.norm(d)) or 1.0
    ang = math.atan2(d[1], d[0])
    n = int(L / 0.13)
    for i in range(n + 1):
        p = p0 + d * i / max(n, 1)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(float(p[0]), float(p[1]), alto * 0.48))
        t = bpy.context.object
        t.scale = (0.08, 0.025, alto * 0.96)
        t.rotation_euler.z = ang
        asignar(t, pintura)
    m = (p0 + p1) / 2
    for z in (alto * 0.25, alto * 0.75):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(float(m[0]), float(m[1]), z))
        r = bpy.context.object
        r.scale = (L, 0.03, 0.07)
        r.rotation_euler.z = ang
        asignar(r, pintura)
