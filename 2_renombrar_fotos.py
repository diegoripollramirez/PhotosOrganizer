"""
╔══════════════════════════════════════════════════════════════╗
║         RENOMBRADOR DE FOTOS — HERRAMIENTA COMPLETA          ║
║                                                              ║
║  Uso: python 2_renombrar_fotos.py [carpetas...] [--si]       ║
║                                   [--simular]                ║
║  Sin argumentos te pide las carpetas.                        ║
║                                                              ║
║  Formato destino: YYYY-MM-DD_HH-MM-SS_descripcion.ext        ║
║                                                              ║
║  Fuentes de fecha (se elige con la regla de comun.py):       ║
║    · nombre del archivo (25+ patrones)                       ║
║    · JSON de Google Takeout (todas sus variantes de nombre)  ║
║    · EXIF / QuickTime leído con ExifTool (HEIC, RAW, vídeo)  ║
║    · ÚLTIMO RECURSO: fecha de modificación del archivo, solo ║
║      si ninguna de las anteriores da fecha (→ sin_fecha_     ║
║      fecha_archivo.txt para revisarlas)                      ║
║  Gana la más antigua, PERO una fecha "solo día" (00:00:00)   ║
║  no pisa la hora exacta de otra fuente del mismo día, y el   ║
║  desfase horario del JSON (UTC) no pisa al EXIF.             ║
║                                                              ║
║  Además:                                                     ║
║    · El JSON de cada foto se renombra con ella               ║
║    · Live Photos: el vídeo (.MOV / .HEIC.qt / .MP4) recibe   ║
║      EXACTAMENTE el mismo nombre base que su foto            ║
║    · Antes de pedir SI guarda el plan completo en            ║
║      logs/plan_renombrado.txt para que puedas revisarlo      ║
║    · Guarda un CSV para deshacer con 8_deshacer.py           ║
║                                                              ║
║  Recomendado: ExifTool (https://exiftool.org), en el PATH o  ║
║  junto a los scripts.                                        ║
║  Sin él solo se lee EXIF de JPEG/TIFF con piexif.            ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C

SKIP_EXT = {'.json', '.zip'}


def nombre_nuevo(fecha, desc, exts, usados, propios):
    """Base de nombre libre para todas las extensiones del grupo."""
    prefijo = fecha.dt.strftime('%Y-%m-%d_%H-%M-%S')
    desc = re.sub(r'[\\/:*?"<>|]', '_', (desc or ''))[:60].strip('_- .')
    i = 0
    while True:
        base = prefijo + (f'_{desc}' if desc else '') + (f'_{i}' if i else '')
        if all((base + e).lower() not in usados or (base + e).lower() in propios for e in exts):
            return base
        i += 1


def ejecutar(carpetas, auto=False, simular=False):
    C.iniciar_logs('sin_fecha.txt', 'fechas_absurdas.txt', 'plan_renombrado.txt', 'renombrados.txt',
                   'fecha_archivo.txt')
    print('\n' + '═' * 65)
    print('  RENOMBRADOR DE FOTOS' + ('   [MODO SIMULACIÓN]' if simular else ''))
    print('═' * 65)

    con_exiftool = C.exiftool_disponible()
    C.aviso_sin_exiftool('las fotos HEIC de iPhone y los vídeos no tendrán fecha EXIF')

    print('\n[1/3] Leyendo JSON de Google Takeout...')
    indice = C.IndiceJSON()
    n = indice.cargar(carpetas)
    print(f'  {n:,} fechas cargadas desde {len(indice.todos):,} JSON.')

    print('\n[2/3] Leyendo metadatos...')
    medias = []
    for c in carpetas:
        for f in C.recorrer(c):
            if C.es_media(f.name):
                medias.append(f)
    metas = C.leer_metadatos(medias) if con_exiftool else {}
    foto_video, video_foto = C.detectar_live_photos(medias, metas)
    print(f'  {len(medias):,} fotos/vídeos  ·  {len(foto_video):,} Live Photos emparejados')
    if not medias:
        for c in carpetas:
            C.diagnosticar_sin_fotos(c)
        return C.NADA

    def fecha_exif(f):
        if con_exiftool:
            m = metas.get(C.clave(f))
            return m.fecha()[0] if m else None
        return C.leer_fecha_piexif(f)

    print('\n[3/3] Analizando...')
    plan = []            # (origen, destino, fuente, tipo)
    sin_fecha, absurdas, por_archivo = [], [], []
    ya_ok = 0
    jsons_usados = set()

    por_carpeta = defaultdict(list)
    for f in medias:
        por_carpeta[f.parent].append(f)

    total = 0
    for carpeta, archivos in por_carpeta.items():
        try:
            usados = {x.lower() for x in os.listdir(carpeta)}
        except OSError:
            usados = {f.name.lower() for f in archivos}

        for f in sorted(archivos):
            total += 1
            if total % 1000 == 0:
                print(f'  {total:,} archivos analizados...', end='\r')
            if C.clave(f) in video_foto:
                continue                         # se trata junto a su foto
            stem, ext = C.separar_nombre(f.name)
            if ext.lower() in SKIP_EXT:
                continue

            f_nombre = C.extraer_fecha_nombre(stem)
            f_json, jpath, propio = indice.buscar(f)
            f_exif = fecha_exif(f)
            for x in (f_nombre, f_json, f_exif):
                if x is not None and not C.fecha_valida(x.dt):
                    absurdas.append((str(f), x.fuente, x.dt.strftime('%Y-%m-%d')))

            video = foto_video.get(C.clave(f))
            elegida = C.elegir_fecha([f_nombre, f_json, f_exif])
            if elegida is None and video is not None:
                fj, _, _ = indice.buscar(video)
                elegida = C.elegir_fecha([fecha_exif(video), fj])
            if elegida is None:
                # Último recurso: fecha de modificación del archivo
                elegida = C.fecha_archivo(f)
                if elegida is not None:
                    por_archivo.append(f'{elegida.dt:%Y-%m-%d %H:%M:%S}  {f}')
            if elegida is None:
                sin_fecha.append(str(f))
                if video is not None:
                    sin_fecha.append(str(video))
                continue

            formateado = C.FORMATO_NOMBRE.match(stem) and f_nombre is not None \
                and C.fecha_valida(f_nombre.dt)
            if f_nombre is not None:
                desc = f_nombre.desc
                if formateado:
                    desc = re.sub(r'_\d{1,3}$', '', desc)   # contador de colisión previo
            else:
                desc = stem

            # Archivos del grupo: foto/vídeo principal + vídeo Live + JSON propios
            json_propio = jpath if (propio and jpath and jpath.exists()
                                    and C.clave(jpath) not in jsons_usados) else None
            jv = None
            if video is not None:
                _, jv_path, jv_propio = indice.buscar(video)
                if jv_propio and jv_path and jv_path.exists() and C.clave(jv_path) not in jsons_usados:
                    jv = jv_path

            mantener = formateado and elegida.dt == f_nombre.dt
            if mantener:
                base = stem
                ya_ok += 1
            else:
                exts = [ext.lower(), ext.lower() + '.json']
                propios = {f.name.lower()}
                if video is not None:
                    vext = C.separar_nombre(video.name)[1].lower()
                    exts += [vext, vext + '.json']
                    propios.add(video.name.lower())
                if json_propio:
                    propios.add(json_propio.name.lower())
                if jv:
                    propios.add(jv.name.lower())
                base = nombre_nuevo(elegida, desc, exts, usados, propios)
                nuevo = base + ext.lower()
                if nuevo != f.name:
                    plan.append((f, carpeta / nuevo, elegida.fuente, 'media'))
                    usados.add(nuevo.lower())
                    if json_propio:
                        nj = nuevo + '.json'
                        if json_propio.name != nj:
                            plan.append((json_propio, json_propio.parent / nj, elegida.fuente, 'json'))
                            usados.add(nj.lower())
                        jsons_usados.add(C.clave(json_propio))

            if video is not None:
                vext = C.separar_nombre(video.name)[1]
                nuevo_v = base + vext.lower()
                if video.name != nuevo_v:
                    if nuevo_v.lower() in usados and nuevo_v.lower() != video.name.lower():
                        nuevo_v = C.nombre_libre(carpeta / nuevo_v, set()).name
                    plan.append((video, carpeta / nuevo_v, 'live', 'live'))
                    usados.add(nuevo_v.lower())
                    if jv and jv.name != nuevo_v + '.json':
                        plan.append((jv, jv.parent / (nuevo_v + '.json'), 'live', 'json'))
                        usados.add((nuevo_v + '.json').lower())
                if jv:
                    jsons_usados.add(C.clave(jv))

    print(f'  {total:,} archivos analizados.      ')

    # ── Logs ───────────────────────────────────────────────
    log_sin = C.ruta_log('sin_fecha.txt')
    with open(log_sin, 'w', encoding='utf-8') as f:
        f.write(f'Archivos sin fecha detectada: {len(sin_fecha)}\n\n')
        f.write(''.join(p + '\n' for p in sin_fecha))
    log_arch = C.ruta_log('fecha_archivo.txt')
    with open(log_arch, 'w', encoding='utf-8') as f:
        f.write(f'Sin fecha en nombre, JSON ni EXIF → fecha de modificación del archivo: {len(por_archivo)}\n')
        f.write('Es la fuente menos fiable (cambia al copiar): revísalas si algo no cuadra.\n\n')
        f.write(''.join(p + '\n' for p in por_archivo))
    log_abs = C.ruta_log('fechas_absurdas.txt')
    with open(log_abs, 'w', encoding='utf-8') as f:
        f.write(f'Fechas fuera de rango descartadas: {len(absurdas)}\n')
        f.write(f'Rango válido: {C.FECHA_MIN:%Y-%m-%d} – {C.FECHA_MAX:%Y-%m-%d}\n\n')
        f.write(''.join(f'[{fu}] {fe}  →  {r}\n' for r, fu, fe in absurdas))

    n_media = sum(1 for p in plan if p[3] == 'media')
    n_live = sum(1 for p in plan if p[3] == 'live')
    n_json = sum(1 for p in plan if p[3] == 'json')
    print(f'\n{"─" * 65}')
    print(f'  Fotos/vídeos analizados          : {len(medias):>10,}')
    print(f'  Ya con nombre correcto           : {ya_ok:>10,}')
    print(f'  A renombrar                      : {n_media:>10,}')
    print(f'    + vídeos Live Photo            : {n_live:>10,}')
    print(f'    + JSON acompañantes            : {n_json:>10,}')
    if por_archivo:
        print(f'  Con fecha del archivo (último    : {len(por_archivo):>10,}')
        print(f'    recurso → logs/fecha_archivo.txt)')
    print(f'  Sin fecha (→ logs/sin_fecha.txt) : {len(sin_fecha):>10,}')
    if absurdas:
        print(f'  Fechas absurdas descartadas      : {len(absurdas):>10,}')
    print(f'{"─" * 65}')

    if not plan:
        print('\n  Nada que renombrar. Todo está al día.')
        return C.OK

    print('\n  Ejemplos:')
    for old, new, src, tipo in [p for p in plan if p[3] != 'json'][:8]:
        print(f'    [{src:7}] {old.name}\n              → {new.name}')
    if len(plan) > 8:
        print(f'    ... y {len(plan) - 8:,} más.')

    # El plan completo se guarda SIEMPRE antes de confirmar, para poder
    # revisarlo con calma antes de escribir SI
    log_plan = C.ruta_log('plan_renombrado.txt')
    with open(log_plan, 'w', encoding='utf-8') as f:
        f.write(f'PLAN — se renombrarían {len(plan)} archivos (aún no se ha cambiado nada)\n\n')
        for old, new, src, tipo in plan:
            f.write(f'[{src}/{tipo}] {old}\n    → {new.name}\n')
    print(f'\n  Plan completo (todavía no se ha cambiado nada): {log_plan}')
    if simular:
        print('  Simulación: no se ha cambiado nada.')
        return C.OK

    log_ok = C.ruta_log('renombrados.txt')
    print()
    if not C.confirmar(f'¿Renombrar {len(plan):,} archivos? (se puede deshacer con 8_deshacer.py)', auto):
        print('  Cancelado. No se ha cambiado nada.')
        return C.CANCELADO

    reg = C.Registro('renombrar')
    hechos = errores = 0
    with open(log_ok, 'w', encoding='utf-8') as log:
        log.write(f'Archivos renombrados: {len(plan)}\n\n')
        for old, new, src, tipo in plan:
            try:
                if new.exists() and C.clave(new) != C.clave(old):
                    raise FileExistsError(f'ya existe {new.name}')
                old.rename(new)
                reg.anotar(old, new)
                log.write(f'[{src}/{tipo}] {old}\n    → {new.name}\n')
                hechos += 1
                if hechos % 500 == 0:
                    print(f'  Renombrados: {hechos:,} / {len(plan):,}...', end='\r')
            except Exception as e:
                log.write(f'ERROR: {old} → {e}\n')
                errores += 1
    reg.cerrar()
    C.caja('RENOMBRADO COMPLETADO', [('Renombrados', f'{hechos:,}'), ('Errores', errores)])
    print(f'\n  Log: {log_ok}')
    if reg.ruta:
        print(f'  Para deshacer: 8_deshacer.py ({reg.ruta.name})')
    return C.ERROR if errores else C.OK


def main():
    ap = argparse.ArgumentParser(description='Renombra fotos a YYYY-MM-DD_HH-MM-SS.')
    ap.add_argument('carpetas', nargs='*')
    ap.add_argument('--si', action='store_true', help='no pedir confirmación')
    ap.add_argument('--simular', action='store_true', help='mostrar el plan sin renombrar')
    a = ap.parse_args()
    carpetas = a.carpetas or C.pedir_carpetas()
    for c in carpetas:
        if not Path(c).is_dir():
            print(f'  ERROR: no existe la carpeta: {c}')
            C.pausa_final()
            sys.exit(C.ERROR)
    codigo = ejecutar(carpetas, a.si, a.simular)
    C.pausa_final()
    sys.exit(C.OK if codigo == C.NADA else codigo)


if __name__ == '__main__':
    main()
