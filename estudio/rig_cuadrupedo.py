"""Forja3D Cine · esqueleto propio para cuadrúpedos (perros, gatos, caballos...).

Las mallas de Meshy/Tripo vienen a veces con esqueletos incompletos (patas de 1 hueso, sin orejas).
Este módulo mide la malla y calcula dónde van las articulaciones, y reparte los pesos de piel.
La parte numérica (este archivo) no depende de Blender: se prueba fuera con numpy.

Convención: malla ya normalizada con la cabeza hacia +Y, arriba +Z, patas en z=0, centrada en X.
Lado derecho del animal = -X (mirando hacia +Y, la derecha queda en -X... ver LADO).
"""
import numpy as np

# Cuando el animal mira hacia +Y, su lado derecho está en +X
LADO = {"R": 1.0, "L": -1.0}


def _kmeans(P, c, it=25):
    for _ in range(it):
        lab = np.argmin(((P[:, None, :] - c[None]) ** 2).sum(-1), 1)
        c = np.array([P[lab == k].mean(0) if np.any(lab == k) else c[k] for k in range(len(c))])
    return lab, c


def orientar(v):
    """Devuelve (eje_largo, signo_cabeza) de una malla de cuadrúpedo con Z arriba.
    La cabeza es el extremo con más masa alta (el cuello sube; la cola es fina)."""
    z0, H = v[:, 2].min(), np.ptp(v[:, 2])
    largo = 0 if np.ptp(v[:, 0]) > np.ptp(v[:, 1]) else 1
    alto = v[v[:, 2] > z0 + 0.75 * H]
    s = np.sign(alto[:, largo].mean() - np.median(v[:, largo])) or 1.0
    return largo, float(s)


