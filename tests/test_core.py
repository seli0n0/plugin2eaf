from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from plugin2eaf.core import ConversionError, convert, inspect_input, validate_archive


SOURCE = '''from base_plugin import BasePlugin
__id__ = "hello_world"
__name__ = "Hello World"
__version__ = "2.0.0"
__requirements__ = ["requests>=2"]
class Hello(BasePlugin):
    pass
'''


class ConverterTests(unittest.TestCase):
    def test_convert_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "hello.plugin"
            source.write_text(SOURCE, encoding="utf-8")
            output = root / "hello.eaf"
            result = convert(source, output)
            self.assertEqual(result["metadata"]["id"], "hello_world")
            self.assertTrue(validate_archive(output)["valid"])
            with zipfile.ZipFile(output) as archive:
                self.assertIn("refmap.yml", archive.namelist())
                self.assertIn("plugin/src/main.py", archive.namelist())
                self.assertIn('id: "hello_world"', archive.read("plugin/meta.yml").decode())

    def test_normalizes_wrapped_zip_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "wrapped.plugin"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("old/main.py", SOURCE)
                archive.writestr("old/assets/icon.png", b"image")
                archive.writestr("old/locales/strings_ru.json", b'{}')
            info = inspect_input(source)
            self.assertEqual(info["entry"], "main.py")
            output = root / "wrapped.eaf"
            convert(source, output)
            with zipfile.ZipFile(output) as archive:
                self.assertIn("plugin/res/icon.png", archive.namelist())
                self.assertIn("plugin/locales/strings_ru.json", archive.namelist())

    def test_rejects_opaque_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "opaque.plugin"
            source.write_bytes(b"not python")
            with self.assertRaises(ConversionError):
                inspect_input(source)

    def test_rejects_zip_slip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "bad.plugin"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../main.py", SOURCE)
            with self.assertRaises(ConversionError):
                inspect_input(source)


if __name__ == "__main__":
    unittest.main()
