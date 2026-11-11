import sys

from .cli import main

# Preserve Chinese JSON when Windows pipes stdout into another UTF-8 application.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

raise SystemExit(main())
