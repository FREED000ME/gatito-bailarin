"""Forja3D Cine: cliente mínimo de la API de datos de YouTube v3 (solo biblioteca estándar de Python).

Subcomandos:
  autorizar --client-id X --client-secret Y
      Se ejecuta UNA vez en tu computadora. Abre el permiso de Google y te imprime el REFRESH TOKEN que
      guardas como secreto YOUTUBE_REFRESH_TOKEN en GitHub (nunca en el código ni en el conector).
  subir --salida salida [--privacidad private]
      Sube largo.mp4 y short.mp4 con su título, descripción, miniatura y subtítulos. Por defecto PRIVADO.
      Marca "contenido alterado o sintético" (status.containsSyntheticMedia) si el guion es realista.
  publicar --video ID --privacidad public|unlisted|private [--publicar-en 2026-11-20T18:00:00Z]
      Cambia la privacidad de un video ya subido (o lo programa). Solo lo lanza el conector cuando tú lo apruebas.

Variables de entorno: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN.
"""
import argparse
import http.server
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comun import aviso, error  # noqa: E402

API = os.environ.get("YOUTUBE_API_URL", "https://www.googleapis.com")
OAUTH = os.environ.get("YOUTUBE_OAUTH_URL", "https://oauth2.googleapis.com")
ALCANCES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"
TROZO = 8 * 1024 * 1024


def peticion(metodo, url, cuerpo=None, cabeceras=None, reintentos=5):
    """HTTP con reintentos exponenciales en 5xx/429 (cuota y errores temporales de Google)."""
    for intento in range(reintentos):
        req = urllib.request.Request(url, data=cuerpo, method=metodo, headers=cabeceras or {})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            datos = e.read()
            if e.code == 308:  # subida reanudable: "sigue enviando"
                return 308, dict(e.headers), datos
            if e.code in (429, 500, 502, 503, 504) and intento < reintentos - 1:
                espera = 2 ** intento + 1
                aviso(f"YouTube respondió {e.code}; reintento en {espera} s")
                time.sleep(espera)
                continue
            raise RuntimeError(f"YouTube {e.code}: {datos.decode('utf-8', 'replace')[:800]}") from None
        except urllib.error.URLError as e:
            if intento < reintentos - 1:
                time.sleep(2 ** intento + 1)
                continue
            raise RuntimeError(f"sin conexión con YouTube: {e}") from None
    raise RuntimeError("YouTube no respondió")


