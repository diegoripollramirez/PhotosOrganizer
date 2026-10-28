"""
╔══════════════════════════════════════════════════════════════╗
║              EXTRACTOR DE ARCHIVOS ZIP                       ║
║                                                              ║
║  Uso: python 1_extraer_zips.py [carpeta] [--si]              ║
║  Sin argumentos te pide la carpeta.                          ║
║                                                              ║
║  · Busca todos los .zip de la carpeta (y subcarpetas)        ║
║  · Muestra tamaño comprimido / descomprimido / libre         ║
║  · Los ZIP de un mismo export que viene partido              ║
║    (takeout-…-001.zip, -002.zip…) se extraen FUSIONADOS en   ║
║    la misma carpeta, para que cada foto quede junto a su     ║
║    JSON aunque vinieran en ZIPs distintos.                   ║
║  · Recuerda qué ZIPs ya se extrajeron (no los repite)        ║
║  · Conserva la fecha de modificación guardada en el ZIP      ║
║  · Protección Zip Slip (rutas tipo ../../)                   ║
║  · Los .zip originales NO se borran                          ║
║                                                              ║
║  Siguiente paso: 2_renombrar_fotos.py                        ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import time
import shutil
import zipfile
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C

CARPETA_SALIDA = 'extraido_zips'
CONTROL = '.zips_extraidos.txt'

# takeout-20240101T120000Z-001.zip → grupo "takeout-20240101T120000Z"
PATRON_PARTE = re.compile(r'^(.+?)[-_ ](\d{3})$')


def grupo_de(zip_path):
    m = PATRON_PARTE.match(zip_path.stem)
    return m.group(1) if m else zip_path.stem


def tamano_descomprimido(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as z:
            return sum(i.file_size for i in z.infolist())
    except Exception:
        return 0


def extraer_seguro(zip_path, destino):
    """Extrae miembro a miembro. Devuelve (ok, renombrados, rutas_zip_slip)."""
    destino = Path(destino).resolve()
    prefijo = str(destino) + os.sep
    ok = renombrados = 0
    slip = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (destino / info.filename).resolve()
            if str(target) != str(destino) and not str(target).startswith(prefijo):
                slip.append(info.filename)
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                # Mismo archivo en dos partes del export: si es igual, se salta
                if target.stat().st_size == info.file_size:
                    ok += 1
                    continue
                target = C.nombre_libre(target)
                renombrados += 1
            with z.open(info) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            try:
                ts = time.mktime(info.date_time + (0, 0, -1))
                os.utime(target, (ts, ts))
            except Exception:
                pass
            ok += 1
    return ok, renombrados, slip


def ejecutar(carpeta, auto=False):
    C.iniciar_logs('extraccion_zips.txt')
    carpeta = Path(carpeta)
    salida = carpeta / CARPETA_SALIDA
    control = salida / CONTROL

    print(f'\n  Buscando .zip en : {carpeta}')
    print(f'  Extrayendo en    : {salida}\n')

    excluir = C.CARPETAS_EXCLUIR | {CARPETA_SALIDA}
    zips = sorted(f for f in C.recorrer(carpeta, excluir) if f.suffix.lower() == '.zip')
    if not zips:
        print('  No se encontraron archivos .zip.')
        return C.OK

    hechos = set()
    if control.exists():
        hechos = set(control.read_text(encoding='utf-8').splitlines())
    pendientes = [z for z in zips if z.name not in hechos]
    ya = [z for z in zips if z.name in hechos]

    grupos = defaultdict(list)
    for z in pendientes:
        grupos[grupo_de(z)].append(z)

    tam_c = sum(z.stat().st_size for z in pendientes)
    print('  Calculando tamaño descomprimido...', end='\r')
    tam_d = sum(tamano_descomprimido(z) for z in pendientes)
    libre = shutil.disk_usage(carpeta).free

    print(f'  ZIPs encontrados        : {len(zips):>8,}')
    if ya:
        print(f'  Ya extraídos (se omiten): {len(ya):>8,}')
    print(f'  Pendientes              : {len(pendientes):>8,}  en {len(grupos)} grupo(s)')
    print(f'  Tamaño comprimido       : {C.gb(tam_c):>11}')
    print(f'  Tamaño descomprimido    : {C.gb(tam_d):>11}')
    print(f'  Espacio libre en disco  : {C.gb(libre):>11}')
    for g, lista in grupos.items():
        if len(lista) > 1:
            print(f'    · {g}: {len(lista)} partes → se fusionan en una carpeta')

    if not pendientes:
        print('\n  Todos los ZIPs ya estaban extraídos. Nada que hacer.')
        return C.OK
    if tam_d > libre * 0.9:
        print('\n  ⚠  ADVERTENCIA: puede que no haya suficiente espacio libre.')

    print()
    if not C.confirmar(f'¿Extraer {len(pendientes)} ZIP(s)?', auto):
        print('  Cancelado.')
        return C.CANCELADO

    salida.mkdir(exist_ok=True)
    errores, slip_total = [], []
    total = renom_total = 0
    i = 0
    for g, lista in grupos.items():
        destino = salida / g
        for zp in lista:
            i += 1
            print(f'  [{i}/{len(pendientes)}] {zp.name} → {destino.name}/ ...', end=' ', flush=True)
            try:
                ok, ren, slip = extraer_seguro(zp, destino)
                total += ok
                renom_total += ren
                slip_total += [(zp.name, s) for s in slip]
                print(f'{ok:,} archivos' + (f'  ⚠ {len(slip)} rutas sospechosas omitidas' if slip else ''))
                with open(control, 'a', encoding='utf-8') as f:
                    f.write(zp.name + '\n')
            except Exception as e:
                print(f'ERROR: {e}')
                errores.append((zp.name, str(e)))

    log = C.ruta_log('extraccion_zips.txt')
    with open(log, 'w', encoding='utf-8') as f:
        f.write(f'Carpeta origen    : {carpeta}\nCarpeta destino   : {salida}\n')
        f.write(f'ZIPs procesados   : {len(pendientes)}\nZIPs omitidos     : {len(ya)}\n')
        f.write(f'Archivos extraídos: {total:,}\n' + '=' * 60 + '\n\n')
        for g, lista in grupos.items():
            f.write(f'GRUPO {g}:\n' + ''.join(f'  {z.name}\n' for z in lista))
        if errores:
            f.write('\nERRORES:\n' + ''.join(f'  {n}: {e}\n' for n, e in errores))
        if slip_total:
            f.write('\nENTRADAS ZIP SLIP OMITIDAS:\n' + ''.join(f'  [{z}] {e}\n' for z, e in slip_total))

    filas = [('ZIPs procesados', len(pendientes)), ('Archivos extraídos', f'{total:,}')]
    if renom_total:
        filas.append(('Renombrados por conflicto', renom_total))
    if errores:
        filas.append(('Errores', len(errores)))
    if slip_total:
        filas.append(('Rutas Zip Slip omitidas', len(slip_total)))
    C.caja('EXTRACCIÓN COMPLETADA', filas)
    print(f'\n  Log: {log}')
    print('  Siguiente paso: 2_renombrar_fotos.py')
    return C.ERROR if errores else C.OK


def main():
    ap = argparse.ArgumentParser(description='Extrae los ZIP de una carpeta.')
    ap.add_argument('carpeta', nargs='?')
    ap.add_argument('--si', action='store_true', help='no pedir confirmación')
    a = ap.parse_args()
    carpeta = a.carpeta or C.pedir_carpeta('\nCarpeta donde buscar los .zip: ')
    if not carpeta or not Path(carpeta).is_dir():
        print(f'  ERROR: no existe la carpeta: {carpeta}')
        C.pausa_final()
        sys.exit(C.ERROR)
    codigo = ejecutar(carpeta, a.si)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
