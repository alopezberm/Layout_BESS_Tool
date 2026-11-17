"""Save / load site geometry (boundary + zones) as JSON files.

Lets a site's hand-traced vertices live in their own file (e.g. ``sites/``)
instead of inside a notebook cell, so they are never lost and can be reused by
any run. ``load_site`` returns exactly the geometry keyword arguments of
``build_config``::

    CONFIG = build_config(**load_site("sites/site_1.json"), max_bess_per_mvs=4, ...)
"""

import json
import os

from .config import _as_polygon_list


def _rings(zones):
    """Normalise one polygon or a list of polygons to a list of rings of
    ``(x, y)`` tuples (JSON turns tuples into lists)."""
    return [[(float(x), float(y)) for x, y in ring] for ring in _as_polygon_list(zones)]


def save_site(path, site_vertices, non_buildable=None, restricted=None, name=None):
    """Write the site geometry to ``path`` (JSON). Creates parent folders."""
    data = {
        "name": name or os.path.splitext(os.path.basename(path))[0],
        "site_vertices": _rings(site_vertices),
        "non_buildable": _rings(non_buildable),
        "restricted": _rings(restricted),
    }
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        # One vertex per line-ish: compact rings, readable file.
        f.write("{\n")
        f.write(f'  "name": {json.dumps(data["name"], ensure_ascii=False)},\n')
        keys = ("site_vertices", "non_buildable", "restricted")
        for i, key in enumerate(keys):
            rings = [json.dumps([[x, y] for x, y in ring]) for ring in data[key]]
            body = (",\n    ".join(rings))
            sep = "," if i < len(keys) - 1 else ""
            f.write(f'  "{key}": [\n    {body}\n  ]{sep}\n' if rings else f'  "{key}": []{sep}\n')
        f.write("}\n")
    return path


def load_site(path):
    """Read a site JSON written by ``save_site`` (or by hand). Returns
    ``{"site_vertices", "non_buildable", "restricted"}`` ready for
    ``build_config(**...)``."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "site_vertices" not in data:
        raise ValueError(f"{path}: missing 'site_vertices'")
    return {
        "site_vertices": _rings(data["site_vertices"]),
        "non_buildable": _rings(data.get("non_buildable")),
        "restricted": _rings(data.get("restricted")),
    }
