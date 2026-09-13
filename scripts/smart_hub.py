#!/usr/bin/env python3
"""Project entry point, runnable without installing this source package."""
from pathlib import Path
import os
import sys

# These small matrices do not benefit from BLAS thread pools.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smart_hub.cli import main

if __name__ == "__main__":
    sys.exit(main())
