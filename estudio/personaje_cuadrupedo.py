"""Forja3D Cine · lleva rig_cuadrupedo y anim_cuadrupedo a Blender.

preparar(): quita el esqueleto importado (Tripo/Meshy), normaliza la malla (cabeza hacia +Y, patas en z=0,
altura real), crea un esqueleto propio con pesos por regiones y deja marcadores para la cámara (cabeza, nariz).
animar_toma(): anima los huesos fotograma a fotograma con el motor procedural.
"""
import math

import bpy
import numpy as np
from mathutils import Matrix, Vector

import anim_cuadrupedo as AC
import rig_cuadrupedo as RC
import pelaje as PEL
from comun import aviso

EVENTOS = []  # (fotograma, personaje, pata) cuando una pata toca el suelo: para los pasos del audio


def _activo(obj):
    for o in bpy.context.view_layer.objects:
        try:
            o.select_set(False)
        except Exception:  # noqa: BLE001
            pass
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def _vertices(me):
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def es_cuadrupedo(cfg, carpeta_json):
    t = str(cfg.get("tipo") or "").lower()
    if t in ("perro", "gato", "cuadrupedo", "cuadrúpedo", "animal", "caballo", "zorro", "lobo"):
        return True
    return str((carpeta_json or {}).get("rig_type") or "").lower() == "quadruped"


