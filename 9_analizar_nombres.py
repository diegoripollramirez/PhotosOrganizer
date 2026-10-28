"""
╔══════════════════════════════════════════════════════════════╗
║         ANALIZADOR DE PATRONES DE NOMBRES DE ARCHIVO         ║
║                                                              ║
║  Uso: python 9_analizar_nombres.py [carpetas...] [-o salida] ║
║                                                              ║
║  Agrupa los nombres por patrón (números → #), los ordena por ║
║  frecuencia con 3 ejemplos cada uno e indica si el           ║
║  renombrador reconoce ese patrón. Úsalo para diagnosticar    ║
║  antes de renombrar. No modifica nada.                       ║
╚══════════════════════════════════════════════════════════════╝
"""

import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C


def ejecutar(carpetas, salida=None):
    if not salida:
        C.iniciar_logs('patrones_nombres.txt')
    salida = Path(salida) if salida else C.ruta_log('patrones_nombres.txt')
    print(f'\n  Analizando {len(carpetas)} carpeta(s)...')
    patrones = defaultdict(list)
    extensiones = defaultdict(int)
    total = sin_ext = 0
    for c in carpetas:
        for f in C.recorrer(c):
            stem, ext = C.separar_nombre(f.name)
            if not ext:
                sin_ext += 1
                continue
            extensiones[ext.lower()] += 1
            patrones[re.sub(r'\d+', '#', stem)].append((stem, ext))
            total += 1
            if total % 1000 == 0:
                print(f'  {total:,} archivos procesados...', end='\r')
    print(f'  {total:,} archivos procesados.      ')

    with open(salida, 'w', encoding='utf-8') as f:
        f.write('ANÁLISIS DE PATRONES DE NOMBRES\nCarpetas:\n')
        f.write(''.join(f'  {c}\n' for c in carpetas))
        f.write(f'Total archivos: {total:,}\n')
        if sin_ext:
            f.write(f'Sin extensión (ignorados): {sin_ext:,}\n')
        f.write(f'Patrones distintos: {len(patrones):,}\n' + '=' * 65 + '\n\n')
        f.write('EXTENSIONES\n' + '-' * 40 + '\n')
        for ext, n in sorted(extensiones.items(), key=lambda x: -x[1]):
            f.write(f'  {ext:<20} {n:>8,}\n')
        f.write('\n\nPATRONES (# = cualquier número)\n' + '-' * 40 + '\n')
        f.write('[✓] = el renombrador saca fecha del nombre   [ ] = necesita EXIF/JSON\n\n')
        for patron, lista in sorted(patrones.items(), key=lambda x: -len(x[1])):
            reconoce = '✓' if C.extraer_fecha_nombre(lista[0][0]) else ' '
            exts = '  '.join(f'{e}({n})' for e, n in
                             Counter(e.lower() for _, e in lista).most_common())
            f.write(f'[{reconoce}] [{len(lista):>6}]  {patron}   {exts}\n')
            for stem, ext in lista[:3]:
                f.write(f'                 Ej: {stem}{ext}\n')
            f.write('\n')
    print(f'\n  Resultado: {salida}')
    print(f'  Total archivos: {total:,}  |  Patrones: {len(patrones):,}')
    return C.OK


def main():
    ap = argparse.ArgumentParser(description='Analiza patrones de nombres.')
    ap.add_argument('carpetas', nargs='*')
    ap.add_argument('-o', '--salida')
    a = ap.parse_args()
    carpetas = a.carpetas or C.pedir_carpetas()
    codigo = ejecutar(carpetas, a.salida)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
