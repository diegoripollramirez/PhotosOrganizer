# PhotosOrganizer

Scripts en Python para organizar una colección grande de fotos descargadas de **Google Fotos (Takeout)** y **Amazon Fotos**: extraen los ZIP, renombran cada archivo por su fecha real, eliminan duplicados (exactos y visuales), corrigen las fechas EXIF y mueven lo que está listo a una carpeta *maestra*.

## Qué hace

- **Renombra por fecha** a `YYYY-MM-DD_HH-MM-SS_desc.ext`, tomando la fecha del nombre, del JSON de Takeout o del EXIF. La fecha de modificación del archivo solo se usa como último recurso.
- **Empareja los Live Photos** del iPhone: el vídeo (`.MOV` / `.HEIC.qt`) se queda junto a su foto con el mismo nombre.
- **Detecta duplicados exactos** (MD5) y **visuales** (misma foto con distinta resolución o metadatos). Los visuales se revisan en un HTML con miniaturas y siempre los confirmas tú.
- **Escribe las fechas en el EXIF** y guarda el nombre de la carpeta de origen como etiqueta de álbum (p. ej. "Roma 2015").
- **Todo se puede deshacer**: cada renombrado o movimiento queda registrado en un CSV que `8_deshacer.py` revierte. Nada se borra: los duplicados y archivos innecesarios se apartan a carpetas.

## Requisitos

- **Python 3.10** o superior
- **[ExifTool](https://exiftool.org)**, en `C:\Windows\` o junto a los scripts. Casi imprescindible: sin él no se leen las fechas de las fotos HEIC ni de los vídeos.
- Librerías de Python:

```bash
python -m pip install -r requirements.txt
```

`pillow`, `imagehash` y `pillow-heif` solo hacen falta para los duplicados visuales. `piexif` es un respaldo opcional si no tienes ExifTool (solo lee JPEG/TIFF).

## Uso

Lo normal es lanzar el pipeline completo:

```bash
python 0_pipeline.py
```

Pregunta la carpeta de **pendientes** (fotos por procesar) y la **maestra** (colección organizada) y ejecuta estos pasos:

| Paso | Script | Qué hace |
|---|---|---|
| 1 | `1_extraer_zips.py` | Extrae y fusiona los ZIP partidos de un mismo Takeout |
| 2 | `2_renombrar_fotos.py` | Renombra por fecha y empareja Live Photos |
| 3 | `3_eliminar_duplicados.py` (modo 1) | Duplicados exactos, antes de modificar ningún archivo |
| 4 | `4_escribir_fechas_exif.py` | Escribe las fechas en el EXIF (se salta sin ExifTool) |
| 5 | `5_limpiar_archivos.py` | Aparta `Thumbs.db`, `desktop.ini` y ZIP ya extraídos |
| 6 | `3_eliminar_duplicados.py` (modo 2) | Duplicados visuales, con revisión manual |
| 7 | `6_verificar_coleccion.py` | Auditoría, solo lectura |
| 8 | `7_mover_a_maestra.py` | Mueve lo que está listo a la raíz de la maestra |

Lo que necesita revisión se queda en pendientes. Las carpetas que no son álbumes (Takeout, WhatsApp, años…) se definen en `carpetas_genericas.txt`.

Cada script también funciona suelto, con doble clic o por línea de comandos (`--help` muestra las opciones). Con `--simular`, el renombrado y la escritura de fechas solo generan el plan en `logs/` sin tocar nada.

Otros scripts:

- `0_revisar_maestra.py`: busca duplicados solo dentro de la maestra.
- `8_deshacer.py`: revierte operaciones anteriores (empieza siempre por la más reciente).
- `9_analizar_nombres.py`: diagnóstico de nombres de archivo.

## ⚠️ Antes de empezar

Los cambios de **fechas EXIF no se pueden deshacer**. Haz una copia de seguridad de tus fotos antes de la primera ejecución.

## Archivos que se generan

- `logs/`: informes de cada ejecución y CSV para deshacer.
- `_duplicados/`, `_vacios/`: duplicados y archivos vacíos apartados, junto a los scripts.
- `_basura/`: archivos innecesarios, dentro de la carpeta procesada.
- `cache_fotos.db`: caché local de metadatos y huellas para que las siguientes ejecuciones sean rápidas. Se puede borrar y se reconstruye sola. No se sube al repositorio.

La guía detallada de instalación y solución de problemas está en `0_Instrucciones.docx`.
