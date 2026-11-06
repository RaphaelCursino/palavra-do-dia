"""WSGI entry point for PythonAnywhere and other WSGI hosts."""

import os
import sys
from pathlib import Path


project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

os.environ["PALAVRA_DO_DIA_ENV"] = "production"

from app import create_app


application = create_app()
