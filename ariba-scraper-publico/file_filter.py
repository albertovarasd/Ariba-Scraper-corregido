"""Filtro de adjuntos que no vale la pena descargar ni subir a Drive.

Hay dos fuentes de patrones:

1. ``DEFAULT_IGNORED_PATTERNS``: ejemplos genéricos de documentos administrativos
   que se repiten en casi todas las licitaciones.
2. Un archivo opcional ``ignored_files.txt`` (un patrón por línea, ``#`` para
   comentarios) con los patrones propios de cada comprador. Ese archivo está en
   ``.gitignore`` a propósito: se queda en tu equipo y no se publica.
   Mira ``ignored_files.example.txt`` para ver el formato.

La comparación es por subcadena, sin distinguir mayúsculas y normalizando
Unicode, así que ``"informacion al proveedor"`` calza con
``"copia (1) - Información al proveedor 2025.PDF"`` mientras el patrón no tenga
tildes distintas a las del archivo.
"""
import os
import unicodedata
from pathlib import Path

DEFAULT_IGNORED_PATTERNS = [
    "Instrucciones a los proponentes",
    "Formulario Desviaciones a las Bases",
    "Información al proveedor",
    "Informacion al proveedor",
    "Clausulas aplicables a ofertas",
]

IGNORED_FILES_PATH = Path(
    os.getenv("IGNORED_FILES_PATH", str(Path(__file__).parent / "ignored_files.txt"))
)


def _normalizar(texto: str) -> str:
    """
    Normaliza Unicode a una forma única antes de comparar. Sin esto, una 'Ó'
    escrita con un solo carácter y otra 'Ó' escrita como 'O' + acento
    combinante (visualmente idénticas, pero bytes distintos) NO calzan en una
    comparación de texto normal, y un archivo que ya estaba en la lista igual
    se descargaba.
    """
    return unicodedata.normalize("NFC", texto).lower()


def _cargar_patrones_locales(ruta: Path) -> list:
    """Lee los patrones propios del usuario; si el archivo no existe, no hay extras."""
    if not ruta.is_file():
        return []
    patrones = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#"):
            patrones.append(linea)
    return patrones


IGNORED_FILES = DEFAULT_IGNORED_PATTERNS + _cargar_patrones_locales(IGNORED_FILES_PATH)

_IGNORED_FILES_NORMALIZADOS = [_normalizar(p) for p in IGNORED_FILES]


def should_ignore(filename: str) -> bool:
    """
    Devuelve True si el archivo debe ignorarse (no descargar / no subir).
    La comparación es por substring (no exacta) y normaliza Unicode, para
    que variaciones de codificación no hagan fallar el filtro.
    """
    filename_normalizado = _normalizar(filename)
    return any(patron in filename_normalizado for patron in _IGNORED_FILES_NORMALIZADOS)
