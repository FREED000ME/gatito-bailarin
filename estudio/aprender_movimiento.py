"""Forja3D Cine · matriz de movimiento aprendida para cuadrúpedos.

La "matriz" (estudio/movimiento/perro.json) describe cómo se mueve un perro real en cada marcha:
  · temporización: factor de apoyo (duty), desfase de cada pata, zancada relativa a la altura de cadera,
  · pelvis: balanceo (roll), giro (yaw) y cabeceo (pitch) con su número de ciclos por zancada,
  · articulaciones (hombro, codo, carpo, cadera, rodilla, tarso): curva de flexión/extensión durante el ciclo,
    guardada como coeficientes de Fourier (a0, a1..a4, b1..b4) normalizados a [-1, 1] más su rango (ROM) en grados,
    y cuánto crece el ROM con la velocidad (grados por m/s).
Fase 0 = la pata toca el suelo; la pata está apoyada mientras fase < duty.

Fuentes de la matriz:
  1. desde_literatura(): cinemática veterinaria publicada (beagles y otras razas). Son datos científicos: uso comercial libre.
  2. desde_keypoints(): puntos de articulaciones de un video lateral propio (p. ej. exportados de DeepLabCut o marcados a mano).
     Úsalo con videos tuyos o con licencia: el movimiento aprendido se mezcla en la matriz con su peso.

Uso:
  python3 estudio/aprender_movimiento.py literatura                # regenera la matriz base
  python3 estudio/aprender_movimiento.py video puntos.csv --fps 60 --marcha trotar --velocidad 1.6 --peso 1.0
  python3 estudio/aprender_movimiento.py prueba                    # comprueba el aprendizaje con datos sintéticos

CSV de puntos (vista lateral, una fila por fotograma; columnas x,y en píxeles, y hacia abajo como en imagen):
  frame, hombro_x, hombro_y, codo_x, codo_y, carpo_x, carpo_y, mano_x, mano_y,
         cadera_x, cadera_y, rodilla_x, rodilla_y, tarso_x, tarso_y, pie_x, pie_y
"""
import argparse
import csv
import json
import math
import os

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
MATRIZ = os.path.join(AQUI, "movimiento", "perro.json")
ARMONICOS = 4
ARTICULACIONES = ("hombro", "codo", "carpo", "cadera", "rodilla", "tarso")


# ------------------------------------------------------------------ Fourier
def ajustar(fases, valores, k=ARMONICOS):
    """Mínimos cuadrados de una serie de Fourier periódica en la fase [0,1)."""
    f = np.asarray(fases, float) % 1.0
    A = [np.ones_like(f)]
    for n in range(1, k + 1):
        A += [np.cos(2 * np.pi * n * f), np.sin(2 * np.pi * n * f)]
    A = np.stack(A, 1)
    c, *_ = np.linalg.lstsq(A, np.asarray(valores, float), rcond=None)
    return [float(x) for x in c]


