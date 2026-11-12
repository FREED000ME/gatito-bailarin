"""Forja3D Animación: cálculos de la rutina y de la capa de naturalidad, sin Blender (se prueban sin bpy).

hornear_animacion.py los usa para armar la línea de tiempo NLA y hornear la naturalidad fotograma a fotograma.
Siguen la misma lógica que Forja3DRutina.cs y Forja3DNaturalidad.cs de la plantilla Unity, para que el
personaje se mueva igual en el juego y en el cine.
"""
import math
import random
import re

CAMINA = re.compile(r"walk|run|jog|sprint|step|march|skip|jump", re.I)


def _f(v, defecto):
    try:
        return float(v)
    except (TypeError, ValueError):
        return defecto


def perfil_completo(p):
    base = {"respiracion_rpm": 15, "respiracion_amp": 1.6, "sacadas_hz": 3, "sacada_amp": 1.2, "mirada_adelanto_s": 0.2,
            "temblor_hz": 10, "temblor_amp": 0.12, "inercia": 0.35, "lateralidad": "derecha", "asimetria": 0.08,
            "fatiga_por_rep": 0.012, "variacion_tempo": 0.06, "anticipacion_s": 0.12, "pausa_entre_s": 0.35,
            "balanceo_peso": 1.2, "naturalidad": 0.6}
    base.update({k: v for k, v in (p or {}).items() if v is not None})
    return base


def plan_tiras(rutina, perfil, largos, fps, fundido_s=0.25, semilla=1234):
    """Convierte la rutina en tiras NLA.

    rutina: lista de tiempos del forja3d.json ({clip, repeticiones, duracion_s, tempo, lado, accion}).
    largos: {clip: fotogramas del clip}. Devuelve (tiras, total_fotogramas, avisos).
    Cada tira: {clip, inicio, fotogramas, escala, repetir, fatiga, quieto, tiempo, mezcla_entrada}.
    """
    p = perfil_completo(perfil)
    rnd = random.Random(semilla)
    tiras, avisos = [], []
    cursor, reps_totales = 1.0, 0
    validos = [t for t in rutina or [] if t and t.get("clip") in largos]
    for t in rutina or []:
        if not t or t.get("clip") not in largos:
            avisos.append(f"falta el clip {t.get('clip') if t else '?'} de «{(t or {}).get('texto', '')}»; se salta")
    fundido = max(0.0, fundido_s) * fps
    for i, t in enumerate(validos):
        largo = max(1.0, float(largos[t["clip"]]))
        tempo = _f(t.get("tempo"), 1.0) or 1.0
        nombre = ((t.get("accion") or {}).get("nombre") or t["clip"])
        quieto = 0.0 if CAMINA.search(nombre) else 1.0

        def velocidad(con_variacion=True):
            dv = p["variacion_tempo"] * (rnd.random() * 2 - 1) if con_variacion else 0.0
            cansancio = min(1.0, max(0.0, 1.0 - p["fatiga_por_rep"] * reps_totales))
            return max(0.1, tempo * (1 + dv) * (0.6 + 0.4 * cansancio)), 1.0 - cansancio

        dur = _f(t.get("duracion_s"), 0.0)
        if dur > 0:
            v, fat = velocidad(False)
            marcos = dur * fps
            repetir = marcos * v / largo
            tiras.append({"clip": t["clip"], "inicio": cursor, "fotogramas": marcos, "escala": 1.0 / v, "repetir": repetir,
                          "fatiga": fat, "quieto": quieto, "tiempo": i, "mezcla_entrada": fundido if tiras else 0.0})
            cursor += marcos
            reps_totales += max(1, math.ceil(repetir))
        else:
            for _ in range(max(1, int(_f(t.get("repeticiones"), 1)))):
                v, fat = velocidad()
                marcos = largo / v
                tiras.append({"clip": t["clip"], "inicio": cursor, "fotogramas": marcos, "escala": 1.0 / v, "repetir": 1.0,
                              "fatiga": fat, "quieto": quieto, "tiempo": i, "mezcla_entrada": fundido if tiras else 0.0})
                cursor += marcos
                reps_totales += 1
        if i < len(validos) - 1 and p["pausa_entre_s"] > 0:
            cursor += p["pausa_entre_s"] * (1 + 0.8 * tiras[-1]["fatiga"]) * fps
    return tiras, int(math.ceil(cursor)), avisos