def preparar(raiz, nuevos, altura_m, pid, fps, semilla, poner_pelo=True, pelaje=None, estilo_pelo=None):
    mallas = [o for o in nuevos if o.type == "MESH" and len(o.data.vertices) > 50]
    if not mallas:
        return None
    bpy.context.view_layer.update()
    for m in mallas:
        if m.data.users > 1:
            m.data = m.data.copy()
        mw = m.matrix_world.copy()
        for md in list(m.modifiers):
            if md.type == "ARMATURE":
                m.modifiers.remove(md)
        m.parent = None
        m.data.transform(mw)
        m.matrix_world = Matrix.Identity(4)
        m.vertex_groups.clear()
        try:
            m.shape_key_clear()
        except Exception:  # noqa: BLE001
            pass
        if m.animation_data:
            m.animation_data_clear()
    for o in nuevos:
        if o not in mallas and o.name in bpy.data.objects:
            bpy.data.objects.remove(o, do_unlink=True)
    malla = max(mallas, key=lambda o: len(o.data.vertices))
    if len(mallas) > 1:
        try:
            with bpy.context.temp_override(active_object=malla, selected_editable_objects=mallas, selected_objects=mallas):
                bpy.ops.object.join()
        except Exception as e:  # noqa: BLE001
            aviso(f"aviso: no pude unir las mallas de {pid} ({e}); uso la principal")
            for o in mallas:
                if o is not malla:
                    o.parent = malla
    me = malla.data
    v = _vertices(me)
    largo, signo = RC.orientar(v)
    ang = (0.0 if signo > 0 else math.pi) if largo == 1 else (-math.pi / 2 if signo > 0 else math.pi / 2)
    me.transform(Matrix.Rotation(ang, 4, "Z"))
    v = _vertices(me)
    c = Vector((float(np.median(v[:, 0])), float((v[:, 1].min() + v[:, 1].max()) / 2), float(v[:, 2].min())))
    alto = float(np.ptp(v[:, 2])) or 1.0
    esc = float(altura_m or alto) / alto
    me.transform(Matrix.Scale(esc, 4) @ Matrix.Translation(-c))
    me.update()
    v = _vertices(me)
    J = RC.simetrizar_landmarks(RC.landmarks(v))
    J = RC.ajustar_orejas(v, J)  # cada oreja donde está de verdad (en Milo la derecha va más adelante)
    B = RC.huesos(J)
    nombres, w = RC.pesos(v, B, J)
    w, frac = RC.simetrizar_pesos(v, nombres, w, 0.025)
    aviso(f"{pid}: pesos de piel simétricos en el {frac * 100:.0f}% de la malla (tronco, cabeza y cola)")
    try:
        aristas = np.zeros(len(me.edges) * 2, np.int64)
        me.edges.foreach_get("vertices", aristas)
        w, hechos = RC.corregir_orejas_y_cola(v, aristas.reshape(-1, 2), J, nombres, w)
        aviso(f"{pid}: pesos por superficie ({'; '.join(hechos)})")
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: pesos de orejas y cola de {pid} ({type(e).__name__}: {e})")
    try:
        n0 = len(nombres)
        nombres, w = RC.anadir_carne(v, J, B, nombres, w)
        if len(nombres) > n0:
            aviso(f"{pid}: carne con física de resorte: {', '.join(nombres[n0:])}")
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: carne de {pid} ({type(e).__name__}: {e}); sigue sin ella")
    aviso(f"{pid}: esqueleto propio de {len(B)} huesos (patas, columna, cuello, cabeza, orejas y cola)")
    # ---- armadura
    datos = bpy.data.armatures.new(f"ARM_{pid}")
    arm = bpy.data.objects.new(f"ARM_{pid}", datos)
    bpy.context.scene.collection.objects.link(arm)
    _activo(arm)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = {}
    for n, (h, t, _) in B.items():
        b = datos.edit_bones.new(n)
        h, t = Vector(map(float, h)), Vector(map(float, t))
        if (t - h).length < 1e-4:
            t = h + Vector((0, 0, 0.01))
        b.head, b.tail = h, t
        b.use_deform = True
        eb[n] = b
    for n, (_, _, p) in B.items():
        if p and p in eb:
            eb[n].parent = eb[p]
            eb[n].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    # ---- pesos
    for k, n in enumerate(nombres):
        g = malla.vertex_groups.new(name=n)
        idx = np.nonzero(w[:, k] > 0.003)[0]
        for i in idx:
            g.add([int(i)], float(w[i, k]), "REPLACE")
    md = malla.modifiers.new("Esqueleto", "ARMATURE")
    try:  # piel con "conservar volumen" (dual quaternion): al bajar el cuello el pecho y la garganta no se aplastan
        md.use_deform_preserve_volume = True
    except Exception:  # noqa: BLE001
        pass
    md.object = arm
    malla.parent = arm
    arm.parent = raiz
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    # ---- marcadores para la cámara
    partes = {}
    for nombre, hueso in (("cabeza", "cuello2"), ("nariz", "cabeza"), ("cuerpo", "espalda"), ("cola", "cola_raiz")):
        if hueso in datos.bones:
            e = bpy.data.objects.new(f"{nombre.upper()}_{pid}", None)
            bpy.context.scene.collection.objects.link(e)
            e.parent = arm
            e.parent_type = "BONE"
            e.parent_bone = hueso
            e.location = (0, 0, 0)
            partes[nombre] = e
    try:
        aclarar_piel(malla)
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: ajuste de piel de {pid} ({e})")
    patron = None
    if pelaje:
        try:
            patron = PEL.aplicar(malla, v, J, pelaje, pid)
            aviso(f"{pid}: pelaje '{pelaje}' pintado por anatomía en la piel" if patron else f"aviso: pelaje '{pelaje}' desconocido o sin UV")
        except Exception as e:  # noqa: BLE001
            aviso(f"aviso: pelaje de {pid} ({type(e).__name__}: {e}); se queda la textura del modelo")
    perro = AC.Perro(B, J, fps, semilla)
    con_pelo = poner_pelo() if callable(poner_pelo) else bool(poner_pelo)
    try:
        if con_pelo:
            pelo(malla, v, J, pid, patron=patron, estilo=estilo_pelo)
        else:
            aviso(f"{pid}: sin pelo en esta escena (vista previa o planos lejanos: el pelo cuesta ~70 % del render)")
    except Exception as e:  # noqa: BLE001
        aviso(f"aviso: el pelo de {pid} falló ({type(e).__name__}: {e}); sigue sin pelo")
    return {"arm": arm, "malla": malla, "perro": perro, "B": B, "J": J, "partes": partes, "q_prev": {}}