def evaluar(c, fase):
    f = np.asarray(fase, float) % 1.0
    y = np.full_like(f, c[0])
    for n in range(1, (len(c) - 1) // 2 + 1):
        y = y + c[2 * n - 1] * np.cos(2 * np.pi * n * f) + c[2 * n] * np.sin(2 * np.pi * n * f)
    return y


def normalizar(c, muestras=400):
    """Escala la curva a [-1, 1] (1 = máxima extensión) y devuelve (coeficientes, ROM relativo usado)."""
    y = evaluar(c, np.linspace(0, 1, muestras, endpoint=False))
    lo, hi = float(y.min()), float(y.max())
    m, s = (hi + lo) / 2, max(1e-9, (hi - lo) / 2)
    c2 = [(c[0] - m) / s] + [x / s for x in c[1:]]
    return c2, hi - lo


def interpolar_periodico(puntos, n=200):
    """Curva suave a partir de puntos clave (fase, valor) en un ciclo."""
    p = sorted(puntos)
    xs = np.array([a for a, _ in p] + [p[0][0] + 1])
    ys = np.array([b for _, b in p] + [p[0][1]])
    f = np.linspace(0, 1, n, endpoint=False)
    # interpolación coseno entre puntos clave (sin sobrepicos)
    out = np.empty(n)
    for i, x in enumerate(f):
        j = np.searchsorted(xs, x, side="right") - 1
        j = min(max(j, 0), len(xs) - 2)
        u = (x - xs[j]) / max(1e-9, xs[j + 1] - xs[j])
        u = (1 - math.cos(math.pi * u)) / 2
        out[i] = ys[j] * (1 - u) + ys[j + 1] * u
    return f, out


# ------------------------------------------------------------------ 1. literatura veterinaria
FUENTES = [
    "Fischer MS et al. 2018, Scientific Reports — cinemática 3D de los miembros posteriores de Beagle, Malinois, Whippet y Bulldog francés al paso y al trote (doi:10.1038/s41598-018-34310-0)",
    "Comparative kinematic gait analysis in young and old Beagle dogs, J Vet Sci 2017;18(4):521 — forma de las curvas de hombro, codo, carpo, cadera, rodilla y tarso al trote (1.8 m/s)",
    "Pelvic limb joint motion increases with treadmill walking speed in healthy Beagles, AJVR — el ROM crece 6.11° (cadera), 5.37° (rodilla) y 3.66° (tarso) por km/h",
    "Three-dimensional movements of the pelvis and the lumbar intervertebral joints in walking and trotting dogs, Vet J — roll ~13°/11°, yaw ~5°/6°, pitch ~8° bifásico",
    "Front Vet Sci 2021 (doi:10.3389/fvets.2021.709966) — duty factor ~0.7 al paso y ~0.4 al trote; beagle: rotación lateral de pelvis 4.6° paso / 5.8° trote",
    "Maes LD et al. 2008, J Exp Biol 211:138 — coordinación entre patas y efecto de la velocidad (paso lateral, trote, galopes)",
    "Rohwedder T, Biomechanics of the Canine Elbow Joint (IntechOpen) — ROM sagital del codo al trote 48–70°",
    "De Souza MC et al. 2021, Vet Med-Czech 66:8 — el carpo tiene el mayor ROM: ~80–90° al paso y ~110–120° al trote",
]

# Puntos clave de cada curva (fase desde el apoyo, valor: +1 = máxima extensión, -1 = máxima flexión).
# Trote: descripción del estudio de beagles jóvenes al trote (J Vet Sci 2017; apoyo ~50 % del ciclo). Paso y galope: misma forma con el apoyo desplazado.
CLAVES_TROTE = {
    "hombro": [(0.0, 0.6), (0.25, 0.1), (0.42, 0.25), (0.5, -1.0), (0.75, 0.35), (0.93, 0.85)],
    "codo": [(0.0, 0.3), (0.1, 0.0), (0.4, 0.85), (0.5, 0.45), (0.75, -1.0), (0.95, 0.6)],
    "carpo": [(0.0, 1.0), (0.3, 0.9), (0.45, 0.3), (0.53, -1.0), (0.75, -0.2), (0.92, 0.9)],
    "cadera": [(0.0, -0.55), (0.25, 0.2), (0.5, 1.0), (0.75, -0.3), (0.9, -0.9)],
    "rodilla": [(0.0, 0.4), (0.12, 0.0), (0.45, 0.8), (0.75, -1.0), (0.92, 0.6)],
    "tarso": [(0.0, 0.5), (0.12, 0.0), (0.5, 1.0), (0.7, -1.0), (0.9, 0.4)],
}
ROM = {  # grados (sagital) — paso / trote / galope (galope: estimado, la cadera supera los 60°)
    "caminar": {"hombro": 30, "codo": 55, "carpo": 85, "cadera": 40, "rodilla": 40, "tarso": 32},
    "trotar": {"hombro": 35, "codo": 62, "carpo": 112, "cadera": 44, "rodilla": 45, "tarso": 38},
    "galopar": {"hombro": 50, "codo": 75, "carpo": 125, "cadera": 62, "rodilla": 60, "tarso": 50},
}
POR_VELOCIDAD = {"cadera": 6.11 / 0.278, "rodilla": 5.37 / 0.278, "tarso": 3.66 / 0.278,
                 "hombro": 4.0 / 0.278, "codo": 4.5 / 0.278, "carpo": 5.0 / 0.278}  # °/(m/s); delanteras: estimado
TEMPO = {
    "caminar": {"duty": 0.65, "fase": {"Tra_L": 0.0, "Del_L": 0.25, "Tra_R": 0.5, "Del_R": 0.75}, "zancada": 1.15, "alza": 0.10,
                "pelvis": {"roll": [10.0, 1], "yaw": [4.6, 1], "pitch": [8.0, 2]}, "vel_ref": 0.6},
    "trotar": {"duty": 0.45, "fase": {"Del_L": 0.0, "Tra_R": 0.0, "Del_R": 0.5, "Tra_L": 0.5}, "zancada": 1.75, "alza": 0.16,
               "pelvis": {"roll": [9.0, 1], "yaw": [5.8, 1], "pitch": [8.0, 2]}, "vel_ref": 1.8},
    "galopar": {"duty": 0.3, "fase": {"Tra_L": 0.0, "Tra_R": 0.12, "Del_L": 0.48, "Del_R": 0.6}, "zancada": 2.8, "alza": 0.22,
                "pelvis": {"roll": [6.0, 1], "yaw": [4.0, 1], "pitch": [16.0, 1]}, "vel_ref": 4.0},
}


def remapear_apoyo(puntos, duty_origen, duty_destino):
    """Estira la parte de apoyo y comprime el vuelo (o al revés) para pasar de una marcha a otra."""
    out = []
    for f, v in puntos:
        if f < duty_origen:
            g = f / duty_origen * duty_destino
        else:
            g = duty_destino + (f - duty_origen) / (1 - duty_origen) * (1 - duty_destino)
        out.append((g, v))
    return out


def desde_literatura():
    M = {"version": 1, "especie": "perro", "convencion": "fase 0 = apoyo; +1 = extensión máxima; ROM en grados",
         "armonicos": ARMONICOS, "fuentes": FUENTES, "marchas": {}, "aprendido_de": ["literatura veterinaria"]}
    for marcha, t in TEMPO.items():
        arts = {}
        for a, pts in CLAVES_TROTE.items():
            p = remapear_apoyo(pts, 0.5, t["duty"]) if marcha != "trotar" else pts
            f, y = interpolar_periodico(p)
            c, _ = normalizar(ajustar(f, y))
            arts[a] = {"fourier": [round(x, 5) for x in c], "rom": ROM[marcha][a], "rom_por_mps": round(POR_VELOCIDAD[a], 2)}
        M["marchas"][marcha] = {**t, "articulaciones": arts}
    return M


# ------------------------------------------------------------------ 2. aprender de puntos de un video
def _angulo(a, b, c):
    """Ángulo interior en b (grados) entre los segmentos b→a y b→c."""
    v1, v2 = a - b, c - b
    cos = np.sum(v1 * v2, 1) / np.maximum(1e-9, np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1))
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def leer_csv(ruta):
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    P = {}
    for k in ("hombro", "codo", "carpo", "mano", "cadera", "rodilla", "tarso", "pie"):
        if f"{k}_x" in filas[0]:
            P[k] = np.array([[float(r[f"{k}_x"]), -float(r[f"{k}_y"])] for r in filas])  # y hacia arriba
    return P


