#!/usr/bin/env python3
"""Forja3D Cine · GPU alquilada en Vast.ai como runner de un solo uso.

  python3 estudio/gpu_vast.py encender   # alquila la GPU más barata que cumpla, la registra como runner y espera a que esté en línea
  python3 estudio/gpu_vast.py apagar     # destruye la instancia (se llama siempre al final, aunque el render falle o se cancele)

Solo biblioteca estándar. Variables de entorno:
  VAST_API_KEY (secreto), RUNNER_PAT (secreto: token fine-grained del repo con Administration RW),
  GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_OUTPUT (las pone Actions),
  GPU_MODELO (por defecto todas las de la tabla PUNTAJE), GPU_PRECIO_MAX (USD/h, por defecto 1.00), GPU_HORAS_MAX (por defecto 24),
  INSTANCIA (solo para apagar).
"""
import json, os, sys, time, urllib.request, urllib.error

VAST = os.environ.get("VAST_URL", "https://console.vast.ai").rstrip("/")
GH = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
RUN = os.environ.get("GITHUB_RUN_ID", "0")
ETIQUETA = f"gpu-{RUN}"
IMAGEN = os.environ.get("GPU_IMAGEN", "nvidia/cuda:12.4.1-base-ubuntu22.04")
ESPERA_MIN = float(os.environ.get("GPU_ESPERA_MIN", "10"))  # un servidor sano conecta en 2-4 min  # minutos para que el runner aparezca en línea
SONDEO = float(os.environ.get("GPU_SONDEO_S", "20"))


# Puntaje de Blender Open Data (mediana, OptiX; más = más rápido). Sirve para elegir la tarjeta
# por COSTO POR FOTOGRAMA (precio/h ÷ puntaje), no por precio por hora: una más cara pero más rápida puede salir igual o más barata.
# Fuente: Blender Open Data vía renderjuice.com (2026). Los marcados con ~ son aproximados.
PUNTAJE = {
    "RTX 5090": 15017, "RTX 4090": 11688, "RTX 3090 Ti": 6062, "RTX 3090": 5825,
    "RTX 5080": 9300,   # ~
    "RTX 4080S": 8700, "RTX 4080 SUPER": 8700, "RTX 4080": 8200,  # ~
    "L40S": 10500,      # ~
}


def log(m):
    print(f"[ForjaCine] {m}", flush=True)


