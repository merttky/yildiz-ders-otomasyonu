import py_compile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


class TestSyntaxCompatibility(unittest.TestCase):
    def test_all_python_files_compile(self):
        """Ensure all python files compile without SyntaxError or TypeError."""
        py_files = [
            f for f in BASE_DIR.glob("*.py")
            if not f.name.startswith(".")
        ]
        self.assertTrue(len(py_files) >= 5, "Should find at least 5 Python files in root")

        for f in py_files:
            with self.subTest(file=f.name):
                # py_compile verifies syntax and annotations
                py_compile.compile(str(f), doraise=True)


if __name__ == "__main__":
    unittest.main()