def _ruido(t, s):
    """Ruido suave y determinista en [-0.5, 0.5] (suma de senos inconmensurables, como un Perlin barato)."""
    return 0.5 * (math.sin(t * 1.0 + s) * 0.5 + math.sin(t * 2.17 + s * 1.3) * 0.3 + math.sin(t * 4.31 + s * 0.7) * 0.2)


class Naturalidad:
    """Capas procedurales en grados. paso(dt) avanza el tiempo; luego giros(fila) da los giros de un hueso."""

    def __init__(self, perfil, matriz, semilla=7):
        self.p = perfil_completo(perfil)
        self.k = max(0.0, min(1.0, _f(self.p["naturalidad"], 0.6)))
        self.capas = list((matriz or {}).get("capas") or [])
        self.rnd = random.Random(semilla)
        self.t = 0.0
        self.mirada = [0.0, 0.0]
        self.objetivo = [0.0, 0.0]
        self.proxima = 0.0
        self.resp = self.bal = 0.0
        self.fatiga = 0.0
        self.quieto = 1.0

    def peso(self, fila, capa):
        if capa not in self.capas:
            return 0.0
        i = self.capas.index(capa)
        pesos = fila.get("pesos") or []
        return max(0.0, min(1.0, _f(pesos[i], 0.0))) if i < len(pesos) else 0.0

    def paso(self, dt, fatiga=0.0, quieto=1.0):
        p, k = self.p, self.k
        self.t += dt
        self.fatiga = fatiga
        self.quieto += (quieto - self.quieto) * (1 - math.exp(-dt * 4))
        rpm = p["respiracion_rpm"] * (1 + fatiga * 0.6)
        fase = (self.t * rpm / 60.0) % 1.0
        ola = math.sin(fase / 0.4 * math.pi / 2) if fase < 0.4 else math.cos((fase - 0.4) / 0.6 * math.pi / 2)
        self.resp = (ola - 0.5) * p["respiracion_amp"] * (1 + fatiga) * k
        if p["sacadas_hz"] > 0 and self.t >= self.proxima:
            ang = self.rnd.random() * 2 * math.pi
            r = math.sqrt(self.rnd.random()) * p["sacada_amp"]
            self.objetivo = [math.cos(ang) * r, math.sin(ang) * r]
            self.proxima = self.t - math.log(1 - self.rnd.random() * 0.999) / p["sacadas_hz"]
        a = 1 - math.exp(-dt * 25)
        self.mirada = [m + (o - m) * a for m, o in zip(self.mirada, self.objetivo)]
        self.bal = (math.sin(self.t * 1.55) * 0.7 + _ruido(self.t * 0.3, 7.1) * 0.6) * p["balanceo_peso"] * self.quieto * k

    def giros(self, fila, hueso, semilla):
        """Lista de (eje, grados) con eje en 'derecha' | 'arriba' | 'frente' (ejes del personaje)."""
        p, k = self.p, self.k
        izquierdo = hueso.lower().startswith("left")
        no_dominante = 1 + p["asimetria"] * 3 if izquierdo != (p["lateralidad"] == "izquierda") else 1.0
        cabeza = fila.get("grupo") in ("cabeza", "cuello")
        g = []
        w = self.peso(fila, "respiracion")
        if w:
            g.append(("derecha", -self.resp * w))
        w = self.peso(fila, "sacadas")
        if w and cabeza:
            g += [("arriba", self.mirada[0] * w * k), ("derecha", self.mirada[1] * w * k)]
        w = self.peso(fila, "temblor")
        if w and p["temblor_amp"] > 0:
            amp = p["temblor_amp"] * w * k * (1 + self.fatiga) * no_dominante * 2
            f = p["temblor_hz"] * 2 * math.pi
            g += [(e, _ruido(self.t * f, semilla + j * 11) * amp) for j, e in enumerate(("derecha", "arriba", "frente"))]
        w = self.peso(fila, "balanceo")
        if w:
            g.append(("frente", self.bal * w))
        return g

    def rigidez(self, fila, hueso):
        """Cuánto sigue la pose animada cada fotograma (0..1) y con qué peso se mezcla la inercia."""
        p = self.p
        izquierdo = hueso.lower().startswith("left")
        no_dominante = 1 + p["asimetria"] * 3 if izquierdo != (p["lateralidad"] == "izquierda") else 1.0
        return 30 + (5 - 30) * max(0.0, min(1.0, p["inercia"])), no_dominante, self.peso(fila, "inercia") * self.k