def landmarks(v):
    """Articulaciones de un cuadrúpedo normalizado (cabeza +Y, z0=0). Devuelve dict nombre -> (x,y,z)."""
    x, y, z = v.T
    H = z.max()
    Lmin, Lmax = y.min(), y.max()
    J = {}
    # ---- patas: se separan cerca del suelo (donde están más claras) y se siguen hacia arriba
    bajo = v[z < 0.18 * H]
    med = np.median(bajo[:, 1])
    init = np.array([[sx * np.abs(bajo[:, 0]).mean(), med + sy * np.abs(bajo[:, 1] - med).mean()] for sx in (1, -1) for sy in (1, -1)])
    _, c = _kmeans(bajo[:, :2], init)
    patas = v[z < 0.42 * H]
    lab, _ = _kmeans(patas[:, :2], c, it=3)
    nombres = {}
    for k in range(4):
        lado = "R" if c[k, 0] > 0 else "L"
        delante = c[k, 1] > np.median(c[:, 1])
        nombres[k] = ("Del" if delante else "Tra") + "_" + lado
    rodilla = {}
    for k, n in nombres.items():
        L = patas[lab == k]
        def corte(h, ancho=0.035):
            b = L[np.abs(L[:, 2] - h * H) < ancho * H]
            return b.mean(0) if len(b) else None
        pata = corte(0.02)
        pie = corte(0.11)
        med_ = corte(0.24)
        alto_ = corte(0.38)
        if pata is None or pie is None or med_ is None or alto_ is None:
            continue
        # la punta de la pata hacia delante (dedos)
        punta = L[L[:, 2] < 0.05 * H]
        dedos = np.array([pata[0], punta[:, 1].max(), 0.01 * H])
        J[f"{n}_dedos"] = dedos
        J[f"{n}_pata"] = np.array([pata[0], pata[1], 0.045 * H])
        J[f"{n}_tobillo"] = pie
        J[f"{n}_rodilla"] = med_
        J[f"{n}_alto"] = alto_
        rodilla[n] = med_
    # ---- columna: centroides de secciones del torso entre hombros y caderas
    del_y = np.mean([J[f"Del_{s}_alto"][1] for s in "RL" if f"Del_{s}_alto" in J])
    tra_y = np.mean([J[f"Tra_{s}_alto"][1] for s in "RL" if f"Tra_{s}_alto" in J])
    def seccion(yc, ancho=0.04, zmin=0.42):
        b = v[(np.abs(y - yc) < ancho * (Lmax - Lmin)) & (z > zmin * H)]
        return np.array([0.0, yc, b[:, 2].mean()]) if len(b) else np.array([0.0, yc, 0.65 * H])
    J["cadera"] = seccion(tra_y)
    J["lomo"] = seccion((2 * tra_y + del_y) / 3)
    J["pecho"] = seccion((tra_y + 2 * del_y) / 3)
    J["hombros"] = seccion(del_y)
    # hombros y caderas laterales (donde nace cada pata)
    for s in "RL":
        for pre, base in (("Del", "hombros"), ("Tra", "cadera")):
            if f"{pre}_{s}_alto" in J:
                a = J[f"{pre}_{s}_alto"]
                J[f"{pre}_{s}_raiz"] = np.array([a[0] * 0.8, a[1], J[base][2] - 0.08 * H])
    # ---- cabeza: la parte alta por delante de los hombros
    cab = v[(y > del_y) & (z > 0.7 * H)]
    nariz_v = cab[np.argmax(cab[:, 1])]
    J["nariz"] = np.array([0.0, nariz_v[1], nariz_v[2]])
    craneo = cab[cab[:, 1] > nariz_v[1] - 0.45 * (nariz_v[1] - del_y)]
    centro = craneo.mean(0)
    J["cabeza"] = np.array([0.0, centro[1] - 0.25 * (nariz_v[1] - centro[1]), centro[2]])
    J["cuello"] = np.array([0.0, del_y + 0.35 * (J["cabeza"][1] - del_y), (J["hombros"][2] + J["cabeza"][2]) / 2 + 0.05 * H])
    # ---- orejas: lo más ancho de la cabeza por debajo de la coronilla (orejas caídas) o por encima (orejas paradas)
    region = v[(y > J["cabeza"][1] - 0.12 * (Lmax - Lmin)) & (y < J["cabeza"][1] + 0.08 * (Lmax - Lmin)) & (z > J["hombros"][2])]
    for s in "RL":
        lado = region[region[:, 0] * LADO[s] > 0]
        if len(lado) < 20:
            continue
        lejos = lado[np.abs(lado[:, 0]) > np.percentile(np.abs(lado[:, 0]), 80)]
        raiz = np.array([lejos[:, 0].mean() * 0.8, lejos[:, 1].mean(), min(lejos[:, 2].max(), J["cabeza"][2] + 0.1 * H)])
        punta = lejos[np.argmax(np.abs(lejos[:, 2] - raiz[2]))]
        J[f"oreja_{s}_raiz"] = raiz
        J[f"oreja_{s}_punta"] = punta
    # ---- cola: empieza donde el cuerpo se vuelve fino (sección baja) detrás de las caderas
    detras = v[(y < tra_y) & (z > 0.4 * H)]
    if len(detras) > 20:
        paso = 0.02 * (Lmax - Lmin)
        inicio = None
        for yc in np.arange(tra_y, detras[:, 1].min(), -paso):
            b = detras[np.abs(detras[:, 1] - yc) < paso]
            if len(b) > 3 and np.ptp(b[:, 2]) < 0.22 * H:
                inicio = yc
                break
        if inicio is not None:
            cola = detras[detras[:, 1] <= inicio + paso]
            J["cola_base"] = np.array([0.0, inicio + paso, cola[cola[:, 1] > inicio - paso][:, 2].mean()])
            ys = np.linspace(inicio, cola[:, 1].min() + 0.5 * paso, 5)
            for i, yc in enumerate(ys):
                b = cola[np.abs(cola[:, 1] - yc) < 1.5 * paso]
                if len(b):
                    J[f"cola_{i}"] = np.array([0.0, yc, b[:, 2].mean()])
    return J


