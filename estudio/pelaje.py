"""Pelaje por raza: el patrón de color real del perro, pintado sobre la malla y horneado en una textura UV.

Las texturas de Meshy/Tripo de un beagle salen manchadas en blanco y negro sin el fuego (canela) de la raza,
y eso es lo primero que delata al CG. Aquí el patrón se calcula por anatomía, a partir de los puntos del
esqueleto (nariz, cabeza, hombros, cadera, cola, patas), con bordes suaves e irregulares como los reales.
La piel y el pelo leen la MISMA textura, así cada pelo tiene el color de la piel donde nace.

Patrones: "beagle_tricolor" (canela, montura negra, blanco en hocico, lista, pecho, vientre, patas y punta
de la cola). Los ojos y el interior de la boca conservan la textura original del modelo.
Solo numpy: el patrón se prueba fuera de Blender; el horneado usa la malla de Blender.
"""
import numpy as np

# colores en espacio lineal (sRGB del beagle de referencia: canela 182,108,52 · negro 26,21,18 · blanco 236,229,216)
def _lin(c):
    c = np.asarray(c, float) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


COLORES = {
    "canela": _lin((182, 108, 52)),
    "canela_oscuro": _lin((140, 76, 34)),
    "negro": _lin((26, 21, 18)),
    "blanco": _lin((236, 229, 216)),
    "trufa": _lin((14, 11, 10)),
}