def http(metodo, url, cuerpo=None, token=None, gh=False):
    h = {"Content-Type": "application/json", "Accept": "application/vnd.github+json" if gh else "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode() or "{}"
            return r.status, json.loads(t)
    except urllib.error.HTTPError as e:
        t = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(t)
        except ValueError:
            return e.code, {"error": t[:300]}


def vast(metodo, ruta, cuerpo=None):
    return http(metodo, VAST + ruta, cuerpo, os.environ["VAST_API_KEY"])


def github(metodo, ruta, cuerpo=None):
    return http(metodo, GH + ruta, cuerpo, os.environ["RUNNER_PAT"], gh=True)


def salida(clave, valor):
    f = os.environ.get("GITHUB_OUTPUT")
    if f:
        with open(f, "a", encoding="utf-8") as o:
            o.write(f"{clave}={valor}\n")


def buscar_ofertas():
    precio = float(os.environ.get("GPU_PRECIO_MAX") or 1.00)
    modelos = [m.strip() for m in (os.environ.get("GPU_MODELO") or ",".join(PUNTAJE)).split(",") if m.strip()]
    filtro = {
        "gpu_name": {"in": modelos},
        "num_gpus": {"eq": 1},
        "verified": {"eq": True},
        "rentable": {"eq": True},
        "reliability": {"gte": 0.98},
        "dph_total": {"lte": precio},
        "disk_space": {"gte": 60},
        "gpu_ram": {"gte": 15000},
        "inet_down": {"gte": 500},
        "inet_up": {"gte": 200},
        "inet_up_cost": {"lte": 0.02},
        "inet_down_cost": {"lte": 0.02},
        "cuda_max_good": {"gte": 12.4},
        "type": "on-demand",
        "order": [["dph_total", "asc"]],
        "limit": 64,
    }
    st, r = vast("POST", "/api/v0/bundles/", filtro)
    if st >= 400 or "offers" not in r:  # algunas versiones de la API piden la consulta dentro de "q"
        st, r = vast("POST", "/api/v0/bundles/", {"q": filtro})
    if st >= 400:
        raise SystemExit(f"[ForjaCine] ERROR: Vast.ai rechazó la búsqueda ({st}): {r}. ¿Es válido el secreto VAST_API_KEY?")
    vet = vetados()
    ofertas = [o for o in (r.get("offers") or []) if PUNTAJE.get(o.get("gpu_name"))
               and o.get("id") not in vet["ofertas"] and o.get("machine_id") not in vet["maquinas"]]
    base = PUNTAJE["RTX 4090"]
    fot, spf4090 = trabajo()
    arranque_h = float(os.environ.get("GPU_ARRANQUE_MIN") or 4) / 60
    for o in ofertas:
        vel = PUNTAJE[o["gpu_name"]] / base
        horas = arranque_h + fot * spf4090 / vel / 3600 + 2 / 60  # + montaje y subida
        o["_horas"] = horas
        o["_costo"] = o.get("dph_total", 99) * horas  # costo TOTAL estimado del trabajo (arranque incluido)
    ofertas.sort(key=lambda o: o["_costo"])
    log(f"{len(ofertas)} ofertas por menos de ${precio:.2f}/h · trabajo: {fot} fotogramas a ~{spf4090:.2f} s (en una 4090) + arranque")
    log("ordenadas por costo TOTAL estimado:")
    for o in ofertas[:6]:
        log(f"  {o['gpu_name']:<12} ${o.get('dph_total', 0):.3f}/h · {PUNTAJE[o['gpu_name']] / base:.2f}× · ~{o['_horas'] * 60:.0f} min · ≈ ${o['_costo']:.3f}")
    return ofertas


def vetados():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vast_vetados.json"), encoding="utf-8") as f:
            d = json.load(f)
        return {"ofertas": set(d.get("ofertas") or []), "maquinas": set(d.get("maquinas") or [])}
    except Exception:  # noqa: BLE001
        return {"ofertas": set(), "maquinas": set()}


def trabajo():
    """Fotogramas a renderizar (según guion, calidad y tomas) y segundos por fotograma medidos (equivalente 4090)."""
    fot, spf = 700, 1.0
    calidad = os.environ.get("CALIDAD", "previa")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import comun
        G = comun.filtrar_tomas(comun.cargar(os.environ.get("GUION") or ""), os.environ.get("TOMAS", ""))
        paso = int((G.get("render") or {}).get("paso_previa") or 2) if calidad == "previa" else 1
        fot = sum(len(range(e["tomas"][0]["f0"], e["tomas"][-1]["f1"] + 1, paso)) for e in G["escenas"])
    except (Exception, SystemExit) as e:  # noqa: BLE001  (comun.error sale con SystemExit)
        log(f"(no pude contar los fotogramas del guion: {e}; supongo {fot})")
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "medidas.json"), encoding="utf-8") as f:
            spf = float(json.load(f)[calidad]["spf_4090"])
    except Exception:  # noqa: BLE001
        pass
    return fot, spf


def publicar_estimacion(o):
    """Deja el costo estimado como estado del commit (lo lee Claude para avisarte antes de que empiece a gastar)."""
    token, sha = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_SHA")
    if not (token and sha):
        return
    txt = f"{o['gpu_name']} ${o.get('dph_total', 0):.3f}/h - ~{o['_horas'] * 60:.0f} min - costo estimado ${o['_costo']:.2f}"
    http("POST", f"{GH}/repos/{REPO}/statuses/{sha}", {"state": "pending", "context": f"forja/costo-{RUN}", "description": txt}, token, gh=True)


RUNNER_VERSION = os.environ.get("GPU_RUNNER_VERSION", "2.338.0")

