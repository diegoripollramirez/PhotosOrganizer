# Organizador de fotos

## Instalación

- Python 3.10 o superior
- **ExifTool** (https://exiftool.org), en `C:\Windows\` o junto a los scripts. Es casi imprescindible: sin él no se leen las fechas de las fotos HEIC de iPhone ni de los vídeos.
- Para los duplicados visuales: `python -m pip install pillow imagehash pillow-heif`
- Opcional (solo si no tienes ExifTool): `python -m pip install piexif`

Los scripts pueden estar en cualquier carpeta, siempre que estén todos juntos (usan `comun.py`).

## Uso

Lo normal es lanzar `0_pipeline.py`: solo pregunta la carpeta de PENDIENTES y la MAESTRA, y ejecuta todos los pasos seguidos. Si un paso falla o se cancela, se detiene. La única pausa es la revisión de duplicados visuales.

Pasos del pipeline y script que usa cada uno:

1. `1_extraer_zips.py`. Los ZIP partidos de un mismo Takeout se fusionan en una sola carpeta. Si no hay ZIP nuevos, no hace nada.
2. `2_renombrar_fotos.py`. Renombra a `YYYY-MM-DD_HH-MM-SS_desc.ext` y empareja los Live Photos.
3. `3_eliminar_duplicados.py`, modo 1 (duplicados exactos). Va **antes** de tocar ningún byte de las fotos.
4. `4_escribir_fechas_exif.py`. Se salta si no hay ExifTool.
5. `5_limpiar_archivos.py`. Solo aparta Thumbs.db, desktop.ini y ZIP ya extraídos; nunca bases de datos de digiKam, ZIP sin extraer ni JSON (salvo que los pidas).
6. `3_eliminar_duplicados.py`, modo 2 (duplicados visuales). Revisas un HTML con miniaturas y siempre confirmas tú.
7. `6_verificar_coleccion.py`. Solo lectura.
8. `7_mover_a_maestra.py`. Guarda en cada foto el nombre de su carpeta como etiqueta de álbum (p. ej. "Roma 2015") y pasa lo que está listo (nombre YYYY-MM-DD…) a la raíz de la maestra, cada foto con su vídeo Live; sus JSON van a `_basura/`. Lo que necesita revisión se queda en pendientes, en su carpeta. Las carpetas que no son álbumes (Takeout, WhatsApp, 2015…) se listan en `carpetas_genericas.txt`, que puedes editar.

Para lanzar un paso a mano, ejecuta su script; en `3_eliminar_duplicados.py` eliges el modo en el menú.

Fuera del flujo: `8_deshacer.py` y `9_analizar_nombres.py` (diagnóstico de nombres, opcional).

Cada script también funciona suelto, con doble clic o con argumentos (`--help` muestra las opciones). Renombrar y escribir EXIF guardan el plan completo en `logs/plan_renombrado.txt` y `logs/plan_fechas.txt` antes de pedirte SI, para que puedas revisarlo. Con `--simular` solo generan ese plan y terminan.

## Deshacer

Cada vez que se renombra o se mueve algo (renombrar, limpiar, duplicados, mover a la maestra) se guarda un CSV en `logs/deshacer/`. `8_deshacer.py` lo revierte. Deshaz siempre empezando por el más reciente.

Los cambios de **fechas EXIF no se pueden deshacer**: haz una copia de seguridad antes del paso 4 si tienes dudas.

## Dónde queda cada cosa

- `logs/`: los informes de la última ejecución (`renombrados.txt`, `sin_fecha.txt`, `duplicados_exactos.txt`, `duplicados_visuales.html`, `verificacion_coleccion.txt`…). Los de ejecuciones anteriores se guardan en `logs/anteriores/<fecha_hora>/`.
- `_duplicados/` (junto a los scripts): duplicados apartados, conservando su ruta original.
- `_basura/` (dentro de la carpeta limpiada): archivos innecesarios apartados.
- `cache_fotos.db` (junto a los scripts): caché con lo ya calculado de cada foto (metadatos, MD5, huellas visuales). Hace que a partir de la segunda ejecución solo se analicen las fotos nuevas. Se puede borrar sin problema: se reconstruye sola. Para prepararla por adelantado, ejecuta el pipeline con pendientes vacía (te lo ofrece) o `python 3_eliminar_duplicados.py --preparar-cache`.