def aclarar_piel(malla):
    """Las texturas de Meshy/Tripo salen oscuras y brillantes como plástico: se aclaran (+60 %),
    se vuelven mates y se les da 'sheen' (el brillo aterciopelado del pelaje corto)."""
    for m in malla.data.materials:
        if not m or not m.node_tree:
            continue
        nt = m.node_tree
        for n in nt.nodes:
            if n.type != "BSDF_PRINCIPLED":
                continue
            for nombre, val in (("Roughness", 0.72), ("Specular IOR Level", 0.25), ("Sheen Weight", 0.5), ("Sheen Roughness", 0.4), ("Coat Weight", 0.0)):
                if nombre in n.inputs and not n.inputs[nombre].is_linked:
                    n.inputs[nombre].default_value = val
            if "Roughness" in n.inputs and n.inputs["Roughness"].is_linked:
                for l in list(n.inputs["Roughness"].links):
                    nt.links.remove(l)
                n.inputs["Roughness"].default_value = 0.72
            if n.inputs["Base Color"].is_linked:
                src = n.inputs["Base Color"].links[0].from_socket
                hsv = nt.nodes.new("ShaderNodeHueSaturation")
                hsv.inputs["Value"].default_value = 1.0  # ya no hace falta aclarar: lo oscuro era el mapa de normales roto
                hsv.inputs["Saturation"].default_value = 1.05
                nt.links.new(src, hsv.inputs["Color"])
                nt.links.new(hsv.outputs["Color"], n.inputs["Base Color"])


def _imagen_base(malla):
    """La textura de color del modelo (para teñir cada pelo con el color de la piel debajo)."""
    for m in malla.data.materials:
        if not m or not m.node_tree:
            continue
        for n in m.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED" and n.inputs["Base Color"].is_linked:
                src = n.inputs["Base Color"].links[0].from_node
                if src.type == "TEX_IMAGE" and src.image:
                    return src.image
        for n in m.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                return n.image
    return None


ESTILOS_PELO = {
    # beagle (Milo): corto, duro y pegado al cuerpo, sin física
    "corto": {"largo": 0.016, "normal": 0.35, "peinado": (0.0, -0.9, -0.4), "guias": (24000, 100000), "hijos": (5, 10),
              "mechon": 0.07, "frizz": 1.0, "radio": 0.00016, "dinamica": False},
    # cría de gato: pelusa larga relativa al cuerpo, que sale hacia fuera, con frizz y física de tela.
    # Menos pelos guía (son los que se simulan) y muchos hijos interpolados alrededor: volumen sin costo de física.
    "esponjoso": {"largo": 0.07, "normal": 0.85, "peinado": (0.0, -0.35, -0.15), "guias": (8000, 24000), "hijos": (14, 40),
                  "mechon": 0.02, "frizz": 4.0, "radio": 0.00022, "dinamica": True},
}


