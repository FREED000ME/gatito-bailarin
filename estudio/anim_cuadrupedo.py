"""Forja3D Cine · motor de animación procedural para cuadrúpedos (sin Blender: numpy puro).

Calcula, fotograma a fotograma, la rotación de cada hueso del esqueleto de rig_cuadrupedo:
  · marcha con patas plantadas (caminar, trote, galope) con los desfases de un perro real
    y cinemática inversa de 2 huesos por pata (las patas no patinan),
  · movimiento del cuerpo (rebote, cabeceo, balanceo), cabeza que compensa,
  · cola, orejas con física de resorte, respiración,
  · conductas: olfatear el suelo, levantar la cabeza, oler el aire, temblar, mover la cola, orejas atentas.

Salida por fotograma: posición y rumbo del animal + rotación (3x3, ejes de la armadura en reposo) de cada hueso,
y los contactos de las patas (para sincronizar pasos en el audio).
Convención: cabeza hacia +Y, arriba +Z. Girar sobre +X con ángulo positivo levanta lo que apunta a +Y.
"""
import json
import re
import math
import os

import numpy as np

PATAS = ("Del_R", "Del_L", "Tra_R", "Tra_L")

# Desfase de cada pata (fracción de ciclo) y fracción del ciclo apoyada (duty)
MARCHAS = {
    "caminar": {"fase": {"Tra_L": 0.0, "Del_L": 0.25, "Tra_R": 0.5, "Del_R": 0.75}, "duty": 0.62, "zancada": 1.1, "alza": 0.10, "rebote": 0.012, "cabeceo": 1.5},
    "trotar": {"fase": {"Del_L": 0.0, "Tra_R": 0.0, "Del_R": 0.5, "Tra_L": 0.5}, "duty": 0.45, "zancada": 1.7, "alza": 0.16, "rebote": 0.025, "cabeceo": 2.5},
    "galopar": {"fase": {"Tra_L": 0.0, "Tra_R": 0.12, "Del_L": 0.48, "Del_R": 0.6}, "duty": 0.3, "zancada": 2.8, "alza": 0.22, "rebote": 0.06, "cabeceo": 7.0},
}


