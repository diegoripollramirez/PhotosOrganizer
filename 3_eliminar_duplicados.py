"""
╔══════════════════════════════════════════════════════════════╗
║           ELIMINADOR DE DUPLICADOS                           ║
╠══════════════════════════════════════════════════════════════╣
║  Uso: python 3_eliminar_duplicados.py                        ║
║       python 3_eliminar_duplicados.py --modo 1 --maestra M   ║
║              [--pendientes P] [--si]                         ║
║                                                              ║
║  1. Duplicados EXACTOS (mismo contenido byte a byte)         ║
║     · Primero agrupa por tamaño y solo calcula el hash de    ║
║       los archivos que coinciden en tamaño (mucho más rápido)║
║     · Conserva: maestra > copia dentro de un álbum > nombre  ║
║       formateado > ruta más profunda (contenido idéntico)    ║
║                                                              ║
║  2. Duplicados VISUALES (misma foto recomprimida/reescalada) ║
║     · pHash + dHash con tolerancia, confirmado con huellas   ║
║       finas de 256 bits (no confunde tomas seguidas)         ║
║     · Salvaguardas para no confundir fotos de ráfaga:        ║
║       misma proporción y fechas EXIF compatibles             ║
║     · Conserva la de MAYOR resolución/tamaño (nunca gana la  ║
║       copia de WhatsApp por tener fecha más antigua)         ║
║     · Genera un HTML con miniaturas para revisar ANTES y     ║
║       nunca se confirma automáticamente                      ║
║     · NO detecta recortes (es una limitación del método)     ║
║                                                              ║
║  Los duplicados se MUEVEN a _duplicados/ junto al script,    ║
║  conservando su ruta original. Su JSON y su vídeo Live Photo ║
║  se mueven con ellos. Se puede deshacer con 8_deshacer.py.   ║
║                                                              ║
║  Modo 2 requiere: pip install pillow imagehash pillow-heif   ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import io
import math
import sys
import base64
import hashlib
import argparse
import html as htmlmod
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun as C

DUP_DIR = C.SCRIPTS_DIR / '_duplicados'
LOG_EXACTOS = C.ruta_log('duplicados_exactos.txt')
LOG_VISUALES = C.ruta_log('duplicados_visuales.txt')
HTML_PATH = C.ruta_log('duplicados_visuales.html')

UMBRAL_PHASH = 6      # bits distintos (de 64) para considerar "misma imagen"
UMBRAL_DHASH = 10
# Confirmación con huellas finas (16×16 = 256 bits). Una copia recomprimida o
# reescalada da prácticamente 0; dos tomas distintas de la misma escena (una
# serie de fotos posando, una ráfaga) dan 30 o más aunque la huella de 64 bits
# coincida casi del todo.
UMBRAL_PHASH_FINO = 16
UMBRAL_DHASH_FINO = 20
TOL_PROPORCION = 0.02


# ══════════════════════════════════════════════════════════════
#  COMÚN A LOS DOS MODOS
# ══════════════════════════════════════════════════════════════

def pedir_carpetas_modo():
    maestra = C.pedir_carpeta('  Carpeta MAESTRA: ')
    pend = C.pedir_carpeta('  Carpeta PENDIENTES (Enter para omitir): ', opcional=True)
    return maestra, pend


def candidatos_live(medias):
    """Fotos y vídeos que comparten nombre en la misma carpeta (posibles Live Photos)."""
    grupos = defaultdict(list)
    for f in medias:
        e = C.ext_de(f.name)
        if e in C.EXT_LIVE_FOTO or e in C.EXT_LIVE_VIDEO:
            grupos[(C.clave(f.parent), C.separar_nombre(f.name)[0].lower())].append(f)
    res = []
    for g in grupos.values():
        exts = {C.ext_de(f.name) for f in g}
        if exts & C.EXT_LIVE_FOTO and exts & C.EXT_LIVE_VIDEO:
            res += g
    return res


_VISTAS = None


def purgar_cache():
    """Borra de la caché los archivos que ya no existen (al final, para que antes
    se puedan reconocer las fotos movidas de carpeta)."""
    if _VISTAS:
        C.cache().purgar(*_VISTAS)


def recopilar(carpetas, con_fechas_imagen=False):
    """
    Archivos a comparar + mapa de acompañantes (JSON propio, vídeo Live).
    Solo se leen metadatos de lo necesario: posibles Live Photos y, en el modo
    visual, las imágenes (para las salvaguardas por fecha). Todo pasa por la caché.
    """
    todos = []
    for c in carpetas:
        todos += list(C.recorrer(c))
    global _VISTAS
    _VISTAS = (carpetas, {C.clave(f) for f in todos})   # se purga la caché al terminar
    medias = [f for f in todos if C.es_media(f.name)]
    C.aviso_sin_exiftool('los Live Photos solo se detectan por nombre')
    leer = candidatos_live(medias)
    if con_fechas_imagen:
        ya = {C.clave(f) for f in leer}
        leer += [f for f in medias if C.ext_de(f.name) in C.EXT_PIXELES and C.clave(f) not in ya]
    metas = C.leer_metadatos(leer, 'Leyendo metadatos') if C.exiftool_disponible() else {}
    foto_video, video_foto = C.detectar_live_photos(medias, metas)
    indice = C.IndiceJSON()
    indice.cargar(carpetas)

    unidades = []
    for f in todos:
        if f.name.lower().endswith('.json') or C.clave(f) in video_foto:
            continue          # se mueven con su foto, no se comparan sueltos
        unidades.append(f)

    def acompanantes(f):
        extra = []
        v = foto_video.get(C.clave(f))
        if v is not None:
            extra.append(v)
            _, jv, prop = indice.buscar(v)
            if prop and jv:
                extra.append(jv)
        _, jp, prop = indice.buscar(f)
        if prop and jp:
            extra.append(jp)
        return [x for x in extra if x.exists()]

    return unidades, metas, acompanantes


def zona_de(f, maestra):
    return 'maestra' if C.en_carpeta(f, maestra) else 'pendientes'


def destino_dup(f, maestra, pendientes, sello):
    zona = zona_de(f, maestra)
    raiz = maestra if zona == 'maestra' else (pendientes or Path(f).parent)
    return DUP_DIR / sello / zona / C.ruta_relativa_segura(f, raiz)


def mover_duplicados(duplicados, maestra, pendientes, acompanantes, accion):
    """duplicados: lista de (dup, conservar, tamaño). Mueve dup + acompañantes."""
    sello = datetime.now().strftime('%Y%m%d_%H%M%S')
    reg = C.Registro(accion)
    movidos = errores = 0
    try:
        for dup, _, _ in duplicados:
            for f in [dup] + acompanantes(dup):
                try:
                    C.mover(f, destino_dup(f, maestra, pendientes, sello), reg)
                    movidos += 1
                except Exception as e:
                    print(f'\n  Error moviendo {f}: {e}')
                    errores += 1
            if movidos % 100 == 0:
                print(f'  Movidos: {movidos:,}...', end='\r')
    except KeyboardInterrupt:
        print(f'\n\n  Interrumpido. Movidos hasta ahora: {movidos:,}')
    finally:
        reg.cerrar()
    print(f'\n  Movidos: {movidos:,} archivos (incluye JSON y vídeos Live)  |  Errores: {errores}')
    print(f'  Carpeta: {DUP_DIR / sello}')
    if reg.ruta:
        print(f'  Para deshacer: 8_deshacer.py ({reg.ruta.name})')
    return C.ERROR if errores else C.OK


def escribir_log(ruta, duplicados, total_size, maestra, titulo, niveles=None):
    niveles = niveles or {}
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(f'{titulo}\nDuplicados: {len(duplicados):,}\n')
        f.write(f'Espacio a liberar: {C.gb(total_size)}\nDestino: {DUP_DIR}\n' + '=' * 60 + '\n\n')
        for dup, orig, size in duplicados:
            f.write(f'MOVER:     {dup}  [{zona_de(dup, maestra)}]\n')
            f.write(f'CONSERVAR: {orig}  [{zona_de(orig, maestra)}]\n')
            if C.clave(dup) in niveles:
                f.write(f'Nivel:     {niveles[C.clave(dup)]}\n')
            f.write(f'Tamaño:    {C.mb(size)}\n\n')


# ══════════════════════════════════════════════════════════════
#  MODO 1 — EXACTOS
# ══════════════════════════════════════════════════════════════

def md5(path, limite=None, chunk=1024 * 1024):
    h = hashlib.md5()
    leido = 0
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk if limite is None else min(chunk, limite - leido))
            if not b:
                break
            h.update(b)
            leido += len(b)
            if limite is not None and leido >= limite:
                break
    return h.hexdigest()


def grupos_exactos(unidades):
    por_tam = defaultdict(list)
    for f in unidades:
        try:
            s = f.stat().st_size
            if s > 0:
                por_tam[s].append(f)
        except OSError:
            pass
    candidatos = [g for g in por_tam.values() if len(g) > 1]
    n_cand = sum(len(g) for g in candidatos)
    print(f'  {len(unidades):,} archivos · {n_cand:,} comparten tamaño con otro (solo esos se leen)')

    todos_cand = [f for g in candidatos for f in g]
    cache = C.cache()
    ini_cache = cache.obtener(todos_cand, 'md5ini')
    md5_cache = cache.obtener(todos_cand, 'md5')
    nuevos_ini, nuevos_md5 = [], []

    def md5_de(f, inicial):
        k = C.clave(f)
        dic, nuevos = (ini_cache, nuevos_ini) if inicial else (md5_cache, nuevos_md5)
        if k not in dic:
            dic[k] = md5(f, 65536) if inicial else md5(f)
            nuevos.append((f, dic[k]))
        return dic[k]

    grupos = []
    hechos = 0
    for g in candidatos:
        # Hash rápido de los primeros 64 KB y luego hash completo
        por_ini = defaultdict(list)
        for f in g:
            try:
                por_ini[md5_de(f, True)].append(f)
            except OSError as e:
                print(f'\n  Error leyendo {f}: {e}')
        for sub in por_ini.values():
            if len(sub) < 2:
                continue
            por_md5 = defaultdict(list)
            for f in sub:
                try:
                    por_md5[md5_de(f, False)].append(f)
                except OSError as e:
                    print(f'\n  Error leyendo {f}: {e}')
            grupos += [x for x in por_md5.values() if len(x) > 1]
        hechos += len(g)
        if hechos % 200 < len(g):
            print(f'  {hechos:,} / {n_cand:,} comparados...', end='\r')
    cache.guardar(nuevos_ini, 'md5ini')
    cache.guardar(nuevos_md5, 'md5')
    print(f'  {n_cand:,} / {n_cand:,} comparados.        ')
    return grupos


def elegir_exacto(grupo, maestra, pendientes=None):
    """
    Copias idénticas: solo importa dónde están y cómo se llaman.
    maestra > dentro de un álbum (p.ej. "Roma 2015" antes que "Photos from
    2015", para no perder el álbum) > nombre ya formateado > ruta más profunda.
    """
    def score(f):
        raiz = maestra if C.en_carpeta(f, maestra) else (pendientes or f.parent)
        return (0 if C.en_carpeta(f, maestra) else 1,
                0 if C.albumes_de(f, raiz) else 1,
                0 if C.FORMATO_NOMBRE.match(f.name) else 1,
                -len(f.parts),
                len(f.name),
                str(f))
    conservar = min(grupo, key=score)
    return conservar, [f for f in grupo if f != conservar]


def modo_1(maestra, pendientes=None, auto=False):
    print('\n── MODO 1: Duplicados exactos ──────────────────────────')
    C.iniciar_logs(LOG_EXACTOS.name)
    carpetas = [maestra] + ([pendientes] if pendientes else [])
    unidades, _, acompanantes = recopilar(carpetas)
    grupos = grupos_exactos(unidades)
    purgar_cache()

    duplicados, total_size = [], 0
    for g in grupos:
        conservar, mover_ = elegir_exacto(g, maestra, pendientes)
        for f in mover_:
            s = f.stat().st_size
            duplicados.append((f, conservar, s))
            total_size += s

    internos = sum(1 for d, o, _ in duplicados
                   if zona_de(d, maestra) == 'maestra' and zona_de(o, maestra) == 'maestra')
    print(f'\n  Grupos de duplicados : {len(grupos):>10,}')
    print(f'  Archivos a mover     : {len(duplicados):>10,}')
    print(f'  Espacio a liberar    : {C.gb(total_size):>13}')
    if not duplicados:
        print('\n  No se encontraron duplicados.')
        return C.OK
    if internos:
        print(f'\n  ℹ  {internos:,} duplicado(s) están DENTRO de la propia maestra (las dos')
        print('     copias en maestra). Se conserva la de nombre formateado / ruta más profunda.')

    escribir_log(LOG_EXACTOS, duplicados, total_size, maestra, 'MODO 1 — duplicados exactos')
    print(f'\n  Detalle en: {LOG_EXACTOS}')
    if not C.confirmar(f'¿Mover {len(duplicados):,} duplicados a _duplicados/?', auto):
        print('  Cancelado. No se ha movido nada.')
        return C.CANCELADO
    return mover_duplicados(duplicados, maestra, pendientes, acompanantes, 'duplicados_exactos')


# ══════════════════════════════════════════════════════════════
#  MODO 2 — VISUALES
# ══════════════════════════════════════════════════════════════

class Img:
    __slots__ = ('f', 'ph', 'dh', 'ph16', 'dh16', 'w', 'h', 'size', 'fecha', 'cid', 'thumb')


def calcular_huellas(candidatas):
    """
    Huellas visuales de todas las imágenes: las que no han cambiado salen de la
    caché; el resto se calcula repartido entre los núcleos del ordenador.
    Devuelve {clave(ruta): [ph, dh, ph16, dh16, ancho, alto]} (None si no se pudo).
    """
    from concurrent.futures import ProcessPoolExecutor
    cache = C.cache()
    res = cache.obtener(candidatas, 'huellas')
    faltan = [str(f) for f in candidatas if C.clave(f) not in res]
    if res:
        print(f'  {len(res):,} de {len(candidatas):,} imágenes ya estaban en la caché.')
    if not faltan:
        return res
    cpu = os.cpu_count() or 1
    nucleos = cpu if cpu <= 2 else cpu - 1      # deja un núcleo libre para el sistema
    print(f'  Analizando {len(faltan):,} imágenes con {nucleos} núcleo(s)...')
    nuevos, hechos = [], 0
    try:
        with ProcessPoolExecutor(max_workers=nucleos) as ex:
            for ruta, val in ex.map(C.huellas_imagen, faltan, chunksize=32):
                res[C.clave(ruta)] = val
                if val is not None:
                    nuevos.append((ruta, val))
                hechos += 1
                if hechos % 200 == 0:
                    print(f'  {hechos:,} / {len(faltan):,} imágenes analizadas...', end='\r')
                    if len(nuevos) >= 2000:            # guardar por tandas: si se corta, no se pierde
                        cache.guardar(nuevos, 'huellas')
                        nuevos = []
    except Exception as e:
        print(f'\n  ℹ  Sin procesamiento en paralelo ({e}); se sigue con un solo núcleo.')
        for ruta in faltan:
            if C.clave(ruta) in res:
                continue
            ruta, val = C.huellas_imagen(ruta)
            res[C.clave(ruta)] = val
            if val is not None:
                nuevos.append((ruta, val))
    cache.guardar(nuevos, 'huellas')
    print(f'  {len(faltan):,} imágenes analizadas.{" " * 20}')
    return res


def construir_img(f, val, metas):
    o = Img()
    o.f = f
    o.ph, o.dh, o.ph16, o.dh16 = (int(x, 16) for x in val[:4])
    o.w, o.h = val[4], val[5]
    try:
        o.size = f.stat().st_size
    except OSError:
        return None
    m = metas.get(C.clave(f))
    fe = m.fecha()[0] if m else C.leer_fecha_piexif(f)
    o.fecha = fe.dt if fe and C.fecha_valida(fe.dt) and fe.precision == 'segundo' else None
    if o.fecha and m and m.dto_preciso and m.dto and m.dto_preciso.replace(microsecond=0) == o.fecha:
        o.fecha = m.dto_preciso          # con milésimas
    o.cid = m.content_id if m else None
    o.thumb = None
    return o


class BKTree:
    """Árbol para buscar hashes a distancia de Hamming ≤ r sin comparar todos con todos."""

    def __init__(self):
        self.raiz = None

    def add(self, h, i):
        if self.raiz is None:
            self.raiz = [h, i, {}]
            return
        n = self.raiz
        while True:
            d = (h ^ n[0]).bit_count()
            if d in n[2]:
                n = n[2][d]
            else:
                n[2][d] = [h, i, {}]
                return

    def buscar(self, h, r):
        res, pila = [], [self.raiz] if self.raiz else []
        while pila:
            n = pila.pop()
            d = (h ^ n[0]).bit_count()
            if d <= r:
                res.append(n[1])
            for k, hijo in n[2].items():
                if d - r <= k <= d + r:
                    pila.append(hijo)
        return res


def son_duplicados(a, b):
    if (a.dh ^ b.dh).bit_count() > UMBRAL_DHASH:
        return False
    # Huellas finas: separan una copia de verdad de otra toma casi idéntica
    if (a.ph16 ^ b.ph16).bit_count() > UMBRAL_PHASH_FINO:
        return False
    if (a.dh16 ^ b.dh16).bit_count() > UMBRAL_DHASH_FINO:
        return False
    if abs(a.w / a.h - b.w / b.h) > TOL_PROPORCION * max(a.w / a.h, b.w / b.h):
        return False
    # El iPhone da a cada disparo un identificador propio: si los dos lo tienen
    # y es distinto, son fotos diferentes (p. ej. dos fotos de una ráfaga)
    if a.cid and b.cid and a.cid != b.cid:
        return False
    if a.fecha and b.fecha:
        fa, fb = a.fecha, b.fecha
        # Si solo una de las dos guarda milésimas (p. ej. una copia a la que se le
        # reescribió la fecha), se comparan al segundo: si no, la misma foto
        # parecería una ráfaga (12:20:35.030 frente a 12:20:35.000)
        if not fa.microsecond or not fb.microsecond:
            fa, fb = fa.replace(microsecond=0), fb.replace(microsecond=0)
        diff = abs(fa - fb)
        if diff > timedelta(seconds=60):
            return False          # misma escena, momento distinto
        if (a.w, a.h) == (b.w, b.h) and diff > timedelta(0):
            return False          # dos originales casi iguales: probable ráfaga
                                  # (la fecha lleva milésimas si la cámara las guarda)
    return True


# Niveles de confianza de un grupo de duplicados visuales
CASI_PH16 = 4      # bits distintos (de 256) para considerar dos fotos "prácticamente idénticas"
CASI_DH16 = 6
NIVELES = ('seguro', 'probable', 'dudoso')


def nivel_grupo(cons, mov):
    """
    seguro   : todas las copias son prácticamente idénticas y de MENOR resolución
               que la que se conserva (copia de WhatsApp, "ahorro de espacio"…)
    probable : prácticamente idénticas, pero con la misma resolución
               (reexportaciones, conversiones de HEIC a JPG…)
    dudoso   : parecidas pero no idénticas (recortes, ediciones, tomas casi iguales)
    """
    nivel = 'seguro'
    for o in mov:
        if (cons.ph16 ^ o.ph16).bit_count() > CASI_PH16 or (cons.dh16 ^ o.dh16).bit_count() > CASI_DH16:
            return 'dudoso'
        if o.w * o.h >= cons.w * cons.h:
            nivel = 'probable'
    return nivel


def _tramo_tamano(size):
    """
    Tamaño agrupado en tramos de ~2 %: unos KB de diferencia por metadatos
    (etiqueta de álbum, fechas reescritas) no deciden qué copia se conserva.
    """
    return round(math.log(max(size, 1)) * 50)


def puntuacion_visual(o, maestra):
    """
    Menor = mejor candidata a conservar: más resolución, más tamaño (en tramos),
    con fecha, en maestra, en una subcarpeta (ya organizada) antes que en la raíz.
    """
    en_maestra = C.en_carpeta(o.f, maestra)
    profundidad = len(C.ruta_relativa_segura(o.f, maestra).parts) if en_maestra else 0
    return (-(o.w * o.h), -_tramo_tamano(o.size), 0 if o.fecha else 1,
            0 if en_maestra else 1, -profundidad, -o.size, o.fecha or datetime.max, str(o.f))


def agrupar_visuales(imgs, maestra):
    """
    Forma los grupos alrededor de las mejores fotos. Se recorren de mejor a peor:
    cada foto se une a la foto ya elegida "a conservar" más parecida con la que
    sea duplicado; si no hay ninguna, pasa a ser una foto a conservar.
    Así una copia de WhatsApp parecida a varias fotos de una ráfaga se asigna a
    una sola, en vez de encadenarlas todas en el mismo grupo.
    """
    orden = sorted(range(len(imgs)), key=lambda i: puntuacion_visual(imgs[i], maestra))
    arbol = BKTree()
    grupos = {}            # índice de la foto a conservar → [copias]
    for i in orden:
        o = imgs[i]
        mejor, dist = None, None
        for j in arbol.buscar(o.ph, UMBRAL_PHASH):
            if son_duplicados(imgs[j], o):
                d = (imgs[j].ph16 ^ o.ph16).bit_count()
                if dist is None or d < dist:
                    mejor, dist = j, d
        if mejor is None:
            grupos[i] = []
            arbol.add(o.ph, i)          # solo las fotos a conservar entran en el árbol
        else:
            grupos[mejor].append(o)
    return [(imgs[k], copias) for k, copias in grupos.items() if copias]


def elegir_visual(grupo, maestra):
    conservar = min(grupo, key=lambda o: puntuacion_visual(o, maestra))
    return conservar, [o for o in grupo if o is not conservar]


def motivo_conservar(cons, otra):
    """Explicación breve de por qué se conserva 'cons' frente a 'otra'."""
    mp_c, mp_o = cons.w * cons.h / 1e6, otra.w * otra.h / 1e6
    if (cons.w, cons.h) != (otra.w, otra.h):
        veces = mp_c / mp_o if mp_o else 0
        return f'más resolución: {mp_c:.1f} MP frente a {mp_o:.1f} MP ({veces:.1f} veces más)', False
    if cons.size != otra.size:
        return (f'misma resolución ({cons.w}×{cons.h}); pesa más: {C.mb(cons.size)} frente a '
                f'{C.mb(otra.size)}'), True
    return f'misma resolución y tamaño ({cons.w}×{cons.h}); se prefiere la que tiene fecha o está en la maestra', True


GRUPOS_POR_PAGINA = 200


def generar_miniaturas(grupos_decididos):
    from concurrent.futures import ProcessPoolExecutor
    pendientes = [o for cons, mov in grupos_decididos for o in [cons] + mov if o.thumb is None]
    if not pendientes:
        return
    por_ruta = {str(o.f): o for o in pendientes}
    cpu = os.cpu_count() or 1
    nucleos = cpu if cpu <= 2 else cpu - 1
    print(f'  Preparando miniaturas del informe: {len(pendientes):,} imágenes con {nucleos} núcleo(s)...')
    hechas = 0
    try:
        with ProcessPoolExecutor(max_workers=nucleos) as ex:
            for ruta, b64 in ex.map(C.miniatura_imagen, list(por_ruta), chunksize=16):
                por_ruta[ruta].thumb = b64
                hechas += 1
                if hechas % 200 == 0:
                    print(f'  {hechas:,} / {len(pendientes):,} miniaturas...', end='\r')
    except Exception:
        for ruta, o in por_ruta.items():
            if o.thumb is None:
                o.thumb = C.miniatura_imagen(ruta)[1]
    print(f'  {len(pendientes):,} miniaturas preparadas.{" " * 20}')


def generar_html(grupos_decididos, maestra, ruta=None, titulo='Duplicados visuales', muestra=False):
    generar_miniaturas(grupos_decididos)
    ruta = ruta or HTML_PATH

    def tarjeta(o, num, conservar):
        color = '#2e7d32' if conservar else '#c62828'
        etiqueta = 'SE CONSERVA' if conservar else 'SE APARTA'
        fecha = o.fecha.strftime('%Y-%m-%d %H:%M:%S') if o.fecha else 'sin fecha EXIF'
        try:
            enlace = Path(o.f).resolve().as_uri()
        except ValueError:
            enlace = ''
        mp = o.w * o.h / 1e6
        return (f'<div class="c" style="border-color:{color}">'
                f'<a href="{htmlmod.escape(enlace)}" target="_blank" title="Abrir la foto original">'
                f'<img src="data:image/jpeg;base64,{o.thumb}"></a>'
                f'<span class="n">Foto {num}</span> <b style="color:{color}">{etiqueta}</b><br>'
                f'<b>{o.w}×{o.h}</b> ({mp:.1f} MP) · {C.mb(o.size)}<br>{fecha}<br>'
                f'<small>[{zona_de(o.f, maestra)}] {htmlmod.escape(str(o.f))}</small></div>')

    partes = ['<!doctype html><meta charset="utf-8"><title>Duplicados visuales</title><style>'
              'body{font-family:sans-serif;margin:16px;background:#f5f5f5;max-width:1200px}'
              '.g{background:#fff;margin:12px 0;padding:10px;border-radius:8px}'
              '.g.dudoso{border-left:6px solid #ef6c00}'
              '.m{margin:4px 0 8px;font-size:13px}.aviso{color:#ef6c00;font-weight:bold}'
              '.c{display:inline-block;vertical-align:top;width:200px;margin:4px;padding:6px;'
              'border:3px solid;border-radius:6px;font-size:12px;word-break:break-all}'
              '.c img{max-width:180px;max-height:180px;display:block;margin-bottom:4px;cursor:zoom-in}'
              '.n{background:#333;color:#fff;border-radius:4px;padding:1px 6px}'
              '.ayuda{background:#fff;padding:10px 14px;border-radius:8px}</style>',
              f'<h1>{htmlmod.escape(titulo)} — {len(grupos_decididos)} grupos</h1>'
              + ('<p class="ayuda"><b>Muestra al azar de los grupos que se apartarán SIN revisar.</b> Sirve para '
                 'comprobar que el criterio es fiable. Si alguno no es un duplicado, cancela y elige revisarlos.</p>'
                 if muestra else '') +
              '<div class="ayuda"><p>En cada grupo, la foto con borde <b style="color:#2e7d32">verde</b> se '
              'conserva y las de borde <b style="color:#c62828">rojo</b> se apartan a _duplicados. Por defecto '
              'se conserva la de <b>mayor resolución</b>. Haz clic en una miniatura para abrir la foto original '
              'y compararlas a tamaño completo.</p>'
              '<p>Después, en la ventana del script escribes <b>solo lo que quieras cambiar</b>, en una '
              'línea y separado por comas. Si todo está bien, pulsa Enter.</p><ul>'
              '<li><b>Número</b>: no tocar ese grupo, no son duplicados. Ej.: <code>3</code></li>'
              '<li><b>grupo:fotos</b>: en ese grupo, conservar esas fotos y apartar las demás (incluida la '
              'verde si no la pones). Varias fotos se unen con <b>+</b>. Ej.: <code>4:2</code> conserva solo la 2; '
              '<code>6:1+3</code> conserva la 1 y la 3</li></ul>'
              '<p>Ejemplo completo: <code>3, 5, 4:2, 6:1+3</code></p>'
              '<p>Los grupos marcados en <span class="aviso">naranja</span> son parecidos pero <b>no idénticos</b> '
              '(recorte, edición, otra toma casi igual): van primero, revísalos con calma.</p></div>']
    cabecera = ''.join(partes)
    n_dudosos = sum(1 for cons, mov in grupos_decididos if nivel_grupo(cons, mov) == 'dudoso')
    paginas = max(1, -(-len(grupos_decididos) // GRUPOS_POR_PAGINA))

    def nombre_pagina(n):
        return ruta.name if n == 1 else f'{ruta.stem}_{n:03d}.html'

    def navegacion(n):
        if paginas == 1:
            return ''
        enlaces = ' '.join(
            f'<b>[{k}]</b>' if k == n else
            f'<a href="{nombre_pagina(k)}">{(k - 1) * GRUPOS_POR_PAGINA + 1}–'
            f'{min(k * GRUPOS_POR_PAGINA, len(grupos_decididos))}</a>'
            for k in range(1, paginas + 1))
        return f'<p class="ayuda">Páginas (grupos): {enlaces}</p>'

    for viejo in ruta.parent.glob(f'{ruta.stem}_[0-9][0-9][0-9].html'):
        try:
            viejo.unlink()
        except OSError:
            pass
    for n in range(1, paginas + 1):
        trozo = []
        for i in range((n - 1) * GRUPOS_POR_PAGINA + 1, min(n * GRUPOS_POR_PAGINA, len(grupos_decididos)) + 1):
            cons, mov = grupos_decididos[i - 1]
            motivo, _ = motivo_conservar(cons, mov[0])
            dudoso = nivel_grupo(cons, mov) == 'dudoso'
            aviso = '<span class="aviso">⚠ Parecidas, no idénticas: </span>' if dudoso else ''
            trozo.append(f'<div class="g{" dudoso" if dudoso else ""}"><h3>Grupo {i}</h3>'
                         f'<div class="m">{aviso}Se conserva la foto 1 por {htmlmod.escape(motivo)}.</div>'
                         + tarjeta(cons, 1, True)
                         + ''.join(tarjeta(o, k, False) for k, o in enumerate(mov, 2)) + '</div>')
        resumen = ''
        (ruta.parent / nombre_pagina(n)).write_text(
            cabecera + resumen + navegacion(n) + ''.join(trozo) + navegacion(n), encoding='utf-8')
    return n_dudosos


def leer_elecciones(n_grupos, tamanos):
    """
    Una sola pregunta, solo con lo que se quiere cambiar:
      · número (3)                  → no tocar ese grupo
      · grupo:fotos (4:2, 6:1+3)    → en ese grupo, conservar esas fotos
    Devuelve (excluir:set, conservar:dict grupo→set de nº de foto).
    """
    print('\n  Escribe solo lo que quieras cambiar, en una línea y separado por comas:')
    print('     3              → no tocar ese grupo (no son duplicados)')
    print('     4:2            → en el grupo 4, conservar solo la foto 2 (se aparta la verde)')
    print('     6:1+3          → en el grupo 6, conservar las fotos 1 y 3')
    while True:
        try:
            resp = input('  Cambios, o Enter si todo está bien: ').strip()
        except EOFError:
            resp = ''
        excluir, conservar, malos = set(), {}, []
        for x in [x for x in resp.replace(' ', '').split(',') if x]:
            if ':' in x:                               # grupo:fotos
                g, _, fs = x.partition(':')
                nums = fs.split('+')
                if g.isdigit() and 1 <= int(g) <= n_grupos and nums and all(
                        n.isdigit() and 1 <= int(n) <= tamanos[int(g) - 1] for n in nums):
                    conservar.setdefault(int(g), set()).update(int(n) for n in nums)
                    continue
                malos.append(x)
                continue
            a, _, b = x.partition('-')                 # número o rango: no tocar
            if a.isdigit() and (b.isdigit() if b else True):
                lo, hi = int(a), int(b or a)
                if 1 <= lo <= hi <= n_grupos:
                    excluir |= set(range(lo, hi + 1))
                    continue
            malos.append(x)
        if malos:
            print(f'  ⚠  No entiendo: {", ".join(malos)}. Los grupos van del 1 al {n_grupos}; '
                  'las fotos, con grupo:foto (4:2) o grupo:foto+foto (6:1+3), deben existir en ese grupo.')
            continue
        choques = sorted(excluir & set(conservar))
        if choques:
            print(f'  ⚠  {"El grupo" if len(choques) == 1 else "Los grupos"} {", ".join(map(str, choques))} '
                  f'{"aparece" if len(choques) == 1 else "aparecen"} como "no tocar" y con fotos elegidas. '
                  'Pon solo el número o solo grupo:fotos.')
            continue
        return excluir, conservar


def preparar_cache(maestra):
    """
    Rellena la caché con los datos de la maestra (metadatos y huellas visuales)
    sin buscar ni mover nada. Así la siguiente búsqueda de duplicados solo tiene
    que analizar las fotos nuevas.
    """
    print('\n── Preparar la caché de la maestra ─────────────────────')
    unidades, _, _ = recopilar([maestra], con_fechas_imagen=True)
    faltan = librerias_visuales_faltan()
    if faltan:
        print(f'  ⚠  Faltan librerías ({", ".join(faltan)}): solo se guardan los metadatos, no las huellas visuales.')
        purgar_cache()
        return C.OK
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        exts = C.EXT_PIXELES
    except ImportError:
        exts = C.EXT_PIXELES - {'.heic', '.heif'}
    calcular_huellas([f for f in unidades if C.ext_de(f.name) in exts])
    purgar_cache()
    print(f'  Caché lista: {C.CACHE_PATH}')
    return C.OK


def librerias_visuales_faltan():
    """Nombres pip de las librerías obligatorias del modo 2 que no están instaladas."""
    faltan = []
    for modulo, pip in (('PIL', 'pillow'), ('imagehash', 'imagehash')):
        try:
            __import__(modulo)
        except ImportError:
            faltan.append(pip)
    return faltan


def modo_2(maestra, pendientes=None):
    print('\n── MODO 2: Duplicados visuales ─────────────────────────')
    C.iniciar_logs(LOG_VISUALES.name, HTML_PATH.name)
    faltan = librerias_visuales_faltan()
    if faltan:
        print(f'\n  ⚠  Faltan librerías: {", ".join(faltan)}. Instálalas con (CMD como administrador):')
        print(f'     python -m pip install {" ".join(faltan)} pillow-heif')
        print('     (si da error de permisos, añade --user; más ayuda en la guía, sección 1)')
        return C.ERROR
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        heic = True
    except ImportError:
        heic = False
        print('  ⚠  Falta pillow-heif: las fotos HEIC no se compararán. Instálalo con:')
        print('     python -m pip install pillow-heif')

    carpetas = [maestra] + ([pendientes] if pendientes else [])
    unidades, metas, acompanantes = recopilar(carpetas, con_fechas_imagen=True)
    exts = C.EXT_PIXELES - (set() if heic else {'.heic', '.heif'})
    candidatas = [f for f in unidades if C.ext_de(f.name) in exts]

    huellas = calcular_huellas(candidatas)
    purgar_cache()
    imgs, sin_soporte = [], 0
    for f in candidatas:
        val = huellas.get(C.clave(f))
        o = construir_img(f, val, metas) if val else None
        if o is None:
            sin_soporte += 1
        else:
            imgs.append(o)

    decididos = agrupar_visuales(imgs, maestra)
    por_nivel = {n: [] for n in NIVELES}
    for g in decididos:
        por_nivel[nivel_grupo(*g)].append(g)

    n_mov = sum(len(m) for _, m in decididos)
    print(f'\n  Imágenes analizadas : {len(imgs):>8,}')
    print(f'  Sin soporte / error : {sin_soporte:>8,}')
    print(f'  Grupos              : {len(decididos):>8,}   ({n_mov:,} imágenes a apartar)')
    if not decididos:
        print('\n  No se encontraron duplicados visuales.')
        return C.OK
    descr = {'seguro': 'copia casi idéntica y de menor resolución (WhatsApp, ahorro de espacio…)',
             'probable': 'casi idéntica y con la misma resolución (reexportación, HEIC→JPG…)',
             'dudoso': 'parecida pero no idéntica (recorte, edición, toma casi igual)'}
    for n in NIVELES:
        print(f'    · {n.capitalize():9}: {len(por_nivel[n]):>7,} grupos — {descr[n]}')

    print('\n  ¿Qué revisas a mano? (lo que se aparta va a _duplicados y se puede deshacer)')
    print('    1. Los dudosos; apartar SIN revisar los seguros y probables  (recomendado)')
    print('    2. Los dudosos y probables; apartar SIN revisar solo los seguros')
    print('    3. Todos los grupos')
    print('    4. Ninguno: apartar TODOS los grupos SIN revisar')
    print('    5. No apartar nada y salir')
    while True:
        try:
            op = input('  Elige [1-5] (Enter = 1): ').strip() or '1'
        except EOFError:
            op = '1'
        if op in ('1', '2', '3', '4', '5'):
            break
        print('  ⚠  Escribe un número del 1 al 5.')
    if op == '5':
        print('  No se mueve nada.')
        return C.OK
    a_revisar = {'1': ('dudoso',), '2': ('dudoso', 'probable'), '3': NIVELES, '4': ()}[op]
    # En el informe, primero los parecidos-no-idénticos, luego probables y seguros
    revisar = [g for n in ('dudoso', 'probable', 'seguro') if n in a_revisar
               for g in sorted(por_nivel[n], key=lambda g: str(g[0].f))]
    automaticos = [g for n in NIVELES if n not in a_revisar for g in por_nivel[n]]
    if automaticos:
        print(f'\n  {len(automaticos):,} grupos ({", ".join(n + "s" for n in NIVELES if n not in a_revisar)}) '
              f'se apartarán SIN revisar.')

    excluir, conservar = set(), {}
    if revisar:
        n_dudosos = generar_html(revisar, maestra)
        print(f'\n  👉 REVISA el informe con miniaturas ({len(revisar):,} grupos):\n     {HTML_PATH}')
        if len(revisar) > GRUPOS_POR_PAGINA:
            print(f'     (está dividido en páginas de {GRUPOS_POR_PAGINA} grupos, enlazadas entre sí)')
        print('     En cada grupo se conserva la foto 1 (verde) y se apartan las demás (rojas).')
        if n_dudosos and n_dudosos < len(revisar):
            print('     El primero (en naranja) es parecido pero no idéntico: revísalo con calma.' if n_dudosos == 1 else
                  f'     Los {n_dudosos:,} primeros (en naranja) son parecidos pero no idénticos: revísalos con calma.')
        excluir, conservar = leer_elecciones(len(revisar), [1 + len(m) for _, m in revisar])

    duplicados, total, niveles = [], 0, {}
    for cons, mov in automaticos:
        for o in mov:
            duplicados.append((o.f, cons.f, o.size))
            niveles[C.clave(o.f)] = nivel_grupo(cons, mov) + ' (sin revisar)'
            total += o.size
    n_auto = len(duplicados)
    def fotos_txt(nums):
        return ('la foto ' if len(nums) == 1 else 'las fotos ') + '+'.join(map(str, nums))

    for i, (cons, mov) in enumerate(revisar, 1):
        if i in excluir:
            print(f'  Grupo {i}: sin tocar.')
            continue
        nivel = nivel_grupo(cons, mov)
        fotos = [cons] + mov
        if i in conservar:
            quedan = [fotos[n - 1] for n in sorted(conservar[i])]
            if len(quedan) == len(fotos):
                print(f'  Grupo {i}: sin tocar (se conservan todas).')
                continue
            cons = quedan[0]
            mov = [o for o in fotos if o not in quedan]
            si = sorted(conservar[i])
            no = [k for k in range(1, len(fotos) + 1) if k not in conservar[i]]
            print(f'  Grupo {i}: se conserva{"n" if len(si) > 1 else ""} {fotos_txt(si)}; '
                  f'se aparta{"n" if len(no) > 1 else ""} {fotos_txt(no)}.')
        for o in mov:
            duplicados.append((o.f, cons.f, o.size))
            niveles[C.clave(o.f)] = nivel + (' (revisado, fotos elegidas)' if i in conservar else ' (revisado)')
            total += o.size
    if not duplicados:
        print('  No queda nada que apartar. No se mueve nada.')
        return C.OK
    escribir_log(LOG_VISUALES, duplicados, total, maestra, 'MODO 2 — duplicados visuales', niveles)
    print(f'\n  Detalle en: {LOG_VISUALES}')
    # El modo visual NUNCA se confirma automáticamente
    if not C.confirmar(f'¿Mover {len(duplicados):,} imágenes ({C.gb(total)}) a _duplicados/? '
                       f'({n_auto:,} sin revisar + {len(duplicados) - n_auto:,} revisadas)', False):
        print('  Cancelado. No se ha movido nada.')
        return C.CANCELADO
    return mover_duplicados(duplicados, maestra, pendientes, acompanantes, 'duplicados_visuales')


# ══════════════════════════════════════════════════════════════
#  MENÚ
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description='Busca y aparta duplicados.')
    ap.add_argument('--modo', choices=['1', '2'])
    ap.add_argument('--maestra')
    ap.add_argument('--pendientes')
    ap.add_argument('--si', action='store_true', help='(solo modo 1) no pedir confirmación')
    ap.add_argument('--preparar-cache', action='store_true',
                    help='solo rellena la caché con la maestra (no busca ni mueve nada)')
    ap.add_argument('--vaciar-cache', action='store_true',
                    help='borra la caché (cache_fotos.db) antes de empezar; se reconstruye sola')
    a = ap.parse_args()
    if a.vaciar_cache:
        C.cache().vaciar()
        print('  Caché vaciada: esta ejecución volverá a analizar todas las fotos.')

    if a.preparar_cache:
        maestra = a.maestra or C.pedir_carpeta('  Carpeta MAESTRA: ')
        if not maestra or not Path(maestra).is_dir():
            print(f'  ERROR: no existe la carpeta: {maestra}')
            C.pausa_final()
            sys.exit(C.ERROR)
        codigo = preparar_cache(maestra)
        C.pausa_final()
        sys.exit(codigo)
    modo = a.modo
    if not modo:
        print(__doc__.split('╠')[0] + '╚' + '═' * 62 + '╝')
        print('  1. Duplicados exactos   2. Duplicados visuales (con revisión)')
        modo = input('  Elige un modo [1/2]: ').strip()
    if a.maestra:
        maestra, pend = a.maestra, a.pendientes
    else:
        maestra, pend = pedir_carpetas_modo()
    for c in [maestra] + ([pend] if pend else []):
        if not c or not Path(c).is_dir():
            print(f'  ERROR: no existe la carpeta: {c}')
            C.pausa_final()
            sys.exit(C.ERROR)
        msg = C.problema_carpeta_trabajo(c)
        if msg:
            print('  ERROR: ' + msg)
            C.pausa_final()
            sys.exit(C.ERROR)
    if modo == '1':
        codigo = modo_1(maestra, pend, a.si)
    elif modo == '2':
        codigo = modo_2(maestra, pend)
    else:
        print('  Opción no válida.')
        codigo = C.ERROR
    C.pausa_final()
    sys.exit(codigo)


if __name__ == '__main__':
    main()
