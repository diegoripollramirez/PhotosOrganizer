"""
╔══════════════════════════════════════════════════════════════╗
║  COMUN — utilidades compartidas por todos los scripts        ║
║                                                              ║
║  No se ejecuta directamente. Contiene en un solo sitio:      ║
║    · extensiones y rango de fechas válidas                   ║
║    · extracción de fecha del nombre (con PRECISIÓN)          ║
║    · regla para elegir la fecha buena entre varias fuentes   ║
║    · lectura/escritura de metadatos con ExifTool en lote     ║
║    · índice de JSON de Google Takeout (todas las variantes)  ║
║    · detección de parejas Live Photo (.MOV / .HEIC.qt / .MP4)║
║    · carpetas excluidas, movimientos con registro deshacer   ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import csv
import json
import shutil
import sqlite3
import fnmatch
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

if sys.version_info < (3, 10):
    print(f'\n  ⚠  Estos scripts necesitan Python 3.10 o superior (tienes {sys.version.split()[0]}).')
    print('     Descarga la última versión en https://www.python.org/downloads/')
    try:
        input('\nPulsa Enter para salir.')
    except EOFError:
        pass
    sys.exit(1)

# Evitar que la consola de Windows (cp1252) reviente con caracteres raros
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

SCRIPTS_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPTS_DIR / 'logs'
REGISTROS_DIR = LOGS_DIR / 'deshacer'

# Códigos de salida (el pipeline se detiene si no es 0)
OK, ERROR, CANCELADO, NADA = 0, 1, 2, 3   # NADA: no hay nada que procesar


# ══════════════════════════════════════════════════════════════
#  EXTENSIONES
# ══════════════════════════════════════════════════════════════

# Extensiones dobles que se tratan como una sola
EXT_DOBLES = ('.heic.qt',)

EXT_FOTO = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
            '.webp', '.heic', '.heif'}
EXT_RAW = {'.orf', '.cr2', '.cr3', '.nef', '.nrw', '.arw', '.srf', '.sr2',
           '.dng', '.rw2', '.raf', '.raw', '.rw1', '.pef', '.srw', '.rwl',
           '.mrw', '.x3f', '.3fr', '.fff', '.iiq', '.cap'}
EXT_VIDEO = {'.mp4', '.mov', '.avi', '.mkv', '.3gp', '.qt', '.m4v', '.wmv',
             '.mts', '.m2ts', '.mpg', '.mpeg', '.ts', '.vob', '.flv', '.f4v',
             '.heic.qt'}
EXT_MEDIA = EXT_FOTO | EXT_RAW | EXT_VIDEO

# Imágenes cuyos píxeles se pueden comparar con Pillow (HEIC si hay pillow-heif)
EXT_PIXELES = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
               '.webp', '.heic', '.heif'}

# Live Photos: foto + vídeo corto con el mismo nombre base
EXT_LIVE_FOTO = {'.heic', '.heif', '.jpg', '.jpeg'}
EXT_LIVE_VIDEO = {'.mov', '.mp4', '.heic.qt', '.qt'}
LIVE_DURACION_MAX = 4.5          # segundos
LIVE_TAMANO_MAX = 25 * 1024**2   # si no se conoce la duración

CARPETAS_EXCLUIR = {
    '_basura', '_duplicados', '_vacios', 'logs',
    '@eaDir', '@Recycle', '#recycle', '.Spotlight-V100', '.Trashes',
    '$RECYCLE.BIN', 'System Volume Information', '.thumbnails',
}


def separar_nombre(fname):
    """'IMG_1.HEIC.qt' → ('IMG_1', '.HEIC.qt'); 'a.jpg' → ('a', '.jpg')."""
    fname = Path(fname).name
    low = fname.lower()
    for d in EXT_DOBLES:
        if low.endswith(d) and len(fname) > len(d):
            return fname[:-len(d)], fname[-len(d):]
    p = Path(fname)
    return p.stem, p.suffix


def ext_de(fname):
    return separar_nombre(fname)[1].lower()


def es_media(fname):
    return ext_de(fname) in EXT_MEDIA


def es_video(fname):
    return ext_de(fname) in EXT_VIDEO


# ══════════════════════════════════════════════════════════════
#  FECHAS
# ══════════════════════════════════════════════════════════════

FECHA_MIN = datetime(1980, 1, 1)
FECHA_MAX = datetime.now() + timedelta(days=30)   # margen por zonas horarias

FORMATO_NOMBRE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})')


def fecha_valida(dt):
    return dt is not None and FECHA_MIN <= dt <= FECHA_MAX


class Fecha:
    """Una fecha candidata con su precisión ('segundo' o 'dia') y su origen."""
    __slots__ = ('dt', 'precision', 'fuente', 'desc')

    def __init__(self, dt, precision='segundo', fuente='', desc=''):
        # Una hora exacta 00:00:00 casi siempre es "solo día" (o el fallo
        # antiguo que escribía la medianoche), así que se trata como tal.
        if precision == 'segundo' and dt.hour == dt.minute == dt.second == 0:
            precision = 'dia'
        self.dt, self.precision, self.fuente, self.desc = dt, precision, fuente, desc

    def __repr__(self):
        return f'Fecha({self.dt:%Y-%m-%d %H:%M:%S}, {self.precision}, {self.fuente})'


def elegir_fecha(candidatas):
    """
    Regla común para decidir la fecha buena:
      1. Se descartan las fechas fuera de rango.
      2. Una fecha 'solo día' se descarta si otra fuente da hora exacta ese
         mismo día (evita que la medianoche pise la hora real).
      3. Una fecha JSON que esté a menos de 26 h de una fecha EXIF/nombre
         exacta se descarta: es la misma foto con desfase de zona horaria
         (el JSON va en UTC y la cámara en hora local).
      4. La fecha 'json_subida' (fecha de subida a Google) solo se usa si no
         hay ninguna otra.
      5. De lo que queda, gana la más antigua.
    """
    c = [x for x in candidatas if x is not None and fecha_valida(x.dt)]
    if not c:
        return None
    exactas = [x for x in c if x.precision == 'segundo']
    locales = [x for x in exactas if x.fuente in ('exif', 'nombre')]

    utiles = []
    for x in c:
        if x.precision == 'dia' and any(e.dt.date() == x.dt.date() for e in exactas):
            continue
        if x.fuente == 'json' and x.precision == 'segundo' and any(
                abs(x.dt - l.dt) < timedelta(hours=26) for l in locales):
            continue
        utiles.append(x)

    principales = [x for x in utiles if x.fuente != 'json_subida'] or utiles
    return min(principales, key=lambda x: (x.dt, x.precision != 'segundo'))


def _p(fmt, s):
    try:
        return datetime.strptime(s, fmt)
    except Exception:
        return None


def _dt(y, mo, d, h=0, mi=0, s=0):
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except Exception:
        return None


def _t(s):
    return (s or '').strip('_- ').strip()


def extraer_fecha_nombre(stem):
    """
    Devuelve Fecha(dt, precision, 'nombre', desc) o None.
    Reconoce los mismos patrones que la versión anterior.
    """
    s = stem
    S, D = 'segundo', 'dia'

    def r(dt, desc, prec=S):
        return Fecha(dt, prec, 'nombre', desc) if dt else None

    m = re.match(r'^(IMG|VID|SAVE|PANO|PXL|MVIMG)_(\d{8})_(\d{6})\d*(.*)', s, re.I)
    if m:
        extra = _t(m.group(4))
        f = r(_p('%Y%m%d%H%M%S', m.group(2) + m.group(3)),
              _t(m.group(1) + ('_' + extra if extra else '')))
        if f: return f

    # WhatsApp (PTV = vídeo circular, AUD = audio, STK = sticker)
    m = re.match(r'^(IMG|VID|PTV|AUD|STK)-(\d{8})-(.*)', s, re.I)
    if m:
        f = r(_p('%Y%m%d', m.group(2)), _t(m.group(3)), D)
        if f: return f

    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(.*)', s)
    if m:
        f = r(_dt(*m.groups()[:6]), _t(m.group(7)))
        if f: return f

    m = re.match(r'^Screenshot_(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?:-\d+)?_(.*)', s, re.I)
    if m:
        f = r(_dt(*m.groups()[:6]), _t(m.group(7)))
        if f: return f

    m = re.match(r'^dji_fly_(\d{8})_(\d{6})(.*)', s, re.I)
    if m:
        f = r(_p('%Y%m%d%H%M%S', m.group(1) + m.group(2)), 'dji_fly')
        if f: return f

    m = re.match(r'^(photo|video|animation)_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(.*)', s, re.I)
    if m:
        f = r(_dt(*m.groups()[1:7]), _t(m.group(8)) or 'telegram')
        if f: return f

    m = re.match(r'^signal-(\d{4})-(\d{2})-(\d{2})-(\d{6})(.*)', s, re.I)
    if m:
        f = r(_p('%Y%m%d%H%M%S', ''.join(m.groups()[:4])), _t(m.group(5)) or 'signal')
        if f: return f

    m = re.match(r'^KakaoTalk_(\d{8})_(\d{6})(.*)', s, re.I)
    if m:
        f = r(_p('%Y%m%d%H%M%S', m.group(1) + m.group(2)), _t(m.group(3)) or 'kakaotalk')
        if f: return f

    m = re.match(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\d{4,}$', s)
    if m:
        f = r(_dt(*m.groups()), 'instagram')
        if f: return f

    m = re.match(r'^WhatsApp (?:Image|Video) (\d{4})-(\d{2})-(\d{2}) at (\d{2})\.(\d{2})\.(\d{2})(.*)', s, re.I)
    if m:
        f = r(_dt(*m.groups()[:6]), _t(m.group(7)) or 'whatsapp')
        if f: return f

    m = re.match(r'^(\d{4})-(\d{2})-(\d{2}) (\d{2})\.(\d{2})\.(\d{2})(.*)', s)
    if m:
        f = r(_dt(*m.groups()[:6]), _t(m.group(7)))
        if f: return f

    m = re.match(r'^Captura de Pantalla (\d{4})-(\d{2})-(\d{2}) a las (\d{2})\.(\d{2})\.(\d{2})(.*)', s, re.I)
    if m:
        f = r(_dt(*m.groups()[:6]), _t(m.group(7)) or 'captura')
        if f: return f

    m = re.match(r'^\d+-(\d{14})-\d+$', s)        # 109412320-20250324202601-765389
    if m:
        f = r(_p('%Y%m%d%H%M%S', m.group(1)), '')
        if f: return f

    m = re.match(r'^(pp|temp_image)_(\d{8})_?(\d{6})(?!\d)', s, re.I)   # pp_20250325214721, temp_image_20250509_122239_…
    if m:
        f = r(_p('%Y%m%d%H%M%S', m.group(2) + m.group(3)), m.group(1).lower())
        if f: return f

    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})(.*)', s)                # solo fecha
    if m:
        f = r(_dt(*m.groups()[:3]), _t(m.group(4)), D)
        if f: return f

    m = re.match(r'^(\d{8})_(\d{6})(.*)', s)
    if m:
        f = r(_p('%Y%m%d%H%M%S', m.group(1) + m.group(2)), _t(m.group(3)))
        if f: return f

    m = re.match(r'^FB_IMG_(\d{10,13})$', s, re.I)
    if m:
        ts = int(m.group(1))
        if ts > 9_999_999_999:
            ts //= 1000
        try:
            return Fecha(datetime.fromtimestamp(ts), S, 'nombre', 'fb_img')
        except Exception:
            pass

    m = re.search(r'_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_photo_original$', s, re.I)
    if m:
        f = r(_dt(*m.groups()), 'dji_photo')
        if f: return f

    m = re.match(r'^mcm_C\d+-[a-zA-Z](\d{2})(\d{1})(\d{2})(\d{2})(\d{2})(\d{2})-', s)
    if m:
        f = r(_dt(2000 + int(m.group(1)), *m.groups()[1:]), 'mcm')
        if f: return f

    m = re.match(r'^(\d{8})$', s)
    if m:
        f = r(_p('%Y%m%d', m.group(1)), '', D)
        if f: return f

    return None


def fecha_archivo(fpath):
    """
    Fecha de modificación del archivo como Fecha(fuente='archivo'), o None.
    SOLO como último recurso, cuando no hay fecha en el nombre, el JSON ni
    el EXIF: cambia al copiar el archivo, así que es la menos fiable.
    """
    try:
        dt = datetime.fromtimestamp(os.stat(fpath).st_mtime).replace(microsecond=0)
    except (OSError, ValueError, OverflowError):
        return None
    return Fecha(dt, 'segundo', 'archivo') if fecha_valida(dt) else None


def fecha_formato_nombre(fname):
    """Fecha del prefijo YYYY-MM-DD_HH-MM-SS (o None)."""
    stem = separar_nombre(fname)[0]
    m = FORMATO_NOMBRE.match(stem)
    if not m:
        return None
    dt = _dt(*m.groups())
    return Fecha(dt, 'segundo', 'nombre') if dt else None


def a_exif(dt):
    return dt.strftime('%Y:%m:%d %H:%M:%S')


def parsear_fecha_exif(valor):
    if not valor or not isinstance(valor, str):
        return None
    return _p('%Y:%m:%d %H:%M:%S', valor.strip()[:19])


# ══════════════════════════════════════════════════════════════
#  RECORRIDO DE CARPETAS
# ══════════════════════════════════════════════════════════════

ERRORES_LECTURA = []   # carpetas que no se pudieron leer (permisos, rutas largas…)


def recorrer(carpeta, excluir=CARPETAS_EXCLUIR):
    """Genera todos los archivos (Path) saltando las carpetas excluidas."""
    for root, dirs, files in os.walk(carpeta, onerror=ERRORES_LECTURA.append):
        dirs[:] = [d for d in dirs if d not in excluir]
        rp = Path(root)
        for f in files:
            yield rp / f


def diagnosticar_sin_fotos(carpeta):
    """Explica por qué no se ha encontrado ninguna foto o vídeo en 'carpeta'."""
    from collections import Counter
    total, exts, ignoradas, errores = 0, Counter(), Counter(), []
    raiz = Path(carpeta)
    for root, dirs, files in os.walk(carpeta, onerror=errores.append):
        rel = Path(root).relative_to(raiz).parts
        excluida = next((p for p in rel if p in CARPETAS_EXCLUIR), None)
        for f in files:
            total += 1
            if excluida and es_media(f):
                ignoradas[excluida] += 1
            elif not excluida:
                exts[ext_de(f) or '(sin extensión)'] += 1
    print(f'\n  ⚠  No hay fotos ni vídeos que procesar en: {carpeta}')
    if total == 0 and not errores:
        print('     La carpeta está vacía (ni siquiera en sus subcarpetas hay archivos).')
        print('     ¿Es la carpeta correcta? ¿Se movió ya su contenido a la maestra?')
    if ignoradas:
        print('     Hay fotos/vídeos dentro de carpetas que los scripts ignoran a propósito:')
        for nombre, n in ignoradas.most_common():
            print(f'       · {n:,} en carpetas llamadas "{nombre}"')
        print('     Si quieres procesarlas, sácalas de ahí o cambia el nombre de esa carpeta.')
    otras = [(e, n) for e, n in exts.most_common(8)]
    if otras:
        print('     Archivos encontrados que no son fotos ni vídeos: '
              + ', '.join(f'{e} ({n:,})' for e, n in otras))
    if errores:
        print(f'     No se pudieron leer {len(errores)} carpeta(s) (permisos, rutas muy largas…), p. ej.:')
        for e in errores[:3]:
            print(f'       · {getattr(e, "filename", e)}')


def clave(path):
    """Clave normalizada para comparar rutas (mayúsculas en Windows, etc.)."""
    return os.path.normcase(os.path.abspath(str(path)))


# Carpetas que no son álbumes (no se guardan como etiqueta ni cuentan como
# "álbum" al elegir qué duplicado conservar). La lista está en un archivo de
# texto junto a los scripts para que se pueda editar sin tocar el código.
GENERICAS_TXT = SCRIPTS_DIR / 'carpetas_genericas.txt'
_GENERICAS_DEFECTO = [
    'takeout', 'takeout-*', 'google fotos', 'google photos', 'amazon fotos', 'amazon photos',
    'extraido_zips', 'photos from [0-9][0-9][0-9][0-9]', 'fotos de [0-9][0-9][0-9][0-9]',
    '[0-9][0-9][0-9][0-9]', '[0-9][0-9][0-9][0-9]-[0-9][0-9]', 'untitled*', 'sin título*',
    'fotos', 'photos', 'pictures', 'images', 'imágenes', 'camera', 'cámara', 'dcim',
    '[0-9][0-9][0-9]apple', '1[0-9][0-9]?????',
]
_GENERICAS = None


def patrones_genericos():
    """Lee carpetas_genericas.txt (o usa una lista mínima si no existe)."""
    global _GENERICAS
    if _GENERICAS is None:
        try:
            try:
                texto = GENERICAS_TXT.read_text(encoding='utf-8-sig')
            except UnicodeDecodeError:          # guardado en ANSI con el Bloc de notas
                texto = GENERICAS_TXT.read_text(encoding='cp1252')
            lineas = texto.splitlines()
            _GENERICAS = [l.strip().lower() for l in lineas if l.strip() and not l.strip().startswith('#')]
        except OSError:
            print(f'  ℹ  No se encuentra {GENERICAS_TXT.name}: se usa la lista básica de carpetas genéricas.')
            _GENERICAS = list(_GENERICAS_DEFECTO)
    return _GENERICAS


def es_generica(nombre):
    n = nombre.strip().lower()
    return any(fnmatch.fnmatchcase(n, p) for p in patrones_genericos())


def albumes_de(fpath, raiz):
    """Nombres de carpeta (no genéricos) entre raiz y el archivo."""
    try:
        rel = Path(fpath).parent.resolve().relative_to(Path(raiz).resolve())
    except ValueError:
        return []
    return [p for p in rel.parts if p and not es_generica(p)]


# Archivos de 0 bytes apartados por 7_mover_a_maestra.py (como _duplicados:
# junto a los scripts, una subcarpeta por ejecución)
VACIOS_DIR = SCRIPTS_DIR / '_vacios'


def problema_carpeta_trabajo(ruta):
    """
    Devuelve un mensaje si 'ruta' no se puede usar como pendientes/maestra porque
    coincide con (o contiene) la carpeta _duplicados de los scripts: los
    duplicados nuevos acabarían dentro de la carpeta que se está procesando.
    """
    r = Path(ruta)
    for dup in (SCRIPTS_DIR / '_duplicados', VACIOS_DIR):
        if dup.exists() and (en_carpeta(r, dup) or en_carpeta(dup, r)):
            return (f'La carpeta {ruta} es (o contiene) la carpeta {dup.name} de los scripts.\n'
                    '     Los archivos nuevos se guardarían dentro de ella. Cámbiale el nombre\n'
                    f'     (p. ej. "{dup.name.strip("_")}_antiguos") o mueve su contenido a otra carpeta y usa esa.')
    return None


def en_carpeta(fpath, carpeta):
    try:
        Path(fpath).resolve().relative_to(Path(carpeta).resolve())
        return True
    except ValueError:
        return False


# ══════════════════════════════════════════════════════════════
#  CACHÉ (SQLite, incluido en Python: un solo archivo, sin instalar nada)
# ══════════════════════════════════════════════════════════════

CACHE_PATH = SCRIPTS_DIR / 'cache_fotos.db'


class Cache:
    """
    Guarda lo ya calculado de cada archivo (metadatos, MD5, huellas visuales)
    con su tamaño y fecha de modificación. Si el archivo no ha cambiado, se
    reutiliza en vez de volver a abrirlo. Es solo una aceleración: si se borra
    el archivo cache_fotos.db, se reconstruye solo.
    """

    def __init__(self):
        self.con = None
        try:
            self._abrir()
        except sqlite3.DatabaseError:
            # Archivo dañado: se descarta y se empieza de cero
            _borrar(CACHE_PATH)
            try:
                self._abrir()
            except Exception:
                self.con = None
        except Exception:
            self.con = None

    def _abrir(self):
        self.con = sqlite3.connect(str(CACHE_PATH))
        cols = [c[1] for c in self.con.execute('PRAGMA table_info(datos)')]
        if cols and 'nombre' not in cols:          # caché de una versión anterior: se rehace
            self.con.execute('DROP TABLE datos')
        self.con.execute('CREATE TABLE IF NOT EXISTS datos (ruta TEXT, tipo TEXT, nombre TEXT, '
                         'tam INTEGER, mtime INTEGER, valor TEXT, PRIMARY KEY (ruta, tipo))')
        # Para reconocer una foto que se ha movido de carpeta (cambia la ruta, pero
        # no el nombre, el tamaño ni la fecha de modificación)
        self.con.execute('CREATE INDEX IF NOT EXISTS por_firma ON datos (tipo, nombre, tam, mtime)')
        self.con.execute('SELECT COUNT(*) FROM datos').fetchone()

    @staticmethod
    def firma(ruta):
        try:
            st = os.stat(ruta)
            return st.st_size, st.st_mtime_ns
        except OSError:
            return None

    def obtener(self, rutas, tipo):
        """{clave(ruta): valor} de las rutas cuya entrada sigue siendo válida."""
        if self.con is None or not rutas:
            return {}
        # Solo se piden a la base de datos las rutas necesarias, por tandas,
        # para no cargar en memoria la caché entera aunque sea muy grande.
        por_clave = {clave(r): r for r in rutas}
        claves = list(por_clave)
        filas = {}
        try:
            for i in range(0, len(claves), 500):
                trozo = claves[i:i + 500]
                marcas = ','.join('?' * len(trozo))
                for r, t, m, v in self.con.execute(
                        f'SELECT ruta, tam, mtime, valor FROM datos WHERE tipo=? AND ruta IN ({marcas})',
                        [tipo] + trozo):
                    filas[r] = (t, m, v)
        except Exception:
            return {}
        res, movidas = {}, []
        for k, ruta in por_clave.items():
            fm = self.firma(ruta)
            f = filas.get(k)
            valor = None
            if f and fm == (f[0], f[1]):
                valor = f[2]
            elif fm:
                # ¿Es una foto que se ha movido o copiado desde otra carpeta?
                try:
                    fila = self.con.execute(
                        'SELECT valor FROM datos WHERE tipo=? AND nombre=? AND tam=? AND mtime=? LIMIT 1',
                        (tipo, Path(ruta).name.lower(), fm[0], fm[1])).fetchone()
                except Exception:
                    fila = None
                if fila:
                    valor = fila[0]
                    movidas.append(ruta)
            if valor is not None:
                try:
                    res[k] = json.loads(valor)
                except ValueError:
                    pass
        if movidas:      # se guardan también con su ruta nueva
            self.guardar([(r, res[clave(r)]) for r in movidas if clave(r) in res], tipo)
        return res

    def guardar(self, items, tipo):
        """items: iterable de (ruta, valor_serializable_json)."""
        if self.con is None:
            return
        filas = []
        for ruta, valor in items:
            fm = self.firma(ruta)
            if fm:
                filas.append((clave(ruta), tipo, Path(ruta).name.lower(), fm[0], fm[1], json.dumps(valor)))
        try:
            with self.con:
                self.con.executemany('INSERT OR REPLACE INTO datos VALUES (?,?,?,?,?,?)', filas)
        except Exception:
            pass

    def purgar(self, carpetas, vistas):
        """Borra entradas de archivos que estaban bajo 'carpetas' y ya no existen."""
        if self.con is None:
            return
        prefijos = tuple(clave(c).rstrip(os.sep) + os.sep for c in carpetas)
        try:
            rutas = {r for (r,) in self.con.execute('SELECT DISTINCT ruta FROM datos')}
            fuera = [(r,) for r in rutas if r.startswith(prefijos) and r not in vistas]
            if fuera:
                with self.con:
                    self.con.executemany('DELETE FROM datos WHERE ruta=?', fuera)
        except Exception:
            pass


    def vaciar(self):
        if self.con is not None:
            with self.con:
                self.con.execute('DELETE FROM datos')
            self.con.execute('VACUUM')


_CACHE = None


def cache():
    global _CACHE
    if _CACHE is None:
        _CACHE = Cache()
    return _CACHE


# ══════════════════════════════════════════════════════════════
#  EXIFTOOL
# ══════════════════════════════════════════════════════════════

_EXIFTOOL = None
# ExifTool puede estar en el PATH (p.ej. C:\\Windows) o junto a los scripts
_EXE_LOCAL = SCRIPTS_DIR / ('exiftool.exe' if os.name == 'nt' else 'exiftool')
EXIFTOOL = str(_EXE_LOCAL) if _EXE_LOCAL.is_file() else 'exiftool'


def exiftool_disponible():
    global _EXIFTOOL
    if _EXIFTOOL is None:
        try:
            subprocess.run([EXIFTOOL, '-ver'], capture_output=True, check=True)
            _EXIFTOOL = True
        except (OSError, subprocess.CalledProcessError):
            _EXIFTOOL = False
    return _EXIFTOOL


def piexif_disponible():
    try:
        import piexif  # noqa: F401
        return True
    except ImportError:
        return False


_AVISO_EXIFTOOL_DADO = False


def aviso_sin_exiftool(consecuencia):
    """Avisa (una sola vez por ejecución) de que falta ExifTool y qué implica."""
    global _AVISO_EXIFTOOL_DADO
    if exiftool_disponible() or _AVISO_EXIFTOOL_DADO:
        return
    _AVISO_EXIFTOOL_DADO = True
    print('\n  ⚠  Falta ExifTool: ' + consecuencia + '.')
    print('     Descárgalo de https://exiftool.org y deja exiftool.exe (con su carpeta')
    print('     exiftool_files) en C:\\Windows o junto a los scripts.')
    if piexif_disponible():
        print('     Mientras tanto se usa piexif, que solo lee fechas de JPEG/TIFF.')
    else:
        print('     Tampoco está piexif, así que NO se leerá ninguna fecha EXIF. Como mínimo:')
        print('     python -m pip install piexif')


def _argfile(lineas):
    fd, ruta = tempfile.mkstemp(prefix='_exiftool_', suffix='.args', dir=SCRIPTS_DIR)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
        for l in lineas:
            f.write(l + '\n')
    return ruta


class Meta:
    __slots__ = ('dto', 'cdt', 'dto_preciso', 'content_id', 'duracion', 'ancho', 'alto')

    def __init__(self, d):
        self.dto = parsear_fecha_exif(d.get('DateTimeOriginal'))
        # Con milésimas (SubSecDateTimeOriginal): distingue fotos de una ráfaga
        # hechas dentro del mismo segundo
        self.dto_preciso = _fecha_con_milesimas(d.get('SubSecDateTimeOriginal')) or self.dto
        self.cdt = parsear_fecha_exif(d.get('CreateDate'))
        cid = d.get('ContentIdentifier')
        self.content_id = str(cid).strip().upper() if cid else None
        try:
            self.duracion = float(d.get('Duration')) if d.get('Duration') is not None else None
        except (TypeError, ValueError):
            self.duracion = None
        try:
            self.ancho = int(d.get('ImageWidth') or 0) or None
            self.alto = int(d.get('ImageHeight') or 0) or None
        except (TypeError, ValueError):
            self.ancho = self.alto = None

    def fecha(self):
        """Fecha EXIF preferida: DateTimeOriginal y si no CreateDate."""
        if self.dto:
            return Fecha(self.dto, 'segundo', 'exif'), 'DateTimeOriginal'
        if self.cdt:
            return Fecha(self.cdt, 'segundo', 'exif'), 'CreateDate'
        return None, None


def _fecha_con_milesimas(valor):
    """'2026:04:22 18:53:56.023+02:00' → datetime con microsegundos (o None)."""
    dt = parsear_fecha_exif(valor)
    if dt is None:
        return None
    m = re.match(r'^.{19}\.(\d+)', str(valor).strip())
    if m:
        dt = dt.replace(microsecond=int((m.group(1) + '000000')[:6]))
    return dt


def leer_metadatos(rutas, etiqueta='Leyendo metadatos', lote=1500):
    """
    Lee en lote con ExifTool fechas, ContentIdentifier (Live Photo),
    duración y tamaño de imagen. Devuelve {clave(ruta): Meta}.
    Los vídeos se leen con QuickTimeUTC: su fecha UTC se pasa a hora local.
    """
    rutas = [str(r) for r in rutas]
    res = {}
    if not rutas or not exiftool_disponible():
        return res
    guardados = cache().obtener(rutas, TIPO_META)
    for k, d in guardados.items():
        res[k] = Meta(d)
    total = len(rutas)
    rutas = [r for r in rutas if clave(r) not in guardados]
    if guardados:
        print(f'  {etiqueta}: {len(guardados):,} de {total:,} ya estaban en la caché.')
    nuevos = []
    for i in range(0, len(rutas), lote):
        trozo = rutas[i:i + lote]
        print(f'  {etiqueta}: {min(i + lote, len(rutas)):,} / {len(rutas):,}...', end='\r')
        af = _argfile(trozo)
        try:
            p = subprocess.run(
                [EXIFTOOL, '-j', '-q', '-q', '-m', '-fast', '-charset', 'filename=utf8',
                 '-api', 'QuickTimeUTC=1', '-api', 'LargeFileSupport=1',
                 '-DateTimeOriginal', '-SubSecDateTimeOriginal', '-CreateDate', '-ContentIdentifier',
                 '-Duration#', '-ImageWidth', '-ImageHeight', '-@', af],
                capture_output=True)
            salida = p.stdout.decode('utf-8', errors='replace').strip()
            if salida:
                for d in json.loads(salida):
                    src = d.get('SourceFile')
                    if src:
                        res[clave(src)] = Meta(d)
                        nuevos.append((src, {k: d.get(k) for k in _CAMPOS_META if d.get(k) is not None}))
        except Exception as e:
            print(f'\n  ⚠  Error leyendo metadatos: {e}')
        finally:
            _borrar(af)
    cache().guardar(nuevos, TIPO_META)
    print(f'  {etiqueta}: {total:,} archivos.{" " * 20}')
    return res


_CAMPOS_META = ('DateTimeOriginal', 'SubSecDateTimeOriginal', 'CreateDate', 'ContentIdentifier',
                'Duration', 'ImageWidth', 'ImageHeight')
TIPO_META = 'meta2'     # cambia si cambian los campos guardados, para no usar datos incompletos


def huellas_imagen(ruta):
    """
    Calcula las huellas visuales de una imagen (se ejecuta en paralelo en
    varios procesos). Los JPEG se descomprimen directamente a tamaño reducido,
    que basta para las huellas y es 4-5 veces más rápido.
    Devuelve (ruta, [ph, dh, ph16, dh16, ancho, alto]) en hexadecimal, o (ruta, None).
    """
    try:
        from PIL import Image, ImageOps
        import imagehash
        # Son tus propias fotos: se quita el límite de seguridad de Pillow para
        # imágenes enormes (panorámicas, escaneos), que si no avisa o las rechaza.
        Image.MAX_IMAGE_PIXELS = None
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        with Image.open(ruta) as im:
            w, h = im.size
            try:
                if im.getexif().get(274) in (5, 6, 7, 8):     # orientación girada 90°
                    w, h = h, w
            except Exception:
                pass
            if im.format == 'JPEG':
                im.draft('RGB', (512, 512))
            im = ImageOps.exif_transpose(im)
            im.load()
            hs = [imagehash.phash(im), imagehash.dhash(im),
                  imagehash.phash(im, hash_size=16), imagehash.dhash(im, hash_size=16)]
            return ruta, [str(x) for x in hs] + [w, h]
    except Exception:
        return ruta, None


def miniatura_imagen(ruta):
    """
    Miniatura JPEG en base64 para el informe HTML (se ejecuta en paralelo).
    HEIC: usa la miniatura que el iPhone ya guarda dentro del archivo (unas 35
    veces más rápido que abrir la foto entera). JPEG: apertura reducida.
    """
    import io
    import base64
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = None
        im = None
        if ext_de(ruta) in ('.heic', '.heif'):
            try:
                import pillow_heif
                hf = pillow_heif.open_heif(ruta)
                if hf.info.get('thumbnails'):
                    im = hf.get_thumbnail(0).to_pillow()
            except Exception:
                im = None
            if im is None:
                from pillow_heif import register_heif_opener
                register_heif_opener()
        if im is None:
            im = Image.open(ruta)
            if im.format == 'JPEG':
                im.draft('RGB', (360, 360))
            im = ImageOps.exif_transpose(im)
        im = im.convert('RGB')
        im.thumbnail((180, 180))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=70)
        return ruta, base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        return ruta, ''


def leer_fecha_piexif(fpath):
    """Respaldo sin ExifTool: solo JPEG/TIFF. Devuelve Fecha o None."""
    if ext_de(fpath) not in ('.jpg', '.jpeg', '.tif', '.tiff'):
        return None
    try:
        import piexif
        ex = piexif.load(str(fpath)).get('Exif', {})
        for tag in (piexif.ExifIFD.DateTimeOriginal, piexif.ExifIFD.DateTimeDigitized):
            v = ex.get(tag)
            if v:
                dt = parsear_fecha_exif(v.decode('ascii', errors='ignore'))
                if dt:
                    return Fecha(dt, 'segundo', 'exif')
    except Exception:
        pass
    return None


def ejecutar_bloques(bloques, comunes, etiqueta='Escribiendo', lote=500):
    """
    Ejecuta ExifTool con un bloque de argumentos por archivo (-execute).
    bloques: lista de (ruta, [args...]). comunes: args para todos.
    Devuelve (ok:set(clave), errores:dict(clave → mensaje)).
    """
    ok, errores = set(), {}
    for i in range(0, len(bloques), lote):
        trozo = bloques[i:i + lote]
        print(f'  {etiqueta}: {min(i + lote, len(bloques)):,} / {len(bloques):,}...', end='\r')
        lineas = []
        for ruta, args in trozo:
            lineas += args + [str(ruta), '-echo3', '@@FIN@@', '-echo4', '@@FIN@@', '-execute']
        af = _argfile(lineas)
        try:
            p = subprocess.run(
                [EXIFTOOL, '-charset', 'filename=utf8', '-@', af, '-common_args'] + comunes,
                capture_output=True)
            outs = p.stdout.decode('utf-8', errors='replace').split('@@FIN@@')
            errs = p.stderr.decode('utf-8', errors='replace').split('@@FIN@@')
            for j, (ruta, _) in enumerate(trozo):
                o = outs[j] if j < len(outs) else ''
                e = errs[j].strip() if j < len(errs) else 'sin respuesta de ExifTool'
                if re.search(r'\b1 (image )?files? (updated|unchanged)', o):
                    ok.add(clave(ruta))
                else:
                    errores[clave(ruta)] = (e or o.strip() or 'error desconocido').splitlines()[0]
        except Exception as e:
            for ruta, _ in trozo:
                errores[clave(ruta)] = str(e)
        finally:
            _borrar(af)
    print(f'  {etiqueta}: {len(bloques):,} archivos.{" " * 20}')
    return ok, errores


def args_fecha_sistema(dt):
    """Fecha de modificación (y de creación en Windows) del archivo."""
    v = a_exif(dt)
    a = [f'-FileModifyDate={v}']
    if os.name == 'nt':
        a.append(f'-FileCreateDate={v}')
    return a


def _borrar(ruta):
    try:
        os.unlink(ruta)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  JSON DE GOOGLE TAKEOUT
# ══════════════════════════════════════════════════════════════

_SUPL = 'supplemental-metadata'
_EDITADO = re.compile(r'-(edited|editado|editada|bearbeitet|modifié|modificato|bewerkt)$', re.I)


def _base_json(jname):
    """
    'IMG_1.jpg.supplemental-metadata(1).json' → ('IMG_1.jpg', '(1)')
    'IMG_1.jpg.supp.json'                     → ('IMG_1.jpg', '')
    'IMG_1.jpg(2).json'                       → ('IMG_1.jpg', '(2)')
    """
    base = jname[:-5]
    num = ''
    m = re.match(r'^(.*?)(\(\d+\))$', base)
    if m:
        base, num = m.group(1), m.group(2)
    m = re.match(r'^(.*)\.([\w-]+)$', base)
    if m and len(m.group(2)) >= 1 and _SUPL.startswith(m.group(2).lower()) \
            and '.' in m.group(1):
        base = m.group(1)
    return base, num


def _nombres_media(base, num):
    """Nombre(s) de archivo multimedia al que puede corresponder el JSON."""
    if not num:
        return [base]
    stem, ext = separar_nombre(base)
    return [f'{stem}{num}{ext}', base + num]


class IndiceJSON:
    """
    Índice por carpeta de los JSON de Takeout:
      buscar(ruta_media) → (Fecha, ruta_json, propio)
    'propio' indica que el JSON pertenece a ese archivo (y debe moverse o
    renombrarse con él); es False si se ha encontrado por derivación
    (p.ej. 'foto-edited.jpg' usando el JSON de 'foto.jpg').
    """

    def __init__(self):
        self.por_carpeta = defaultdict(dict)    # carpeta → nombre_lower → (Fecha, json)
        self.titulos = defaultdict(dict)        # carpeta → title_lower → (Fecha, json)
        self.truncados = defaultdict(list)      # carpeta → [(base_lower, Fecha, json)]
        self.todos = []                         # rutas de todos los JSON

    def cargar(self, carpetas):
        n = 0
        for carpeta in carpetas:
            for fpath in recorrer(carpeta):
                if not fpath.name.lower().endswith('.json'):
                    continue
                self.todos.append(fpath)
                try:
                    with open(fpath, encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                fecha = None
                ts = (data.get('photoTakenTime') or {}).get('timestamp')
                if ts:
                    fecha = _fecha_ts(ts, 'json')
                if fecha is None:
                    ts = (data.get('creationTime') or {}).get('timestamp')
                    if ts:
                        fecha = _fecha_ts(ts, 'json_subida')
                if fecha is None:
                    continue
                n += 1
                ck = clave(fpath.parent)
                base, num = _base_json(fpath.name)
                for nm in _nombres_media(base, num):
                    self.por_carpeta[ck].setdefault(nm.lower(), (fecha, fpath))
                if ext_de(base) not in EXT_MEDIA and len(base) >= 30:
                    self.truncados[ck].append((base.lower(), fecha, fpath))
                titulo = data.get('title')
                if isinstance(titulo, str) and titulo:
                    self.titulos[ck].setdefault(titulo.lower(), (fecha, fpath))
        return n

    def buscar(self, fpath):
        fpath = Path(fpath)
        ck = clave(fpath.parent)
        nombre = fpath.name.lower()
        d = self.por_carpeta.get(ck, {})
        if nombre in d:
            return d[nombre][0], d[nombre][1], True
        for base, fecha, jp in sorted(self.truncados.get(ck, []), key=lambda x: -len(x[0])):
            if nombre.startswith(base):
                return fecha, jp, True
        stem, ext = separar_nombre(fpath.name)
        sin_editar = _EDITADO.sub('', stem)
        if sin_editar != stem:
            nm = (sin_editar + ext).lower()
            if nm in d:
                return d[nm][0], d[nm][1], False
        t = self.titulos.get(ck, {})
        if nombre in t and not self._json_usado_por_otro(t[nombre][1], fpath):
            return t[nombre][0], t[nombre][1], True
        return None, None, False

    def _json_usado_por_otro(self, jpath, fpath):
        base, num = _base_json(jpath.name)
        return any((jpath.parent / nm).exists() and nm.lower() != fpath.name.lower()
                   for nm in _nombres_media(base, num))


def _fecha_ts(ts, fuente):
    try:
        return Fecha(datetime.fromtimestamp(int(ts)), 'segundo', fuente)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
#  LIVE PHOTOS
# ══════════════════════════════════════════════════════════════

def _parece_video_live(fpath, meta):
    if ext_de(fpath.name) == '.heic.qt':
        return True
    if meta and meta.content_id:
        return True
    if meta and meta.duracion is not None:
        return meta.duracion <= LIVE_DURACION_MAX
    try:
        return fpath.stat().st_size <= LIVE_TAMANO_MAX
    except OSError:
        return False


def detectar_live_photos(archivos, metas=None):
    """
    Empareja fotos y vídeos Live Photo dentro de cada carpeta.
      1. Mismo ContentIdentifier (aunque los nombres ya no coincidan).
      2. Mismo nombre base + vídeo corto (≤ 4,5 s) o .HEIC.qt.
         Si los dos tienen ContentIdentifier y difieren, NO se emparejan.
    Devuelve (foto→video, video→foto) con claves clave(ruta) y valores Path.
    """
    metas = metas or {}
    por_carpeta = defaultdict(list)
    for f in archivos:
        f = Path(f)
        por_carpeta[clave(f.parent)].append(f)

    foto_video, video_foto = {}, {}
    for lista in por_carpeta.values():
        fotos = [f for f in lista if ext_de(f.name) in EXT_LIVE_FOTO]
        videos = [f for f in lista if ext_de(f.name) in EXT_LIVE_VIDEO]
        if not fotos or not videos:
            continue

        def cid(f):
            m = metas.get(clave(f))
            return m.content_id if m else None

        # 1. Por ContentIdentifier
        vid_por_cid = {}
        for v in videos:
            c = cid(v)
            if c:
                vid_por_cid.setdefault(c, v)
        for foto in fotos:
            c = cid(foto)
            v = vid_por_cid.get(c) if c else None
            if v is not None and clave(v) not in video_foto:
                foto_video[clave(foto)] = v
                video_foto[clave(v)] = foto

        # 2. Por nombre
        vid_por_stem = defaultdict(list)
        for v in videos:
            if clave(v) not in video_foto:
                vid_por_stem[separar_nombre(v.name)[0].lower()].append(v)
        for foto in fotos:
            if clave(foto) in foto_video:
                continue
            for v in vid_por_stem.get(separar_nombre(foto.name)[0].lower(), []):
                if clave(v) in video_foto:
                    continue
                cf, cv = cid(foto), cid(v)
                if cf and cv and cf != cv:
                    continue
                if not _parece_video_live(v, metas.get(clave(v))):
                    continue
                foto_video[clave(foto)] = v
                video_foto[clave(v)] = foto
                break
    return foto_video, video_foto


# ══════════════════════════════════════════════════════════════
#  MOVIMIENTOS Y REGISTRO PARA DESHACER
# ══════════════════════════════════════════════════════════════

def nombre_libre(destino, ocupados=None):
    """Si destino existe (o está en 'ocupados'), añade _1, _2… antes de la extensión."""
    destino = Path(destino)
    ocupados = ocupados if ocupados is not None else set()

    def libre(p):
        return not p.exists() and clave(p) not in ocupados

    if libre(destino):
        return destino
    stem, ext = separar_nombre(destino.name)
    i = 1
    while True:
        c = destino.with_name(f'{stem}_{i}{ext}')
        if libre(c):
            return c
        i += 1


class Registro:
    """CSV origen→destino de cada movimiento, para poder deshacerlo."""

    def __init__(self, accion, simular=False):
        self.simular = simular
        self.ruta = None
        self.n = 0
        if not simular:
            REGISTROS_DIR.mkdir(parents=True, exist_ok=True)
            ahora = datetime.now()
            ts = ahora.strftime('%Y%m%d_%H%M%S_') + f'{ahora.microsecond // 1000:03d}'
            self.ruta = REGISTROS_DIR / f'{ts}_{accion}.csv'
            self._f = open(self.ruta, 'w', newline='', encoding='utf-8')
            self._w = csv.writer(self._f)
            self._w.writerow(['origen', 'destino'])

    def anotar(self, origen, destino):
        self.n += 1
        if not self.simular:
            self._w.writerow([str(origen), str(destino)])
            self._f.flush()

    def cerrar(self):
        if not self.simular:
            self._f.close()
            if self.n == 0:
                _borrar(self.ruta)
                self.ruta = None


def mover(origen, destino, registro=None):
    """Mueve creando carpetas; anota en el registro. Devuelve el destino final."""
    origen, destino = Path(origen), Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino = nombre_libre(destino)
    shutil.move(str(origen), str(destino))
    if registro:
        registro.anotar(origen, destino)
    return destino


def ruta_relativa_segura(fpath, raiz):
    """Ruta relativa de fpath respecto a raiz (o solo el nombre si no cuelga)."""
    try:
        return Path(fpath).resolve().relative_to(Path(raiz).resolve())
    except ValueError:
        return Path(Path(fpath).name)


# ══════════════════════════════════════════════════════════════
#  ENTRADA / SALIDA
# ══════════════════════════════════════════════════════════════

def interactivo():
    """True si el script se lanzó sin argumentos (doble clic / 'python x.py')."""
    return len(sys.argv) <= 1


def pedir_carpeta(mensaje, opcional=False):
    while True:
        try:
            ruta = input(mensaje).strip().strip('"\'')
        except EOFError:
            return None
        if not ruta:
            if opcional:
                return None
            print('  ⚠  Debes introducir una carpeta.')
            continue
        if not Path(ruta).is_dir():
            print(f'  ⚠  No existe o no es una carpeta: {ruta}')
            continue
        return ruta


def pedir_carpetas(mensaje='Carpeta'):
    print('  Introduce las carpetas (una por línea). Línea vacía para terminar.')
    carpetas = []
    while True:
        r = pedir_carpeta(f'  {mensaje} {len(carpetas) + 1}: ', opcional=True)
        if not r:
            if carpetas:
                return carpetas
            print('  Introduce al menos una carpeta.')
            continue
        carpetas.append(r)


def confirmar(mensaje, auto=False):
    if auto:
        print(f'  {mensaje} → SI (automático)')
        return True
    try:
        return input(f'  {mensaje} (escribe SI): ').strip().upper() == 'SI'
    except EOFError:
        return False


def preguntar_sn(mensaje, defecto=False):
    try:
        r = input(f'  {mensaje} (s/n): ').strip().lower()
    except EOFError:
        return defecto
    return r.startswith('s') if r else defecto


def pausa_final():
    if interactivo():
        try:
            input('\nPulsa Enter para salir.')
        except EOFError:
            pass


_SELLO_EJECUCION = datetime.now().strftime('%Y%m%d_%H%M%S')


def iniciar_logs(*nombres):
    """
    Al empezar un paso, aparta sus logs de la ejecución anterior a
    logs/anteriores/<fecha_hora>/, para que en logs/ solo quede lo de esta
    ejecución (y no un informe viejo si esta vez no hubo nada que hacer).
    Todos los pasos de un mismo pipeline comparten la misma subcarpeta.
    """
    for n in nombres:
        p = LOGS_DIR / n
        if p.is_file():
            destino = LOGS_DIR / 'anteriores' / _SELLO_EJECUCION
            destino.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(p), str(nombre_libre(destino / n)))
            except OSError:
                pass


def ruta_log(nombre):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / nombre


def caja(titulo, filas):
    """Imprime un recuadro de resumen. filas: [(etiqueta, valor)]."""
    ancho = 62
    print()
    print('╔' + '═' * ancho + '╗')
    print('║  ' + titulo.ljust(ancho - 2) + '║')
    for et, val in filas:
        txt = f'  {et:<26}: {val}'
        print('║' + txt[:ancho].ljust(ancho) + '║')
    print('╚' + '═' * ancho + '╝')


def mb(n):
    return f'{n / 1024**2:,.1f} MB'


def gb(n):
    return f'{n / 1024**3:,.2f} GB'