# Huesos: nombre -> (cabeza, cola, padre). Los nombres de las patas incluyen el lado.
def huesos(J):
    B = {}
    def h(n, a, b, p):
        if a in J and b in J:
            B[n] = (J[a], J[b], p)
    h("pelvis", "cadera", "lomo", None)
    h("espalda", "lomo", "pecho", "pelvis")
    h("pecho", "pecho", "hombros", "espalda")
    h("cuello", "hombros", "cuello", "pecho")
    h("cuello2", "cuello", "cabeza", "cuello")
    h("cabeza", "cabeza", "nariz", "cuello2")
    for s in "RL":
        for pre, padre in (("Del", "pecho"), ("Tra", "pelvis")):
            n = f"{pre}_{s}"
            h(f"{n}_1", f"{n}_raiz", f"{n}_rodilla", padre)
            h(f"{n}_2", f"{n}_rodilla", f"{n}_tobillo", f"{n}_1")
            h(f"{n}_3", f"{n}_tobillo", f"{n}_pata", f"{n}_2")
            h(f"{n}_pie", f"{n}_pata", f"{n}_dedos", f"{n}_3")
        h(f"oreja_{s}", f"oreja_{s}_raiz", f"oreja_{s}_punta", "cabeza")
    h("cola_raiz", "cadera", "cola_base", "pelvis")
    prev, a, i = "cola_raiz", "cola_base", 0
    while f"cola_{i}" in J and a in J:
        B[f"cola{i}"] = (J[a], J[f"cola_{i}"], prev)
        prev, a, i = f"cola{i}", f"cola_{i}", i + 1
    return B


def _dist_segmento(P, a, b):
    ab = b - a
    t = np.clip(((P - a) @ ab) / max(1e-9, ab @ ab), 0, 1)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


def pesos(v, B, J):
    """Pesos de piel por regiones: cada vértice solo puede pertenecer a los huesos de su zona
    (una pata no arrastra a la otra), con caída suave 1/d^4 entre los 3 huesos más cercanos."""
    x, y, z = v.T
    H = z.max()
    nombres = list(B)
    D = np.stack([_dist_segmento(v, B[n][0], B[n][1]) for n in nombres], 1)
    permitido = np.ones_like(D, dtype=bool)
    idx = {n: i for i, n in enumerate(nombres)}
    tronco = [n for n in nombres if n in ("pelvis", "espalda", "pecho", "cuello", "cuello2", "cabeza") or n.startswith("cola")]
    # zona de cada pata: debajo del vientre, la pata más cercana
    pies = {f"{p}_{s}": J.get(f"{p}_{s}_pata") for p in ("Del", "Tra") for s in "RL"}
    pies = {k: q for k, q in pies.items() if q is not None}
    if pies:
        cerca = np.argmin(np.stack([np.linalg.norm(v[:, :2] - q[:2], axis=1) for q in pies.values()], 1), 1)
        claves = list(pies)
        vientre = 0.45 * H
        for i in range(len(v)):
            if z[i] < vientre:
                pata = claves[cerca[i]]
                ok = [idx[n] for n in nombres if n.startswith(pata)]
                if z[i] > 0.33 * H:  # transición con el tronco
                    ok += [idx[n] for n in ("pelvis", "pecho") if n in idx]
                permitido[i] = False
                permitido[i, ok] = True
            else:
                patas = [idx[n] for n in nombres if n[:3] in ("Del", "Tra")]
                lejos = [j for j in patas if D[i, j] > 0.12 * H]
                permitido[i, lejos] = False
    for s in "RL":
        n = f"oreja_{s}"
        if n in idx:  # la oreja solo mueve lo que está pegado a ella
            permitido[:, idx[n]] &= D[:, idx[n]] < 0.06 * H
    D = np.where(permitido, D, np.inf)
    w = 1.0 / np.maximum(D, 1e-4) ** 4
    orden = np.argsort(-w, 1)[:, 3:]
    np.put_along_axis(w, orden, 0, 1)
    w /= w.sum(1, keepdims=True)
    return nombres, w