def ciclos_por_apoyo(pie_y, pie_x, fps):
    """Detecta apoyos: la pata está abajo y casi quieta en horizontal. Devuelve índices de inicio de cada apoyo y duty."""
    y = pie_y - np.percentile(pie_y, 5)
    vx = np.abs(np.gradient(pie_x)) * fps
    abajo = (y < 0.25 * np.ptp(y)) & (vx < np.percentile(vx, 45))
    inicios = [i for i in range(1, len(abajo)) if abajo[i] and not abajo[i - 1]]
    duty = float(np.mean(abajo))
    return inicios, duty


def desde_keypoints(ruta_csv, fps, marcha, velocidad=None, peso=1.0, matriz=None):
    P = leer_csv(ruta_csv)
    angulos = {}
    if all(k in P for k in ("hombro", "codo", "carpo")):
        angulos["codo"] = _angulo(P["hombro"], P["codo"], P["carpo"])
    if all(k in P for k in ("codo", "carpo", "mano")):
        angulos["carpo"] = _angulo(P["codo"], P["carpo"], P["mano"])
    if all(k in P for k in ("cadera", "rodilla", "tarso")):
        angulos["rodilla"] = _angulo(P["cadera"], P["rodilla"], P["tarso"])
    if all(k in P for k in ("rodilla", "tarso", "pie")):
        angulos["tarso"] = _angulo(P["rodilla"], P["tarso"], P["pie"])
    # hombro y cadera: ángulo del segmento superior con la vertical (protracción/retracción)
    if all(k in P for k in ("hombro", "codo")):
        d = P["codo"] - P["hombro"]
        angulos["hombro"] = 180 - np.degrees(np.arctan2(d[:, 0], -d[:, 1]))
    if all(k in P for k in ("cadera", "rodilla")):
        d = P["rodilla"] - P["cadera"]
        angulos["cadera"] = 180 - np.degrees(np.arctan2(d[:, 0], -d[:, 1]))
    resultado = {}
    for grupo, pie in (("del", "mano"), ("tra", "pie")):
        if pie not in P:
            continue
        inicios, duty = ciclos_por_apoyo(P[pie][:, 1], P[pie][:, 0], fps)
        if len(inicios) < 2:
            continue
        arts = ("hombro", "codo", "carpo") if grupo == "del" else ("cadera", "rodilla", "tarso")
        frec = fps / np.mean(np.diff(inicios))
        for a in arts:
            if a not in angulos:
                continue
            fases, vals = [], []
            for i0, i1 in zip(inicios[:-1], inicios[1:]):
                n = i1 - i0
                fases += list(np.arange(n) / n)
                vals += list(angulos[a][i0:i1])
            c = ajustar(fases, vals)
            cn, rom = normalizar(c)
            resultado[a] = {"fourier": cn, "rom": rom}
        resultado[f"_duty_{grupo}"] = duty
        resultado[f"_frec_{grupo}"] = float(frec)
    M = matriz or (json.load(open(MATRIZ, encoding="utf-8")) if os.path.isfile(MATRIZ) else desde_literatura())
    m = M["marchas"].setdefault(marcha, {"articulaciones": {}})
    n_prev = float(m.get("_n_aprendido", 1.0))
    w = float(peso)
    for a, d in resultado.items():
        if a.startswith("_"):
            continue
        prev = m["articulaciones"].get(a)
        if prev:
            mezcla = [(p * n_prev + x * w) / (n_prev + w) for p, x in zip(prev["fourier"], d["fourier"])]
            prev["fourier"] = [round(x, 5) for x in normalizar(mezcla)[0]]
            prev["rom"] = round((prev["rom"] * n_prev + d["rom"] * w) / (n_prev + w), 2)
        else:
            m["articulaciones"][a] = {"fourier": [round(x, 5) for x in d["fourier"]], "rom": round(d["rom"], 2), "rom_por_mps": 0}
    duties = [resultado[k] for k in ("_duty_del", "_duty_tra") if k in resultado]
    if duties:
        m["duty"] = round((m.get("duty", np.mean(duties)) * n_prev + np.mean(duties) * w) / (n_prev + w), 3)
    m["_n_aprendido"] = n_prev + w
    M.setdefault("aprendido_de", []).append(f"video {os.path.basename(ruta_csv)} ({marcha}" + (f", {velocidad} m/s" if velocidad else "") + f", peso {w})")
    return M, resultado


