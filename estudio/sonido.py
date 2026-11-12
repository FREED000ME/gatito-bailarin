"""Forja3D Cine · sonido procedural (solo biblioteca estándar: funciona en el PC y en Vast sin instalar nada).

Genera en salida/audio/:
  amb_<escena>.wav  ambiente de cada escena según el guion (lluvia, viento, pájaros, grillos, pueblo...)
  foley.wav         pasos sincronizados con la animación (eventos.json), olfateos, jadeos, pasos de personas
  musica.wav        piano suave procedural (si el guion no trae música propia)
Todo es mono a 24 kHz; montar.py lo pasa a estéreo 48 kHz y normaliza a -14 LUFS.
"""
import json
import math
import re
import os
import random
import struct
import wave
from array import array

SR = 24000


# ------------------------------------------------------------------ utilidades
def ceros(n):
    return array("f", bytes(4 * n))


def guardar(ruta, x, ganancia=1.0):
    pico = max(1e-6, max((abs(v) for v in x), default=1e-6))
    k = ganancia / pico  # cada capa se normaliza a su pico; los niveles relativos los pone montar.py
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with wave.open(ruta, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        datos = array("h", (max(-32767, min(32767, int(v * k * 32767))) for v in x))
        w.writeframes(datos.tobytes())
    return ruta


def sumar(dest, src, pos, g=1.0):
    n = min(len(src), len(dest) - pos)
    if pos < 0 or n <= 0:
        return
    for i in range(n):
        dest[pos + i] += src[i] * g


def lp(x, fc):
    a = 1 - math.exp(-2 * math.pi * fc / SR)
    y, s = array("f", x), 0.0
    for i, v in enumerate(x):
        s += a * (v - s)
        y[i] = s
    return y


def hp(x, fc):
    a = 1 - math.exp(-2 * math.pi * fc / SR)
    y, s = array("f", x), 0.0
    for i, v in enumerate(x):
        s += a * (v - s)
        y[i] = v - s
    return y


def ruido(n, r):
    g = r.random
    return array("f", (g() * 2 - 1 for _ in range(n)))


def env_exp(n, tau_s, ataque_s=0.002):
    a = max(1, int(ataque_s * SR))
    t = tau_s * SR
    return array("f", ((i / a) if i < a else math.exp(-(i - a) / t) for i in range(n)))


def mult(x, e):
    return array("f", (a * b for a, b in zip(x, e)))


# ------------------------------------------------------------------ capas de ambiente
def lluvia(n, r, fuerza=1.0):
    base = hp(lp(ruido(n, r), 5500), 350)
    mod = lp(ruido(n, r), 0.6)
    pico = max(1e-6, max(abs(v) for v in mod))
    x = array("f", (b * 0.22 * fuerza * (0.8 + 0.6 * m / pico) for b, m in zip(base, mod)))
    # gotas: golpecitos agudos sobre hojas y charcos
    gotas = int(n / SR * 140 * fuerza)
    for _ in range(gotas):
        f = r.uniform(1800, 5200)
        L = int(SR * r.uniform(0.004, 0.012))
        g = r.uniform(0.04, 0.16) * fuerza
        burst = array("f", (math.sin(2 * math.pi * f * i / SR) * math.exp(-i / (L / 3)) for i in range(L)))
        sumar(x, burst, r.randrange(0, max(1, n - L)), g)
    return x


def viento(n, r, fuerza=1.0):
    x = lp(ruido(n, r), 420)
    x = lp(x, 900)
    ola = lp(ruido(n, r), 0.25)
    pico = max(1e-6, max(abs(v) for v in ola))
    return array("f", (v * 0.9 * fuerza * (0.35 + 0.65 * abs(o) / pico) for v, o in zip(x, ola)))


def pajaros(n, r, densidad=1.0):
    x = ceros(n)
    t = r.uniform(0.2, 1.0)
    especies = [(r.uniform(2800, 3600), r.uniform(1.2, 1.6)), (r.uniform(3800, 4600), r.uniform(0.7, 0.9)), (r.uniform(2200, 2800), r.uniform(1.0, 1.3))]
    while t < n / SR - 0.5:
        f0, k = r.choice(especies)
        for s in range(r.randint(2, 6)):
            L = int(SR * r.uniform(0.05, 0.13))
            fase = 0.0
            sil = array("f", bytes(4 * L))
            for i in range(L):
                u = i / L
                f = f0 * (1 + (k - 1) * u) if s % 2 == 0 else f0 * (k - (k - 1) * u)
                fase += 2 * math.pi * f / SR
                sil[i] = math.sin(fase) * math.sin(math.pi * u) ** 2
            sumar(x, sil, int((t + s * r.uniform(0.09, 0.16)) * SR), r.uniform(0.05, 0.12) * densidad)
        t += r.uniform(1.2, 4.5) / max(0.3, densidad)
    return x


def grillos(n, r):
    x = ceros(n)
    for c in range(3):
        f = r.uniform(4200, 4900)
        g = r.uniform(0.04, 0.09)
        t = r.uniform(0, 0.5)
        pulso = array("f", (math.sin(2 * math.pi * f * i / SR) * math.sin(math.pi * i / (SR * 0.012)) for i in range(int(SR * 0.012))))
        while t < n / SR:
            for p in range(r.randint(3, 5)):
                sumar(x, pulso, int((t + p * 0.022) * SR), g)
            t += r.uniform(0.32, 0.5)
    return x


def campana(n, r, f0=420.0):
    L = min(n, int(SR * 4))
    parc = [(1.0, 1.0, 2.5), (2.0, 0.5, 1.8), (2.76, 0.35, 1.2), (5.4, 0.2, 0.6)]
    return array("f", (sum(a * math.sin(2 * math.pi * f0 * k * i / SR) * math.exp(-i / (SR * d)) for k, a, d in parc) * 0.25 for i in range(L)))


def pueblo(n, r):
    x = lp(hp(lp(ruido(n, r), 900), 180), 700)
    x = array("f", (v * 0.5 for v in x))
    sumar(x, pajaros(n, r, 0.5), 0, 0.7)
    if n > SR * 3:
        sumar(x, campana(n, r), int(r.uniform(0.5, 2.5) * SR), 0.35)
    return x


# ------------------------------------------------------------------ foley
def paso(superficie, r, fuerza=1.0):
    if superficie == "piedra":
        L = int(SR * 0.05)
        clic = mult(hp(ruido(L, r), 1600), env_exp(L, 0.006))
        golpe = array("f", (math.sin(2 * math.pi * 140 * i / SR) * math.exp(-i / (SR * 0.012)) for i in range(L)))
        return array("f", ((a * 0.55 + b * 0.35) * fuerza for a, b in zip(clic, golpe)))
    if superficie == "barro":
        L = int(SR * 0.11)
        x = mult(lp(ruido(L, r), 1300), env_exp(L, 0.035, 0.008))
        chap = mult(hp(ruido(L, r), 2500), env_exp(L, 0.02, 0.02))
        return array("f", ((a * 0.8 + b * 0.25) * fuerza for a, b in zip(x, chap)))
    if superficie == "tierra":
        L = int(SR * 0.07)
        return array("f", (v * 0.7 * fuerza for v in mult(lp(ruido(L, r), 1800), env_exp(L, 0.02, 0.004))))
    L = int(SR * 0.09)  # pasto: roce suave
    return array("f", (v * 0.55 * fuerza for v in mult(hp(lp(ruido(L, r), 4500), 600), env_exp(L, 0.03, 0.01))))


def zapato(superficie, r):
    L = int(SR * 0.09)
    talon = mult(hp(ruido(L, r), 900 if superficie == "piedra" else 300), env_exp(L, 0.01))
    cuerpo = array("f", (math.sin(2 * math.pi * 95 * i / SR) * math.exp(-i / (SR * 0.02)) for i in range(L)))
    return array("f", (a * 0.6 + b * 0.5 for a, b in zip(talon, cuerpo)))


def olfateo(r):
    L = int(SR * 0.075)
    return mult(hp(lp(ruido(L, r), 5000), 1400), array("f", (math.sin(math.pi * i / L) ** 1.5 for i in range(L))))


def jadeo(r):
    L = int(SR * 0.16)
    return mult(lp(hp(ruido(L, r), 500), 2600), array("f", (math.sin(math.pi * i / L) ** 2 for i in range(L))))


def puerta(tipo, r):
    """Puerta de madera: 'abre' = picaporte + chirrido de bisagra (rozamiento a saltos); 'cierra' = golpe sordo + pestillo."""
    if tipo == "abre":
        L = int(SR * 0.9)
        x = ceros(L)
        picaporte = mult(hp(ruido(int(SR * 0.04), r), 2500), env_exp(int(SR * 0.04), 0.006))
        sumar(x, picaporte, 0, 0.5)
        # chirrido: impulsos cuya frecuencia sube y baja (stick-slip), filtrados como madera y metal
        t, i = 0.12, 0
        while t < 0.85:
            f = 380 + 260 * math.sin(math.pi * (t - 0.12) / 0.73)
            pulso = array("f", (math.sin(2 * math.pi * 1700 * k / SR) * math.exp(-k / (SR * 0.0016)) for k in range(int(SR * 0.006))))
            sumar(x, pulso, int(t * SR), 0.18 * math.sin(math.pi * (t - 0.12) / 0.73) ** 0.6)
            t += 1.0 / f * r.uniform(0.8, 1.25)
            i += 1
        return x
    if tipo == "trueno":  # chasquido y retumbo largo
        L = int(SR * 3.0)
        x = mult(lp(ruido(L, r), 220), array("f", ((1 - math.exp(-i / (SR * 0.05))) * math.exp(-i / (SR * 1.1)) for i in range(L))))
        sumar(x, mult(hp(ruido(int(SR * 0.15), r), 1500), env_exp(int(SR * 0.15), 0.04)), 0, 0.6)
        return array("f", (v * 1.6 for v in x))
    if tipo == "chapoteo":  # piedra que cae al agua: plop grave + burbujeo
        L = int(SR * 0.6)
        plop, fase = array("f", [0.0] * L), 0.0
        for i in range(L):
            u = i / L
            fase += 2 * math.pi * (600 - 380 * u) / SR
            plop[i] = math.sin(fase) * math.exp(-u * 9)
        sumar(plop, mult(lp(ruido(L, r), 1800), env_exp(L, 0.12)), 0, 0.5)
        return plop
    if tipo == "reja":  # reja de madera ligera: chirrido corto y golpecito contra el poste
        x = puerta("abre", r)[: int(SR * 0.45)]
        sumar(x, mult(lp(ruido(int(SR * 0.08), r), 1500), env_exp(int(SR * 0.08), 0.02)), int(SR * 0.38), 0.4)
        return x
    L = int(SR * 0.5)
    golpe = array("f", (math.sin(2 * math.pi * 70 * k / SR) * math.exp(-k / (SR * 0.05)) for k in range(L)))
    madera = mult(lp(ruido(L, r), 900), env_exp(L, 0.03))
    x = array("f", (a * 0.7 + b * 0.5 for a, b in zip(golpe, madera)))
    clic = mult(hp(ruido(int(SR * 0.03), r), 3000), env_exp(int(SR * 0.03), 0.004))
    sumar(x, clic, int(SR * 0.06), 0.45)
    return x


def gemido(r):
    """Gemido bajito de perro: tono nasal que sube y cae (900→650 Hz) con vibrato."""
    L = int(SR * r.uniform(0.45, 0.7))
    f0 = r.uniform(820, 980)
    x, fase = array("f", [0.0] * L), 0.0
    for i in range(L):
        u = i / L
        f = f0 * (1 + 0.12 * math.sin(math.pi * u)) - 250 * u ** 2 + 18 * math.sin(2 * math.pi * 7 * i / SR)
        fase += 2 * math.pi * f / SR
        x[i] = (math.sin(fase) + 0.35 * math.sin(2 * fase) + 0.15 * math.sin(3 * fase)) * math.sin(math.pi * u) ** 1.2
    return lp(x, 2400)


def ladridos_en(dur):
    """Los mismos tiempos que anim_cuadrupedo.ladridos (tandas de tres)."""
    ts, t = [], 0.5
    while t < dur - 0.45:
        for k in range(3):
            if t + k * 0.36 < dur - 0.45:
                ts.append(round(t + k * 0.36, 3))
        t += 3 * 0.36 + 0.9
    return ts


def ladrido(r):
    """Ladrido de beagle: golpe de aire ruidoso y un tono que cae rápido (~620→380 Hz) con armónicos y formante."""
    L = int(SR * r.uniform(0.2, 0.26))
    f0 = r.uniform(560, 660)
    x, fase = array("f", [0.0] * L), 0.0
    for i in range(L):
        u = i / L
        f = f0 * (1 - 0.38 * u ** 0.6) + 25 * math.sin(2 * math.pi * 31 * i / SR)
        fase += 2 * math.pi * f / SR
        env = (1 - math.exp(-i / (SR * 0.006))) * math.exp(-u * 3.2)
        x[i] = (math.sin(fase) + 0.6 * math.sin(2 * fase) + 0.4 * math.sin(3 * fase) + 0.2 * math.sin(4 * fase)) * env
    sumar(x, mult(lp(hp(ruido(int(SR * 0.06), r), 600), 3500), env_exp(int(SR * 0.06), 0.015)), 0, 0.8)
    return lp(hp(x, 180), 3400)


def sacudida(r):
    """Sacudida de agua: aleteo de orejas y piel a ~4.6 Hz más gotas que salpican."""
    L = int(SR * 1.1)
    x = ceros(L)
    t = 0.0
    while t < 1.05:
        a = math.sin(math.pi * t / 1.1)
        sumar(x, mult(lp(hp(ruido(int(SR * 0.05), r), 300), 2200), env_exp(int(SR * 0.05), 0.015)), int(t * SR), 0.6 * a)
        t += 1 / 4.6 / 2
    for _ in range(40):
        g = mult(hp(ruido(int(SR * 0.01), r), 3000), env_exp(int(SR * 0.01), 0.002))
        sumar(x, g, int(r.uniform(0.1, 1.08) * SR), r.uniform(0.1, 0.3))
    return x


# ------------------------------------------------------------------ música
ESCALA = {"C": 261.63, "D": 293.66, "E": 329.63, "F": 349.23, "G": 392.0, "A": 440.0, "B": 493.88}


def nota_piano(f, dur, vel=0.5):
    L = int(SR * dur)
    parc = [(1, 1.0, 1.0), (2, 0.45, 0.6), (3, 0.22, 0.4), (4, 0.12, 0.3), (5, 0.06, 0.2)]
    out = array("f", bytes(4 * L))
    for k, a, d in parc:
        w = 2 * math.pi * f * k / SR
        tau = SR * dur * 0.35 * d
        for i in range(L):
            out[i] += a * math.sin(w * i) * math.exp(-i / tau)
    ataque = int(SR * 0.004)
    for i in range(min(ataque, L)):
        out[i] *= i / ataque
    return array("f", (v * vel * 0.18 for v in out))


def musica(n, r, bpm=66):
    """Arpegios suaves: Am - F - C - G (melancólico que se resuelve), y al final un acorde de Do mayor."""
    x = ceros(n)
    acordes = [("A", "C", "E"), ("F", "A", "C"), ("C", "E", "G"), ("G", "B", "D")]
    negra = 60 / bpm
    t, i = 0.5, 0
    cache = {}
    while t < n / SR - 3:
        ac = acordes[(i // 4) % 4]
        bajo = ESCALA[ac[0]] / 2
        for j, nombre in enumerate((ac[0], ac[1], ac[2], ac[1])):
            f = ESCALA[nombre] * (2 if j == 2 and r.random() < 0.4 else 1)
            clave = (round(f, 1), j == 0)
            if clave not in cache:
                cache[clave] = nota_piano(f, 2.6, 0.55)
            sumar(x, cache[clave], int((t + j * negra / 2) * SR))
        if i % 4 == 0:
            if ("b", bajo) not in cache:
                cache[("b", bajo)] = nota_piano(bajo, 3.5, 0.7)
            sumar(x, cache[("b", bajo)], int(t * SR))
        t += negra * 2
        i += 1
    for nombre in ("C", "E", "G"):
        sumar(x, nota_piano(ESCALA[nombre], 3.0, 0.5), int((n / SR - 3.2) * SR))
    return x


# ------------------------------------------------------------------ generación por guion
def _suelo(esc):
    s = ((esc.get("set") or {}).get("suelo") or "pasto").lower()
    mojado = bool((esc.get("atmosfera") or {}).get("lluvia")) or bool((esc.get("atmosfera") or {}).get("charcos"))
    if s == "tierra":
        return "barro" if mojado else "tierra"
    return "piedra" if s in ("piedra", "asfalto") else ("tierra" if s == "arena" else "pasto")


def ambiente_escena(esc, n, r):
    atm = esc.get("atmosfera") or {}
    luz = esc.get("luz") or {}
    hora = (luz.get("hora") or "tarde").lower()
    amb = ((esc.get("set") or {}).get("ambiente") or "").lower()
    x = ceros(n)
    sumar(x, viento(n, r, 0.6 if amb in ("bosque", "camino") else 0.35), 0)
    if atm.get("lluvia"):
        sumar(x, lluvia(n, r, float(atm.get("intensidad_lluvia") or 1.0)), 0)
    elif atm.get("charcos") or amb == "camino":
        sumar(x, lluvia(n, r, 0.25), 0, 0.6)  # goteo de árboles / llovizna lejana
    if hora == "noche":
        sumar(x, grillos(n, r), 0)
    elif amb in ("pueblo", "calle"):
        sumar(x, pueblo(n, r), 0)
    elif not atm.get("lluvia"):
        sumar(x, pajaros(n, r, 1.2 if hora in ("amanecer", "manana") else 0.6), 0)
    return x


def generar(G, salida, eventos=None):
    """Escribe los WAV y devuelve {'ambientes': {id_escena: ruta}, 'foley': ruta, 'musica': ruta|None}."""
    r = random.Random(int(G.get("semilla", 7)) * 13 + 1)
    carpeta = os.path.join(salida, "audio")
    res = {"ambientes": {}, "foley": None, "musica": None}
    for esc in G["escenas"]:
        dur = esc["fin_s"] - esc["inicio_s"]
        x = ambiente_escena(esc, int((dur + 0.5) * SR), r)
        res["ambientes"][esc["id"]] = guardar(os.path.join(carpeta, f"amb_{esc['id']}.wav"), x, 0.6)
    DUR = G["duracion_s"]
    n = int((DUR + 1) * SR)
    fol = ceros(n)
    fps = G["fps"]
    # pasos de cuadrúpedos (exactos, de la animación)
    superficie_de = {}
    for esc in G["escenas"]:
        for t in esc["tomas"]:
            superficie_de[t["id"] if "/" in t["id"] else f"{esc['id']}/{t['id']}"] = _suelo(esc)
    def escena_en(seg):
        for esc in G["escenas"]:
            if esc["inicio_s"] <= seg < esc["fin_s"]:
                return esc
        return G["escenas"][-1]
    cache = {}
    for (f, pid, pata, modo) in (eventos or {}).get("pasos", []):
        seg = (f - 1) / fps
        sup = _suelo(escena_en(seg))
        k = (sup, pata)
        if k not in cache:
            cache[k] = paso(sup, r)
        fuerza = {"galopar": 1.0, "trotar": 0.75, "caminar": 0.55}.get(modo, 0.5)
        sumar(fol, cache[k], int(seg * SR + r.uniform(0, 0.01) * SR), fuerza * (1.0 if pata.startswith("Del") else 0.8))
    # puertas (fotogramas exactos de la animación)
    for (f, tipo) in (eventos or {}).get("puertas", []):
        sumar(fol, puerta(tipo, r), int((f - 1) / fps * SR), 0.7)
    # conductas por toma: olfateo, jadeo; pasos de personas que caminan
    for esc in G["escenas"]:
        sup = _suelo(esc)
        for t in esc["tomas"]:
            nota = (t.get("nota") or "").lower()
            t0, t1 = t["inicio_s"], t["inicio_s"] + t["duracion_s"]
            if "gime" in nota:
                s = t0 + 0.8
                while s < t1 - 0.6:
                    sumar(fol, gemido(r), int(s * SR), 0.22)
                    s += r.uniform(1.6, 2.6)
            if re.search(r"\b(ladra|ladrar|ladrando|ladridos?)\b", nota):  # a la misma hora que el golpe de cabeza
                for tb in ladridos_en(t["duracion_s"]):
                    sumar(fol, ladrido(r), int((t0 + tb + 0.03) * SR), 0.6)
            if "sacude" in nota:  # a la misma hora que la animación (55 % de la toma)
                sumar(fol, sacudida(r), int((t0 + 0.55 * t["duracion_s"]) * SR), 0.5)
            if "olfate" in nota or "huele" in nota:
                s = t0 + 0.6
                while s < t1 - 0.4:
                    for j in range(r.randint(3, 5)):
                        sumar(fol, olfateo(r), int((s + j * 0.17) * SR), 0.55)
                    s += r.uniform(1.0, 1.8)
            for a in t.get("accion") or []:
                clip = (a.get("clip") or "").lower()
                if a.get("personaje") == "milo" and "correr" in clip:
                    s = t0
                    while s < t1:
                        sumar(fol, jadeo(r), int(s * SR), 0.25)
                        s += 0.32
                if a.get("personaje") not in ("milo",) and a.get("hasta") and a.get("hasta") != a.get("desde"):
                    s = t0 + 0.2
                    while s < t1:
                        sumar(fol, zapato(sup, r), int(s * SR), 0.5)
                        s += 0.55
    res["foley"] = guardar(os.path.join(carpeta, "foley.wav"), fol, 0.8)
    if not (G.get("musica") or {}).get("archivo_existe"):
        res["musica"] = guardar(os.path.join(carpeta, "musica.wav"), musica(int(DUR * SR), r), 0.7)
    return res