# ------------------------------------------------------------------ simetría
def espejo_hueso(n):
    """Nombre del hueso simétrico (Del_L_1 <-> Del_R_1, oreja_L <-> oreja_R); los del centro se quedan igual."""
    for a, b in (("_L_", "_R_"), ("_R_", "_L_")):
        if a in n:
            return n.replace(a, b)
    for a, b in (("_L", "_R"), ("_R", "_L")):
        if n.endswith(a):
            return n[: -len(a)] + b
    return n


def vecinos_espejo(v, tolerancia):
    """Para cada vértice, el índice de su reflejo (x -> -x) si existe a menos de 'tolerancia'; si no, -1."""
    ref = v * np.array([-1.0, 1.0, 1.0])
    idx = np.full(len(v), -1)
    paso = 1500
    for i in range(0, len(v), paso):
        d = ((ref[i:i + paso, None, :] - v[None, :, :]) ** 2).sum(-1)
        j = np.argmin(d, 1)
        ok = d[np.arange(len(j)), j] < tolerancia ** 2
        idx[i:i + paso][ok] = j[ok]
    return idx


def simetrizar_pesos(v, nombres, w, tolerancia_rel=0.012):
    """Promedia los pesos de cada vértice con los de su reflejo (intercambiando huesos izquierda/derecha),
    para que la piel se deforme igual a los dos lados. Solo donde la malla es simétrica (tronco, cabeza, cola):
    las patas, que están en postura de paso, se dejan como están."""
    H = float(np.ptp(v[:, 2])) or 1.0
    idx = vecinos_espejo(v, tolerancia_rel * H)
    perm = [nombres.index(espejo_hueso(n)) if espejo_hueso(n) in nombres else k for k, n in enumerate(nombres)]
    ok = idx >= 0
    w2 = w.copy()
    w2[ok] = 0.5 * (w[ok] + w[idx[ok]][:, perm])
    w2 /= np.maximum(1e-9, w2.sum(1, keepdims=True))
    return w2, float(ok.mean())


def simetrizar_landmarks(J):
    """Orejas y raíces de las patas: misma altura y separación a los dos lados (las patas en sí no, por la postura)."""
    J = dict(J)
    for base in ("oreja_{}_raiz", "oreja_{}_punta", "Del_{}_raiz", "Tra_{}_raiz"):
        a, b = base.format("R"), base.format("L")
        if a in J and b in J:
            x = (abs(J[a][0]) + abs(J[b][0])) / 2
            y = (J[a][1] + J[b][1]) / 2 if "oreja" in base else None
            z = (J[a][2] + J[b][2]) / 2
            for k, s in ((a, np.sign(J[a][0]) or 1), (b, np.sign(J[b][0]) or -1)):
                J[k] = np.array([s * x, J[k][1] if y is None else y, z])
    return J


# ------------------------------------------------------------------ carne: tejido blando con física de resorte
# Como la "física de tela" de los estudios (Zootopia: músculo → piel → pelo), pero barata: unos pocos huesos
# de carne (papada, belfos, vientre, piel del lomo) que heredan parte del peso de la piel y en la animación
# se mueven con un resorte amortiguado excitado por la aceleración de su punto de enganche.
CARNE = {
    # nombre: (padre, radio·H, fracción máxima de peso, huesos de los que puede robar peso)
    "papada": ("cuello", 0.11, 0.55, ("cuello", "cuello2", "pecho", "cabeza")),
    "belfo_R": ("cabeza", 0.055, 0.6, ("cabeza",)),
    "belfo_L": ("cabeza", 0.055, 0.6, ("cabeza",)),
    "vientre": ("espalda", 0.2, 0.45, ("pelvis", "espalda", "pecho")),
    "piel_lomo": ("pecho", 0.14, 0.3, ("espalda", "pecho", "cuello")),
}


