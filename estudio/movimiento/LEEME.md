# Matriz de movimiento de cuadrúpedos

`perro.json` es lo que el estudio "sabe" de cómo se mueve un perro. La usa `anim_cuadrupedo.py` en cada render:

- **Temporización por marcha** (`caminar`, `trotar`, `galopar`): fracción del ciclo con la pata apoyada (`duty`), desfase de cada pata (`fase`), zancada en alturas de cadera (`zancada`) y altura del paso (`alza`).
- **Pelvis**: balanceo (`roll`), giro (`yaw`) y cabeceo (`pitch`) como `[ROM en grados, ciclos por zancada]`.
- **Articulaciones** (hombro, codo, carpo, cadera, rodilla, tarso): curva de flexión/extensión en coeficientes de Fourier (`fourier` = a0, a1, b1… a4, b4), normalizada a [-1, 1] (+1 = máxima extensión), más su rango (`rom`, grados) y cuánto crece con la velocidad (`rom_por_mps`).
- La fase 0 es el instante en que la pata toca el suelo.

## De dónde sale

La versión actual sale de **cinemática veterinaria publicada** (beagles y otras razas, ver `fuentes` en el JSON). Al ser datos científicos, se puede usar comercialmente.

Regenerarla: `python3 estudio/aprender_movimiento.py literatura`

## Aprender de videos (más adelante)

1. Graba (o consigue con licencia) un video **lateral** del perro caminando, trotando o corriendo en línea recta, a 60 fps si se puede.
2. Marca en cada fotograma las articulaciones de la pata que da a la cámara. Sirven:
   - **Kinovea** (gratis): marcado manual y exportación a CSV.
   - **DeepLabCut** con el modelo SuperAnimal-Quadruped: marcado automático. Revisa la licencia del modelo antes de usarlo comercialmente.
3. Deja el CSV con estas columnas (en píxeles, con la *y* hacia abajo como en la imagen):
   `frame, hombro_x, hombro_y, codo_x, codo_y, carpo_x, carpo_y, mano_x, mano_y, cadera_x, cadera_y, rodilla_x, rodilla_y, tarso_x, tarso_y, pie_x, pie_y`
4. Ejecuta:
   `python3 estudio/aprender_movimiento.py video puntos.csv --fps 60 --marcha trotar --velocidad 1.6 --peso 1`

El script mide los ángulos, detecta los apoyos y separa los ciclos. Después promedia las curvas y las mezcla en la matriz con el `peso` indicado. La lista `aprendido_de` guarda de qué videos aprendió.

Cada raza o personaje nuevo puede tener su propia matriz, por ejemplo `movimiento/gato.json` o `movimiento/perro_galgo.json`.

Para comprobar que el aprendizaje funciona: `python3 estudio/aprender_movimiento.py prueba`
