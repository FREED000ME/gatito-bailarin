"""Forja3D Animación: hornea en Blender la rutina de un modelo animado con animar_modelo.

Uso (lo lanza el workflow hornear-animacion.yml):
    blender -b --factory-startup -P estudio/hornear_animacion.py -- --modelo assets/modelos/<n> --fps 30 --salida salida

1. Importa el personaje con esqueleto (personaje.fbx) y los FBX de cada clip de Meshy.
2. Arma la rutina en el NLA: un clip por tiempo, repeticiones con fatiga y variación, pausas y fundidos.
3. Hornea todo en una sola acción y le suma la capa de naturalidad con la matriz de pesos
   (respiración, sacadas de la mirada, temblor fisiológico, inercia y balanceo del peso).
4. Exporta <n>.fbx (Unity, Unreal, Godot), <n>.glb (web, three.js) y <n>.blend (para seguir en Blender),
   más informe.json con lo que se hizo.
"""
import argparse
import json
import math
import os
import sys
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rutina as R  # noqa: E402
from comun import aviso, error  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--modelo", required=True)
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--salida", default="salida")
ap.add_argument("--raiz", default=os.getcwd())
ap.add_argument("--nombre", default="")
ARGS = ap.parse_args(argv)
RAIZ = os.path.abspath(ARGS.raiz)
CARPETA = ARGS.modelo if os.path.isabs(ARGS.modelo) else os.path.join(RAIZ, ARGS.modelo)
SALIDA = os.path.abspath(ARGS.salida)
FPS = max(12, min(60, ARGS.fps))
NOMBRE = ARGS.nombre or os.path.basename(os.path.normpath(CARPETA))
os.makedirs(SALIDA, exist_ok=True)
MESHY = {}  # clip ("accion_30") → nombre en la biblioteca de Meshy ("Casual_Walk")


def intentar(desc, fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: {desc} falló ({type(e).__name__}: {e}); sigo sin eso")
        return None


def limpio(s):
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(s or "modelo").strip())


def importar_fbx(ruta):
    antes = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=ruta, automatic_bone_orientation=False, ignore_leaf_bones=True)
    return [o for o in bpy.data.objects if o not in antes]


def armadura(objs):
    for o in objs:
        if o.type == "ARMATURE":
            return o
    return None


def accion_de(obj):
    ad = getattr(obj, "animation_data", None)
    return ad.action if ad and ad.action else None


def borrar(objs):
    for o in objs:
        intentar("borrar objeto importado", bpy.data.objects.remove, o, do_unlink=True)


def largo_accion(a):
    ini, fin = a.frame_range
    return max(1.0, float(fin) - float(ini))