def pelo(malla, v, J, pid, previa=None, patron=None, estilo=None):
    """Pelo con partículas de pelo (Cycles las renderiza como curvas): rompe el aspecto de plástico.
    Densidad por zonas: sin pelo en la trufa y menos en hocico y almohadillas. Color = textura del modelo.
    estilo: "corto" (Milo, por defecto) o "esponjoso" (cría de gato: pelusa con física de tela)."""
    import bpy as _b
    escena = _b.context.scene
    previa = escena.render.resolution_y <= 720 if previa is None else previa
    E = ESTILOS_PELO.get(str(estilo or "corto").lower(), ESTILOS_PELO["corto"])
    esponjoso = E is ESTILOS_PELO["esponjoso"]
    H = float(np.ptp(v[:, 2])) or 0.5
    # densidad: 1 en el cuerpo, 0 en la trufa, poca en el hocico y las almohadillas
    nariz = np.asarray(J.get("nariz", (0, 9, 9)), float)
    d_nariz = np.linalg.norm(v - nariz, axis=1)
    dens = np.clip((d_nariz - 0.03 * H) / (0.08 * H), 0, 1)
    dens = np.where(v[:, 2] < 0.03 * H, 0.3, dens)
    cab = np.asarray(J.get("cabeza", nariz), float)
    for lado in (1, -1):
        ojo = cab + 0.45 * (nariz - cab) + np.array([lado * 0.07 * H, 0, 0.05 * H])
        d_ojo = np.linalg.norm(v - ojo, axis=1)
        dens = np.minimum(dens, np.clip((d_ojo - 0.025 * H) / (0.03 * H), 0, 1))
    g = malla.vertex_groups.new(name="densidad_pelo")
    for i in np.nonzero(dens > 0.01)[0]:
        g.add([int(i)], float(dens[i]), "REPLACE")
    # largo por zonas (como el pelaje real de un beagle): cara y orejas muy cortas, hocico casi raso,
    # cuerpo corto, cola y parte de atrás de los muslos un poco más largos, patas cortas
    lg = largo_por_zonas(v, J, H)
    if esponjoso:
        lg = largo_esponjoso(v, J, H, lg)
    g2 = malla.vertex_groups.new(name="largo_pelo")
    for i in range(len(v)):
        g2.add([i], float(lg[i]), "REPLACE")
    # material de pelo
    m = _b.data.materials.new(f"Pelo_{pid}")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    salida = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    hair = nt.nodes.new("ShaderNodeBsdfHairPrincipled")
    try:
        hair.parametrization = "COLOR"
    except Exception:  # noqa: BLE001
        pass
    # la pelusa de cría es más fina y suave: más mate, con más dispersión (se ve "nube" y no cerda)
    ajustes = ((("Roughness", 0.6), ("Radial Roughness", 0.8), ("Coat", 0.0), ("IOR", 1.55), ("Random Roughness", 0.3))
               if esponjoso else
               (("Roughness", 0.48), ("Radial Roughness", 0.65), ("Coat", 0.0), ("IOR", 1.55), ("Random Roughness", 0.2)))
    for nombre, val in ajustes:
        if nombre in hair.inputs:
            hair.inputs[nombre].default_value = val
    img = _imagen_base(malla)
    if patron is not None:
        # el mismo patrón que la piel: cada pelo con el color de la raza donde nace (y la textura en los ojos)
        nt.links.new(PEL.nodos_color(nt, patron[0], patron[1]), hair.inputs["Color"])
    elif img is not None:
        uv = nt.nodes.new("ShaderNodeUVMap")  # Cycles da a cada pelo la UV de la piel donde nace
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        nt.links.new(uv.outputs["UV"], tex.inputs["Vector"])
        # el pelo real es un poco más oscuro y saturado que la textura difusa
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Saturation"].default_value = 1.1
        hsv.inputs["Value"].default_value = 1.0
        nt.links.new(tex.outputs["Color"], hsv.inputs["Color"])
        nt.links.new(hsv.outputs["Color"], hair.inputs["Color"])
    else:
        hair.inputs["Color"].default_value = (0.25, 0.14, 0.07, 1)
    nt.links.new(hair.outputs[0], salida.inputs["Surface"])
    malla.data.materials.append(m)
    # sistema de partículas de pelo (después del modificador de esqueleto: el pelo sigue la piel)
    malla.modifiers.new("Pelo", "PARTICLE_SYSTEM")
    ps = malla.particle_systems[-1]
    st = ps.settings
    st.type = "HAIR"
    st.count = E["guias"][0] if previa else E["guias"][1]
    largo = E["largo"] * H  # beagle de 50 cm: 8 mm · gatito de 18 cm: ~13 mm de pelusa
    st.hair_length = largo
    st.emit_from = "FACE"
    st.distribution = "RAND"
    st.use_emit_random = True
    # Con "avanzado", el LARGO del pelo lo dan las velocidades (en metros), no hair_length: deben ser del tamaño del pelo.
    # (Con normal = 1 los pelos medían un metro: el render salió como un erizo.)
    st.use_advanced_hair = True
    st.normal_factor = E["normal"] * largo  # corto: pegado a la piel · esponjoso: sale hacia fuera y da volumen
    px, py, pz = E["peinado"]
    st.object_align_factor = (px * largo, py * largo, pz * largo)  # peinado hacia la cola y hacia abajo
    st.length_random = 0.45 if not esponjoso else 0.55
    st.child_type = "INTERPOLATED"
    for attr, val in (("child_percent", 10), ("child_nbr", 1), ("display_percentage", 20)):
        if hasattr(st, attr):
            setattr(st, attr, val)  # solo afecta a la vista en pantalla (los nombres cambian entre versiones)
    st.rendered_child_count = E["hijos"][0] if previa else E["hijos"][1]
    st.child_length = 0.9
    st.clump_factor = E["mechon"]  # casi sin mechones: la pelusa de cría no se agrupa
    st.clump_shape = 0.3
    f = E["frizz"]
    st.roughness_1 = 0.002 * f * H / 0.5          # ondulación uniforme
    st.roughness_endpoint = 0.004 * f * H / 0.5   # puntas despeinadas
    if esponjoso:
        st.roughness_1_size = 0.6
        st.roughness_2 = 0.0015 * f * H / 0.5     # algunos pelos sueltos que se escapan: rompe lo "peinado" del CG
        st.roughness_2_threshold = 0.65
        if hasattr(st, "child_radius"):
            st.child_radius = 0.06 * H            # los hijos se reparten alrededor de su guía: más volumen
    st.hair_step = 4 if not esponjoso else 5      # un segmento más: la física dobla el pelo con suavidad
    st.display_step = 2
    st.render_step = 3 if not esponjoso else 4
    st.root_radius = 1.0
    st.tip_radius = 0.0
    st.radius_scale = E["radio"] * H / 0.5  # en la raíz; termina en punta
    st.material_slot = m.name
    ps.vertex_group_density = "densidad_pelo"
    ps.vertex_group_length = "largo_pelo"
    ps.seed = 7
    if E["dinamica"]:
        dinamica_pelo(ps, escena, pid)
    aviso(f"{pid}: pelo {'esponjoso con física de tela' if esponjoso else 'corto'}: {st.count} pelos guía × "
          f"{st.rendered_child_count} hijos, color de su textura")


