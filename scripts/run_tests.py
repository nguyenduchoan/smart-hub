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
selection = parser.add_mutually_exclusive_group()
selection.add_argument("--mock", action="store_true", help="Chỉ test mock/logic; không cần numpy/model/microphone.")
selection.add_argument("--stt", action="store_true", help="Test STT và model thật; không cần backbone/enrollment của engine cá nhân.")
selection.add_argument("--runtime", action="store_true", help="Test runtime và thu lượt: state, buffer, worker; không dùng microphone.")
args = parser.parse_args()
if args.stt:
    import importlib.util
    from smart_hub.stt_assets import verify_models
    try:
        if importlib.util.find_spec("sherpa_onnx") is None:
            raise ImportError("Cần pip install -r requirements-stt.txt trong .venv.")
        verify_models()
    except (ImportError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    suite = unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
                               for pattern in ["test_mock_stt.py", "test_stt.py"])
elif args.runtime:
    suite = unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
                               for pattern in ["test_mock_runtime.py", "test_runtime.py", "test_mock_turn.py", "test_turn.py"])
else:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_mock*.py" if args.mock else "test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(f"{'PASS' if result.wasSuccessful() else 'FAIL'}: {result.testsRun} test tự động; không thay thế kiểm thử giọng thật.")
if result.skipped:
    print(f"SKIP: {len(result.skipped)} test chưa chạy; xem lý do ở từng test phía trên.")
sys.exit(0 if result.wasSuccessful() else 1)
