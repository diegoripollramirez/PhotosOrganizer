"""
╔══════════════════════════════════════════════════════════════╗
║           ESCRITOR DE FECHAS EXIF                            ║
║                                                              ║
║  Uso: python 4_escribir_fechas_exif.py [carpeta] [--si]      ║
║                                        [--simular]           ║
║                                                              ║
║  Para cada foto/vídeo con nombre YYYY-MM-DD_HH-MM-SS compara ║
║  la fecha del nombre con la de los metadatos y escribe la    ║
║  buena, con la misma regla que el renombrador:               ║
║    · Sin fecha en metadatos o fecha absurda → la del nombre  ║
║    · Nombre más antiguo → la del nombre                      ║
║    · PERO si el nombre es "solo día" (00-00-00) y los        ║
║      metadatos tienen hora real ese mismo día, NO se toca:   ║
║      la hora real se conserva.                               ║
║                                                              ║
║  Vídeos: la fecha QuickTime se guarda en UTC (como hacen los ║
║  móviles), así no se desplazan 1–2 h respecto a las fotos.   ║
║  Milésimas: si el nombre las trae (…_18-53-56_023) se        ║
║  guardan; si no, se borran las antiguas para no dejarlas     ║
║  pegadas a una fecha que no es la suya.                      ║
║  Live Photos: no se toca el ContentIdentifier que une foto   ║
║  y vídeo.                                                    ║
║                                                              ║
║  También pone la fecha de modificación del archivo (y la de  ║
║  creación en Windows) para que el explorador ordene bien.    ║
║                                                              ║
║  Archivos VACÍOS (0 bytes, p. ej. copias fallidas desde el   ║
║  móvil): no tienen foto dentro, así que se omiten y se       ║
║  listan en logs/archivos_vacios.txt.                         ║
║                                                              ║
║  Antes de pedir SI guarda el plan en logs/plan_fechas.txt.   ║
║  ⚠  Los cambios EXIF modifican los archivos y no se pueden   ║
║     deshacer con 8_deshacer.py: haz copia si dudas.          ║
║  Requiere ExifTool (en el PATH o junto a los scripts).       ║
╚══════════════════════════════════════════════════════════════╝
"""

import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C

ETIQUETAS_FOTO = ['DateTimeOriginal', 'CreateDate', 'ModifyDate']
ETIQUETAS_VIDEO = ['QuickTime:CreateDate', 'QuickTime:ModifyDate',
                   'QuickTime:TrackCreateDate', 'QuickTime:TrackModifyDate',
                   'QuickTime:MediaCreateDate', 'QuickTime:MediaModifyDate']