def dinamica_pelo(ps, escena, pid):
    """Física de tela en los pelos guía (el solver de pelo de Blender es el de tela): la gravedad los dobla,
    los saltos y giros del baile los hacen rebotar y se asientan solos. Los hijos siguen a sus guías.
    Ajustes de pelusa de cría: poca masa, rigidez de flexión baja (se dobla) pero raíz firme (no se aplasta)."""
    ps.use_hair_dynamics = True
    cs = ps.cloth.settings
    for nombre, val in (
        ("quality", 6),                 # pasos por fotograma: estable con saltos rápidos
        ("mass", 0.002),                # pelusa: casi nada de inercia propia
        ("bending_stiffness", 0.08),    # se dobla con la gravedad y el movimiento (esponjoso, no rígido)
        ("bending_damping", 0.6),       # rebota una o dos veces y se asienta: tierno, no gelatina
        ("air_damping", 1.8),           # el aire frena las puntas: movimiento suave
        ("pin_stiffness", 1.2),         # la raíz se queda clavada en la piel: no se aplasta contra el cuerpo
        ("internal_friction", 0.15),    # los pelos se rozan entre sí: el pelaje se mueve como una masa
        ("collider_friction", 0.3),
        ("density_target", 0.0),
    ):
        if hasattr(cs, nombre):
            setattr(cs, nombre, val)
    try:
        cs.effector_weights.gravity = 1.0
    except Exception:  # noqa: BLE001
        pass
    pc = ps.point_cache
    pc.frame_start = int(escena.frame_start)
    pc.frame_end = int(escena.frame_end)
    aviso(f"{pid}: física de tela en el pelo (gravedad, rebote y asentamiento); se hornea antes del render")


