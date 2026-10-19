import unicodedata
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from file_filter import _cargar_patrones_locales, should_ignore


class ShouldIgnoreTests(unittest.TestCase):
    def test_ignores_known_pattern(self):
        self.assertTrue(should_ignore("Instrucciones a los proponentes.pdf"))

    def test_ignores_by_substring_match(self):
        self.assertTrue(
            should_ignore("copia (1) - Instrucciones a los proponentes.pdf")
        )

    def test_match_is_case_insensitive(self):
        self.assertTrue(should_ignore("INSTRUCCIONES A LOS PROPONENTES.PDF"))

    def test_match_ignores_unicode_composition(self):
        # La misma "ó" escrita como "o" + acento combinante debe calzar igual.
        descompuesto = unicodedata.normalize("NFD", "Información al proveedor.pdf")
        self.assertTrue(should_ignore(descompuesto))

    def test_does_not_ignore_unrelated_filename(self):
        self.assertFalse(should_ignore("Cotizacion_Proveedor_XYZ.pdf"))


class PatronesLocalesTests(unittest.TestCase):
    def test_missing_file_means_no_extra_patterns(self):
        self.assertEqual(_cargar_patrones_locales(Path("no_existe.txt")), [])

    def test_reads_patterns_and_skips_comments_and_blank_lines(self):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "ignored_files.txt"
            ruta.write_text("# comentario\n\nFORM_EJEMPLO_\n  Bases tipo  \n", encoding="utf-8")
            self.assertEqual(_cargar_patrones_locales(ruta), ["FORM_EJEMPLO_", "Bases tipo"])


if __name__ == "__main__":
    unittest.main()
