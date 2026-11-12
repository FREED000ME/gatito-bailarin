"""Forja3D Cine · poses procedurales para personajes humanos (esqueleto tipo Meshy/Mixamo).

La pose se define por DIRECCIONES de cada hueso en el marco del personaje (x = derecha, y = delante, z = arriba),
así funciona igual si el modelo viene en pose T o en pose A. La parte numérica no depende de Blender.

Poses: 'abrazo' (arrodillarse sobre una rodilla y rodear con los brazos algo que está delante y abajo,
p. ej. un perro), 'agacharse'.
"""
import math

import numpy as np

CADENAS = {
    "columna": ["Hips", "Spine02", "Spine01", "Spine", "neck", "Head"],
    "pierna_izq": ["LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase"],
    "pierna_der": ["RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"],
    "brazo_izq": ["LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand"],
    "brazo_der": ["RightShoulder", "RightArm", "RightForeArm", "RightHand"],
}


def _norm(v):
    v = np.asarray(v, float)
    return v / (np.linalg.norm(v) or 1.0)


def rot(eje, ang):
    eje = _norm(eje)
    K = np.array([[0, -eje[2], eje[1]], [eje[2], 0, -eje[0]], [-eje[1], eje[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def entre(a, b):
    a, b = _norm(a), _norm(b)
    c = np.cross(a, b)
    s = np.linalg.norm(c)
    if s < 1e-8:
        return np.eye(3) if a @ b > 0 else rot(np.cross(a, [1, 0, 0]) if abs(a[0]) < 0.9 else np.cross(a, [0, 1, 0]), math.pi)
    return rot(c, math.atan2(s, float(a @ b)))


def marco(B):
    """Marco del personaje en el espacio de la armadura: derecha, delante, arriba (a partir de pies y caderas)."""
    arriba = _norm(B["Head"][0] - B["Hips"][0]) if "Head" in B else np.array([0, 0, 1.0])
    pie, dedo = B.get("LeftFoot"), B.get("LeftToeBase")
    delante = (dedo[1] - pie[0]) if (pie is not None and dedo is not None) else np.array([0, 1.0, 0])
    delante = _norm(delante - (delante @ arriba) * arriba)
    derecha = _norm(np.cross(delante, arriba))
    # ¿el hueso "Left" está a la derecha del personaje en este modelo? (depende del exportador)
    if "LeftUpLeg" in B and (B["LeftUpLeg"][0] - B["Hips"][0]) @ derecha > 0:
        derecha_es_left = True
    else:
        derecha_es_left = False
    return derecha, delante, arriba, derecha_es_left


def orden(B):
    hecho, out = set(), []
    def v(n):
        if n in hecho or n not in B:
            return
        p = B[n][2]
        if p:
            v(p)
        hecho.add(n)
        out.append(n)
    for n in B:
        v(n)
    return out


def fk(B, R):
    G = {}
    for n in orden(B):
        h = B[n][0]
        Rl = R.get(n, np.eye(3))
        p = B[n][2]
        if p is None or p not in G:
            G[n] = (Rl, h - Rl @ h)
        else:
            Rp, tp = G[p]
            G[n] = (Rp @ Rl, Rp @ (h - Rl @ h) + tp)
    return G


def pos(B, G, n, cola=False):
    Rg, t = G[n]
    return Rg @ (B[n][1] if cola else B[n][0]) + t


def pose_por_direcciones(B, direcciones):
    """direcciones: {hueso: vector en espacio de armadura}. Devuelve rotaciones locales (convención de ejes de reposo)."""
    R, Gr = {}, {}
    for n in orden(B):
        h, t, p = B[n]
        Rp = Gr.get(p, np.eye(3)) if p else np.eye(3)
        if n in direcciones:
            actual = Rp @ (t - h)
            Rg = entre(actual, direcciones[n]) @ Rp
        else:
            Rg = Rp
        R[n] = Rp.T @ Rg
        Gr[n] = Rg
    return R


def pose_abrazo(B, distancia=0.7, alto_objetivo=None, mecer=0.0, caricia=0.0):
    """Arrodillarse (rodilla del lado derecho en el suelo) y abrazar con cariño lo que hay delante: los brazos
    rodean el cuello y se juntan detrás, la mejilla se apoya de lado sobre la cabeza del perro.
    mecer (-1..1) inclina el cuerpo a un lado (para mecerse); caricia (0..1) desliza la mano derecha por el lomo.
    Devuelve (R, desplazamiento_caderas) en espacio de armadura."""
    der, fr, up, der_es_left = marco(B)
    H = float((B["Head"][1] - B["LeftFoot"][0]) @ up) if "Head" in B and "LeftFoot" in B else 1.7
    L = (lambda n: n.replace("Left", "Right")) if der_es_left else (lambda n: n)
    Rr = (lambda n: n.replace("Right", "Left")) if der_es_left else (lambda n: n)
    def d(x, y, z):
        return _norm(x * der + y * fr + z * up)
    alto_objetivo = alto_objetivo if alto_objetivo is not None else 0.2 * H
    D = {}
    # columna inclinada hacia delante, cabeza mirando abajo al perro
    for n, (y, z, x) in (("Hips", (0.35, 1, 0.0)), ("Spine02", (0.6, 1, 0.05)), ("Spine01", (0.85, 1, 0.1)), ("Spine", (1.1, 1, 0.14)),
                         ("neck", (1.3, 0.25, 0.2)), ("Head", (1.0, -0.7, 0.5))):
        D[n] = d(x * (0.4 + mecer), y, z)  # se inclina más hacia el perro; la cabeza ladeada, mejilla contra él
    # de rodillas, sentada sobre los talones (así queda a la altura de un beagle): muslos hacia delante,
    # espinillas apoyadas en el suelo hacia atrás, empeines estirados
    for lado, s in (("Left", -1), ("Right", 1)):
        n = (L if lado == "Left" else Rr)
        D[n(f"{lado}UpLeg")] = d(s * 0.12, 0.85, -0.5)
        D[n(f"{lado}Leg")] = d(0.0, -1.0, -0.1)
        D[n(f"{lado}Foot")] = d(0, -0.5, -0.85)
        D[n(f"{lado}ToeBase")] = d(0, -1.0, -0.3)
    # brazos: hacia delante y abajo, cerrándose alrededor del lomo
    for lado, s in (("Left", -1), ("Right", 1)):
        n = (L if lado == "Left" else Rr)
        D[n(f"{lado}Shoulder")] = d(s * 1.0, 0.35, 0.05)
        D[n(f"{lado}Arm")] = d(s * 0.25, 0.6, -0.8)  # brazos hacia abajo y delante, al cuello del perro
        D[n(f"{lado}ForeArm")] = d(-s * 0.6, 0.55, -0.6)  # se cierran alrededor de su cuello
        D[n(f"{lado}Hand")] = d(-s * 0.7, 0.2, -0.5)  # las manos se juntan en su nuca
    if caricia and Rr("RightForeArm") in B:  # la mano derecha baja acariciando el lomo
        D[Rr("RightArm")] = d(0.3, 0.5, -1.0)
        D[Rr("RightForeArm")] = d(-0.5 + 0.2 * caricia, 0.6 - 0.3 * caricia, -0.45 - 0.2 * caricia)
        D[Rr("RightHand")] = d(-0.3, 0.4 - 0.5 * caricia, -0.6)
    D = {k: v for k, v in D.items() if k in B}
    R = pose_por_direcciones(B, D)
    # bajar las caderas hasta que la rodilla y el pie toquen el suelo
    G = fk(B, R)
    suelo = min(float(B[n][0] @ up) for n in ("LeftFoot", "RightFoot") if n in B)
    puntos = [pos(B, G, n) for n in (Rr("RightLeg"), L("LeftLeg")) if n in B]  # las dos rodillas en el suelo
    bajar = (min(float(p @ up) for p in puntos) - suelo) if puntos else 0.4 * H
    return R, -bajar * up


def esqueleto_prueba():
    """Humano en pose T (y = delante, z = arriba, x = derecha) para probar las poses sin Blender."""
    def b(h, t, p):
        return (np.array(h, float), np.array(t, float), p)
    B = {
        "Hips": b((0, 0, 0.95), (0, 0, 1.05), None), "Spine02": b((0, 0, 1.05), (0, 0, 1.15), "Hips"),
        "Spine01": b((0, 0, 1.15), (0, 0, 1.3), "Spine02"), "Spine": b((0, 0, 1.3), (0, 0, 1.45), "Spine01"),
        "neck": b((0, 0, 1.45), (0, 0, 1.55), "Spine"), "Head": b((0, 0, 1.55), (0, 0, 1.75), "neck"),
        "LeftUpLeg": b((-0.09, 0, 0.92), (-0.09, 0, 0.5), "Hips"), "LeftLeg": b((-0.09, 0, 0.5), (-0.09, 0, 0.08), "LeftUpLeg"),
        "LeftFoot": b((-0.09, 0, 0.08), (-0.09, 0.14, 0.02), "LeftLeg"), "LeftToeBase": b((-0.09, 0.14, 0.02), (-0.09, 0.2, 0.02), "LeftFoot"),
        "RightUpLeg": b((0.09, 0, 0.92), (0.09, 0, 0.5), "Hips"), "RightLeg": b((0.09, 0, 0.5), (0.09, 0, 0.08), "RightUpLeg"),
        "RightFoot": b((0.09, 0, 0.08), (0.09, 0.14, 0.02), "RightLeg"), "RightToeBase": b((0.09, 0.14, 0.02), (0.09, 0.2, 0.02), "RightFoot"),
        "LeftShoulder": b((-0.03, 0, 1.42), (-0.17, 0, 1.42), "Spine"), "LeftArm": b((-0.17, 0, 1.42), (-0.45, 0, 1.42), "LeftShoulder"),
        "LeftForeArm": b((-0.45, 0, 1.42), (-0.7, 0, 1.42), "LeftArm"), "LeftHand": b((-0.7, 0, 1.42), (-0.8, 0, 1.42), "LeftForeArm"),
        "RightShoulder": b((0.03, 0, 1.42), (0.17, 0, 1.42), "Spine"), "RightArm": b((0.17, 0, 1.42), (0.45, 0, 1.42), "RightShoulder"),
        "RightForeArm": b((0.45, 0, 1.42), (0.7, 0, 1.42), "RightArm"), "RightHand": b((0.7, 0, 1.42), (0.8, 0, 1.42), "RightForeArm"),
    }
    return B


# ------------------------------------------------------------------ Blender
def huesos_de(arm):
    """{nombre: (cabeza, cola, padre)} en espacio de la armadura, desde la pose de reposo del modelo."""
    return {b.name: (np.array(b.head_local), np.array(b.tail_local), b.parent.name if b.parent else None) for b in arm.data.bones}


def aplicar_poses(arm, claves, f_ini, f_fin, transicion=18, nombre="pose"):
    """Varias poses con clave en el tiempo (claves = [(fotograma, R, desplazamiento)]) en una sola tira NLA:
    el cuerpo se mece y las manos acarician en vez de quedarse congelados."""
    from mathutils import Matrix, Vector
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = None
    rest = {pb.name: pb.bone.matrix_local.to_3x3() for pb in arm.pose.bones}
    prev = {}
    for f, R, desplazamiento in claves:
        for pb in arm.pose.bones:
            if pb.name not in R:
                continue
            pb.rotation_mode = "QUATERNION"
            q = (rest[pb.name].inverted() @ Matrix(R[pb.name].tolist()) @ rest[pb.name]).to_quaternion()
            if pb.name in prev and prev[pb.name].dot(q) < 0:
                q.negate()
            prev[pb.name] = q
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=int(f))
            if pb.parent is None:
                pb.location = rest[pb.name].inverted() @ Vector(tuple(float(x) for x in desplazamiento))
                pb.keyframe_insert("location", frame=int(f))
    accion = ad.action
    ad.action = None
    pista = ad.nla_tracks.new()
    pista.name = nombre
    ini = int(claves[0][0])
    tira = pista.strips.new(nombre, ini, accion)
    tira.action_frame_start = ini
    tira.action_frame_end = max(ini + 1, int(claves[-1][0]))
    tira.frame_end = max(int(f_fin), ini + 1)
    tira.blend_type = "REPLACE"
    tira.blend_in = float(min(transicion, max(1, (f_fin - f_ini) / 2)))
    tira.extrapolation = "NOTHING"
    return tira


def aplicar_pose(arm, R, desplazamiento, f_ini, f_fin, transicion=18, nombre="pose"):
    """Pone la pose como una tira NLA encima de la caminata, con entrada suave de 'transicion' fotogramas."""
    from mathutils import Matrix, Vector
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = None
    rest = {pb.name: pb.bone.matrix_local.to_3x3() for pb in arm.pose.bones}
    for pb in arm.pose.bones:
        if pb.name not in R:
            continue
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (rest[pb.name].inverted() @ Matrix(R[pb.name].tolist()) @ rest[pb.name]).to_quaternion()
        pb.keyframe_insert("rotation_quaternion", frame=int(f_ini))
        if pb.parent is None:
            pb.location = rest[pb.name].inverted() @ Vector(tuple(float(x) for x in desplazamiento))
            pb.keyframe_insert("location", frame=int(f_ini))
    accion = ad.action
    ad.action = None
    pista = ad.nla_tracks.new()
    pista.name = nombre
    tira = pista.strips.new(nombre, int(f_ini), accion)
    tira.action_frame_start = int(f_ini)
    tira.action_frame_end = int(f_ini) + 1
    tira.scale = max(1.0, float(f_fin - f_ini))
    tira.blend_type = "REPLACE"
    tira.blend_in = float(min(transicion, max(1, (f_fin - f_ini) / 2)))
    tira.extrapolation = "NOTHING"
    return tira


# ------------------------------------------------------------------ poses con nombre (para secuencias en el guion)
# Cada pose: direcciones de los huesos en (derecha, delante, arriba) + qué toca el suelo ('pies' o 'rodillas').
# La mano que actúa es la DERECHA (en la izquierda suele ir la linterna o nada).
def _dirs_de_pie(s):
    return {"Spine02": (0, 0.03, 1), "Spine01": (0, 0.04, 1), "Spine": (0, 0.05, 1), "neck": (0, 0.12, 1), "Head": (0, 0.15, 1),
            "{L}Shoulder": (-1, 0.05, -0.05), "{L}Arm": (-0.18, 0.04, -1), "{L}ForeArm": (-0.06, 0.18, -1), "{L}Hand": (-0.02, 0.1, -1),
            "{R}Shoulder": (1, 0.05, -0.05), "{R}Arm": (0.18, 0.04, -1), "{R}ForeArm": (0.06, 0.18, -1), "{R}Hand": (0.02, 0.1, -1),
            "{L}UpLeg": (-0.03, 0.02, -1), "{L}Leg": (0, -0.02, -1), "{R}UpLeg": (0.03, 0.02, -1), "{R}Leg": (0, -0.02, -1)}


POSES = {
    # de pie, relajada: brazos caídos (sin esto, sin clip, se queda con los brazos abiertos del modelo)
    "de_pie": (lambda: _dirs_de_pie(1), "pies"),
    # agacharse a la altura del perro: rodillas dobladas, torso inclinado, la mano derecha al collar
    "agacharse": (lambda: {**_dirs_de_pie(1),
                           "Spine02": (0, 0.9, 1), "Spine01": (0, 1.3, 1), "Spine": (0, 1.6, 1), "neck": (0, 1.3, 0.4), "Head": (0, 1, -0.4),
                           "{R}Arm": (0.1, 0.35, -1), "{R}ForeArm": (0.0, 0.45, -1), "{R}Hand": (0, 0.3, -1),
                           "{L}Arm": (-0.25, 0.4, -1), "{L}ForeArm": (0.05, 0.85, -0.45),
                           "{L}UpLeg": (-0.12, 0.9, -0.45), "{L}Leg": (0, -0.55, -1), "{R}UpLeg": (0.12, 0.9, -0.45), "{R}Leg": (0, -0.55, -1)}, "pies"),
    # alcanzar algo delante con la mano derecha (abrir la reja, empujar una puerta)
    "alcanzar": (lambda: {**_dirs_de_pie(1), "Spine": (0, 0.18, 1), "neck": (0, 0.25, 1),
                          "{R}Arm": (0.18, 1, -0.3), "{R}ForeArm": (0.02, 1, -0.12), "{R}Hand": (0, 1, 0.05)}, "pies"),
    # lanzar una piedra: preparar (brazo atrás y arriba, torso girado), soltar (brazo delante), terminar (cruzado abajo)
    "lanzar_preparar": (lambda: {**_dirs_de_pie(1), "Spine": (-0.12, -0.08, 1), "neck": (-0.05, 0.1, 1), "Head": (0, 0.25, 1),
                                 "{R}Arm": (0.55, -0.55, 0.6), "{R}ForeArm": (0.1, -0.45, 1), "{R}Hand": (0, -0.4, 1),
                                 "{L}Arm": (-0.25, 0.8, -0.35), "{L}ForeArm": (-0.1, 1, -0.1),
                                 "{R}UpLeg": (0.06, -0.12, -1), "{L}UpLeg": (-0.06, 0.18, -1)}, "pies"),
    "lanzar_soltar": (lambda: {**_dirs_de_pie(1), "Spine": (0.1, 0.3, 1), "neck": (0.05, 0.35, 1),
                               "{R}Arm": (0.12, 1, 0.3), "{R}ForeArm": (0.0, 1, 0.35), "{R}Hand": (0, 1, 0.2),
                               "{L}Arm": (-0.3, -0.2, -1), "{L}UpLeg": (-0.06, 0.2, -1), "{R}UpLeg": (0.06, -0.2, -1)}, "pies"),
    "lanzar_terminar": (lambda: {**_dirs_de_pie(1), "Spine": (0.12, 0.45, 1), "neck": (0.05, 0.4, 1),
                                 "{R}Arm": (-0.2, 0.75, -0.65), "{R}ForeArm": (-0.45, 0.6, -0.5), "{R}Hand": (-0.4, 0.4, -0.6),
                                 "{L}UpLeg": (-0.06, 0.2, -1), "{R}UpLeg": (0.06, -0.25, -1)}, "pies"),
}


def pose_nombrada(B, nombre, respiro=0.0):
    """Pose por nombre (ver POSES). respiro (-1..1) mueve un poco el pecho y los hombros (para que no se congele)."""
    if nombre == "abrazo":
        return pose_abrazo(B)
    f, apoyo = POSES[nombre]
    der, fr, up, der_es_left = marco(B)
    L = "Right" if der_es_left else "Left"
    Rr = "Left" if der_es_left else "Right"
    D = {}
    for k, (x, y, z) in f().items():
        k = k.replace("{L}", L).replace("{R}", Rr)
        if der_es_left:
            x = -x  # el lado 'derecho' del marco es la izquierda del modelo
        if k.startswith("Spine") or k == "neck":
            z += 0.03 * respiro
        D[k] = _norm(x * der + y * fr + z * up)
    D = {k: v for k, v in D.items() if k in B}
    R = pose_por_direcciones(B, D)
    G = fk(B, R)
    suelo = min(float(B[n][0] @ up) for n in ("LeftFoot", "RightFoot") if n in B) if "LeftFoot" in B else 0.0
    if apoyo == "pies":
        puntos = [pos(B, G, n) for n in ("LeftFoot", "RightFoot") if n in B]
    else:
        puntos = [pos(B, G, n) for n in ("LeftLeg", "RightLeg") if n in B]
    bajar = (min(float(p @ up) for p in puntos) - suelo) if puntos else 0.0
    return R, -bajar * up