def token():
    faltan = [v for v in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN") if not os.environ.get(v)]
    if faltan:
        error(f"faltan los secretos {', '.join(faltan)} en el repo (Settings → Secrets → Actions). Ver README.md, sección YouTube")
    cuerpo = urllib.parse.urlencode({"client_id": os.environ["YOUTUBE_CLIENT_ID"], "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                                     "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"}).encode()
    try:
        _, _, datos = peticion("POST", f"{OAUTH}/token", cuerpo, {"Content-Type": "application/x-www-form-urlencoded"})
    except RuntimeError as e:
        error(f"no pude renovar el permiso de YouTube ({e}). Si dice invalid_grant, vuelve a ejecutar 'autorizar' y actualiza YOUTUBE_REFRESH_TOKEN")
    return json.loads(datos)["access_token"]


def subir_video(tok, ruta, snippet, status):
    tam = os.path.getsize(ruta)
    meta = json.dumps({"snippet": snippet, "status": status}).encode()
    _, cab, _ = peticion("POST", f"{API}/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", meta, {
        "Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(tam)})
    destino = cab.get("Location") or cab.get("location")
    if not destino:
        error("YouTube no devolvió la URL de subida")
    enviado = 0
    with open(ruta, "rb") as f:
        while enviado < tam:
            f.seek(enviado)
            trozo = f.read(TROZO)
            fin = enviado + len(trozo) - 1
            codigo, cab, datos = peticion("PUT", destino, trozo, {
                "Authorization": f"Bearer {tok}", "Content-Length": str(len(trozo)), "Content-Range": f"bytes {enviado}-{fin}/{tam}"})
            if codigo == 308:
                rango = cab.get("Range") or cab.get("range")
                enviado = int(rango.split("-")[1]) + 1 if rango else enviado
                aviso(f"subiendo {os.path.basename(ruta)}: {enviado * 100 // tam}%")
                continue
            return json.loads(datos)
    error("la subida terminó sin respuesta de YouTube")


def miniatura(tok, video_id, ruta):
    with open(ruta, "rb") as f:
        peticion("POST", f"{API}/upload/youtube/v3/thumbnails/set?videoId={video_id}&uploadType=media", f.read(),
                 {"Authorization": f"Bearer {tok}", "Content-Type": "image/jpeg"})


def subtitulos(tok, video_id, ruta, idioma):
    limite = uuid.uuid4().hex
    meta = json.dumps({"snippet": {"videoId": video_id, "language": idioma, "name": "Subtítulos", "isDraft": False}})
    with open(ruta, "rb") as f:
        srt = f.read()
    cuerpo = (f"--{limite}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
              f"--{limite}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + srt + f"\r\n--{limite}--\r\n".encode()
    peticion("POST", f"{API}/upload/youtube/v3/captions?uploadType=multipart&part=snippet", cuerpo,
             {"Authorization": f"Bearer {tok}", "Content-Type": f"multipart/related; boundary={limite}"})


def cmd_subir(a):
    sal = os.path.abspath(a.salida)
    with open(os.path.join(sal, "metadatos.json"), encoding="utf-8") as f:
        m = json.load(f)
    if a.privacidad != "private":
        aviso("por seguridad, la subida automática siempre es privada; publica después con 'publicar'")
    tok = token()
    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": bool(m.get("para_ninos")),
              "containsSyntheticMedia": bool(m.get("sintetico_realista")), "embeddable": True}
    resultado = {}
    for archivo, sufijo in (("largo.mp4", ""), ("short.mp4", " #Shorts")):
        ruta = os.path.join(sal, archivo)
        if not os.path.isfile(ruta) or archivo not in m.get("archivos", []):
            continue
        snippet = {"title": (m["titulo"][:100 - len(sufijo)] + sufijo), "description": m["descripcion"],
                   "tags": m.get("etiquetas", []), "categoryId": m.get("categoria", "1"),
                   "defaultLanguage": m.get("idioma", "es"), "defaultAudioLanguage": m.get("idioma", "es")}
        v = subir_video(tok, ruta, snippet, status)
        vid = v["id"]
        resultado[archivo.split(".")[0]] = {"id": vid, "url": f"https://youtu.be/{vid}", "studio": f"https://studio.youtube.com/video/{vid}/edit"}
        aviso(f"subido {archivo} como PRIVADO: https://youtu.be/{vid}")
        if archivo == "largo.mp4" and os.path.isfile(os.path.join(sal, "miniatura.jpg")):
            try:
                miniatura(tok, vid, os.path.join(sal, "miniatura.jpg"))
            except RuntimeError as e:
                aviso(f"aviso: no se pudo poner la miniatura ({e}). YouTube exige verificar el canal por teléfono para miniaturas propias")
        if os.path.isfile(os.path.join(sal, "subtitulos.srt")):
            try:
                subtitulos(tok, vid, os.path.join(sal, "subtitulos.srt"), m.get("idioma", "es"))
            except RuntimeError as e:
                aviso(f"aviso: no se pudieron subir los subtítulos ({e})")
    with open(os.path.join(sal, "youtube.json"), "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2)
    if not resultado:
        error("no había videos para subir")


def cmd_publicar(a):
    tok = token()
    _, _, datos = peticion("GET", f"{API}/youtube/v3/videos?part=status&id={urllib.parse.quote(a.video)}", None,
                           {"Authorization": f"Bearer {tok}"})
    items = json.loads(datos).get("items") or []
    if not items:
        error(f"no encuentro el video {a.video} en tu canal")
    status = items[0]["status"]
    # videos.update borra las propiedades que no se reenvían: se manda el estado completo
    nuevo = {k: status[k] for k in ("privacyStatus", "embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia") if k in status}
    if a.publicar_en:
        nuevo["privacyStatus"] = "private"
        nuevo["publishAt"] = a.publicar_en
    else:
        nuevo["privacyStatus"] = a.privacidad
    cuerpo = json.dumps({"id": a.video, "status": nuevo}).encode()
    peticion("PUT", f"{API}/youtube/v3/videos?part=status", cuerpo, {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    aviso(f"video {a.video}: {'programado para ' + a.publicar_en if a.publicar_en else 'ahora es ' + a.privacidad} · https://youtu.be/{a.video}")


def cmd_autorizar(a):
    codigo = {}

    class Receptor(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            codigo.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Listo. Ya puedes cerrar esta pestaña y volver a la terminal.</h2>".encode())

        def log_message(self, *x):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Receptor)
    redir = f"http://127.0.0.1:{srv.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": a.client_id, "redirect_uri": redir, "response_type": "code", "scope": ALCANCES,
        "access_type": "offline", "prompt": "consent"})
    print(f"\nAbre este enlace, elige la cuenta de tu canal y acepta:\n{url}\n")
    webbrowser.open(url)
    while "code" not in codigo and "error" not in codigo:
        srv.handle_request()
    if "error" in codigo:
        error(f"Google rechazó el permiso: {codigo['error']}")
    cuerpo = urllib.parse.urlencode({"code": codigo["code"], "client_id": a.client_id, "client_secret": a.client_secret,
                                     "redirect_uri": redir, "grant_type": "authorization_code"}).encode()
    _, _, datos = peticion("POST", f"{OAUTH}/token", cuerpo, {"Content-Type": "application/x-www-form-urlencoded"})
    rt = json.loads(datos).get("refresh_token")
    if not rt:
        error("Google no devolvió refresh_token; revoca el acceso en myaccount.google.com/permissions y repite")
    print("\n✅ Copia estos tres valores como secretos del repo (Settings → Secrets and variables → Actions):")
    print(f"YOUTUBE_CLIENT_ID     = {a.client_id}\nYOUTUBE_CLIENT_SECRET = {a.client_secret}\nYOUTUBE_REFRESH_TOKEN = {rt}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("subir")
    s.add_argument("--salida", default="salida")
    s.add_argument("--privacidad", default="private")
    p = sub.add_parser("publicar")
    p.add_argument("--video", required=True)
    p.add_argument("--privacidad", default="public", choices=["public", "unlisted", "private"])
    p.add_argument("--publicar-en", default="")
    z = sub.add_parser("autorizar")
    z.add_argument("--client-id", required=True)
    z.add_argument("--client-secret", required=True)
    a = ap.parse_args()
    {"subir": cmd_subir, "publicar": cmd_publicar, "autorizar": cmd_autorizar}[a.cmd](a)


if __name__ == "__main__":
    main()