def huesos_carne(v, J):
    """Puntos de la carne a partir de la malla: (cabeza del hueso = enganche, cola = centro del tejido)."""
    x, y, z = v.T
    H = float(z.max()) or 0.5
    L = float(np.ptp(y)) or H
    hom, cab, nar = J["hombros"], J["cabeza"], J["nariz"]
    out = {}
    # papada: lo más bajo de la garganta entre los hombros y la cabeza
    zona = v[(y > hom[1] + 0.1 * (cab[1] - hom[1])) & (y < cab[1]) & (z > 0.5 * H) & (np.abs(x) < 0.06 * H)]
    if len(zona) > 10:
        bajo = zona[zona[:, 2] < np.percentile(zona[:, 2], 12)]
        c = bajo.mean(0)
        c[0] = 0.0
        c[2] += 0.025 * H  # un poco hacia dentro de la piel
        out["papada"] = (np.asarray(J.get("cuello", hom), float), c)
    # belfos: la parte baja y lateral del hocico
    zona = v[(y > cab[1] + 0.45 * (nar[1] - cab[1])) & (y < nar[1] - 0.1 * (nar[1] - cab[1])) & (z < nar[2])]
    for s, lado in (("R", 1), ("L", -1)):
        ld = zona[zona[:, 0] * lado > 0.01 * H]
        if len(ld) > 6:
            c = ld[ld[:, 2] < np.percentile(ld[:, 2], 40)].mean(0)
            c[0] -= lado * 0.01 * H
            gancho = cab + 0.6 * (nar - cab)
            out[f"belfo_{s}"] = (np.array([0.0, c[1] - 0.02 * L, gancho[2]]), c)
    for s, o in (("R", "L"), ("L", "R")):  # simetría: si un lado no se encontró (boca entreabierta), se refleja
        if f"belfo_{s}" not in out and f"belfo_{o}" in out:
            h, c = out[f"belfo_{o}"]
            out[f"belfo_{s}"] = (h.copy(), c * np.array([-1.0, 1, 1]))
    # vientre: lo más bajo del tronco entre hombros y cadera (encima de las patas)
    zona = v[(y < hom[1] - 0.05 * L) & (y > J["cadera"][1] + 0.05 * L) & (z > 0.33 * H) & (np.abs(x) < 0.08 * H)]
    if len(zona) > 10:
        c = zona[zona[:, 2] < np.percentile(zona[:, 2], 15)].mean(0)
        c[0], c[2] = 0.0, c[2] + 0.04 * H
        out["vientre"] = (np.asarray(J["lomo"], float), c)
    # piel del lomo y la cruz (se desliza y rebota al trotar)
    zona = v[(y > hom[1] - 0.1 * L) & (y < hom[1]) & (np.abs(x) < 0.05 * H) & (z < hom[2] + 0.2 * H)]
    if len(zona) > 10:
        c = zona[zona[:, 2] > np.percentile(zona[:, 2], 90)].mean(0)
        c[0], c[2] = 0.0, c[2] - 0.02 * H
        out["piel_lomo"] = (np.asarray(J["pecho"], float), c)
    return out