def guardar(M, ruta=MATRIZ):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(M, f, ensure_ascii=False, indent=1)
    return ruta


# ------------------------------------------------------------------ 3. prueba con un "video" sintético
def prueba():
    """Genera puntos de un perro trotando con curvas conocidas y comprueba que el aprendizaje las recupera."""
    import tempfile
    M = desde_literatura()
    t = M["marchas"]["trotar"]
    fps, frec, n = 60, 2.4, 300
    filas = []
    for i in range(n):
        fase = (i / fps * frec) % 1.0
        ang = {a: 140 + d["rom"] / 2 * float(evaluar(d["fourier"], fase)) for a, d in t["articulaciones"].items()}
        # cadena delantera y trasera en 2D (x hacia delante), longitudes de un beagle en píxeles
        def cadena(base, a1, a2, a3, L=(120, 110, 50), signo=1):
            th = math.radians(180 - a1) * signo
            p1 = (base[0] + L[0] * math.sin(th), base[1] - L[0] * math.cos(th))
            th2 = th - signo * math.radians(180 - a2)
            p2 = (p1[0] + L[1] * math.sin(th2), p1[1] - L[1] * math.cos(th2))
            th3 = th2 + signo * math.radians(180 - a3)
            p3 = (p2[0] + L[2] * math.sin(th3), p2[1] - L[2] * math.cos(th3))
            return p1, p2, p3
        hombro = (400.0, 300.0)
        cadera = (150.0, 300.0)
        c1, c2, c3 = cadena(hombro, ang["hombro"], ang["codo"], ang["carpo"])
        r1, r2, r3 = cadena(cadera, ang["cadera"], ang["rodilla"], ang["tarso"], signo=-1)
        apoyo = fase < t["duty"]
        def ajuste_suelo(p):  # en apoyo la pata está abajo
            return p
        fila = {"frame": i}
        for k, p in (("hombro", hombro), ("codo", c1), ("carpo", c2), ("mano", c3), ("cadera", cadera), ("rodilla", r1), ("tarso", r2), ("pie", r3)):
            fila[f"{k}_x"], fila[f"{k}_y"] = p[0], -p[1]
        filas.append(fila)
    d = tempfile.mkdtemp()
    ruta = os.path.join(d, "puntos.csv")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    # aprender solo ángulos (los apoyos se marcan por fase conocida para aislar la parte de ángulos)
    P = leer_csv(ruta)
    ang = _angulo(P["hombro"], P["codo"], P["carpo"])
    fases = (np.arange(n) / fps * frec) % 1.0
    c, rom = normalizar(ajustar(fases, ang))
    ref = t["articulaciones"]["codo"]
    err = np.abs(evaluar(c, np.linspace(0, 1, 100)) - evaluar(ref["fourier"], np.linspace(0, 1, 100))).max()
    print(f"codo: ROM real {ref['rom']}° · aprendido {rom:.1f}° · error máx. de forma {err:.3f}")
    assert abs(rom - ref["rom"]) < 3 and err < 0.08, "el aprendizaje no recupera la curva"
    print("✔ el aprendizaje desde puntos recupera la curva de la articulación")