SCRIPT_ARRANQUE = r"""#!/bin/bash
mkdir -p /var/log && exec > >(tee -a /var/log/forja-gpu.log) 2>&1
set -x
echo "[forja] arranque $(date -u +%H:%M:%S)"
morir(){ echo "[forja] fin: $(date -u +%H:%M:%S)"; curl -s -X DELETE -H "Authorization: Bearer $CONTAINER_API_KEY" "https://console.vast.ai/api/v0/instances/$CONTAINER_ID/" || true; }
trap morir EXIT
export DEBIAN_FRONTEND=noninteractive
# Solo lo mínimo con apt (Blender y el runner); FFmpeg ya compilado desde GitHub, en paralelo con el runner
apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates git python3 xz-utils libgl1 libegl1 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 libicu70 >/dev/null || echo "[forja] ERROR apt"
echo "[forja] apt listo $(date -u +%H:%M:%S)"
( curl -fsSL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz | tar -xJ -C /opt && \
  ln -sf /opt/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg && ln -sf /opt/ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/ffprobe && \
  echo "[forja] ffmpeg listo $(date -u +%H:%M:%S)" || echo "[forja] ERROR ffmpeg" ) &
FFPID=$!
# Blender se descarga en paralelo mientras el runner se conecta (el workflow espera a que termine)
( touch /root/.forja_blender_descargando
  VER="__BLENDER__"; MM="${VER%.*}"
  for BASE in https://mirrors.ocf.berkeley.edu/blender/release https://download.blender.org/release https://mirror.clarkson.edu/blender/release https://ftp.halifax.rwth-aachen.de/blender/release; do
    if curl -fsSL --retry 2 -A "Mozilla/5.0 (ForjaCine)" -o /tmp/blender.tar.xz "$BASE/Blender$MM/blender-$VER-linux-x64.tar.xz"; then
      mkdir -p /root/blender && tar -xJf /tmp/blender.tar.xz -C /root/blender --strip-components=1 && echo "[forja] blender listo $(date -u +%H:%M:%S)" && break
    fi
  done
  rm -f /tmp/blender.tar.xz /root/.forja_blender_descargando ) &
timeout 30 nvidia-smi || echo "[forja] ERROR sin GPU visible"
mkdir -p /runner && cd /runner
curl -fsSL -o r.tgz "https://github.com/actions/runner/releases/download/v__RV__/actions-runner-linux-x64-__RV__.tar.gz" && tar xzf r.tgz && rm r.tgz || echo "[forja] ERROR descarga runner"
./bin/installdependencies.sh >/dev/null 2>&1 || echo "[forja] aviso installdependencies"
wait "$FFPID"
export RUNNER_ALLOW_RUNASROOT=1
echo "[forja] runner listo, conectando $(date -u +%H:%M:%S)"
set +x  # que el token de un solo uso no salga en el log
timeout "__HORAS__h" ./run.sh --jitconfig "$(cat /root/forja_jit)"
echo "[forja] runner salió con código $?"
"""


def script_arranque(jit, horas, blender=None):
    # El JIT va dentro del propio script (se escribe en un archivo al arrancar): no depende de cómo Vast pase las variables
    return ("#!/bin/bash\nprintf '%s' '" + jit + "' > /root/forja_jit\n" +
            SCRIPT_ARRANQUE.split("\n", 1)[1].replace("__RV__", RUNNER_VERSION).replace("__HORAS__", str(horas))
            .replace("__BLENDER__", blender or os.environ.get("BLENDER_VERSION") or "5.2.0"))


def log_instancia(inst):
    """Trae las últimas líneas del log de la máquina (para saber por qué no conectó)."""
    try:
        st, r = vast("PUT", f"/api/v0/instances/request_logs/{inst}/", {"tail": "80"})
        url = r.get("result_url") if st < 400 else None
        if not url:
            log(f"(no pude pedir el log de la instancia: {st} {r})")
            return
        for _ in range(8):
            time.sleep(5)
            try:
                with urllib.request.urlopen(url, timeout=30) as f:
                    texto = f.read().decode(errors="replace")
                break
            except urllib.error.HTTPError:
                continue
        else:
            log("(el log de la instancia no llegó a tiempo)")
            return
        log(f"--- log de la instancia {inst} ---")
        for l in texto.splitlines()[-80:]:
            print(f"[ForjaCine] | {l}", flush=True)
    except Exception as e:  # nunca debe impedir que se destruya la máquina
        log(f"(error leyendo el log de la instancia: {e})")



def runner_en_linea(nombre):
    st, r = github("GET", f"/repos/{REPO}/actions/runners?per_page=100")
    for x in r.get("runners", []) if st < 400 else []:
        if x.get("name") == nombre:
            return x.get("status") == "online", x.get("id")
    return False, None


def destruir(inst):
    st, r = vast("DELETE", f"/api/v0/instances/{inst}/")
    if st == 404 or r.get("error") == "no_such_instance":
        log(f"instancia {inst} ya estaba destruida (se apagó sola al terminar)")
        return True
    log(f"instancia {inst} destruida" if st < 400 else f"no pude destruir la instancia {inst} ({st}): {r}")
    return st < 400