def anadir_carne(v, J, B, nombres, w):
    """Añade los huesos de carne a B y les pasa una parte del peso de la piel cercana (caída suave)."""
    H = float(v[:, 2].max()) or 0.5
    puntos = huesos_carne(v, J)
    idx = {n: i for i, n in enumerate(nombres)}
    nombres = list(nombres)
    cols = [w]
    for n, (h, t) in puntos.items():
        padre, radio, frac, de = CARNE[n]
        if padre not in B:
            continue
        d = np.linalg.norm(v - t, axis=1)
        m = np.clip(1 - d / (radio * H), 0, 1)
        m = m * m * (3 - 2 * m)
        base = sum(w[:, idx[b]] for b in de if b in idx) if any(b in idx for b in de) else np.zeros(len(v))
        if n.startswith("belfo"):
            lado = 1 if n.endswith("R") else -1
            m *= np.clip(v[:, 0] * lado / (0.01 * H), 0, 1)
        f = frac * m * np.clip(base, 0, 1)
        W = np.concatenate(cols, 1)
        W = W * (1 - f)[:, None]
        cols = [W, f[:, None]]
        B[n] = (h, t, padre)
        nombres.append(n)
    W = np.concatenate(cols, 1)
    W /= np.maximum(W.sum(1, keepdims=True), 1e-9)
    return nombres, W


# ------------------------------------------------------------------ orejas y cola: pesos que no invaden el cuerpo
def geodesica(v, aristas, origen, limite):
    """Distancia por la superficie (Dijkstra sobre las aristas) desde el vértice 'origen', hasta 'limite'."""
    import heapq
    n = len(v)
    vec = [[] for _ in range(n)]
    L = np.linalg.norm(v[aristas[:, 0]] - v[aristas[:, 1]], axis=1)
    for (a, b), l in zip(aristas.tolist(), L.tolist()):
        vec[a].append((b, l))
        vec[b].append((a, l))
    d = np.full(n, np.inf)
    d[origen] = 0.0
    cola = [(0.0, origen)]
    while cola:
        da, a = heapq.heappop(cola)
        if da > d[a] or da > limite:
            continue
        for b, l in vec[a]:
            nd = da + l
            if nd < d[b]:
                d[b] = nd
                heapq.heappush(cola, (nd, b))
    return d


def corregir_orejas_y_cola(v, aristas, J, nombres, w):
    """La oreja de un beagle cuelga pegada a la mejilla y al cuello: por distancia en el espacio, la piel del cuello
    caía dentro de la oreja y se balanceaba con ella ('cuello colgado'). Aquí la oreja son solo los vértices
    cercanos a su punta POR LA SUPERFICIE (para llegar al cuello hay que rodear el borde de la oreja).
    La cola, igual: sus huesos no mueven la grupa (solo lo que está detrás de la base de la cola)."""
    w = w.copy()
    idx = {n: i for i, n in enumerate(nombres)}
    H = float(v[:, 2].max()) or 0.5
    cab = idx.get("cabeza")
    hechos = []
    if aristas is not None and len(aristas):
        mascaras = {}
        for s in "RL":
            n = f"oreja_{s}"
            if n not in idx or f"oreja_{s}_punta" not in J:
                continue
            punta = np.asarray(J[f"oreja_{s}_punta"], float)
            raiz = np.asarray(J[f"oreja_{s}_raiz"], float)
            largo = float(np.linalg.norm(punta - raiz))
            o = int(np.argmin(np.linalg.norm(v - punta, axis=1)))
            g = geodesica(v, aristas, o, 1.3 * largo)
            m = np.clip((1.05 * largo - g) / (0.3 * largo), 0, 1)  # 1 en la oreja, cae a 0 cerca de la raíz
            # y además cerca del eje de la oreja: en mallas donde la punta quedó soldada al cuello, la superficie
            # "se escapa" por la soldadura; el eje la frena
            m = m * np.clip((0.07 * H - _dist_segmento(v, raiz, punta)) / (0.025 * H), 0, 1)
            mascaras[s] = m
        # cada oreja en su sitio real: la capa lateral de su lado se suma a lo que llega por la superficie
        for s_ in list(mascaras):
            mascaras[s_] = np.maximum(mascaras[s_], capa_oreja(v, J, s_))
        # simetría solo de rescate: si en un lado casi no queda oreja, se usa el reflejo del otro lado
        if len(mascaras) == 2 and min((m > 0.5).sum() for m in mascaras.values()) < 30:
            esp = vecinos_espejo(v, 0.04 * H)
            ok = esp >= 0
            for s, o_ in (("R", "L"), ("L", "R")):
                m2 = mascaras[s].copy()
                m2[ok] = np.maximum(m2[ok], mascaras[o_][esp[ok]])
                mascaras[s] = m2
        corte = float(J["cabeza"][2]) - 0.11 * H
        for s, m in mascaras.items():
            n = f"oreja_{s}"
            # nunca por debajo de la mandíbula (cuello y collar) ni pegado al centro de la cabeza: en las mallas
            # de Meshy/Tripo la punta de la oreja suele quedar soldada al cuello y arrastraba el collar
            m = m * np.clip((v[:, 2] - corte) / (0.04 * H), 0, 1)
            m = m * np.clip((np.abs(v[:, 0]) - 0.045 * H) / (0.03 * H), 0, 1)
            m = m * (v[:, 0] * (1 if s == "R" else -1) > 0)
            m = m * m * (3 - 2 * m)
            j = idx[n]
            resto = w.copy()
            resto[:, j] = 0
            sin = resto.sum(1) < 1e-6
            if cab is not None:
                resto[sin, cab] = 1.0
            resto /= np.maximum(resto.sum(1, keepdims=True), 1e-9)
            w = resto * (1 - m)[:, None]
            w[:, j] = m
            hechos.append(f"{n}: {(m > 0.5).sum()} vértices")
    cola = [idx[n] for n in nombres if n.startswith("cola")]
    if cola and "cola_base" in J:
        L = float(np.ptp(v[:, 1])) or 1.0
        f = np.clip((J["cola_base"][1] + 0.02 * L - v[:, 1]) / (0.03 * L), 0, 1)  # 0 delante de la base de la cola
        pel = idx.get("pelvis")
        quitar = w[:, cola].sum(1) * (1 - f)
        w[:, cola] *= f[:, None]
        if pel is not None:
            w[:, pel] += quitar
        hechos.append("cola sin grupa")
    w /= np.maximum(w.sum(1, keepdims=True), 1e-9)
    return w, hechos