# ------------------------------------------------------------------ 4. matriz de pesos de movimiento (simétrica)
CANALES = ["respiracion", "ciclo_sin", "ciclo_cos", "ciclo2_sin", "ciclo2_cos", "mirada", "cola", "temblor"]
EJES = ["pitch", "yaw", "roll"]  # grados por unidad de canal; pitch = sube/baja, yaw = gira, roll = ladea


def _espejo(n):
    for a, b in (("_L", "_R"), ("_R", "_L")):
        if n.endswith(a):
            return n[: -len(a)] + b
    return n


def generar_pesos(M=None):
    """Matriz W[hueso][eje][canal]: cuánto responde cada hueso a cada señal del cuerpo.
    Se deriva de la matriz de movimiento (amplitudes reales de pelvis) y es simétrica por construcción:
    el hueso izquierdo es el reflejo del derecho (mismo pitch, yaw y roll con signo opuesto)."""
    M = M or (json.load(open(MATRIZ, encoding="utf-8")) if os.path.isfile(MATRIZ) else desde_literatura())
    C = {c: i for i, c in enumerate(CANALES)}
    def fila():
        return [[0.0] * len(CANALES) for _ in EJES]
    salida = {"canales": CANALES, "ejes": EJES, "convencion": "grados por unidad de canal; *_L es el espejo de *_R", "marchas": {}}
    modos = {"quieto": None, **{m: d for m, d in M["marchas"].items()}}
    for modo, d in modos.items():
        W = {}
        def pon(h, eje, canal, v):
            W.setdefault(h, fila())[EJES.index(eje)][C[canal]] += v
        if d is None:  # quieto: respiración repartida por la columna, mirada que recorre cuello y cabeza
            for h, v in (("espalda", 0.6), ("pecho", 0.8), ("cuello", -0.4)):
                pon(h, "pitch", "respiracion", v)
            for h, v in (("cuello2", 4.0), ("cabeza", 5.0)):
                pon(h, "yaw", "mirada", v)
            pon("cabeza", "roll", "mirada", 2.0)
            pon("oreja_R", "pitch", "respiracion", 0.6)
            pon("oreja_R", "roll", "mirada", 1.5)
            pon("cola_raiz", "yaw", "cola", 3.0)
        else:
            pv = d.get("pelvis", {"pitch": [8, 2], "yaw": [5, 1], "roll": [9, 1]})
            p, y, r = pv["pitch"][0] / 2, pv["yaw"][0] / 2, pv["roll"][0] / 2
            galope = modo == "galopar"
            # columna: contra-ondas que reparten el movimiento de la pelvis (la cabeza queda estable)
            pon("espalda", "pitch", "ciclo_sin" if galope else "ciclo2_sin", (0.5 if galope else -0.3) * p)
            pon("pecho", "pitch", "ciclo_sin" if galope else "ciclo2_sin", (-0.3 if galope else -0.4) * p)
            pon("cuello", "pitch", "ciclo2_sin", 0.5 * p)
            pon("cabeza", "pitch", "ciclo2_sin", 0.3 * p)
            pon("espalda", "yaw", "ciclo_sin", -0.5 * y)
            pon("pecho", "yaw", "ciclo_cos", 0.4 * y)
            pon("cabeza", "roll", "ciclo_sin", -0.5 * r)
            pon("cuello2", "yaw", "mirada", 1.5)
            # orejas: rebote vertical igual en las dos, balanceo lateral en espejo
            pon("oreja_R", "pitch", "ciclo2_cos", 6.0 if not galope else 10.0)
            pon("oreja_R", "roll", "ciclo_sin", 4.0)
            pon("cola_raiz", "yaw", "ciclo_sin", 6.0)
            pon("espalda", "pitch", "respiracion", 0.3)
        # temblor (todas las marchas): vibración pequeña en la columna y las orejas
        for h, v in (("pelvis", 0.8), ("espalda", 1.0), ("pecho", 1.0), ("cuello", 0.8)):
            pon(h, "roll", "temblor", v)
        pon("oreja_R", "roll", "temblor", 3.0)
        # simetría: cada hueso *_R genera su *_L con yaw y roll invertidos
        for h in [h for h in W if h.endswith("_R")]:
            W[_espejo(h)] = [W[h][0][:], [-x for x in W[h][1]], [-x for x in W[h][2]]]
        salida["marchas"][modo] = {h: [[round(x, 3) for x in eje] for eje in v] for h, v in sorted(W.items())}
    return salida


