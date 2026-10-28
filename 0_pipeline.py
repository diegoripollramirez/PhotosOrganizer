"""
╔══════════════════════════════════════════════════════════════╗
║           PIPELINE AUTOMÁTICO DE FOTOS                       ║
║                                                              ║
║  Uso: python 0_pipeline.py                                   ║
║  Pregunta las carpetas UNA sola vez y ejecuta todo seguido.  ║
║  La única pausa es la revisión de duplicados visuales, que   ║
║  siempre es manual.                                          ║
║                                                              ║
║  Pasos y script que usa cada uno:                            ║
║    1. 1_extraer_zips          (si no hay ZIP nuevos, no hace ║
║                               nada)                          ║
║    2. 2_renombrar_fotos       YYYY-MM-DD_HH-MM-SS + Live     ║
║    3. 3_eliminar_duplicados   modo 1: EXACTOS, antes de      ║
║                               tocar ningún byte              ║
║    4. 4_escribir_fechas_exif  (se salta si no hay ExifTool)  ║
║    5. 5_limpiar_archivos      Thumbs.db, desktop.ini y ZIP   ║
║                               ya extraídos                   ║
║    6. 3_eliminar_duplicados   modo 2: VISUALES, con revisión ║
║                               HTML                           ║
║    7. 6_verificar_coleccion   auditoría, solo lectura        ║
║    8. 7_mover_a_maestra       guarda el álbum como etiqueta  ║
║                               y pasa lo listo a la RAÍZ de   ║
║                               la maestra; lo demás se queda  ║
║                                                              ║
║  Cada paso solo empieza si el anterior fue bien. Todo lo que ║
║  se mueve o renombra se puede deshacer con 8_deshacer.py.    ║
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
    print('║           PIPELINE AUTOMÁTICO DE FOTOS                       ║')
    print('╚══════════════════════════════════════════════════════════════╝')
    print()
    pendientes = C.pedir_carpeta('  Carpeta de fotos nuevas     : ')
    maestra = C.pedir_carpeta('  Carpeta de fotos organizadas: ')
    if not pendientes or not maestra:
        return C.ERROR
    for c in (pendientes, maestra):
        msg = C.problema_carpeta_trabajo(c)
        if msg:
            print('  ERROR: ' + msg)
            return C.ERROR
    if C.clave(pendientes) == C.clave(maestra):
        print('  ERROR: pendientes y maestra no pueden ser la misma carpeta.')
        return C.ERROR

    exiftool = C.exiftool_disponible()
    C.aviso_sin_exiftool('se saltará el paso 4 (escribir fechas), el renombrado no podrá leer las '
                         'fechas de HEIC ni de vídeos y no se guardarán los álbumes')

    def paso4():
        if not exiftool:
            print('  ⚠  Sin ExifTool: paso omitido.')
            return C.OK
        return cargar('4_escribir_fechas_exif.py').ejecutar(pendientes, auto=True)

    faltan_visual = cargar('3_eliminar_duplicados.py').librerias_visuales_faltan()
    if faltan_visual:
        print(f'\n  ⚠  Faltan librerías para los duplicados visuales: {", ".join(faltan_visual)}.')
        print('     Ese paso se saltará. Para tenerlo, instala (CMD como administrador):')
        print(f'     python -m pip install {" ".join(faltan_visual)} pillow-heif')
        print('     (si da error de permisos, añade --user; más ayuda en la guía, sección 1)')

    def paso7():
        if faltan_visual:
            print('  ⚠  Faltan librerías: paso omitido.')
            return C.OK
        return cargar('3_eliminar_duplicados.py').modo_2(maestra, pendientes)

    limpiar = cargar('5_limpiar_archivos.py').SEGUROS
    pasos = [
        ('Extraer ZIPs', lambda: cargar('1_extraer_zips.py').ejecutar(pendientes, auto=True)),
        ('Renombrar fotos', lambda: cargar('2_renombrar_fotos.py').ejecutar([pendientes], auto=True)),
        ('Duplicados exactos (script 3, modo 1)', lambda: cargar('3_eliminar_duplicados.py').modo_1(maestra, pendientes, auto=True)),
        ('Escribir fechas EXIF' + ('' if exiftool else ' (omitido: falta ExifTool)'), paso4),
        ('Limpiar Thumbs.db, desktop.ini y ZIP extraídos',
         lambda: cargar('5_limpiar_archivos.py').ejecutar(pendientes, limpiar, auto=True)),
        ('Duplicados visuales (omitido: faltan librerías)' if faltan_visual else 'Duplicados visuales (script 3, modo 2)',
         paso7),
        ('Verificar colección', lambda: cargar('6_verificar_coleccion.py').ejecutar([pendientes])),
        ('Mover a la maestra (con etiqueta de álbum)',
         lambda: cargar('7_mover_a_maestra.py').ejecutar(pendientes, maestra, auto=True)),
    ]

    print('\n  ┌──────────────────────────────────────────────────────────')
    print(f'  │  Pendientes : {pendientes}')
    print(f'  │  Maestra    : {maestra}')
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
        if codigo == C.NADA:
            print('\n  No hay nada que procesar en PENDIENTES.')
            print('  Puedes aprovechar para preparar la caché de la MAESTRA: así la próxima vez')
            print('  que haya fotos nuevas solo se analizarán esas. La primera vez puede tardar')
            print('  varias horas; si ya está preparada, es cuestión de minutos.')
            if C.preguntar_sn('¿Preparar la caché ahora?'):
                return cargar('3_eliminar_duplicados.py').preparar_cache(maestra)
            print('  El pipeline se detiene aquí.')
            return C.OK
        if codigo != C.OK:
            estado = 'cancelado' if codigo == C.CANCELADO else 'con errores'
            print(f'\n  ✗ Paso "{nombre}" {estado}. El pipeline se detiene aquí.')
            print('    Revisa el log de ese paso en la carpeta logs/ y vuelve a lanzarlo.')
            return codigo

    print('\n╔══════════════════════════════════════════════════════════════╗')
    print('║  PIPELINE COMPLETADO                                         ║')
    print('╚══════════════════════════════════════════════════════════════╝')
    print(f'\n  Logs en: {C.LOGS_DIR}')
    print('  Para deshacer cualquier paso: 8_deshacer.py')
    print('\n  Las fotos listas están en la raíz de la MAESTRA: repártelas en sus subcarpetas.')
    print('  Lo que queda en PENDIENTES necesita revisión (mira logs/verificacion_coleccion.txt')
    print('  y logs/movidos_a_maestra.txt).')
    return C.OK


if __name__ == '__main__':
    codigo = main()
    C.pausa_final()
    sys.exit(codigo)
