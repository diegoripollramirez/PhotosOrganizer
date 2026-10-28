"""
╔══════════════════════════════════════════════════════════════╗
║           DESHACER                                           ║
║                                                              ║
║  Uso: python 8_deshacer.py [registro.csv] [--si]             ║
║                                                              ║
║  Cada script que mueve o renombra archivos guarda un CSV en  ║
║  logs/deshacer/ (renombrar, limpiar, duplicados, mover).     ║
║  Este script lo recorre al revés y devuelve cada archivo a   ║
║  su sitio y nombre originales.                               ║
║                                                              ║
║  · Deshaz en orden inverso (el más reciente primero).        ║
║  · Solo mueve si el archivo sigue donde se dejó y el sitio   ║
║    original está libre: nunca sobrescribe nada.              ║
║  · Los cambios de fechas EXIF NO se pueden deshacer.         ║
╚══════════════════════════════════════════════════════════════╝
"""

import csv
import sys
import shutil
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C


def registros():
    if not C.REGISTROS_DIR.exists():
        return []
    return sorted((p for p in C.REGISTROS_DIR.glob('*.csv') if not p.name.endswith('.deshecho.csv')),
                  reverse=True)


def ejecutar(registro, auto=False):
    C.iniciar_logs('deshacer.txt')
    registro = Path(registro)
    with open(registro, encoding='utf-8', newline='') as f:
        filas = list(csv.DictReader(f))
    print(f'\n  {registro.name}: {len(filas):,} movimientos')
    lista = registros()
    if lista and C.clave(lista[0]) != C.clave(registro):
        print(f'  ⚠  Hay un registro más reciente sin deshacer ({lista[0].name}).')
        print('     Lo seguro es deshacer primero el más reciente.')
    if not filas:
        return C.OK
    for r in filas[:5]:
        print(f'    {Path(r["destino"]).name}  →  {r["origen"]}')
    if not C.confirmar(f'¿Deshacer {len(filas):,} movimientos?', auto):
        print('  Cancelado.')
        return C.CANCELADO

    hechos, saltados = 0, []
    for r in reversed(filas):
        origen, destino = Path(r['origen']), Path(r['destino'])
        if not destino.exists():
            saltados.append(f'ya no está: {destino}')
            continue
        if origen.exists() and C.clave(origen) != C.clave(destino):
            saltados.append(f'ocupado: {origen}')
            continue
        try:
            origen.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destino), str(origen))
            hechos += 1
        except Exception as e:
            saltados.append(f'error: {destino} — {e}')

    # Borrar carpetas que hayan quedado vacías en _duplicados/_basura
    for fila in filas:
        p = Path(fila['destino']).parent
        while p.exists() and p.name not in ('', '.'):
            try:
                p.rmdir()
            except OSError:
                break
            p = p.parent

    log = C.ruta_log('deshacer.txt')
    with open(log, 'w', encoding='utf-8') as f:
        f.write(f'Registro: {registro}\nDeshechos: {hechos}\nSaltados: {len(saltados)}\n\n')
        f.write(''.join(s + '\n' for s in saltados))
    if not saltados:
        registro.rename(registro.with_name(registro.stem + '.deshecho.csv'))
    C.caja('DESHECHO', [('Archivos restaurados', f'{hechos:,}'), ('Saltados (ver log)', len(saltados))])
    print(f'\n  Log: {log}')
    return C.OK if not saltados else C.ERROR


def main():
    ap = argparse.ArgumentParser(description='Deshace un movimiento o renombrado.')
    ap.add_argument('registro', nargs='?')
    ap.add_argument('--si', action='store_true')
    a = ap.parse_args()
    reg = a.registro
    if not reg:
        lista = registros()
        if not lista:
            print('\n  No hay nada que deshacer.')
            C.pausa_final()
            sys.exit(C.OK)
        print('\n  Registros disponibles (el más reciente primero):\n')
        for i, p in enumerate(lista, 1):
            n = sum(1 for _ in open(p, encoding='utf-8')) - 1
            print(f'  {i:>3}. {p.name}   ({n:,} movimientos)')
        try:
            sel = input('\n  Número a deshacer (Enter = 1): ').strip() or '1'
            reg = lista[int(sel) - 1]
        except (ValueError, IndexError, EOFError):
            print('  Selección no válida.')
            C.pausa_final()
            sys.exit(C.ERROR)
    codigo = ejecutar(reg, a.si)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
