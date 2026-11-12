"""Forja3D Cine: monta el video final con FFmpeg (sin dependencias de Python).

Toma los fotogramas que renderizó Blender (salida/frames/f_####.png) y produce:
  salida/largo.mp4        master 16:9 (ficha técnica de YouTube: H.264 High, 4:2:0, BT.709, AAC 48 kHz, Fast Start)
  salida/short.mp4        recorte vertical 9:16 de hasta 3 minutos
  salida/miniatura.jpg    1280x720, menos de 2 MB
  salida/subtitulos.srt   a partir de los diálogos
  salida/metadatos.json   título, descripción (con capítulos, créditos y licencias), etiquetas y divulgación
El audio se mezcla por escena (ambiente + música + voces) y se normaliza a -14 LUFS.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402
from comun import aviso, error  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--guion", required=True)
ap.add_argument("--salida", default="salida")
ap.add_argument("--calidad", default="previa", choices=["previa", "final"])
ap.add_argument("--formatos", default="ambos", choices=["largo", "short", "ambos"])
ap.add_argument("--tomas", default="")
ap.add_argument("--raiz", default=os.getcwd())
ARGS = ap.parse_args()
PREVIA = ARGS.calidad == "previa"
RAIZ = os.path.abspath(ARGS.raiz)
SAL = os.path.abspath(ARGS.salida)
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

G = comun.filtrar_tomas(comun.cargar(ARGS.guion), ARGS.tomas)
FPS = G["fps"]
DUR = G["duracion_s"]


def ff(*args, desc="ffmpeg"):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        error(f"{desc} falló:\n{r.stderr[-2000:]}")


def ruta_asset(rel):
    if not rel:
        return None
    p = os.path.join(RAIZ, rel)
    if os.path.isfile(p):
        return p
    aviso(f"no encuentro {rel}; sigo sin ese audio")
    return None


# ---------------------------------------------------------------- audio
def mezclar_audio(destino):
    """Arma un filter_complex: una capa de ambiente por escena, música y voces, todo a -14 LUFS."""
    entradas, filtros, capas = [], [], []

    def entrada(*a):
        entradas.extend(a)
        return sum(1 for x in entradas if x == "-i") - 1

    for esc in G["escenas"]:
        ini, dur = esc["inicio_s"], esc["fin_s"] - esc["inicio_s"]
        amb = (esc.get("audio") or {}).get("ambiente")
        vol = float((esc.get("audio") or {}).get("volumen_ambiente_db", -6))
        archivo = ruta_asset(amb)
        if archivo:
            i = entrada("-stream_loop", "-1", "-i", archivo)
            fuente = f"[{i}:a]"
        else:
            # Sin archivo: "tono de sala" o viento suave sintético. El silencio absoluto se siente falso.
            interior = (esc.get("set") or {}).get("tipo") == "interior"
            color, corte, amp = ("brown", 400, 0.02) if interior else ("pink", 900, 0.05)
            i = entrada("-f", "lavfi", "-i", f"anoisesrc=color={color}:amplitude={amp}:sample_rate=48000:duration={dur + 1:.3f}")
            filtros.append(f"[{i}:a]lowpass=f={corte},aformat=channel_layouts=stereo[r{i}]")
            fuente = f"[r{i}]"
        ms = int(ini * 1000)
        filtros.append(f"{fuente}atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.4,afade=t=out:st={max(0, dur - 0.4):.3f}:d=0.4,"
                       f"volume={vol}dB,aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[e{len(capas)}]")
        capas.append(f"[e{len(capas)}]")
        for tom in esc["tomas"]:
            for d in tom.get("dialogo") or []:
                voz = ruta_asset(d.get("audio"))
                if voz:
                    j = entrada("-i", voz)
                    ms2 = int((tom["inicio_s"] + float(d.get("desde_s") or 0)) * 1000)
                    filtros.append(f"[{j}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms2}|{ms2}[v{len(capas)}]")
                    capas.append(f"[v{len(capas)}]")
    mus = G.get("musica") or {}
    archivo = ruta_asset(mus.get("archivo"))
    if archivo:
        i = entrada("-stream_loop", "-1", "-i", archivo)
        vol = float(mus.get("volumen_db", -14))
        filtros.append(f"[{i}:a]atrim=0:{DUR:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=1,afade=t=out:st={max(0, DUR - 2):.3f}:d=2,"
                       f"volume={vol}dB,aformat=sample_rates=48000:channel_layouts=stereo[m]")
        capas.append("[m]")
    filtros.append(f"{''.join(capas)}amix=inputs={len(capas)}:normalize=0:duration=longest,atrim=0:{DUR:.3f},"
                   "loudnorm=I=-14:TP=-1:LRA=11,aresample=48000[mix]")
    ff(*entradas, "-filter_complex", ";".join(filtros), "-map", "[mix]", "-ac", "2", "-ar", "48000", destino, desc="mezcla de audio")
    aviso(f"audio mezclado: {len(capas)} capas, normalizado a -14 LUFS")


# ---------------------------------------------------------------- video
def lista_fotogramas():
    frames = sorted(glob.glob(os.path.join(SAL, "frames", "f_*.png")))
    if not frames:
        error("no hay fotogramas en salida/frames: el render de Blender no produjo imágenes")
    stats = {}
    try:
        with open(os.path.join(SAL, "render_stats.json"), encoding="utf-8") as f:
            stats = json.load(f)
    except OSError:
        pass
    paso = int(stats.get("paso") or (2 if PREVIA else 1))
    # Lista del demuxer concat (funciona igual en Linux y Windows, sin patrones glob)
    lista = os.path.join(SAL, "fotogramas.txt")
    dur = paso / FPS
    with open(lista, "w", encoding="utf-8") as f:
        for fr in frames:
            f.write(f"file '{fr.replace(os.sep, '/').replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\nduration {dur:.6f}\n")
        f.write(f"file '{frames[-1].replace(os.sep, '/')}'\n")
    return lista, len(frames), stats


def codificar_largo(lista, audio, destino):
    res = "854:480" if PREVIA else ("3840:2160" if (G.get("render") or {}).get("entrega", "4k") == "4k" else "1920:1080")
    acabado = (G.get("acabado") or {})
    grano = int(acabado.get("grano", 5 if not PREVIA else 0))
    vineta = acabado.get("vineta", True)
    # format=rgb24: los fotogramas finales son PNG de 16 bits y la mezcla 'screen' del bloom se desborda en 16 bits
    # (colores rosas y verdes). El video sale en 8 bits de todas formas.
    vf = [f"fps={FPS}", f"scale={res}:flags=lanczos", "format=rgb24"]
    if grano:
        vf.append(f"noise=alls={grano}:allf=t")  # grano después de escalar, para que no se "derrita"
    if vineta:
        vf.append("vignette=angle=0.32")
    vf += ["scale=out_color_matrix=bt709:out_range=tv", "format=yuv420p"]
    calidad = ["-preset", "veryfast", "-crf", "23"] if PREVIA else ["-preset", "slow", "-crf", "16"]
    ff("-f", "concat", "-safe", "0", "-i", lista, "-i", audio, "-vf", ",".join(vf),
       "-c:v", "libx264", *calidad, "-profile:v", "high", "-bf", "2", "-g", str(FPS // 2),
       "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
       "-c:a", "aac", "-b:a", "384k", "-ar", "48000", "-t", f"{DUR:.3f}", "-movflags", "+faststart", destino, desc="video largo")


def codificar_short(largo, destino):
    cfg = {} if ARGS.tomas else (G.get("short") or {})  # en una previa parcial, el Short es el tramo renderizado
    desde = float(cfg.get("desde_s") or 0)
    hasta = min(float(cfg.get("hasta_s") or min(DUR, 60)), DUR, desde + 180)
    if hasta - desde < 1:
        aviso("short omitido: el tramo elegido dura menos de 1 s")
        return False
    ancho, alto = ("540", "960") if PREVIA else ("1080", "1920")
    # Recorte central 9:16 (la acción debe ir en el tercio central del cuadro 16:9)
    ff("-ss", f"{desde:.3f}", "-t", f"{hasta - desde:.3f}", "-i", largo,
       "-vf", f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={ancho}:{alto}:flags=lanczos,format=yuv420p",
       "-c:v", "libx264", "-preset", "veryfast" if PREVIA else "slow", "-crf", "23" if PREVIA else "17",
       "-c:a", "copy", "-movflags", "+faststart", destino, desc="short")
    return True


def miniatura(largo, destino):
    cfg = G.get("miniatura") or {}
    seg = float(cfg.get("segundo") if cfg.get("segundo") is not None else DUR * 0.4)
    ff("-ss", f"{min(seg, max(0, DUR - 0.1)):.3f}", "-i", largo, "-frames:v", "1",
       "-vf", "scale=1280:720:flags=lanczos,eq=contrast=1.06:saturation=1.08", "-q:v", "3", destino, desc="miniatura")
    if os.path.getsize(destino) > 2 * 1024 * 1024:
        ff("-i", destino, "-q:v", "6", destino + ".jpg", desc="miniatura comprimida")
        shutil.move(destino + ".jpg", destino)


# ---------------------------------------------------------------- textos
def licencias_usadas():
    try:
        with open(os.path.join(RAIZ, "assets", "licencias.json"), encoding="utf-8") as f:
            todas = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    texto = json.dumps(G, ensure_ascii=False).lower()
    usadas = []
    for clave, info in (todas.items() if isinstance(todas, dict) else []):
        if clave.startswith("_"):
            continue
        if clave.lower() in texto or info.get("siempre"):
            usadas.append(f"- {info.get('titulo') or clave}: {info.get('autor', 'autor desconocido')} · {info.get('licencia', '?')}"
                          + (f" · {info['url']}" if info.get("url") else ""))
    return usadas


def metadatos(archivos, stats):
    yt = G.get("youtube") or {}
    partes = [yt.get("descripcion") or G.get("sinopsis") or ""]
    caps = comun.capitulos(G)
    if caps:
        partes.append("Capítulos:\n" + "\n".join(caps))
    creditos = G.get("creditos") or []
    if creditos:
        partes.append("Créditos:\n" + "\n".join(f"- {c}" for c in creditos))
    lic = licencias_usadas()
    if lic:
        partes.append("Recursos y licencias:\n" + "\n".join(lic))
    mus = G.get("musica") or {}
    if mus.get("licencia"):
        partes.append(f"Música: {mus.get('titulo', '')} · {mus['licencia']}")
    partes.append("Animación hecha con Blender y Forja3D.")
    sintetico = bool(G.get("sintetico_realista", True))
    m = {
        "titulo": (yt.get("titulo") or G.get("titulo") or "Corto Forja3D")[:100],
        "descripcion": "\n\n".join(p for p in partes if p).strip()[:4900],
        "etiquetas": yt.get("etiquetas") or [],
        "categoria": str(yt.get("categoria") or "1"),  # 1 = Cine y animación
        "idioma": G.get("idioma") or "es",
        "para_ninos": bool(yt.get("para_ninos", False)),
        "sintetico_realista": sintetico,
        "duracion_s": round(DUR, 2),
        "calidad": ARGS.calidad,
        "archivos": archivos,
        "render": stats,
    }
    with open(os.path.join(SAL, "metadatos.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return m


def main():
    if not shutil.which(FFMPEG) and not os.path.isfile(FFMPEG):
        error("no encuentro ffmpeg: instálalo o define la variable FFMPEG")
    lista, n, stats = lista_fotogramas()
    aviso(f"montando {n} fotogramas · {DUR:.1f} s · {ARGS.calidad}")
    audio = os.path.join(SAL, "mezcla.wav")
    mezclar_audio(audio)
    archivos = []
    largo = os.path.join(SAL, "largo.mp4")
    codificar_largo(lista, audio, largo)
    if ARGS.formatos in ("largo", "ambos"):
        archivos.append("largo.mp4")
    if ARGS.formatos in ("short", "ambos") and codificar_short(largo, os.path.join(SAL, "short.mp4")):
        archivos.append("short.mp4")
    miniatura(largo, os.path.join(SAL, "miniatura.jpg"))
    archivos.append("miniatura.jpg")
    srt = comun.subtitulos(G)
    if srt:
        with open(os.path.join(SAL, "subtitulos.srt"), "w", encoding="utf-8") as f:
            f.write(srt)
        archivos.append("subtitulos.srt")
    if ARGS.formatos == "short" and os.path.isfile(largo):
        os.remove(largo)
    m = metadatos(archivos, stats)
    archivos.append("metadatos.json")
    aviso(f"listo: {', '.join(archivos)} · título: {m['titulo']}")


main()