def milesimas_del_nombre(fname):
    """'2026-04-22_18-53-56_023.heic' → '023'; None si el nombre no las lleva."""
    m = re.match(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(\d{3})(?:$|[_.\- ])', C.separar_nombre(fname)[0] + '.')
    return m.group(1) if m else None


def esta_vacio(f):
    """True si el archivo tiene 0 bytes (no hay foto/vídeo dentro)."""
    try:
        return f.stat().st_size == 0
    except OSError:
        return False


def decidir(f_nombre, f_meta, fuente_meta):
    """Devuelve (fecha_a_escribir o None, fecha_final, motivo)."""
    if f_meta is None:
        return f_nombre, f_nombre, 'sin fecha → nombre'
    if not C.fecha_valida(f_meta.dt):
        return f_nombre, f_nombre, f'{fuente_meta} absurdo ({f_meta.dt.year}) → nombre'
    if f_nombre.precision == 'dia' and f_meta.precision == 'segundo' \
            and f_nombre.dt.date() == f_meta.dt.date():
        return None, f_meta, 'nombre solo-día, hora real conservada'
    if f_nombre.dt < f_meta.dt:
        return f_nombre, f_nombre, f'nombre ({f_nombre.dt:%Y-%m-%d}) < {fuente_meta} ({f_meta.dt:%Y-%m-%d})'
    return None, f_meta, 'correcto'


def ejecutar(carpeta, auto=False, simular=False):
    C.iniciar_logs('plan_fechas.txt', 'fechas_escritas.txt', 'archivos_vacios.txt')
    if not C.exiftool_disponible():
        print('  ERROR: este paso necesita ExifTool y no está en el PATH ni junto a los scripts.')
        print('  Descárgalo de https://exiftool.org y deja exiftool.exe (con su carpeta')
        print('  exiftool_files) en C:\\\\Windows o junto a los scripts.')
        return C.ERROR

    print('\n  [1/3] Buscando fotos y vídeos...')
    medias = [f for f in C.recorrer(carpeta) if C.es_media(f.name)]
    # Los archivos de 0 bytes no contienen nada: ExifTool da "Format error"
    # con cada uno y, si son muchos, el paso acababa en ERROR y paraba el pipeline.
    vacios = [f for f in medias if esta_vacio(f)]
    if vacios:
        medias = [f for f in medias if not esta_vacio(f)]
        log_vacios = C.ruta_log('archivos_vacios.txt')
        with open(log_vacios, 'w', encoding='utf-8') as fl:
            fl.write(f'Archivos de 0 bytes (sin foto/vídeo dentro): {len(vacios):,}\n')
            fl.write('No se pueden reparar: hay que recuperarlos del origen (móvil, copia, nube).\n')
            fl.write('=' * 60 + '\n\n')
            fl.write(''.join(f'{f}\n' for f in vacios))
    metas = C.leer_metadatos(medias)

    print('\n  [2/3] Analizando...')
    escribir, solo_fs = [], []      # (f, fecha_a_escribir, motivo) / (f, fecha_final)
    sin_nombre = conservadas = 0
    for f in medias:
        f_nombre = C.fecha_formato_nombre(f.name)
        if f_nombre is None or not C.fecha_valida(f_nombre.dt):
            sin_nombre += 1
            continue
        m = metas.get(C.clave(f))
        f_meta, fuente = (m.fecha() if m else (None, None))
        nueva, final, motivo = decidir(f_nombre, f_meta, fuente)
        if nueva is not None:
            escribir.append((f, nueva, motivo))
        else:
            if motivo != 'correcto':
                conservadas += 1
            solo_fs.append((f, final))

    print(f'  Fotos/vídeos                          : {len(medias):>8,}')
    print(f'  A escribir en metadatos               : {len(escribir):>8,}')
    print(f'  Metadatos ya correctos (solo disco)   : {len(solo_fs):>8,}')
    if conservadas:
        print(f'    de ellos, hora real conservada      : {conservadas:>8,}')
    print(f'  Sin fecha en el nombre (se omiten)    : {sin_nombre:>8,}')
    if vacios:
        print(f'  ⚠  VACÍOS, 0 bytes (se omiten)        : {len(vacios):>8,}')
        print(f'     Lista: {C.ruta_log("archivos_vacios.txt")}')
        print('     No tienen foto dentro: recupéralos del móvil/copia original.')

    if not escribir and not solo_fs:
        print('\n  Nada que hacer.')
        return C.OK

    log_plan = C.ruta_log('plan_fechas.txt')
    with open(log_plan, 'w', encoding='utf-8') as fl:
        fl.write(f'PLAN — se escribirían {len(escribir)} archivos (aún no se ha cambiado nada)\n\n')
        for f, fe, mo in escribir:
            fl.write(f'[{mo}] {f} → {C.a_exif(fe.dt)}\n')
    print(f'\n  Plan completo (todavía no se ha cambiado nada): {log_plan}')
    if simular:
        print('  Simulación: no se ha cambiado nada.')
        return C.OK

    log = C.ruta_log('fechas_escritas.txt')
    print()
    msg = (f'¿Escribir fechas en {len(escribir):,} archivos y ajustar la fecha de disco de {len(solo_fs):,}?'
           if escribir else f'¿Ajustar la fecha de disco de {len(solo_fs):,} archivos?')
    if not C.confirmar(msg, auto):
        print('  Cancelado.')
        return C.CANCELADO

    print('\n  [3/3] Escribiendo...')
    bloques = []
    for f, fe, _ in escribir:
        v = C.a_exif(fe.dt)
        if C.es_video(f.name):
            args = [f'-{t}={v}' for t in ETIQUETAS_VIDEO]
        else:
            args = [f'-{t}={v}' for t in ETIQUETAS_FOTO]
            # Milésimas: las del nombre si las trae (…_18-53-56_023); si no, se borran
            # las antiguas para que no queden pegadas a una fecha que ya no es suya
            ms = milesimas_del_nombre(f.name)
            args += [f'-{t}={ms or ""}' for t in ('SubSecTimeOriginal', 'SubSecTimeDigitized', 'SubSecTime')]
        bloques.append((f, ['-overwrite_original'] + args + C.args_fecha_sistema(fe.dt)))
    ok1, err1 = C.ejecutar_bloques(bloques, ['-m', '-api', 'QuickTimeUTC=1'], 'Metadatos')

    bloques_fs = [(f, C.args_fecha_sistema(fe.dt)) for f, fe in solo_fs]
    ok2, err2 = C.ejecutar_bloques(bloques_fs, ['-m'], 'Fechas de disco')

    with open(log, 'w', encoding='utf-8') as fl:
        fl.write(f'Carpeta: {carpeta}\n')
        fl.write(f'Metadatos escritos: {len(ok1):,} / {len(escribir):,}\n')
        fl.write(f'Fechas de disco   : {len(ok2):,} / {len(solo_fs):,}\n')
        if vacios:
            fl.write(f'Vacíos (0 bytes, omitidos): {len(vacios):,} — ver archivos_vacios.txt\n')
        fl.write('=' * 60 + '\n\n')
        for f, fe, mo in escribir:
            k = C.clave(f)
            estado = 'OK ' if k in ok1 else f'ERROR ({err1.get(k, "?")})'
            fl.write(f'{estado} [{mo}] {f} → {C.a_exif(fe.dt)}\n')
        if err2:
            fl.write('\nERRORES FECHA DE DISCO:\n')
            fl.write(''.join(f'  {k}: {e}\n' for k, e in err2.items()))

    filas = []
    if escribir:
        filas.append(('Metadatos escritos', f'{len(ok1):,}'))
    filas.append(('Fechas de disco', f'{len(ok2):,}'))
    if err1 or err2:
        filas.append(('Errores (ver log)', f'{len(err1) + len(err2):,}'))
    if vacios:
        filas.append(('Vacíos omitidos (0 bytes)', f'{len(vacios):,}'))
    C.caja('FECHAS ESCRITAS', filas)
    print(f'\n  Log: {log}')
    print('  Si usas digiKam: Tools → Maintenance → Read metadata from files into database')
    # Formatos no escribibles (GIF, BMP…) dan error pero no paran el pipeline
    return C.OK if len(err1) + len(err2) < max(1, (len(escribir) + len(solo_fs)) // 2) else C.ERROR


def main():
    ap = argparse.ArgumentParser(description='Escribe en EXIF la fecha del nombre.')
    ap.add_argument('carpeta', nargs='?')
    ap.add_argument('--si', action='store_true')
    ap.add_argument('--simular', action='store_true')
    a = ap.parse_args()
    carpeta = a.carpeta or C.pedir_carpeta('\nCarpeta a procesar: ')
    if not carpeta or not Path(carpeta).is_dir():
        print(f'  ERROR: no existe la carpeta: {carpeta}')
        C.pausa_final()
        sys.exit(C.ERROR)
    codigo = ejecutar(carpeta, a.si, a.simular)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