def encender():
    for v in ("VAST_API_KEY", "RUNNER_PAT"):
        if not os.environ.get(v):
            raise SystemExit(f"[ForjaCine] ERROR: falta el secreto {v} en el repo (Settings → Secrets and variables → Actions)")
    horas = os.environ.get("GPU_HORAS_MAX") or "24"
    ofertas = buscar_ofertas()
    if not ofertas:
        raise SystemExit("[ForjaCine] ERROR: no hay GPUs disponibles con esos filtros. Sube la variable GPU_PRECIO_MAX o borra GPU_MODELO para que pruebe todos los modelos")
    usados = set()
    candidatas = []
    for o in ofertas:  # cada intento en una máquina física distinta (si una falla, su vecina suele fallar igual)
        clave = (o.get("machine_id"), o.get("host_id"))
        if clave in usados and clave != (None, None):
            continue
        usados.add(clave)
        candidatas.append(o)
    for n, o in enumerate(candidatas[:int(os.environ.get('GPU_INTENTOS', '2'))], 1):  # tope de gasto si algo falla
        nombre = f"vast-{RUN}-{n}"
        st, jit = github("POST", f"/repos/{REPO}/actions/runners/generate-jitconfig",
                         {"name": nombre, "runner_group_id": 1, "labels": ["self-hosted", "linux", "x64", ETIQUETA], "work_folder": "_work"})
        if st >= 400:
            raise SystemExit(f"[ForjaCine] ERROR: GitHub no dejó registrar el runner ({st}): {jit.get('message')}. El secreto RUNNER_PAT necesita permiso Administration: Read and write en este repo")
        log(f"alquilando {o.get('gpu_name')} (oferta {o['id']}, máquina {o.get('machine_id')}) a ${o.get('dph_total', 0):.3f}/h · fiabilidad {o.get('reliability', o.get('reliability2', '?'))}"
            + (f" · costo estimado del trabajo ≈ ${o['_costo']:.2f}" if "_costo" in o else ""))
        if n == 1 and "_costo" in o:
            publicar_estimacion(o)
        st, r = vast("PUT", f"/api/v0/asks/{o['id']}/", {
            "image": IMAGEN, "disk": 60, "label": f"forja3d-{REPO.split('/')[-1]}-{RUN}",
            "env": {"NVIDIA_DRIVER_CAPABILITIES": "all"},
            "onstart": script_arranque(jit["encoded_jit_config"], horas),
        })
        inst = r.get("new_contract") if st < 400 else None
        if not inst and r.get("error") == "insufficient_credit":
            if jit.get("runner"):
                github("DELETE", f"/repos/{REPO}/actions/runners/{jit['runner']['id']}")
            raise SystemExit("[ForjaCine] ERROR: tu cuenta de Vast.ai no tiene saldo. Recarga en https://cloud.vast.ai/billing/ y vuelve a lanzar el render")
        if not inst:
            log(f"la oferta {o['id']} ya no está disponible ({st}: {r}); pruebo la siguiente")
            github("DELETE", f"/repos/{REPO}/actions/runners/{jit.get('runner', {}).get('id')}") if jit.get("runner") else None
            continue
        salida("instancia", inst)  # a partir de aquí el job 'apagar_gpu' la destruye pase lo que pase
        limite = time.time() + ESPERA_MIN * 60
        while time.time() < limite:
            ok, _ = runner_en_linea(nombre)
            if ok:
                log(f"✅ GPU lista: runner '{nombre}' en línea (instancia {inst}). Empieza el render.")
                salida("runner", nombre)
                return
            _, i = vast("GET", f"/api/v0/instances/{inst}/")
            estado = (i.get("instances") or {}).get("actual_status") if isinstance(i.get("instances"), dict) else None
            if estado in ("exited", "offline"):
                log(f"la instancia {inst} se apagó sola ({estado})")
                break
            log(f"esperando a la GPU… ({estado or 'arrancando'})")
            time.sleep(SONDEO)
        log(f"la instancia {inst} no conectó a tiempo; traigo su log, la destruyo y pruebo otra máquina"
            f" (para vetarla: oferta {o['id']}, máquina {o.get('machine_id')} → estudio/vast_vetados.json)")
        log_instancia(inst)
        destruir(inst)
        rid = (jit.get("runner") or {}).get("id")
        if rid:
            github("DELETE", f"/repos/{REPO}/actions/runners/{rid}")
        salida("instancia", "")
    raise SystemExit("[ForjaCine] ERROR: ninguna GPU de Vast.ai llegó a conectarse. Vuelve a lanzar el render en un rato")


def apagar():
    ids = {i.strip() for i in os.environ.get("INSTANCIA", "").split(",") if i.strip()}
    # También busca por etiqueta: cubre el caso de cancelar justo mientras se encendía la GPU
    etiqueta = f"forja3d-{REPO.split('/')[-1]}-{RUN}"
    st, r = vast("GET", "/api/v0/instances/")
    for x in (r.get("instances") or []) if st < 400 and isinstance(r.get("instances"), list) else []:
        if x.get("label") == etiqueta:
            ids.add(str(x.get("id")))
    if not ids:
        log("no hay instancia que apagar")
        return
    fallos = []
    for inst in sorted(ids):
        for intento in range(3):
            if destruir(inst):
                break
            time.sleep(10)
        else:
            fallos.append(inst)
    if fallos:
        raise SystemExit(f"[ForjaCine] ERROR: no pude destruir {', '.join(fallos)} en Vast.ai: bórralas a mano en cloud.vast.ai → Instances para que no sigan cobrando")


if __name__ == "__main__":
    {"encender": encender, "apagar": apagar}[sys.argv[1] if len(sys.argv) > 1 else ""]()
