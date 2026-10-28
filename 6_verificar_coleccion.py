"""
╔══════════════════════════════════════════════════════════════╗
║           VERIFICADOR DE COLECCIÓN                           ║
║                                                              ║
║  Uso: python 6_verificar_coleccion.py [carpetas...]          ║
║                                                              ║
║  Auditoría completa. NO modifica nada. Comprueba:            ║
║    · Estadísticas (archivos, extensiones)                    ║
║    · Nombres con/sin formato YYYY-MM-DD_HH-MM-SS             ║
║    · Nombres "solo día" (00-00-00) a revisar                 ║
║    · Fechas EXIF/QuickTime de fotos Y vídeos: presentes,     ║
║      ausentes, absurdas o que no cuadran con el nombre       ║
║    · JSON de Takeout huérfanos (todas sus variantes)         ║
║    · Live Photos: parejas correctas, vídeos huérfanos y      ║
║      parejas con nombres distintos                           ║
║                                                              ║
║  Genera logs/verificacion_coleccion.txt.                     ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C

UMBRAL_DISCREPANCIA = timedelta(days=1)


def ejecutar(carpetas):
    C.iniciar_logs('verificacion_coleccion.txt')
    con_exiftool = C.exiftool_disponible()
    if con_exiftool:
        print('\n  ExifTool detectado — se leen fotos y vídeos en lote.')
    else:
        C.aviso_sin_exiftool('solo se pueden revisar las fechas de JPEG/TIFF, y los Live Photos se detectan solo por nombre')

    print('\n  Escaneando archivos...')
    todos = []
    for c in carpetas:
        todos += list(C.recorrer(c))
    medias = [f for f in todos if C.es_media(f.name)]
    metas = C.leer_metadatos(medias) if con_exiftool else {}

    por_ext = defaultdict(int)
    for f in todos:
        por_ext[C.ext_de(f.name) or '(sin ext)'] += 1

    sin_formato, nombre_absurdo, solo_dia = [], [], []
    sin_exif, exif_absurdo, discrepancias = [], [], []
    analizables = 0
    for f in medias:
        fn = C.fecha_formato_nombre(f.name)
        if fn is None:
            sin_formato.append(str(f))
        elif not C.fecha_valida(fn.dt):
            nombre_absurdo.append((str(f), fn.dt.strftime('%Y-%m-%d')))
        elif fn.precision == 'dia':
            solo_dia.append(str(f))

        if con_exiftool:
            m = metas.get(C.clave(f))
            fe, fuente = m.fecha() if m else (None, None)
        else:
            if C.ext_de(f.name) not in ('.jpg', '.jpeg', '.tif', '.tiff'):
                continue
            fe, fuente = C.leer_fecha_piexif(f), 'EXIF'
        analizables += 1
        if fe is None:
            sin_exif.append(str(f))
        elif not C.fecha_valida(fe.dt):
            exif_absurdo.append((str(f), fuente, fe.dt.year))
        elif fn is not None and C.fecha_valida(fn.dt) and abs(fn.dt - fe.dt) > UMBRAL_DISCREPANCIA:
            discrepancias.append((str(f), fn.dt, fe.dt, fuente, abs(fn.dt - fe.dt).days))

    # JSON huérfanos: los que ningún archivo multimedia reclama como propio
    print('  Comprobando JSON...')
    indice = C.IndiceJSON()
    indice.cargar(carpetas)
    reclamados = set()
    for f in medias:
        _, jp, _ = indice.buscar(f)
        if jp is not None:
            reclamados.add(C.clave(jp))
    json_huerfanos = [str(j) for j in indice.todos if C.clave(j) not in reclamados]

    # Live Photos
    print('  Comprobando Live Photos...')
    foto_video, video_foto = C.detectar_live_photos(medias, metas)
    parejas_ok, parejas_nombre_distinto = 0, []
    for kf, v in foto_video.items():
        foto = video_foto[C.clave(v)]
        if C.separar_nombre(foto.name)[0].lower() == C.separar_nombre(v.name)[0].lower():
            parejas_ok += 1
        else:
            parejas_nombre_distinto.append((str(foto), v.name))
    live_huerfanos = []
    for f in medias:
        if C.ext_de(f.name) in C.EXT_LIVE_VIDEO and C.clave(f) not in video_foto:
            m = metas.get(C.clave(f))
            corto = m is not None and m.duracion is not None and m.duracion <= C.LIVE_DURACION_MAX
            if C.ext_de(f.name) == '.heic.qt' or (m and m.content_id) or corto:
                live_huerfanos.append(str(f))

    # ── Informe ───────────────────────────────────────────
    total = len(todos)
    total_media = len(medias)
    total_json = len(indice.todos)
    lineas = []
    w = lineas.append

    w('=' * 70)
    w('  VERIFICACIÓN DE COLECCIÓN DE FOTOS')
    w('=' * 70)
    w(f'  Fecha de análisis : {datetime.now():%Y-%m-%d %H:%M:%S}')
    w('  Carpetas:')
    for c in carpetas:
        w(f'    {c}')
    w('')
    w('─' * 70 + '\n  ESTADÍSTICAS GENERALES\n' + '─' * 70)
    w(f'  Total archivos          : {total:>10,}')
    w(f'    Fotos / vídeos        : {total_media:>10,}')
    w(f'    Archivos JSON         : {total_json:>10,}')
    w(f'    Otros                 : {total - total_media - total_json:>10,}')
    w('\n  Extensiones más frecuentes:')
    for ext, n in sorted(por_ext.items(), key=lambda x: -x[1])[:12]:
        w(f'    {ext:12}  {n:>8,}  ({n / total * 100 if total else 0:5.1f}%)')
    w('')
    correctos = total_media - len(sin_formato) - len(nombre_absurdo)
    w('─' * 70 + '\n  FORMATO DE NOMBRES\n' + '─' * 70)
    w(f'  Con formato correcto              : {correctos:>8,}  '
      f'({correctos / total_media * 100 if total_media else 0:.1f}%)')
    w(f'    de ellos "solo día" (00-00-00)  : {len(solo_dia):>8,}')
    w(f'  Sin formato (pendientes)          : {len(sin_formato):>8,}')
    w(f'  Con fecha absurda en el nombre    : {len(nombre_absurdo):>8,}')
    w('')
    validos = analizables - len(sin_exif) - len(exif_absurdo)
    w('─' * 70 + '\n  FECHAS EN METADATOS (EXIF / QuickTime)\n' + '─' * 70)
    w(f'  Analizados                        : {analizables:>8,}')
    w(f'  Con fecha válida                  : {validos:>8,}  '
      f'({validos / analizables * 100 if analizables else 0:.1f}%)')
    w(f'  Sin fecha                         : {len(sin_exif):>8,}')
    w(f'  Con fecha absurda                 : {len(exif_absurdo):>8,}')
    w(f'  Discrepancia nombre/metadatos >1d : {len(discrepancias):>8,}')
    w('')
    w('─' * 70 + '\n  JSON (Google Takeout)\n' + '─' * 70)
    w(f'  Total JSON                        : {total_json:>8,}')
    w(f'  Con foto correspondiente          : {total_json - len(json_huerfanos):>8,}')
    w(f'  Huérfanos                         : {len(json_huerfanos):>8,}')
    w('')
    w('─' * 70 + '\n  LIVE PHOTOS\n' + '─' * 70)
    w(f'  Parejas correctas (mismo nombre)  : {parejas_ok:>8,}')
    w(f'  Parejas con nombres distintos     : {len(parejas_nombre_distinto):>8,}')
    w(f'  Vídeos Live huérfanos (sin foto)  : {len(live_huerfanos):>8,}')
    w('')

    w('─' * 70 + '\n  RESUMEN\n' + '─' * 70)
    problemas = [sin_formato, nombre_absurdo, sin_exif, exif_absurdo, discrepancias,
                 json_huerfanos, parejas_nombre_distinto, live_huerfanos]
    if not any(problemas):
        w('  ✓ Colección sin problemas detectados.')
    if sin_formato:
        w(f'  ⚠  {len(sin_formato):,} sin renombrar → 2_renombrar_fotos.py (mira logs/sin_fecha.txt)')
    if nombre_absurdo:
        w(f'  ⚠  {len(nombre_absurdo):,} nombres con fecha absurda → revisar a mano')
    if solo_dia:
        w(f'  ℹ  {len(solo_dia):,} nombres "solo día" → normal en WhatsApp sin EXIF; '
          '2_renombrar_fotos.py los corrige si aparece la hora real')
    if sin_exif or exif_absurdo or discrepancias:
        w(f'  ⚠  {len(sin_exif) + len(exif_absurdo) + len(discrepancias):,} fechas de metadatos '
          'a corregir → 4_escribir_fechas_exif.py')
    if json_huerfanos:
        w(f'  ⚠  {len(json_huerfanos):,} JSON huérfanos → 5_limpiar_archivos.py si ya no los quieres')
    if parejas_nombre_distinto:
        w(f'  ⚠  {len(parejas_nombre_distinto):,} Live Photos con nombres distintos → '
          '2_renombrar_fotos.py los vuelve a emparejar')
    if live_huerfanos:
        w(f'  ⚠  {len(live_huerfanos):,} vídeos Live sin foto → revisar (¿se movió o borró la foto?)')
    w('')

    def detalle(titulo, items, fmt, maximo=None):
        if not items:
            return
        w('─' * 70 + f'\n  DETALLE: {titulo} ({len(items):,})\n' + '─' * 70)
        for it in items[:maximo] if maximo else items:
            w('  ' + fmt(it))
        w('')

    detalle('SIN FORMATO DE NOMBRE', sin_formato, str)
    detalle('NOMBRES CON FECHA ABSURDA', nombre_absurdo, lambda x: f'{x[1]}  {x[0]}')
    detalle('NOMBRES SOLO DÍA', solo_dia, str)
    detalle('SIN FECHA EN METADATOS', sin_exif, str)
    detalle('FECHA DE METADATOS ABSURDA', exif_absurdo, lambda x: f'[{x[1]} {x[2]}]  {x[0]}')
    detalle('DISCREPANCIAS NOMBRE vs METADATOS', discrepancias,
            lambda x: f'nombre={x[1]:%Y-%m-%d %H:%M:%S}  {x[3]}={x[2]:%Y-%m-%d %H:%M:%S}  '
                      f'(diff={x[4]}d)  {x[0]}')
    detalle('JSON HUÉRFANOS', json_huerfanos, str)
    detalle('LIVE PHOTOS CON NOMBRES DISTINTOS', parejas_nombre_distinto, lambda x: f'{x[0]}  ↔  {x[1]}')
    detalle('VÍDEOS LIVE HUÉRFANOS', live_huerfanos, str)

    log = C.ruta_log('verificacion_coleccion.txt')
    log.write_text('\n'.join(lineas) + '\n', encoding='utf-8')

    C.caja('RESULTADO DE VERIFICACIÓN', [
        ('Total archivos', f'{total:,}'), ('Fotos/vídeos', f'{total_media:,}'),
        ('Sin renombrar', f'{len(sin_formato):,}'), ('Sin fecha metadatos', f'{len(sin_exif):,}'),
        ('Fecha absurda', f'{len(exif_absurdo):,}'), ('Discrepancias', f'{len(discrepancias):,}'),
        ('JSON huérfanos', f'{len(json_huerfanos):,}'),
        ('Live Photos OK', f'{parejas_ok:,}'),
        ('Live desemparejados', f'{len(parejas_nombre_distinto) + len(live_huerfanos):,}'),
    ])
    print(f'\n  Informe completo: {log}')
    return C.OK


def main():
    ap = argparse.ArgumentParser(description='Audita la colección sin modificar nada.')
    ap.add_argument('carpetas', nargs='*')
    a = ap.parse_args()
    carpetas = a.carpetas or C.pedir_carpetas()
    codigo = ejecutar(carpetas)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
