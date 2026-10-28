"""
╔══════════════════════════════════════════════════════════════╗
║              LIMPIADOR DE ARCHIVOS INNECESARIOS              ║
║                                                              ║
║  Uso: python 5_limpiar_archivos.py [carpeta]                 ║
║              [--ext .db .ini ...] [--si]                     ║
║                                                              ║
║  ⚠  Ejecutar DESPUÉS de 2_renombrar_fotos.py: los .json de   ║
║     Google Takeout se usan para sacar las fechas.            ║
║                                                              ║
║  Solo aparta lo que es basura de verdad (por NOMBRE, no solo ║
║  por extensión):                                             ║
║    .db    — solo Thumbs.db / ehthumbs.db (nunca las bases de ║
║             datos de digiKam u otros programas)              ║
║    .ini   — solo desktop.ini                                 ║
║    .zip   — solo los ZIP que 1_extraer_zips.py ya extrajo    ║
║  Opcionales (no son basura, solo si los pides):              ║
║    .json  — metadatos de Google Takeout                      ║
║    .picasa.ini — álbumes y caras de Picasa                   ║
║                                                              ║
║  Los archivos se MUEVEN a _basura/ dentro de la carpeta,     ║
║  conservando su subcarpeta original. Los vídeos Live Photo   ║
║  NO se tocan nunca. Se puede deshacer con 8_deshacer.py.     ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C

THUMBS = {'thumbs.db', 'ehthumbs.db', 'ehthumbs_vista.db'}
CONTROL_ZIPS = '.zips_extraidos.txt'

# tipo → (descripción, función que decide si un archivo es de ese tipo)
CANDIDATAS = {
    '.db':   'Miniaturas de Windows (Thumbs.db, ehthumbs.db)',
    '.ini':  'Configuración de carpeta de Windows (desktop.ini)',
    '.zip':  'ZIP ya extraídos por 1_extraer_zips.py',
    '.json': 'Metadatos de Google Takeout (útiles para volver a renombrar)',
    '.picasa.ini': 'Álbumes y caras de Picasa (.picasa.ini)',
}
SEGUROS = ['.db', '.ini', '.zip']        # los que aparta el pipeline


def zips_extraidos(carpeta):
    nombres = set()
    for f in C.recorrer(carpeta):
        if f.name == CONTROL_ZIPS:
            try:
                nombres |= set(f.read_text(encoding='utf-8').splitlines())
            except OSError:
                pass
    return nombres


def tipo_de(f, extraidos):
    n = f.name.lower()
    if n in THUMBS:
        return '.db'
    if n == 'desktop.ini':
        return '.ini'
    if n in ('.picasa.ini', 'picasa.ini'):
        return '.picasa.ini'
    if f.suffix.lower() == '.zip':
        return '.zip' if f.name in extraidos else 'zip_pendiente'
    if f.suffix.lower() == '.json':
        return '.json'
    if f.suffix.lower() in ('.db', '.ini'):
        return 'ignorado'
    return None


def escanear(carpeta):
    extraidos = zips_extraidos(carpeta)
    por_tipo = defaultdict(list)
    for f in C.recorrer(carpeta):
        t = tipo_de(f, extraidos)
        if t:
            por_tipo[t].append(f)
    return por_tipo


def ejecutar(carpeta, extensiones=None, auto=False):
    """extensiones=None → preguntar por cada tipo."""
    C.iniciar_logs('limpieza_realizada.txt')
    carpeta = Path(carpeta)
    print('\n  Escaneando...')
    por_ext = escanear(carpeta)
    if por_ext.get('zip_pendiente'):
        print(f"  ℹ  {len(por_ext['zip_pendiente'])} ZIP sin extraer: no se tocan.")
    if por_ext.get('ignorado'):
        print(f"  ℹ  {len(por_ext['ignorado'])} .db/.ini de otros programas (p.ej. digiKam): no se tocan.")
    if not any(t in por_ext for t in CANDIDATAS):
        print('  No se encontraron archivos de los tipos candidatos.')
        return C.OK

    elegidas = []
    print()
    for ext, desc in CANDIDATAS.items():
        if ext not in por_ext:
            continue
        lista = por_ext[ext]
        tam = sum(f.stat().st_size for f in lista)
        print(f'  {ext:11} {len(lista):>7,} archivos  ({C.mb(tam)})  — {desc}')
        if extensiones is None:
            if C.preguntar_sn(f'       ¿Mover los {ext} a _basura/?'):
                elegidas.append(ext)
        elif ext in extensiones:
            elegidas.append(ext)

    a_mover = [f for e in elegidas for f in por_ext[e]]
    if not a_mover:
        print('\n  No se ha seleccionado ningún tipo.')
        return C.OK

    basura = carpeta / '_basura'
    total = sum(f.stat().st_size for f in a_mover)
    print(f'\n  Total a mover: {len(a_mover):,} archivos ({C.mb(total)}) → {basura}')
    if not C.confirmar('¿Mover?', auto):
        print('  Cancelado. No se ha movido nada.')
        return C.CANCELADO

    reg = C.Registro('limpiar')
    movidos = errores = 0
    log = C.ruta_log('limpieza_realizada.txt')
    with open(log, 'w', encoding='utf-8') as fl:
        fl.write(f'Carpeta: {carpeta}\nDestino: {basura}\n' + '=' * 60 + '\n\n')
        for f in a_mover:
            try:
                d = C.mover(f, basura / C.ruta_relativa_segura(f, carpeta), reg)
                fl.write(f'{f}  →  {d}\n')
                movidos += 1
                if movidos % 200 == 0:
                    print(f'  Movidos: {movidos:,} / {len(a_mover):,}...', end='\r')
            except Exception as e:
                fl.write(f'ERROR: {f} — {e}\n')
                errores += 1
    reg.cerrar()
    C.caja('LIMPIEZA COMPLETADA', [('Archivos movidos', f'{movidos:,}'),
                                   ('Espacio liberado', C.mb(total)), ('Errores', errores)])
    print(f'\n  Log: {log}')
    if reg.ruta:
        print(f'  Para deshacer: 8_deshacer.py ({reg.ruta.name})')
    return C.ERROR if errores else C.OK


def main():
    ap = argparse.ArgumentParser(description='Mueve archivos innecesarios a _basura/.')
    ap.add_argument('carpeta', nargs='?')
    ap.add_argument('--ext', nargs='+', help='tipos a mover: .db .ini .zip .json .picasa.ini')
    ap.add_argument('--si', action='store_true')
    a = ap.parse_args()
    carpeta = a.carpeta or C.pedir_carpeta('\nCarpeta donde buscar: ')
    if not carpeta or not Path(carpeta).is_dir():
        print(f'  ERROR: no existe la carpeta: {carpeta}')
        C.pausa_final()
        sys.exit(C.ERROR)
    exts = [e.lower() if e.startswith('.') else '.' + e.lower() for e in a.ext] if a.ext else None
    if exts and '.picasa' in exts:
        exts.append('.picasa.ini')
    codigo = ejecutar(carpeta, exts, a.si)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