def capa_oreja(v, J, lado):
    """Piel más lateral de la cabeza en un lado (la oreja que cuelga encima de la mejilla), por encima de la
    mandíbula y por detrás del ojo. Sirve para cada lado por separado: en muchos modelos una oreja está más
    adelante o más girada que la otra y la simetría la pondría donde no está."""
    H = float(v[:, 2].max()) or 0.5
    cab = np.asarray(J["cabeza"], float)
    lat = v[:, 0] * (1 if lado == "R" else -1)
    m = np.clip((lat - 0.12 * H) / (0.03 * H), 0, 1)
    m *= np.clip((v[:, 2] - (cab[2] - 0.11 * H)) / (0.04 * H), 0, 1)
    m *= np.clip((cab[1] - 0.03 * H - v[:, 1]) / (0.03 * H), 0, 1)
    m *= np.clip((v[:, 1] - (cab[1] - 0.25 * H)) / (0.04 * H), 0, 1)
    return m


def ajustar_orejas(v, J):
    """Raíz y punta de cada oreja medidas en su propio lado (sin forzar simetría)."""
    J = dict(J)
    for s in "RL":
        m = capa_oreja(v, J, s)
        q = v[m > 0.5]
        if len(q) < 40:
            continue
        raiz = q[q[:, 2] >= np.percentile(q[:, 2], 85)].mean(0)
        punta = q[q[:, 2] <= np.percentile(q[:, 2], 10)].mean(0)
        if raiz[2] - punta[2] < 0.05 * float(v[:, 2].max()):
            continue
        raiz[0] *= 0.85  # la bisagra va un poco hacia dentro de la cabeza
        J[f"oreja_{s}_raiz"], J[f"oreja_{s}_punta"] = raiz, punta
    return J