def cargar_matriz(ruta=None):
    """Matriz de movimiento aprendida (aprender_movimiento.py). Sin ella se usan los valores de MARCHAS."""
    ruta = ruta or os.path.join(os.path.dirname(os.path.abspath(__file__)), "movimiento", "perro.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            M = json.load(f)
    except (OSError, ValueError):
        return None
    for marcha, m in (M.get("marchas") or {}).items():
        if marcha in MARCHAS:
            for k in ("duty", "fase", "zancada", "alza"):
                if k in m:
                    MARCHAS[marcha][k] = m[k]
    return M


def cargar_pesos(ruta=None):
    """Matriz de pesos de movimiento (aprender_movimiento.py pesos): W[marcha][hueso][eje][canal]."""
    ruta = ruta or os.path.join(os.path.dirname(os.path.abspath(__file__)), "movimiento", "pesos_perro.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def fourier(c, fase):
    y = c[0]
    for n in range(1, (len(c) - 1) // 2 + 1):
        y += c[2 * n - 1] * math.cos(2 * math.pi * n * fase) + c[2 * n] * math.sin(2 * math.pi * n * fase)
    return y


def rot(eje, ang):
    eje = np.asarray(eje, float)
    eje = eje / (np.linalg.norm(eje) or 1)
    K = np.array([[0, -eje[2], eje[1]], [eje[2], 0, -eje[0]], [-eje[1], eje[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def entre(a, b):
    """Rotación mínima que lleva la dirección a a la dirección b."""
    a = a / (np.linalg.norm(a) or 1)
    b = b / (np.linalg.norm(b) or 1)
    c = np.cross(a, b)
    s = np.linalg.norm(c)
    if s < 1e-8:
        return np.eye(3)
    return rot(c, math.atan2(s, float(a @ b)))


def suave(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


class Ruido:
    """Ruido suave determinista (suma de senos) para movimientos vivos sin repetirse."""

    def __init__(self, semilla):
        r = np.random.default_rng(semilla)
        self.f = r.uniform(0.15, 0.9, 4)
        self.p = r.uniform(0, 2 * math.pi, 4)

    def __call__(self, t, vel=1.0):
        return float(np.mean(np.sin(2 * math.pi * self.f * vel * t + self.p)))


def conductas(nota):
    """Lee la nota de la toma (español) y decide conductas."""
    n = (nota or "").lower()
    c = set()
    if "olfate" in n or ("huele" in n and "viento" not in n and "aire" not in n) or "nariz" in n:
        c.add("olfatear")
    if "levanta la cabeza" in n or "alza la cabeza" in n:
        c.add("levantar_cabeza")
    if ("huele" in n or "oler" in n) and ("viento" in n or "aire" in n):
        c.add("oler_aire")
    if "tiembl" in n or "mojado" in n:
        c.add("temblar")
    if "cola" in n and ("mueve" in n or "menea" in n):
        c.add("mover_cola")
    if "expresi" in n or "atent" in n or "orejas" in n or "reja" in n:
        c.add("atento")
    if "trota" in n:
        c.add("trotar")
    if re.search(r"\bcorr(e|er|iendo|en)\b", n):  # "correa" no cuenta
        c.add("correr")
    # emociones
    if "voltea" in n or "todos lados" in n or "alrededor" in n or "busca" in n:
        c.add("mirar_alrededor")
    if "perdid" in n or "asustad" in n or "miedo" in n or "triste" in n or "gime" in n:
        c.add("asustado")
    if "emocionad" in n or "alegr" in n or "feliz" in n or "contento" in n:
        c.add("emocionado")
    if "sacude" in n or "sacudi" in n:
        c.add("sacudirse")
    if "abraz" in n or "acurruc" in n or "caricia" in n:
        c.add("acurrucarse")
    if "empuja" in n and "hocico" in n:
        c.add("empujar")
    if re.search(r"\b(lame|lamer|lamiendo|lengüet\w*)\b", n) or ("limpia" in n and "lodo" in n):
        c.add("lamer")
    if re.search(r"\b(ladra|ladrar|ladrando|ladridos?)\b", n):
        c.add("ladrar")
    if re.search(r"\b(salta|saltar|saltando|saltitos|brinca|brincando|brincos)\b", n):
        c.update(("saltar", "emocionado"))
    # baile (gatito bailarín): rebote al ritmo, cadera, cabeceo y ladeo de cabeza, cola al compás
    if re.search(r"\b(baila|bailar|bailando|baile|ritmo|coreograf\w*)\b", n):
        c.add("bailar")
    if re.search(r"\b(patitas?|golpecitos|zapatea\w*|marca el paso)\b", n):
        c.add("patitas")
    if "dos patas" in n or "se para" in n or "patas arriba" in n or "se levanta" in n:
        c.add("pararse")
    if "agacha" in n or "sentadilla" in n:
        c.add("agacharse")
    return c


def ritmo(nota, defecto=2.0):
    """Pulsos por segundo del baile: '120 bpm' en la nota, o 2 Hz (120 bpm) por defecto."""
    m = re.search(r"(\d{2,3})\s*bpm", (nota or "").lower())
    return int(m.group(1)) / 60.0 if m else defecto


def ventana(tt, dur, entra=0.4, sale=0.6):
    """0 → 1 → 0 dentro de la toma: entra suave al principio y sale suave antes del final (para posturas)."""
    return suave((tt - 0.15) / entra) * (1 - suave((tt - (dur - sale - 0.1)) / sale))


def ladridos(dur):
    """Momentos (s desde el inicio de la toma) de cada ladrido: tandas de tres con una pausa. sonido.py usa los
    mismos tiempos (ladridos_en) para que el sonido caiga justo con el golpe de cabeza."""
    ts, t = [], 0.5
    while t < dur - 0.45:
        for k in range(3):
            if t + k * 0.36 < dur - 0.45:
                ts.append(round(t + k * 0.36, 3))
        t += 3 * 0.36 + 0.9
    return ts


def ladrido(tt, dur):
    """0..1: golpe de cada ladrido (sube rápido y baja), con un pequeño impulso previo hacia atrás (-)."""
    v = 0.0
    for tb in ladridos(dur):
        u = (tt - tb) / 0.26
        if -0.35 < u < 0:
            v = min(v, -0.25 * math.sin(math.pi * -u / 0.35))
        elif 0 <= u < 1:
            v = max(v, math.sin(math.pi * u) ** 0.7)
    return v


def salto(tt, dur, periodo=0.62):
    """Saltitos de felicidad: (altura 0..1, cabeceo -1..1) en el tiempo de la toma. Cada salto: se agacha,
    se impulsa con el pecho arriba, vuela y amortigua al caer. Empieza a 0.3 s y para 0.5 s antes del final."""
    t = tt - 0.3
    n = max(1, int((dur - 0.8) / periodo))
    if t < 0 or t >= n * periodo:
        return 0.0, 0.0, 0.0
    u = (t % periodo) / periodo
    if u < 0.22:  # se agacha
        return -0.35 * math.sin(math.pi * u / 0.22), -0.3 * math.sin(math.pi * u / 0.22), 0.0
    if u < 0.82:  # en el aire: parábola; el pecho sube al despegar y baja al caer
        v = (u - 0.22) / 0.6
        return 4 * v * (1 - v), math.cos(math.pi * v), 4 * v * (1 - v)
    w = (u - 0.82) / 0.18  # amortigua
    return -0.25 * math.sin(math.pi * w), -0.2 * math.sin(math.pi * w), 0.0


def mirada(tt, dur, semilla=7):
    """Ángulo (rad) de la mirada al buscar: giros rápidos a un lado y al otro con pausas (no una onda suave).
    Devuelve (yaw, pitch): sube un poco la cabeza cuando mira lejos."""
    r = np.random.default_rng(semilla + 101)
    puntos, t = [(0.0, 0.0, 0.0)], 0.25
    lado = 1 if r.random() < 0.5 else -1
    while t < dur + 2:
        lado = -lado if r.random() < 0.8 else lado
        yaw = lado * r.uniform(25, 50)  # más allá el cuello de la malla se abre
        puntos.append((t, yaw, r.uniform(4, 12)))
        t += r.uniform(0.7, 1.3)
    yaw = pitch = 0.0
    for (ta, ya, pa), (tb, yb, pb) in zip(puntos, puntos[1:]):
        if tt >= tb:
            yaw, pitch = yb, pb
            continue
        u = suave((tt - tb + 0.32) / 0.32)  # giro de 0.32 s al final del tramo
        yaw, pitch = ya + (yb - ya) * u, pa + (pb - pa) * u
        break
    return math.radians(yaw), math.radians(pitch)


# carne: (frecuencia propia en Hz, amortiguamiento ζ, ganancia de inercia)
CARNE = {
    "papada": (3.5, 0.25, 0.55),
    "belfo_R": (5.0, 0.22, 0.3),
    "belfo_L": (5.0, 0.22, 0.3),
    "vientre": (2.8, 0.3, 0.4),
    "piel_lomo": (4.5, 0.35, 0.5),
}


class Perro:
    def __init__(self, B, J, fps=24, semilla=7):
        self.B, self.J, self.fps = B, J, fps
        self.semilla = semilla
        self.H = max(p[2] for p in J.values())
        self.h_cadera = float(J["cadera"][2])
        self.padres = {n: B[n][2] for n in B}
        self.orden = self._orden()
        self.r = {k: Ruido(semilla * 31 + i) for i, k in enumerate(("cab_y", "cab_p", "cola", "resp", "orej", "temb1", "temb2", "temb3"))}
        self.oreja = {s: {"x": 0.0, "v": 0.0} for s in "RL"}
        self.M = cargar_matriz()
        self.P = cargar_pesos()
        self._cab_prev = None
        self._cab_vprev = None

    def _orden(self):
        hecho, orden = set(), []
        def visitar(n):
            if n in hecho:
                return
            p = self.padres[n]
            if p:
                visitar(p)
            hecho.add(n)
            orden.append(n)
        for n in self.B:
            visitar(n)
        return orden

    # ---------- cinemática directa con rotaciones "en ejes de reposo"
    def fk(self, R):
        G = {}
        for n in self.orden:
            h = self.B[n][0]
            Rl = R.get(n, np.eye(3))
            p = self.padres[n]
            if p is None:
                G[n] = (Rl, h - Rl @ h)
            else:
                Rp, tp = G[p]
                G[n] = (Rp @ Rl, Rp @ (h - Rl @ h) + tp)
        return G

    def pos(self, G, n, cola=False):
        Rg, t = G[n]
        return Rg @ (self.B[n][1] if cola else self.B[n][0]) + t

    # ---------- IK de 2 huesos (muslo + pierna) llevando el tobillo a un objetivo
    def ik_pata(self, R, G, pata, objetivo_tobillo, curva):
        b1, b2, b3, b4 = (f"{pata}_1", f"{pata}_2", f"{pata}_3", f"{pata}_pie")
        if b1 not in self.B or b2 not in self.B:
            return
        raiz = self.pos(G, b1)
        Rp = G[self.padres[b1]][0]
        a_rest, k_rest, t_rest = self.B[b1][0], self.B[b2][0], self.B[b2][1]
        l1 = np.linalg.norm(k_rest - a_rest)
        l2 = np.linalg.norm(t_rest - k_rest)
        d = objetivo_tobillo - raiz
        dist = min(np.linalg.norm(d), (l1 + l2) * 0.999)
        dist = max(dist, abs(l1 - l2) * 1.001 + 1e-6)
        dn = d / (np.linalg.norm(d) or 1)
        # plano de la pata: contiene la dirección al objetivo y el lado hacia el que dobla la rodilla en reposo
        rest_dir = (t_rest - a_rest) / np.linalg.norm(t_rest - a_rest)
        doblez = (k_rest - a_rest) - ((k_rest - a_rest) @ rest_dir) * rest_dir
        # la rodilla/codo dobla en el plano de la marcha (adelante-atrás), nunca hacia los lados: si el modelo
        # tiene la pata un poco abierta en reposo, el doblez lateral torcía la pata al bajar el pecho
        doblez = np.array([0.0, doblez[1], doblez[2]])
        if np.linalg.norm(doblez) < 0.004 * self.H:
            doblez = np.array([0.0, 1.0 if pata.startswith("Del") else -1.0, 0.0])
        doblez = Rp @ doblez
        lateral = np.array([1.0, 0, 0])
        polo = doblez - (doblez @ dn) * dn
        if np.linalg.norm(polo) < 1e-6:
            polo = np.cross(lateral, dn)
        polo /= np.linalg.norm(polo)
        cos_a = (l1 ** 2 + dist ** 2 - l2 ** 2) / (2 * l1 * dist)
        ang = math.acos(max(-1, min(1, cos_a)))
        rodilla = raiz + l1 * (math.cos(ang) * dn + math.sin(ang) * polo)
        tobillo = raiz + dist * dn
        # rotación global del muslo: de su dirección actual (por el padre) a la deseada
        dir1_now = Rp @ (k_rest - a_rest)
        R1g = entre(dir1_now, rodilla - raiz) @ Rp
        R[b1] = Rp.T @ R1g
        G1 = R1g
        dir2_now = G1 @ (t_rest - k_rest)
        R2g = entre(dir2_now, tobillo - rodilla) @ G1
        R[b2] = G1.T @ R2g
        # metacarpo y pata: compensan para quedar cerca de su ángulo de reposo respecto al suelo, más la curva al levantar
        if b3 in self.B:
            R3g = rot([1, 0, 0], -curva * 0.9)
            R[b3] = R2g.T @ R3g
            if b4 in self.B:
                R[b4] = rot([1, 0, 0], curva * 1.4)

    # ---------- un fotograma
    def fotograma(self, t, est):
        """est: dict con modo ('quieto'|marcha), vel (m/s), conductas, t_toma (s desde inicio de la toma), dur."""
        H = self.H
        R = {}
        modo = est.get("modo", "quieto")
        cond = est.get("conductas", set())
        if self._cab_prev is None:
            self._carne = {}  # corte de plano: la carne empieza en reposo
        tt, dur = est.get("t_toma", t), max(0.1, est.get("dur", 1.0))
        contactos = {}
        rumbo = 0.0
        # ---- cuerpo
        if modo in MARCHAS:
            m = MARCHAS[modo]
            S = m["zancada"] * self.h_cadera
            f = max(0.3, est.get("vel", 0.5) / S)
            ciclo = f * t
            reb = m["rebote"] * self.H * (math.cos(2 * math.pi * (2 if modo != "galopar" else 1) * ciclo))
            cab = math.radians(m["cabeceo"]) * math.sin(2 * math.pi * ciclo)
            roll = math.radians(2.0 if modo == "caminar" else 1.0) * math.sin(2 * math.pi * ciclo)
        else:
            S = f = ciclo = 0.0
            reb = cab = roll = 0.0
        resp = math.sin(2 * math.pi * (0.45 if modo == "quieto" else 1.2) * t)
        yaw = 0.0
        mm = (self.M or {}).get("marchas", {}).get(modo) if modo in MARCHAS else None
        if mm and "pelvis" in mm:
            pv = mm["pelvis"]
            fase_p = 2 * math.pi * ciclo
            roll = math.radians(pv["roll"][0] / 2) * math.sin(pv["roll"][1] * fase_p)
            yaw = math.radians(pv["yaw"][0] / 2) * math.sin(pv["yaw"][1] * fase_p + math.pi / 2)
            cab = math.radians(pv["pitch"][0] / 2) * math.sin(pv["pitch"][1] * fase_p) + cab * 0.3
        aire = 0.0
        if "ladrar" in cond:  # el cuerpo acompaña cada ladrido con un pequeño empujón de las patas delanteras
            reb += 0.012 * self.H * max(0.0, ladrido(tt, dur))
        if "saltar" in cond:
            alto_s, cabeceo_s, aire = salto(tt, dur)
            reb += (0.2 if alto_s > 0 else 0.12) * self.H * alto_s
            cab += math.radians(14) * cabeceo_s  # positivo en X: el pecho arriba
        self._aire = aire
        # ---- baile: todo sale de la misma fase de pulso para que cuerpo, cabeza, patas y cola caigan en el tiempo
        bpm = est.get("pulso", 2.0)
        fb = 2 * math.pi * bpm * tt
        bailar, parar, agachar = "bailar" in cond, 0.0, 0.0
        if bailar:
            reb -= 0.03 * self.H * (0.5 - 0.5 * math.cos(fb))          # flexiona en cada tiempo (el "groove")
            roll += math.radians(7) * math.sin(fb / 2)                 # cadera de lado a lado cada dos tiempos
            yaw += math.radians(5) * math.sin(fb / 2 + 0.6)
        if "agacharse" in cond:
            agachar = ventana(tt, dur)
            reb -= 0.16 * self.h_cadera * agachar                     # se agacha con las patas abiertas
        if "pararse" in cond:
            parar = ventana(tt, dur, 0.5, 0.6)
            cab += math.radians(24) * parar                            # el pecho sube: se apoya en las patas de atrás
            reb += 0.06 * self.H * parar
        self._baile = (bailar, "patitas" in cond, parar, fb)
        R["pelvis"] = rot([0, 1, 0], roll) @ rot([0, 0, 1], yaw) @ rot([1, 0, 0], cab * 0.5)
        if "espalda" in self.B:
            flex = math.radians(6) * math.sin(2 * math.pi * ciclo + 0.6) if modo == "galopar" else 0.0
            R["espalda"] = rot([1, 0, 0], flex + (0.0 if self.P else math.radians(0.6) * resp))
        if "pecho" in self.B:
            R["pecho"] = rot([1, 0, 0], -cab * 0.6 + (0.0 if self.P else math.radians(0.8) * resp))
        # ---- temblor
        if "temblar" in cond:
            for i, n in enumerate(("pelvis", "espalda", "pecho", "cuello")):
                if n in self.B:
                    a = math.radians(1.6) * math.sin(2 * math.pi * 13 * t + i * 1.7) * (0.6 + 0.4 * self.r["temb1"](t, 3))
                    R[n] = rot([0, 1, 0], a) @ R.get(n, np.eye(3))
        # ---- cuello y cabeza
        cuello_p = -cab * 0.8
        cabeza_p = 0.0
        cabeza_y = math.radians(8) * self.r["cab_y"](t, 0.6) if modo == "quieto" else math.radians(3) * self.r["cab_y"](t)
        if "olfatear" in cond:
            baja = suave(tt / 0.6)
            if "levantar_cabeza" in cond:
                baja *= 1 - suave((tt - dur * 0.45) / 0.5)
            # el giro se reparte por toda la columna del cuello: con casi todo en la base, la piel del pecho y
            # la garganta se aplastaba contra las patas
            cuello_p += -math.radians(52) * baja
            cabeza_p += -math.radians(32) * baja + math.radians(3) * math.sin(2 * math.pi * 6 * t) * baja
            cabeza_y = cabeza_y * (1 - baja) + math.radians(14) * self.r["cab_p"](t, 1.5) * baja
        if "levantar_cabeza" in cond and "olfatear" not in cond:
            sube = suave((tt - dur * 0.3) / 0.5)
            cuello_p += math.radians(12) * sube
            cabeza_p += math.radians(10) * sube
        if "oler_aire" in cond:
            sube = suave(tt / 0.8)
            cuello_p += math.radians(18) * sube
            cabeza_p += math.radians(16) * sube + math.radians(2.5) * math.sin(2 * math.pi * 5 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 0.7 * t))
        if "atento" in cond:
            cabeza_y += math.radians(10) * math.sin(math.pi * suave(tt / dur))
        giro = 0.0
        if "mirar_alrededor" in cond:
            gy, gp = mirada(tt, dur, getattr(self, "semilla", 7))
            cabeza_y = 0.55 * gy + 0.15 * cabeza_y
            giro = 0.45 * gy  # el resto del giro, repartido entre cuello y pecho (ninguna articulación pasa de ~16°)
            cuello_p += gp * 0.6
            cabeza_p += gp * 0.4
        if "asustado" in cond:
            cuello_p -= math.radians(14)  # cabeza gacha
            cabeza_p -= math.radians(6)
        if "acurrucarse" in cond:  # se apoya en ella: levanta el hocico hacia su cara y frota la cabeza despacio
            ac = suave((tt - 0.4) / 0.8)
            cuello_p += math.radians(16) * ac
            cabeza_p += math.radians(12) * ac
            cabeza_y = cabeza_y * (1 - ac) + math.radians(9) * math.sin(2 * math.pi * 0.45 * tt) * ac
        if "lamer" in cond:  # baja la cabeza hacia el pájaro y lo lame a lengüetazos: cabeceos cortos con pausas
            baja_l = suave(tt / 0.7)
            lengua = max(0.0, math.sin(2 * math.pi * 2.3 * tt)) * (0.5 + 0.5 * (math.sin(2 * math.pi * 0.35 * tt) > -0.4))
            cuello_p += -math.radians(54) * baja_l
            cabeza_p += -math.radians(26) * baja_l + math.radians(12) * lengua * baja_l
            cabeza_y = cabeza_y * (1 - baja_l) + math.radians(10) * math.sin(2 * math.pi * 0.3 * tt) * baja_l
        lad = 0.0
        if "ladrar" in cond:  # cada ladrido: la cabeza da un golpe hacia arriba y adelante y el pecho empuja el aire
            lad = ladrido(tt, dur)
            cuello_p += math.radians(10) * lad
            cabeza_p += math.radians(16) * lad
            if "pecho" in self.B:
                R["pecho"] = rot([1, 0, 0], math.radians(4) * lad) @ R["pecho"]
        if "saltar" in cond:  # mira arriba, hacia el pájaro que se va
            cabeza_p += math.radians(10)
        if bailar:  # cabeceo en cada tiempo (adelante en el golpe) y la cabeza compensa el balanceo de la cadera
            golpe = max(0.0, math.sin(fb)) ** 1.5
            cabeza_p += -math.radians(9) * golpe + math.radians(3)
            cuello_p += math.radians(4) * math.sin(fb)
            cabeza_y += math.radians(6) * math.sin(fb / 2 + math.pi)
        if parar:  # al pararse mira al frente (no al techo): el cuello compensa la subida del pecho
            cuello_p -= math.radians(16) * parar
            cabeza_p -= math.radians(6) * parar
        if "empujar" in cond:  # empuja con el hocico al principio de la toma
            emp = math.sin(math.pi * min(1.0, tt / 0.45))
            cuello_p += -math.radians(10) * emp
            cabeza_p += math.radians(14) * emp
        sac = 0.0
        if "sacudirse" in cond:  # sacudida para quitarse el agua: onda de giro de la cabeza a la cola, 1 s a ~4.5 Hz
            t_s = tt - dur * 0.55
            if 0 <= t_s <= 1.1:
                sac = math.sin(math.pi * t_s / 1.1)
        self._sac = (sac, tt)
        if giro and "pecho" in self.B:
            R["pecho"] = rot([0, 0, 1], giro * 0.5) @ R["pecho"]
        if "olfatear" in cond and "pecho" in self.B:
            R["pecho"] = rot([1, 0, 0], -math.radians(16) * baja) @ R["pecho"]  # baja el pecho: los codos se doblan
        elif "lamer" in cond and "pecho" in self.B:
            R["pecho"] = rot([1, 0, 0], -math.radians(16) * baja_l) @ R["pecho"]
        if "cuello" in self.B:
            R["cuello"] = rot([0, 0, 1], giro * 0.5) @ rot([1, 0, 0], cuello_p * 0.42) @ R.get("cuello", np.eye(3))
        if "cuello2" in self.B:
            R["cuello2"] = rot([0, 0, 1], cabeza_y * 0.4) @ rot([1, 0, 0], cuello_p * 0.58)
        if "cabeza" in self.B:
            ladeo = math.radians(9) * math.sin(math.pi * suave((tt - 0.5) / max(0.5, dur - 1))) if "atento" in cond else 0.0
            if "emocionado" in cond:  # ladea la cabeza de un lado a otro, como preguntando
                ladeo += math.radians(12) * math.sin(2 * math.pi * 0.6 * tt)
            if bailar:  # ladeo tierno al compás, contrario a la cadera
                ladeo += math.radians(11) * math.sin(fb / 2 + math.pi)
            R["cabeza"] = rot([0, 0, 1], cabeza_y * 0.6) @ rot([0, 1, 0], ladeo) @ rot([1, 0, 0], cabeza_p)
        if sac:
            for i, n in enumerate(("cabeza", "cuello2", "cuello", "pecho", "espalda", "pelvis")):
                if n in self.B:
                    amp = math.radians((38, 26, 22, 18, 12, 7)[i]) * sac
                    R[n] = rot([0, 1, 0], amp * math.sin(2 * math.pi * 4.6 * tt - i * 0.55)) @ R.get(n, np.eye(3))
        # ---- cola
        cola_n = [n for n in self.orden if n.startswith("cola") and n != "cola_raiz"]
        if modo == "galopar":
            amp, frec, alza = 10, max(1.0, f), 12
        elif modo in MARCHAS:
            amp, frec, alza = 14, f, 8
        else:
            amp, frec, alza = 6, 0.5, 0
        if "lamer" in cond:
            amp, frec, alza = 26, 2.6, 4  # meneo suave mientras lo cuida
        if "mover_cola" in cond or "emocionado" in cond:
            amp, frec, alza = 38, 3.6 if "emocionado" not in cond else 4.4, 18 if "emocionado" not in cond else 28
        if "acurrucarse" in cond:
            amp, frec, alza = 30, 2.2, 6  # meneo amplio y lento, cola a media altura: cariño
        if "temblar" in cond:
            amp, alza = 4, -14  # cola baja: frío y miedo
        if "asustado" in cond:
            amp, frec, alza = 3, 0.4, -60  # cola metida entre las patas
        if bailar:
            amp, frec, alza = 28, bpm / 2, 22  # la cola marca el compás, alta y contenta
        if sac:
            amp, frec = 25 * sac + amp, 4.6
        if "atento" in cond or "oler_aire" in cond:
            alza += 10
        alza = -alza  # en los ejes de reposo del rig, el giro positivo en X baja la cola (comprobado en la malla de Milo)
        if "cola_raiz" in self.B:
            R["cola_raiz"] = rot([1, 0, 0], math.radians(alza) * 0.5)
        if ("mover_cola" in cond or "emocionado" in cond) and modo == "quieto":
            # un contoneo mínimo de la cadera a media frecuencia (más se ve como si se moviera todo el cuerpo)
            R["pelvis"] = rot([0, 0, 1], math.radians(1.2) * math.sin(math.pi * frec * t + math.pi)) @ R["pelvis"]
        for i, n in enumerate(cola_n):
            fase = 2 * math.pi * frec * t - i * 0.55
            R[n] = rot([0, 0, 1], math.radians(amp) * math.sin(fase) * (0.6 + 0.15 * i)) @ rot([1, 0, 0], math.radians(alza) * 0.15)
        # ---- matriz de pesos: capa secundaria simétrica (respiración, ondas de columna, cabeza estable, orejas, cola)
        W = ((self.P or {}).get("marchas") or {}).get(modo if modo in MARCHAS else "quieto")
        if W:
            canal = [resp, math.sin(2 * math.pi * ciclo), math.cos(2 * math.pi * ciclo),
                     math.sin(4 * math.pi * ciclo), math.cos(4 * math.pi * ciclo),
                     self.r["cab_y"](t, 0.6), math.sin(2 * math.pi * 0.8 * t),
                     math.sin(2 * math.pi * 13 * t) if "temblar" in cond else 0.0]
            for h, ejes in W.items():
                if h not in self.B or h.startswith("oreja"):
                    continue  # las orejas las mueve el resorte (más abajo), que también usa sus pesos
                p_, y_, r_ = (math.radians(sum(a * b for a, b in zip(fila, canal))) for fila in ejes)
                R[h] = R.get(h, np.eye(3)) @ rot([0, 0, 1], y_) @ rot([0, 1, 0], r_) @ rot([1, 0, 0], p_)
            self._canal_orejas = {h: tuple(math.radians(sum(a * b for a, b in zip(fila, canal))) for fila in ejes)
                                  for h, ejes in W.items() if h.startswith("oreja")}
        else:
            self._canal_orejas = {}
        # ---- patas
        G = self.fk(R)
        for p in PATAS:
            if f"{p}_pata" not in self.J:
                continue
            reposo_pie = np.array(self.J[f"{p}_pata"], float)
            reposo_tob = np.array(self.J[f"{p}_tobillo"], float)
            curva = 0.0
            if modo in MARCHAS:
                m = MARCHAS[modo]
                duty = m["duty"]
                phi = (ciclo + m["fase"][p]) % 1.0
                if phi < duty:
                    u = phi / duty
                    dy = duty * S / 2 - u * duty * S
                    dz = 0.0
                    contactos[p] = u < (1.0 / max(1, self.fps * duty / max(f, 1e-3)))  # primer fotograma del apoyo
                else:
                    u = (phi - duty) / (1 - duty)
                    dy = -duty * S / 2 + (0.5 - 0.5 * math.cos(math.pi * u)) * duty * S
                    dz = m["alza"] * self.h_cadera * math.sin(math.pi * u)
                    curva = math.radians(55 if p.startswith("Del") else 40) * math.sin(math.pi * u)
                art = (mm or {}).get("articulaciones", {}).get("carpo" if p.startswith("Del") else "tarso")
                if art:
                    rom = art["rom"] + art.get("rom_por_mps", 0) * (est.get("vel", 0.5) - mm.get("vel_ref", est.get("vel", 0.5)))
                    rom = max(0.5 * art["rom"], min(1.4 * art["rom"], rom))
                    # flexión desde la extensión máxima; se escala para que la malla de un modelo genérico no se rompa
                    curva = math.radians(rom / 2) * (1 - fourier(art["fourier"], phi)) * (0.55 if p.startswith("Del") else 0.45)
                pie = reposo_pie + np.array([0, dy, dz])
            else:
                pie = reposo_pie.copy()
                if "temblar" in cond:
                    curva = math.radians(2) * math.sin(2 * math.pi * 13 * t)
                bai, pat, par, fbp = getattr(self, "_baile", (False, False, 0.0, 0.0))
                delantera = p.startswith("Del")
                if delantera and pat:
                    # golpecitos alternos: una patita sube en un tiempo y la otra en el siguiente
                    fase_p = fbp / 2 + (0.0 if p.endswith("_R") else math.pi)
                    sube = max(0.0, math.sin(fase_p)) ** 2
                    pie = pie + np.array([0, 0.04 * self.h_cadera * sube, 0.16 * self.h_cadera * sube])
                    curva = math.radians(40) * sube
                if delantera and par:
                    # parado en dos patas: las delanteras suben y se recogen delante del pecho, agitándose
                    agita = 0.25 * math.sin(fbp * 2 + (0 if p.endswith("_R") else 1.3)) if bai else 0.0
                    pie = pie + np.array([0, 0.08 * self.h_cadera, (0.55 + 0.08 * agita) * self.h_cadera]) * par
                    curva = max(curva, math.radians(60) * par)
                if not delantera and par:
                    pie = pie + np.array([0, 0.05 * self.h_cadera, 0]) * par  # las de atrás se adelantan para equilibrar
            objetivo = pie + rot([1, 0, 0], -curva * 0.6) @ (reposo_tob - reposo_pie)
            # la pelvis sube/baja con el rebote: el objetivo está en el suelo del mundo, así que se compensa
            objetivo = objetivo - np.array([0, 0, reb])
            if getattr(self, "_aire", 0.0) > 0:  # en el aire las patas suben con el cuerpo y se recogen
                objetivo = objetivo + np.array([0, 0, reb * 0.92]) + np.array([0, 0.05 if p.startswith("Del") else -0.03, 0.04]) * self.H * self._aire
                curva = curva + math.radians(45 if p.startswith("Del") else 25) * self._aire
            self.ik_pata(R, G, p, objetivo, curva)
        # ---- orejas: resorte amortiguado excitado por la aceleración vertical y el giro de la cabeza
        G = self.fk(R)
        if "cabeza" in self.B:
            cab_pos = self.pos(G, "cabeza", cola=True) + np.array([0, 0, reb])
            dt = 1.0 / self.fps
            if self._cab_prev is None:
                self._cab_prev, self._cab_vprev = cab_pos, np.zeros(3)
            vel = (cab_pos - self._cab_prev) / dt
            acc = (vel - self._cab_vprev) / dt
            self._cab_prev, self._cab_vprev = cab_pos, vel
            for s in "RL":
                n = f"oreja_{s}"
                if n not in self.B:
                    continue
                o = self.oreja[s]
                k, c = 180.0, 9.0
                fuerza = -k * o["x"] - c * o["v"] + 0.9 * acc[2] / max(self.H, 1e-3) * 0.05 + 0.4 * acc[1] / max(self.H, 1e-3) * 0.05
                o["v"] += fuerza * dt
                o["x"] += o["v"] * dt
                o["x"] = max(-0.9, min(0.9, o["x"]))
                lado = 1 if s == "R" else -1
                atento = math.radians(-14) if ("atento" in cond or "oler_aire" in cond or "emocionado" in cond) else 0.0
                if "asustado" in cond:
                    atento = math.radians(18)  # orejas hacia atrás, pegadas
                if "acurrucarse" in cond:
                    atento = math.radians(10)  # orejas relajadas hacia atrás: contento y tranquilo
                if "mirar_alrededor" in cond:  # se levantan un instante en cada giro de la cabeza
                    gy1, _ = mirada(tt + 0.08, dur, getattr(self, "semilla", 7))
                    gy0, _ = mirada(tt - 0.08, dur, getattr(self, "semilla", 7))
                    atento -= min(math.radians(16), abs(gy1 - gy0) * 0.9)
                temb = math.radians(5) * math.sin(2 * math.pi * 13 * t + (0 if s == "R" else 1)) if "temblar" in cond else 0.0
                pw, yw, rw = self._canal_orejas.get(n, (0.0, 0.0, 0.0))
                R[n] = rot([0, 0, 1], yw) @ rot([0, 1, 0], lado * (o["x"] * 0.8 + temb) + rw) @ rot([1, 0, 0], o["x"] * 0.5 + atento + pw)
        self._fisica_carne(R, reb, cond, t)
        return {"R": R, "rebote": reb, "contactos": [p for p, c in contactos.items() if c]}

    # ---------- carne: papada, belfos, vientre y piel del lomo con resorte amortiguado (física "de tela" barata)
    def _fisica_carne(self, R, reb, cond, t):
        huesos = [n for n in CARNE if n in self.B]
        if not huesos:
            return
        G = self.fk(R)
        dt = 1.0 / self.fps
        est = getattr(self, "_carne", None)
        if est is None:
            est = self._carne = {}
        for n in huesos:
            hz, zeta, ganancia = CARNE[n]
            p = self.padres[n]
            Rp = G[p][0]
            enganche = self.pos(G, n) + np.array([0, 0, reb])
            d = Rp @ (self.B[n][1] - self.B[n][0])
            e = est.get(n)
            if e is None:
                e = est[n] = {"x": np.zeros(3), "v": np.zeros(3), "p": enganche, "vp": np.zeros(3)}
            vel = (enganche - e["p"]) / dt
            acc = 0.5 * ((vel - e["vp"]) / dt + e.get("ap", np.zeros(3)))  # suavizada: sin tirones de un fotograma
            e["p"], e["vp"], e["ap"] = enganche, vel, acc
            w = 2 * math.pi * hz
            empuje = -ganancia * acc
            if "temblar" in cond:  # el temblor sacude la carne a 13 Hz
                empuje = empuje + np.array([0, 0, 0.25]) * math.sin(2 * math.pi * 13 * t + len(n))
            # Euler semi-implícito: estable con dt de 1/24 s para estas frecuencias
            e["v"] = e["v"] + (empuje - w * w * e["x"] - 2 * zeta * w * e["v"]) * dt
            e["x"] = e["x"] + e["v"] * dt
            lim = 0.2 * float(np.linalg.norm(d))  # ~11° como máximo
            mag = float(np.linalg.norm(e["x"]))
            if mag > lim:
                e["x"] *= lim / mag
                e["v"] *= 0.5
            S = entre(d, d + e["x"])
            R[n] = Rp.T @ S @ Rp


def plan_toma(B, J, acciones_milo, f0, f1, fps, nota):
    """Para una toma: modo de marcha, velocidad y conductas."""
    cond = conductas(nota)
    pulso = ritmo(nota)
    desde = np.array((acciones_milo.get("desde") or (0, 0))[:2], float)
    hasta = np.array((acciones_milo.get("hasta") or acciones_milo.get("desde") or (0, 0))[:2], float)
    dur = max(1, f1 - f0) / fps
    dist = float(np.linalg.norm(hasta - desde))
    vel = dist / dur
    h = float(J["cadera"][2])
    if dist < 0.05:
        modo = "quieto"
    else:
        rel = vel / max(h, 1e-3)  # velocidad en "alturas de cadera por segundo"
        clip = (acciones_milo.get("clip") or "").lower()
        if "correr" in clip or "correr" in cond or rel > 5.5:
            modo = "galopar" if rel > 4.0 else "trotar"
        elif "trotar" in cond or rel > 2.4:
            modo = "trotar"
        else:
            modo = "caminar"
    return {"modo": modo, "vel": vel, "conductas": cond, "dur": dur, "desde": desde, "hasta": hasta, "pulso": pulso}