def main():
    t0 = time.time()
    ruta_man = os.path.join(CARPETA, "forja3d.json")
    if not os.path.isfile(ruta_man):
        error(f"no existe {ruta_man}: guarda el modelo con guardar_asset")
    man = json.load(open(ruta_man, encoding="utf-8"))
    rut = man.get("rutina") or []
    if not rut:
        error(f"{NOMBRE} no tiene rutina: anímalo con animar_modelo y vuelve a guardarlo")
    archivos = [a for a in man.get("archivos") or [] if a]
    MESHY.update({t["clip"]: (t.get("accion") or {}).get("nombre") or "" for t in rut if t and t.get("clip")})
    modelo = next((a for a in archivos if a.get("tipo") == "modelo"), None)
    if not modelo:
        error("el manifiesto no tiene el archivo del modelo")

    # Escena limpia (sin el cubo, la cámara ni la luz de inicio) a los fps pedidos
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    esc = bpy.context.scene
    esc.render.fps = FPS

    ruta_mod = os.path.join(CARPETA, f"{limpio(modelo['nombre'])}.fbx")
    if not os.path.isfile(ruta_mod):
        error(f"falta {ruta_mod}")
    objs = importar_fbx(ruta_mod)
    arm = armadura(objs)
    if not arm:
        error("el modelo no trae esqueleto (¿se guardó antes de que terminara el rig?)")
    arm.name = NOMBRE
    if arm.animation_data and arm.animation_data.action:
        arm.animation_data.action = None

    # Clips: cada FBX de animación trae una acción por clip, en el orden de 'clips' del manifiesto
    acciones = {}
    for a in archivos:
        if a.get("tipo") != "animacion":
            continue
        ruta = os.path.join(CARPETA, f"{limpio(a['nombre'])}.fbx")
        if not os.path.isfile(ruta):
            aviso(f"aviso: falta {os.path.basename(ruta)}")
            continue
        antes = set(bpy.data.actions)
        nuevos = intentar(f"importar {os.path.basename(ruta)}", importar_fbx, ruta) or []
        nombres = a.get("clips") or [a.get("nombre")]
        # Un FBX puede traer varias tomas (una acción por clip): se emparejan por el nombre de Meshy
        # (p. ej. "Casual_Walk") y, si no aparece, por orden
        nuevas = [ac for ac in bpy.data.actions if ac not in antes] or [ac for ac in (accion_de(o) for o in nuevos) if ac]
        libres = list(nuevas)
        for nom in nombres:
            meshy = MESHY.get(nom, "").lower()
            ac = next((x for x in libres if meshy and meshy in x.name.lower()), None) or (libres[0] if libres else None)
            if not ac:
                aviso(f"aviso: {os.path.basename(ruta)} no trae la toma de {nom}")
                continue
            libres.remove(ac)
            ac.name = nom
            ac.use_fake_user = True
            acciones[nom] = ac
        borrar(nuevos)
    if not acciones:
        error("no encontré ningún clip de animación en los FBX del modelo")
    aviso(f"clips: {', '.join(sorted(acciones))}")

    perfil = R.perfil_completo(man.get("naturalidad"))
    largos = {n: largo_accion(a) for n, a in acciones.items()}  # el FBX se importó ya a FPS
    tiras, total, avisos = R.plan_tiras(rut, perfil, largos, FPS)
    for av in avisos:
        aviso("aviso: " + av)
    for t in rut:
        if t and t.get("lado") == "izquierda":
            aviso(f"aviso: «{t.get('texto')}» pide la izquierda; en Blender se usa el clip tal cual (Unity sí lo pone en espejo)")
    if not tiras:
        error("ningún tiempo de la rutina tiene clip")

    # NLA: una pista por tira, cada una encima de la anterior; la anterior se queda en su última pose
    # (HOLD_FORWARD) y la nueva funde sobre ella con blend_in
    arm.animation_data_create()
    for i, t in enumerate(tiras):
        ac = acciones[t["clip"]]
        pista = arm.animation_data.nla_tracks.new()
        pista.name = f"Rutina_{i:03d}"
        tr = pista.strips.new(f"{i:03d}_{t['clip']}", int(round(t["inicio"])), ac)
        tr.scale = t["escala"]
        tr.repeat = max(0.05, t["repetir"])
        tr.blend_in = min(t["mezcla_entrada"], t["fotogramas"] * 0.45)
        tr.extrapolation = "HOLD" if i == 0 else "HOLD_FORWARD"
    esc.frame_start, esc.frame_end = 1, total

    # Horneado de la rutina en una sola acción
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    bpy.ops.nla.bake(frame_start=1, frame_end=total, step=1, only_selected=False, visual_keying=True,
                     clear_constraints=False, clear_parents=False, use_current_action=False, bake_types={"POSE"})
    final = arm.animation_data.action
    final.name = f"{NOMBRE}_rutina"
    for p in list(arm.animation_data.nla_tracks):
        arm.animation_data.nla_tracks.remove(p)

    # Naturalidad: giros pequeños en los ejes del personaje, pesados por la matriz
    matriz = man.get("matriz") or {}
    huesos = []
    nat = R.Naturalidad(perfil, matriz)
    inv = arm.matrix_world.to_3x3().inverted()
    ejes_mundo = {"derecha": Vector((-1, 0, 0)), "arriba": Vector((0, 0, 1)), "frente": Vector((0, -1, 0))}  # Meshy mira hacia -Y
    ejes_arm = {k: (inv @ v).normalized() for k, v in ejes_mundo.items()}
    porNombre = {pb.name.split(":")[-1].lower(): pb for pb in arm.pose.bones}
    for fila in matriz.get("filas") or []:
        for h in fila.get("huesos") or []:
            pb = porNombre.get(h.lower())
            if pb:
                rest = pb.bone.matrix_local.to_3x3().inverted()
                huesos.append((fila, h, pb, {k: (rest @ v).normalized() for k, v in ejes_arm.items()}, len(huesos) * 3.7))
    if not huesos:
        aviso("aviso: ningún hueso de la matriz existe en el esqueleto; se exporta sin naturalidad")
    else:
        previos = {}
        cual = 0
        dt = 1.0 / FPS
        for f in range(1, total + 1):
            while cual + 1 < len(tiras) and f >= tiras[cual + 1]["inicio"]:
                cual += 1
            esc.frame_set(f)
            nat.paso(dt, tiras[cual]["fatiga"], tiras[cual]["quieto"])
            for fila, h, pb, ejes, semilla in huesos:
                q = pb.rotation_quaternion.copy()
                k_seg, no_dom, w_iner = nat.rigidez(fila, h)
                prev = previos.get(h, q)
                prev = prev.slerp(q, 1 - math.exp(-dt * k_seg / no_dom))
                previos[h] = prev
                if w_iner > 0:
                    q = q.slerp(prev, w_iner)
                for eje, grados in nat.giros(fila, h, semilla):
                    if grados:
                        q = Quaternion(ejes[eje], math.radians(grados)) @ q
                pb.rotation_quaternion = q
                pb.keyframe_insert("rotation_quaternion", frame=f)
    bpy.ops.object.mode_set(mode="OBJECT")
    esc.frame_set(1)

    # Exportar
    base = os.path.join(SALIDA, NOMBRE)
    hechos = []
    if intentar("exportar FBX", bpy.ops.export_scene.fbx, filepath=base + ".fbx", use_selection=False, bake_anim=True,
                bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False, add_leaf_bones=False, path_mode="COPY",
                embed_textures=True) is not None:
        hechos.append(NOMBRE + ".fbx")
    if intentar("exportar GLB", bpy.ops.export_scene.gltf, filepath=base + ".glb", export_format="GLB",
                export_animations=True) is not None:
        hechos.append(NOMBRE + ".glb")
    if intentar("guardar .blend", bpy.ops.wm.save_as_mainfile, filepath=base + ".blend") is not None:
        hechos.append(NOMBRE + ".blend")
    if not hechos:
        error("no se pudo exportar ningún archivo")
    informe = {
        "modelo": NOMBRE, "fps": FPS, "fotogramas": total, "duracion_s": round(total / FPS, 2),
        "tiempos": [{"texto": t.get("texto"), "clip": t.get("clip"), "repeticiones": t.get("repeticiones"),
                     "duracion_s": t.get("duracion_s"), "tempo": t.get("tempo")} for t in rut if t],
        "tiras": len(tiras), "huesos_naturalidad": len(huesos), "perfil": perfil,
        "blender": bpy.app.version_string, "segundos": round(time.time() - t0, 1), "archivos": hechos + ["informe.json"],
    }
    json.dump(informe, open(os.path.join(SALIDA, "informe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    aviso(f"listo: {', '.join(hechos)} · {informe['duracion_s']} s a {FPS} fps · {len(huesos)} huesos con naturalidad")


main()
