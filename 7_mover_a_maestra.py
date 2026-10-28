"""
╔══════════════════════════════════════════════════════════════╗
║           MOVER A LA MAESTRA                                 ║
║                                                              ║
║  Uso: python 7_mover_a_maestra.py [--pendientes P]           ║
║                                   [--maestra M] [--si]       ║
║                                                              ║
║  Último paso: mueve a la RAÍZ de la maestra las fotos y      ║
║  vídeos de pendientes que ya están listos, para que tú los   ║
║  repartas después en las subcarpetas que quieras.            ║
║                                                              ║
║  · Solo mueve archivos con nombre YYYY-MM-DD_HH-MM-SS. Lo    ║
║    que no se pudo renombrar se queda en pendientes para que  ║
║    lo revises.                                               ║
║  · Cada foto viaja con su vídeo Live Photo; si el nombre ya  ║
║    existe en la maestra, los dos reciben el mismo sufijo.    ║
║  · Antes de moverla, guarda dentro de cada foto el nombre de ║
║    su carpeta como etiqueta (p.ej. "Roma 2015"), para poder  ║
║    buscarla después en digiKam o en el Explorador. Las       ║
║    carpetas genéricas ("Photos from 2015"…) no se guardan.   ║
║  · Sus JSON de Takeout ya no hacen falta (la fecha está en   ║
║    el nombre y en los metadatos): van a _basura/ de          ║
║    pendientes. Cualquier otro archivo se queda donde está.   ║
║  · Los archivos VACÍOS (0 bytes) nunca van a la maestra: se  ║
║    apartan a _vacios/ junto a los scripts (como _duplicados).║
║  · Se puede deshacer con 8_deshacer.py.                      ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import shutil
from datetime import datetime
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C


def _vacio(f):
    try:
        return f.stat().st_size == 0
    except OSError:
        return False


def ejecutar(pendientes, maestra, auto=False):
    C.iniciar_logs('movidos_a_maestra.txt')
    pendientes, maestra = Path(pendientes), Path(maestra)
    if C.clave(pendientes) == C.clave(maestra) or C.en_carpeta(maestra, pendientes) \
            or C.en_carpeta(pendientes, maestra):
        print('  ERROR: pendientes y maestra deben ser carpetas distintas y no estar una dentro de otra.')
        return C.ERROR

    print('\n  Buscando fotos y vídeos listos en pendientes...')
    medias = [f for f in C.recorrer(pendientes) if C.es_media(f.name)]
    C.aviso_sin_exiftool('no se guardará el álbum como etiqueta y los Live Photos solo se detectan por nombre')
    metas = C.leer_metadatos(medias) if C.exiftool_disponible() else {}
    foto_video, video_foto = C.detectar_live_photos(medias, metas)
    indice = C.IndiceJSON()
    indice.cargar([pendientes])

    grupos, se_quedan, vacios = [], [], []
    for f in sorted(medias):
        k = C.clave(f)
        if k in video_foto:
            continue                       # viaja con su foto
        if _vacio(f):
            vacios.append(f)               # 0 bytes: no es una foto, no va a la maestra
            v = foto_video.get(k)
            if v is not None:
                se_quedan.append(v)
            continue
        if C.fecha_formato_nombre(f.name) is None:
            se_quedan.append(f)
            v = foto_video.get(k)
            if v is not None:
                se_quedan.append(v)
            continue
        g = [f]
        v = foto_video.get(k)
        if v is not None:
            g.append(v)
        grupos.append(g)

    n_mover = sum(len(g) for g in grupos)
    tam = sum(x.stat().st_size for g in grupos for x in g)
    print(f'  Fotos/vídeos listos para mover : {n_mover:>8,}  ({C.gb(tam)})')
    if se_quedan:
        print(f'  Se quedan en pendientes        : {len(se_quedan):>8,}  (sin nombre YYYY-MM-DD, revisar)')
    if vacios:
        print(f'  Vacíos (0 bytes) → {C.VACIOS_DIR.name}/    : {len(vacios):>8,}  (no van a la maestra)')
    if not grupos and not vacios:
        print('\n  No hay nada listo para mover.')
        return C.OK
    por_album = defaultdict(list)
    for g in grupos:
        alb = C.albumes_de(g[0], pendientes)
        if alb:
            por_album[tuple(alb)] += g
    if por_album and C.exiftool_disponible():
        print(f'  Con etiqueta de álbum          : {sum(len(v) for v in por_album.values()):>8,}  '
              f'({len(por_album)} álbumes)')
    print(f'  Destino: {maestra}  (raíz)\n')
    if not C.confirmar(f'¿Mover {n_mover:,} archivos a la maestra'
                       + (f' y {len(vacios):,} vacíos a _vacios/' if vacios else '') + '?', auto):
        print('  Cancelado. No se ha movido nada.')
        return C.CANCELADO

    if por_album and C.exiftool_disponible():
        bloques = []
        for alb, archivos in por_album.items():
            args = ['-overwrite_original', '-P']          # -P: conserva la fecha del archivo
            for a in alb:
                args += [f'-XMP-dc:Subject-={a}', f'-XMP-dc:Subject+={a}']   # sin repetir
            bloques += [(x, args) for x in archivos]
        _, err = C.ejecutar_bloques(bloques, ['-m'], 'Guardando álbumes')
        if err:
            print(f'  ℹ  {len(err):,} archivos no admiten etiquetas (GIF, BMP, algunos vídeos…); se mueven igual.')

    ocupados = {C.clave(p) for p in maestra.iterdir()}
    reg = C.Registro('mover_a_maestra')
    sello = datetime.now().strftime('%Y%m%d_%H%M%S')
    movidos = con_sufijo = errores = jsons = 0
    log = C.ruta_log('movidos_a_maestra.txt')
    with open(log, 'w', encoding='utf-8') as fl:
        fl.write(f'Pendientes: {pendientes}\nMaestra   : {maestra}\n' + '=' * 60 + '\n\n')
        for g in grupos:
            stem_p = C.separar_nombre(g[0].name)[0]
            i = 0
            while True:
                destinos = []
                for f in g:
                    st, ex = C.separar_nombre(f.name)
                    nombre = f.name if not i else (f'{stem_p}_{i}{ex}' if st == stem_p else f'{st}_{i}{ex}')
                    destinos.append(maestra / nombre)
                if all(C.clave(d) not in ocupados for d in destinos):
                    break
                i += 1
            if i:
                con_sufijo += 1
            for f, d in zip(g, destinos):
                try:
                    shutil.move(str(f), str(d))
                    reg.anotar(f, d)
                    ocupados.add(C.clave(d))
                    movidos += 1
                    fl.write(f'{f}  →  {d.name}\n')
                except Exception as e:
                    fl.write(f'ERROR: {f} — {e}\n')
                    errores += 1
                _, jp, propio = indice.buscar(f)
                if propio and jp is not None and jp.exists():
                    try:
                        C.mover(jp, pendientes / '_basura' / C.ruta_relativa_segura(jp, pendientes), reg)
                        jsons += 1
                    except Exception as e:
                        fl.write(f'ERROR: {jp} — {e}\n')
            if movidos % 500 < len(g):
                print(f'  Movidos: {movidos:,} / {n_mover:,}...', end='\r')
        for f in vacios:
            try:
                d = C.mover(f, C.VACIOS_DIR / sello / C.ruta_relativa_segura(f, pendientes), reg)
                fl.write(f'VACÍO: {f}  →  {d}\n')
            except Exception as e:
                fl.write(f'ERROR: {f} — {e}\n')
                errores += 1
        if se_quedan:
            fl.write('\nSE QUEDAN EN PENDIENTES (sin nombre YYYY-MM-DD):\n')
            fl.write(''.join(f'  {f}\n' for f in se_quedan))
    reg.cerrar()

    # Borrar subcarpetas de pendientes que hayan quedado vacías
    for sub in sorted((p for p in pendientes.rglob('*') if p.is_dir()), key=lambda p: -len(p.parts)):
        if any(x in C.CARPETAS_EXCLUIR for x in sub.relative_to(pendientes).parts):
            continue
        try:
            sub.rmdir()
        except OSError:
            pass

    filas = [('Movidos a la maestra', f'{movidos:,}')]
    if jsons:
        filas.append(('JSON apartados a _basura', f'{jsons:,}'))
    if con_sufijo:
        filas.append(('Con sufijo (nombre ocupado)', f'{con_sufijo:,}'))
    if vacios:
        filas.append(('Vacíos apartados a _vacios', f'{len(vacios):,}'))
    if se_quedan:
        filas.append(('Se quedan en pendientes', f'{len(se_quedan):,}'))
    if errores:
        filas.append(('Errores', errores))
    C.caja('MOVIDO A LA MAESTRA', filas)
    print(f'\n  Log: {log}')
    if vacios:
        print(f'  Vacíos en: {C.VACIOS_DIR / sello}')
    if reg.ruta:
        print(f'  Para deshacer: 8_deshacer.py ({reg.ruta.name})')
    return C.ERROR if errores else C.OK


def main():
    ap = argparse.ArgumentParser(description='Mueve las fotos listas de pendientes a la raíz de la maestra.')
    ap.add_argument('--pendientes')
    ap.add_argument('--maestra')
    ap.add_argument('--si', action='store_true')
    a = ap.parse_args()
    print()
    pend = a.pendientes or C.pedir_carpeta('  Carpeta PENDIENTES : ')
    maes = a.maestra or C.pedir_carpeta('  Carpeta MAESTRA    : ')
    for c in (pend, maes):
        if not c or not Path(c).is_dir():
            print(f'  ERROR: no existe la carpeta: {c}')
            C.pausa_final()
            sys.exit(C.ERROR)
    codigo = ejecutar(pend, maes, a.si)
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