def hornear_dinamica_pelo(escena):
    """Hornea la física del pelo de TODA la escena antes de renderizar. La simulación necesita avanzar fotograma a
    fotograma; la vista previa renderiza saltando fotogramas (frame_step 2) y sin hornear el pelo 'salta'."""
    sistemas = [(o, p) for o in escena.objects if o.type == "MESH" for p in o.particle_systems
                if getattr(p, "use_hair_dynamics", False)]
    if not sistemas:
        return 0
    for _, p in sistemas:
        p.point_cache.frame_start = int(escena.frame_start)
        p.point_cache.frame_end = int(escena.frame_end)
    paso = escena.frame_step
    escena.frame_step = 1
    try:
        with bpy.context.temp_override(scene=escena):
            bpy.ops.ptcache.free_bake_all()
            bpy.ops.ptcache.bake_all(bake=True)
        aviso(f"física del pelo horneada: {len(sistemas)} sistema(s), fotogramas {escena.frame_start}-{escena.frame_end}")
    except Exception as e:  # noqa: BLE001
        # sin hornear, Blender simula al vuelo: sigue sirviendo si se renderiza cada fotograma
        aviso(f"aviso: no pude hornear la física del pelo ({type(e).__name__}: {e}); se simula al renderizar")
    finally:
        escena.frame_step = paso
    escena.frame_set(escena.frame_start)
    return len(sistemas)


def largo_esponjoso(v, J, H, lg):
    """Largo de pelusa de cría de gato sobre el de zonas: mejillas, pecho y cola más largos (la 'nube' que da
    ternura), hocico y alrededor de la nariz cortos, orejas con penachos moderados y patas cortas pero suaves."""
    x, y, z = v.T
    lg = np.array(lg, float)
    nar = np.asarray(J.get("nariz", (0, 9, 9)), float)
    cab = np.asarray(J.get("cabeza", nar), float)
    hom = np.asarray(J.get("hombros", cab), float)
    # mejillas: a los lados de la cabeza, un poco por debajo del centro
    for lado in (1, -1):
        mej = cab + 0.3 * (nar - cab) + np.array([lado * 0.12 * H, 0, -0.06 * H])
        d = np.linalg.norm(v - mej, axis=1)
        lg = np.maximum(lg, 1.35 * np.clip(1 - d / (0.12 * H), 0, 1))
    # pecho: delante y debajo de los hombros, la "pechera" esponjosa
    pecho = hom + np.array([0, 0.06 * H, -0.18 * H])
    d = np.linalg.norm(v - pecho, axis=1)
    lg = np.maximum(lg, 1.5 * np.clip(1 - d / (0.2 * H), 0, 1))
    # cola: toda la cola algo más larga (en una cría la cola parece un plumero)
    if "cola_base" in J:
        cola = (y < J["cola_base"][1]) & (z > 0.35 * H)
        lg = np.where(cola, np.maximum(lg, 1.4), lg)
    # hocico y alrededor de la nariz: corto (si no, la cara se ve "borrada")
    d_n = np.linalg.norm(v - nar, axis=1)
    lg = np.where(d_n < 0.07 * H, np.minimum(lg, 0.3), lg)
    # patas: cortas pero no rasas
    lg = np.where(z < 0.2 * H, np.maximum(np.minimum(lg, 0.75), 0.6), lg)
    return np.clip(lg, 0.0, 1.6)