def comprobar_simetria(P):
    """Cada par izquierda/derecha debe ser espejo exacto (pitch igual; yaw y roll opuestos)."""
    for modo, W in P["marchas"].items():
        for h, v in W.items():
            if h.endswith("_R"):
                e = W[_espejo(h)]
                assert v[0] == e[0] and v[1] == [-x for x in e[1]] and v[2] == [-x for x in e[2]], (modo, h)
    return True


RUTA_PESOS = os.path.join(AQUI, "movimiento", "pesos_perro.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["literatura", "video", "prueba", "pesos"])
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--fps", type=float, default=60)
    ap.add_argument("--marcha", default="trotar", choices=list(TEMPO))
    ap.add_argument("--velocidad", type=float)
    ap.add_argument("--peso", type=float, default=1.0)
    a = ap.parse_args()
    if a.modo in ("literatura", "pesos"):
        if a.modo == "literatura":
            print("matriz:", guardar(desde_literatura()))
        P = generar_pesos()
        comprobar_simetria(P)
        print("pesos (simétricos):", guardar(P, RUTA_PESOS))
    elif a.modo == "video":
        M, r = desde_keypoints(a.csv, a.fps, a.marcha, a.velocidad, a.peso)
        print("aprendido:", {k: (round(v["rom"], 1) if isinstance(v, dict) else round(v, 3)) for k, v in r.items()})
        print("matriz:", guardar(M))
        P = generar_pesos(M)
        comprobar_simetria(P)
        print("pesos (simétricos):", guardar(P, RUTA_PESOS))
    else:
        prueba()
