"""
╔══════════════════════════════════════════════════════════════╗
║           REVISAR LA MAESTRA (pipeline de mantenimiento)     ║
║                                                              ║
║  Uso: python 0_revisar_maestra.py                            ║
║  Busca duplicados SOLO dentro de la maestra, sin pendientes. ║
║  Útil si sospechas que hay fotos repetidas ya organizadas.   ║
║                                                              ║
║  Pasos:                                                      ║
║    1. 3_eliminar_duplicados  modo 1: EXACTOS (byte a byte)   ║
║    2. 3_eliminar_duplicados  modo 2: VISUALES, con revisión  ║
║                              (misma foto con metadatos o     ║
║                              resolución distintos)           ║
║    3. 6_verificar_coleccion  auditoría, solo lectura         ║
║                                                              ║
║  Entre dos copias se conserva la de más resolución; si es la ║
║  misma imagen, la que ya está en una subcarpeta organizada.  ║
║  Lo que se aparta va a _duplicados/ junto a los scripts y se ║
║  puede deshacer con 8_deshacer.py.                           ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C


def cargar(archivo):
    ruta = C.SCRIPTS_DIR / archivo
    spec = importlib.util.spec_from_file_location(ruta.stem.lstrip('0123456789_'), ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    print()
    print('╔══════════════════════════════════════════════════════════════╗')
    print('║           REVISAR LA MAESTRA                                 ║')
    print('╚══════════════════════════════════════════════════════════════╝')
    print()
    maestra = C.pedir_carpeta('  Carpeta de fotos organizadas (maestra): ')
    if not maestra:
        return C.ERROR
    msg = C.problema_carpeta_trabajo(maestra)
    if msg:
        print('  ERROR: ' + msg)
        return C.ERROR

    dup = cargar('3_eliminar_duplicados.py')
    faltan = dup.librerias_visuales_faltan()
    if faltan:
        print(f'\n  ⚠  Faltan librerías para los duplicados visuales: {", ".join(faltan)}.')
        print(f'     El paso 2 se saltará. Instálalas con: python -m pip install {" ".join(faltan)} pillow-heif')

    def visuales():
        if faltan:
            print('  ⚠  Faltan librerías: paso omitido.')
            return C.OK
        return dup.modo_2(maestra)

    pasos = [
        ('Duplicados exactos (script 3, modo 1)', lambda: dup.modo_1(maestra, None, auto=True)),
        ('Duplicados visuales (script 3, modo 2)' + (' — omitido' if faltan else ''), visuales),
        ('Verificar colección', lambda: cargar('6_verificar_coleccion.py').ejecutar([maestra])),
    ]
    print('\n  ┌──────────────────────────────────────────────────────────')
    print(f'  │  Maestra : {maestra}')
    for i, (nombre, _) in enumerate(pasos, 1):
        print(f'  │  {i}. {nombre}')
    print('  └──────────────────────────────────────────────────────────\n')
    if not C.confirmar('¿Iniciar?'):
        print('  Cancelado.')
        return C.CANCELADO

    for i, (nombre, accion) in enumerate(pasos, 1):
        titulo = f'  PASO {i}/{len(pasos)}: {nombre}'
        print('\n╔' + '═' * 62 + '╗\n║' + titulo.ljust(62) + '║\n╚' + '═' * 62 + '╝')
        try:
            codigo = accion()
        except KeyboardInterrupt:
            print('\n  Interrumpido por el usuario.')
            return C.CANCELADO
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'\n  ✗ Error inesperado en "{nombre}": {e}')
            codigo = C.ERROR
        if codigo not in (C.OK, C.NADA):
            estado = 'cancelado' if codigo == C.CANCELADO else 'con errores'
            print(f'\n  ✗ Paso "{nombre}" {estado}. Se detiene aquí. Revisa logs/ y vuelve a lanzarlo.')
            return codigo

    print('\n╔══════════════════════════════════════════════════════════════╗')
    print('║  REVISIÓN DE LA MAESTRA COMPLETADA                           ║')
    print('╚══════════════════════════════════════════════════════════════╝')
    print(f'\n  Logs en: {C.LOGS_DIR}   ·   Lo apartado está en _duplicados/')
    print('  Para deshacer cualquier paso: 8_deshacer.py')
    return C.OK


if __name__ == '__main__':
    codigo = main()
    C.pausa_final()
    sys.exit(codigo)