def largo_por_zonas(v, J, H):
    """Factor de largo del pelo por vértice (1 = cuerpo)."""
    x, y, z = v.T
    lg = np.ones(len(v))
    nar = np.asarray(J.get("nariz", (0, 9, 9)), float)
    cab = np.asarray(J.get("cabeza", nar), float)
    hom = np.asarray(J.get("hombros", cab), float)
    eje = nar - cab
    t = ((v - cab) @ eje) / max(float(eje @ eje), 1e-9)
    cabeza = (y > hom[1] + 0.25 * (cab[1] - hom[1])) & (z > 0.55 * H)
    lg = np.where(cabeza, 0.55, lg)
    lg = np.where(cabeza & (t > 0.55), 0.35, lg)  # hocico casi raso
    for s in "RL":
        if f"oreja_{s}_raiz" in J:
            d = np.linalg.norm(v - (np.asarray(J[f"oreja_{s}_raiz"]) + np.asarray(J[f"oreja_{s}_punta"])) / 2, axis=1)
            lg = np.where(d < 0.07 * H, 0.45, lg)
    if "cola_base" in J:
        cola = (y < J["cola_base"][1]) & (z > 0.45 * H)
        lg = np.where(cola, 1.3, lg)
        muslo = (y < J["cadera"][1] + 0.03 * H) & (y > J["cola_base"][1] - 0.05 * H) & (z > 0.25 * H) & (z < 0.5 * H)
        lg = np.where(muslo, 1.2, lg)
    lg = np.where(z < 0.25 * H, 0.6, lg)  # patas
    # transición suave entre zonas (promedio con el ruido de cada vértice no hace falta: el length_random ya varía)
    return lg


PARTES_HUESO = {"cabeza": "cuello2", "nariz": "cabeza", "cuerpo": "espalda", "cola": "cola_raiz"}


def animar_toma(info, pid, accion, f0, f1, fps, nota):
    arm, perro = info["arm"], info["perro"]
    plan = AC.plan_toma(info["B"], info["J"], accion, f0, f1, fps, nota)
    aviso(f"{pid} en {f0}-{f1}: {plan['modo']} a {plan['vel']:.2f} m/s" + (f" · {', '.join(sorted(plan['conductas']))}" if plan["conductas"] else ""))
    perro._cab_prev = None  # corte de plano: sin arrastrar inercia de la toma anterior
    rest = {pb.name: pb.bone.matrix_local.to_3x3() for pb in arm.pose.bones}
    inv = {n: m.inverted() for n, m in rest.items()}
    qprev = info["q_prev"]
    esc = bpy.context.scene
    paso = max(1, int(esc.frame_step))
    for f in range(int(f0), int(f1) + 1):
        t = f / fps
        est = dict(plan)
        est["t_toma"] = (f - f0) / fps
        r = perro.fotograma(t, est)  # se calcula cada fotograma (física de orejas y pasos exactos)
        for pata in r["contactos"]:
            EVENTOS.append((f, pid, pata, plan["modo"]))
        # trayectoria de las partes (en ejes del personaje, sin el rebote) para que la cámara las siga con suavidad
        G = perro.fk(r["R"])
        tray = info.setdefault("tray", {})
        for parte, hueso in PARTES_HUESO.items():
            if hueso in perro.B:
                tray.setdefault(parte, {})[f] = perro.pos(G, hueso, cola=True).tolist()
        if (f - esc.frame_start) % paso and f != int(f1):
            continue  # solo se guardan claves en los fotogramas que se van a renderizar
        for pb in arm.pose.bones:
            Rl = r["R"].get(pb.name)
            if Rl is None:
                q = Matrix.Identity(3).to_quaternion()
            else:
                q = (inv[pb.name] @ Matrix(Rl.tolist()) @ rest[pb.name]).to_quaternion()
            p = qprev.get(pb.name)
            if p is not None and p.dot(q) < 0:
                q.negate()
            qprev[pb.name] = q
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=f)
        arm.location = (0, 0, float(r["rebote"]))
        arm.keyframe_insert("location", frame=f)
