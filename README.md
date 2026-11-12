# 🎬 Estudio de cine Forja3D

Proyecto de cine animado en Blender que maneja el conector **Forja3D**. Le pides a Claude una historia y él:

```
Tú: "Un viejo farero encuentra una botella con un mensaje, 30 segundos, melancólico"
Claude → crear_guion        (guion + lista de tomas en guiones/<nombre>.json, con costo y tiempo estimados)
       → tú lo revisas y apruebas (aprobar_guion)
       → renderizar_video   (Blender arma sets, luz, personajes y cámaras; FFmpeg monta largo + Short)
       → estado_video       (enlaces del MP4, Short, miniatura y subtítulos)
       → publicar_youtube   (solo cuando tú lo apruebas; antes se sube como PRIVADO)
```

## Estructura

| Ruta | Qué es |
|---|---|
| `guiones/*.json` | Un guion por corto: personajes, escenas (set, luz, atmósfera, audio) y tomas (plano, lente, movimiento, acción, diálogo) |
| `assets/modelos/<nombre>/` | Modelos 3D (GLB, FBX, OBJ o .blend). Los de Meshy llegan aquí con `guardar_asset` |
| `assets/hdri/`, `assets/audio/` | HDRIs (Poly Haven, CC0) y sonidos/música **con licencia comercial** |
| `assets/licencias.json` | Registro de licencias: lo que use un guion aparece solo en la descripción de YouTube |
| `estudio/forja_cine.py` | Script de Blender: construye y renderiza cada escena (Cycles) |
| `estudio/montar.py` | FFmpeg: video 16:9, Short 9:16, audio a -14 LUFS, miniatura, SRT y metadatos |
| `estudio/youtube.py` | Subida a YouTube (privado), publicación y autorización |
| `.github/workflows/renderizar-video.yml` | Render en la nube de GitHub o en tu PC |
| `.github/workflows/publicar-youtube.yml` | Cambia la privacidad de un video ya subido |

## Calidades

| Calidad | Dónde | Qué hace | Tiempo típico |
|---|---|---|---|
| `previa` | nube (gratis en repos públicos) | 854×480, 12 fps repetidos a 24, 12 muestras | Unos minutos por cada 10 s de video |
| `final` | **tu PC con GPU** (`equipo: mi_pc`) | 2560×1440 escalado a 4K, 256 muestras, motion blur, profundidad de campo, grano | Depende de tu GPU: mide la previa y revisa `render_stats.json` |

La nube de GitHub no tiene GPU y corta los trabajos a las 6 horas: el render final va en tu computadora.

## Conectar tu Blender (equipo `mi_pc`)

Tu PC se registra como *runner* propio del repo. Así el conector manda el trabajo a **tu** Blender con **tu** GPU, igual que manda la compilación de Unity a GitHub.

1. Instala Blender 5.2 LTS, FFmpeg y Python 3, y que los tres estén en el PATH. En Windows instala también **Git for Windows** (trae `bash`).
2. En GitHub: este repo → **Settings → Actions → Runners → New self-hosted runner**. Elige tu sistema y sigue los comandos que te da.
3. Cuando `config` pregunte por etiquetas, escribe: `blender`
4. Si Blender no está en el PATH, crea el archivo `.env` en la carpeta del runner con la línea `BLENDER=C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (o tu ruta).
5. Arranca el runner (`run.cmd` o `./run.sh`), o instálalo como servicio para que esté siempre listo.

Si tu PC está apagada, el trabajo espera en cola hasta que el runner se conecte.

> Seguridad: los workflows del estudio solo arrancan a mano (o desde el conector), nunca desde un pull request, así que nadie de fuera puede mandar trabajos a tu PC. Aun así, GitHub recomienda runners propios en repos **privados**; si haces el repo privado, las vistas previas en la nube gastan de tus 2000 minutos gratis al mes.

## YouTube (opcional)

1. En [Google Cloud Console](https://console.cloud.google.com/) crea un proyecto, activa **YouTube Data API v3** y crea credenciales **OAuth → App de escritorio**.
2. En tu computadora: `python3 estudio/youtube.py autorizar --client-id TU_ID --client-secret TU_SECRETO` y acepta con la cuenta del canal.
3. Copia los tres valores que imprime como secretos del repo: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`.

Notas: los videos siempre se suben **privados**; YouTube deja privados los videos de proyectos de API no verificados hasta que pases su auditoría. Las miniaturas propias requieren canal verificado por teléfono. La cuota normal alcanza para unas 6 subidas al día.

## Reglas del estudio

- Solo modelos, HDRIs, música y efectos con licencia comercial, registrados en `assets/licencias.json`.
- Nada de rostros o voces de personas reales, ni personajes, logotipos o música con derechos de autor.
- Si el corto es realista, `sintetico_realista: true` (por defecto) marca en YouTube *contenido alterado o sintético*.
- Nada se publica en público sin tu aprobación.
