#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
sys.path.insert(0, str(ROOT / "src"))

parser = argparse.ArgumentParser(description="Test tự động; không đọc microphone thật.")
parser.add_argument("--mock", action="store_true", help="Chỉ test mock/logic; không cần numpy/model/microphone.")
args = parser.parse_args()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_mock.py" if args.mock else "test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(f"{'PASS' if result.wasSuccessful() else 'FAIL'}: {result.testsRun} test tự động; không thay thế validate-live.")
sys.exit(0 if result.wasSuccessful() else 1)
