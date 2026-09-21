#!/usr/bin/env python3
"""Regenerate ``swagger.json`` — the OpenAPI contract for this service.

The very same document is served live by the running API at ``/openapi.json``
(and is browsable at ``/docs`` via Swagger UI and ``/redoc``). Run this after
changing routes or models so the checked-in contract stays in sync::

    python3 dump_openapi.py            # writes ./swagger.json
    python3 dump_openapi.py out.json   # custom output path
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.api import app  # noqa: E402

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swagger.json")


def main() -> int:
    output_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT
    spec = app.openapi()
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Wrote {output_path} (OpenAPI {spec['openapi']}, paths: {', '.join(sorted(spec['paths']))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