def _suave(x, ancho):
    """0 → 1 con una rampa suave de 'ancho' centrada en 0."""
    t = np.clip(x / max(ancho, 1e-6) + 0.5, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _ruido(v, escala, semilla=0):
    """Ruido suave barato (suma de senos con fases distintas), en [-1, 1]: bordes de manchas irregulares."""
    r = np.random.default_rng(semilla)
    s = np.zeros(len(v))
    for k in range(5):
        d = r.normal(size=3)
        d /= np.linalg.norm(d)
        f = escala * (1.7 ** k)
        s += np.sin(v @ d * f + r.uniform(0, 6.3)) / (1.6 ** k)
    return s / 2.2


def ojos(J, H):
    nariz = np.asarray(J["nariz"], float)
    cab = np.asarray(J.get("cabeza", nariz), float)
    return [cab + 0.45 * (nariz - cab) + np.array([lado * 0.07 * H, 0, 0.05 * H]) for lado in (1, -1)]


def beagle_tricolor(v, J):
    """Devuelve (rgb Nx3 lineal, mezcla N): mezcla 1 = usar el patrón, 0 = conservar la textura (ojos)."""
    x, y, z = v.T
    H = float(z.max() - z.min()) or 0.5
    L = float(np.ptp(y)) or H
    n1 = _ruido(v, 9.0 / H, 1)   # bordes de manchas grandes
    n2 = _ruido(v, 30.0 / H, 2)  # bordes finos
    nar, cab = np.asarray(J["nariz"], float), np.asarray(J["cabeza"], float)
    hom, cad = np.asarray(J["hombros"], float), np.asarray(J["cadera"], float)
    cue = np.asarray(J.get("cuello", (hom + cab) / 2), float)
    # eje del cuerpo: altura del centro del tronco a lo largo de y
    def zc(yy):
        return np.interp(yy, [cad[1], hom[1]], [cad[2], hom[2]])
    medio_alto = 0.13 * H  # media altura aproximada del tronco
    rgb = np.tile(COLORES["canela"], (len(v), 1))
    # canela más oscuro en orejas y nuca (como en la foto)
    oscuro = np.zeros(len(v))
    for s in "RL":
        if f"oreja_{s}_raiz" in J:
            a, b = np.asarray(J[f"oreja_{s}_raiz"]), np.asarray(J[f"oreja_{s}_punta"])
            ab = b - a
            t = np.clip(((v - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
            d = np.linalg.norm(v - (a + t[:, None] * ab), axis=1)
            oscuro = np.maximum(oscuro, (1 - _suave(d - 0.05 * H, 0.03 * H)) * (0.35 + 0.5 * t))
    rgb = rgb + (COLORES["canela_oscuro"] - rgb) * oscuro[:, None]
    # ---- montura negra: lomo desde la cruz hasta la base de la cola, baja hasta media costilla
    y_cola = float(J["cola_base"][1]) if "cola_base" in J else cad[1] - 0.05 * L
    en_y = _suave(y - (y_cola - 0.01 * L), 0.04 * L) * (1 - _suave(y - (hom[1] + 0.03 * L + 0.02 * L * n1), 0.06 * L))
    alto = z - zc(y)
    montura = en_y * _suave(alto + 0.25 * medio_alto + 0.35 * medio_alto * n1 + 0.08 * medio_alto * n2, 0.25 * medio_alto)
    montura *= (z > 0.45 * H)  # nunca en las patas
    rgb = rgb + (COLORES["negro"] - rgb) * montura[:, None]
    # ---- blanco
    blanco = np.zeros(len(v))
    eje = nar - cab
    t_cab = ((v - cab) @ eje) / max(eje @ eje, 1e-9)  # 0 en el cráneo, 1 en la nariz
    en_cabeza = (y > hom[1] + 0.05 * L) & (z > cue[2] - 0.1 * H)
    # hocico (delante del 'stop') y lista blanca entre los ojos hasta la frente
    hocico = _suave(t_cab - (0.58 + 0.05 * n2), 0.08)
    ancho_lista = 0.018 * H + 0.03 * H * np.clip(t_cab, 0, 1) ** 2
    lista = (1 - _suave(np.abs(x) - ancho_lista, 0.012 * H)) * _suave(t_cab + 0.25, 0.1) * _suave(z - (cab[2] - 0.02 * H), 0.03 * H)
    blanco = np.maximum(blanco, en_cabeza * np.maximum(hocico, lista))
    # garganta y pecho: la parte baja del cuello y el frente del pecho
    en_cuello = (y > hom[1] - 0.06 * L) & (y < cab[1] + 0.02 * L)
    z_cuello = np.interp(y, [hom[1] - 0.06 * L, cab[1]], [hom[2] - 0.9 * medio_alto, cab[2] - 0.06 * H])
    garganta = en_cuello * (1 - _suave(z - (z_cuello + 0.04 * H * n1), 0.04 * H))
    blanco = np.maximum(blanco, garganta)
    # vientre (la parte baja del tronco)
    vientre = (1 - _suave(alto + 0.95 * medio_alto + 0.2 * medio_alto * n1, 0.3 * medio_alto)) * (y > cad[1] - 0.05 * L) * (y < hom[1] + 0.02 * L)
    blanco = np.maximum(blanco, vientre * (z > 0.3 * H))
    # patas: delanteras blancas desde el codo; traseras desde el corvejón (muslos canela)
    delante = y > (hom[1] + cad[1]) / 2
    corte = np.where(delante, 0.43 * H, 0.27 * H) + 0.03 * H * n1
    patas = (1 - _suave(z - corte, 0.04 * H)) * (z < 0.5 * H)
    blanco = np.maximum(blanco, patas)
    # cola: negra arriba en la base, blanca en el último tercio
    if "cola_base" in J:
        ys = [J[k][1] for k in J if k.startswith("cola_") and k[5:].isdigit()]
        y_fin = min(ys) if ys else y_cola - 0.2 * L
        en_cola = (y < y_cola + 0.005 * L) & (z > 0.45 * H)
        tcola = np.clip((y_cola - y) / max(y_cola - y_fin, 1e-6), 0, 1.2)
        punta = en_cola * _suave(tcola - (0.62 + 0.05 * n2), 0.12)
        negro_cola = en_cola * (1 - punta) * _suave(tcola - 0.05, 0.1) * 0.85
        rgb = rgb + (COLORES["negro"] - rgb) * negro_cola[:, None]
        blanco = np.maximum(blanco, punta)
    rgb = rgb + (COLORES["blanco"] - rgb) * blanco[:, None]
    # salpicado (ticking) suave en el blanco de las patas y el hocico, típico de la raza
    salpicado = blanco * np.clip(_ruido(v, 140.0 / H, 3) - 0.55, 0, 1) * 1.6
    rgb = rgb + (COLORES["canela"] - rgb) * (0.35 * salpicado)[:, None]
    # trufa negra
    d_nar = np.linalg.norm(v - nar, axis=1)
    trufa = 1 - _suave(d_nar - 0.04 * H, 0.015 * H)
    rgb = rgb + (COLORES["trufa"] - rgb) * trufa[:, None]
    # ojos: la textura original (el patrón no pinta el ojo)
    mezcla = np.ones(len(v))
    for o in ojos(J, H):
        d = np.linalg.norm(v - o, axis=1)
        mezcla = np.minimum(mezcla, _suave(d - 0.03 * H, 0.015 * H))
    # variación lenta de tono (ningún pelaje real es de un color plano)
    var = 1 + 0.08 * _ruido(v, 5.0 / H, 4)
    return np.clip(rgb * var[:, None], 0, 1), mezcla


PATRONES = {"beagle_tricolor": beagle_tricolor, "beagle": beagle_tricolor, "tricolor": beagle_tricolor}


def patron(nombre, v, J):
    f = PATRONES.get(str(nombre or "").lower())
    return f(v, J) if f else None


# ------------------------------------------------------------------ horneado a una textura UV (sin bake de Blender)
def rasterizar(uv_tri, col_tri, tam=1024, densidad=2.0, semilla=0, max_muestras=8_000_000):
    """uv_tri (T,3,2), col_tri (T,3,C) → imagen (tam,tam,C) y máscara de cobertura.
    Muestras al azar dentro de cada triángulo, proporcionales a su área en píxeles; luego relleno de huecos."""
    r = np.random.default_rng(semilla)
    a = uv_tri[:, 1] - uv_tri[:, 0]
    b = uv_tri[:, 2] - uv_tri[:, 0]
    area = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) * tam * tam
    k = np.maximum(1, np.ceil(area * densidad)).astype(np.int64)
    if k.sum() > max_muestras:
        k = np.maximum(1, (k * (max_muestras / k.sum())).astype(np.int64))
    idx = np.repeat(np.arange(len(uv_tri)), k)
    r1, r2 = np.sqrt(r.random(len(idx))), r.random(len(idx))
    w0, w1, w2 = 1 - r1, r1 * (1 - r2), r1 * r2
    uv = w0[:, None] * uv_tri[idx, 0] + w1[:, None] * uv_tri[idx, 1] + w2[:, None] * uv_tri[idx, 2]
    col = w0[:, None] * col_tri[idx, 0] + w1[:, None] * col_tri[idx, 1] + w2[:, None] * col_tri[idx, 2]
    px = np.clip((uv * tam).astype(np.int64), 0, tam - 1)
    lin = px[:, 1] * tam + px[:, 0]
    C = col_tri.shape[2]
    suma = np.zeros((tam * tam, C))
    for c in range(C):
        suma[:, c] = np.bincount(lin, weights=col[:, c], minlength=tam * tam)
    n = np.bincount(lin, minlength=tam * tam).astype(float)
    img = (suma / np.maximum(n, 1)[:, None]).reshape(tam, tam, C)
    cub = (n > 0).reshape(tam, tam)
    return rellenar(img, cub), cub


def rellenar(img, cub, pasadas=12):
    """Extiende el color a los píxeles vacíos (bordes de islas UV) para que el filtrado no meta negro."""
    img, cub = img.copy(), cub.copy()
    for _ in range(pasadas):
        if cub.all():
            break
        suma = np.zeros_like(img)
        n = np.zeros(cub.shape)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            m = np.roll(np.roll(cub, dy, 0), dx, 1)
            suma += np.roll(np.roll(img * cub[..., None], dy, 0), dx, 1)
            n += m
        nuevo = (~cub) & (n > 0)
        img[nuevo] = suma[nuevo] / n[nuevo][:, None]
        cub = cub | nuevo
    return img


def _imagen_original(malla):
    for m in malla.data.materials:
        if m and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image and n.image.size[0] > 0:
                    return n.image
    return None


def componer(img, original, tam):
    """Donde la mezcla es 0 (ojos) pone el color de la textura original: la imagen final no depende del alfa."""
    if original is None:
        return img[..., :3]
    w, h = original.size
    px = np.zeros(w * h * 4, np.float32)
    original.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[..., :3].astype(float)
    if not original.is_float:
        px = _lin(px * 255.0)
    c = (np.arange(tam) + 0.5) / tam
    ix = np.clip((c * w).astype(int), 0, w - 1)
    iy = np.clip((c * h).astype(int), 0, h - 1)
    orig = px[iy][:, ix]
    m = img[..., 3:4]
    return m * img[..., :3] + (1 - m) * orig


def hornear(malla, rgb, mezcla, nombre, tam=1024):
    """Hornea el patrón por vértice a una imagen en la UV activa de la malla (los ojos con la textura original)."""
    import bpy
    me = malla.data
    if not me.uv_layers:
        return None
    me.calc_loop_triangles()
    T = len(me.loop_triangles)
    loops = np.zeros(T * 3, np.int64)
    verts = np.zeros(T * 3, np.int64)
    me.loop_triangles.foreach_get("loops", loops)
    me.loop_triangles.foreach_get("vertices", verts)
    uvs = np.zeros(len(me.loops) * 2)
    capa = me.uv_layers.active or me.uv_layers[0]
    capa.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2) % 1.0 if np.any(uvs > 1.001) else uvs.reshape(-1, 2)
    col = np.concatenate([rgb, mezcla[:, None]], 1)
    img, cub = rasterizar(uvs[loops].reshape(T, 3, 2), col[verts].reshape(T, 3, 4), tam)
    rgb_img = componer(img, _imagen_original(malla), tam)
    rgba = np.concatenate([rgb_img, np.ones((tam, tam, 1))], -1).astype(np.float32)
    im = bpy.data.images.new(nombre, tam, tam, alpha=False, float_buffer=True)
    try:
        im.colorspace_settings.name = "Linear Rec.709"  # los colores ya están en lineal
    except Exception:  # noqa: BLE001
        pass
    im.pixels.foreach_set(rgba.ravel())
    im.update()
    # comprobación: lo que Blender guardó de verdad (si sale negro, el problema es la imagen, no los nodos)
    leido = np.zeros(tam * tam * 4, np.float32)
    im.pixels.foreach_get(leido)
    leido = leido.reshape(-1, 4)
    from comun import aviso
    aviso(f"pelaje: {T} triángulos, cobertura UV {cub.mean() * 100:.0f} %, color medio "
          f"{np.round(leido[:, :3].mean(0), 3).tolist()} (esperado {np.round(rgb_img.reshape(-1, 3).mean(0), 3).tolist()}), capa UV '{capa.name}'")
    return im, capa.name


def _ent(nodo, ident):
    return next(s for s in nodo.inputs if s.identifier == ident)


def _sal(nodo, ident):
    return next(s for s in nodo.outputs if s.identifier == ident)


def nodos_color(nt, im, capa_uv, original=None):
    """Nodos que dan el color del pelaje: patrón × detalle fino de pelo, mezclado con la textura original (ojos)."""
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = capa_uv
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = im
    tex.interpolation = "Cubic"
    nt.links.new(uv.outputs["UV"], tex.inputs["Vector"])
    # detalle: ruido muy fino que imita mechones y variación de pelo a pelo (±10 %)
    ruido = nt.nodes.new("ShaderNodeTexNoise")
    ruido.inputs["Scale"].default_value = 180.0
    ruido.inputs["Detail"].default_value = 4.0
    nt.links.new(uv.outputs["UV"], ruido.inputs["Vector"])
    rango = nt.nodes.new("ShaderNodeMapRange")
    rango.inputs["To Min"].default_value = 0.88
    rango.inputs["To Max"].default_value = 1.1
    nt.links.new(ruido.outputs["Fac"], rango.inputs["Value"])
    por = nt.nodes.new("ShaderNodeMix")
    por.data_type = "RGBA"
    por.blend_type = "MULTIPLY"
    _ent(por, "Factor_Float").default_value = 1.0
    nt.links.new(tex.outputs["Color"], _ent(por, "A_Color"))
    nt.links.new(rango.outputs["Result"], _ent(por, "B_Color"))
    if original is None:
        return _sal(por, "Result_Color")
    mez = nt.nodes.new("ShaderNodeMix")
    mez.data_type = "RGBA"
    nt.links.new(tex.outputs["Alpha"], _ent(mez, "Factor_Float"))
    nt.links.new(original, _ent(mez, "A_Color"))
    nt.links.new(_sal(por, "Result_Color"), _ent(mez, "B_Color"))
    return _sal(mez, "Result_Color")


def aplicar(malla, v, J, nombre_patron, pid):
    """Pinta el patrón de la raza en la piel. Devuelve (imagen, capa_uv) para que el pelo use el mismo color."""
    res = patron(nombre_patron, v, J)
    if res is None:
        return None
    hecho = hornear(malla, res[0], res[1], f"Pelaje_{pid}")
    if not hecho:
        return None
    im, capa = hecho
    from comun import aviso
    tocados = 0
    for m in malla.data.materials:
        if not m or not m.node_tree:
            continue
        nt = m.node_tree
        for n in list(nt.nodes):
            if n.type == "BSDF_PRINCIPLED":
                nt.links.new(nodos_color(nt, im, capa), n.inputs["Base Color"])
                tocados += 1
    aviso(f"pelaje: {tocados} materiales de piel con el patrón ({', '.join(m.name for m in malla.data.materials if m)})")
    return im, capa
