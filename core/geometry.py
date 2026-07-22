"""Geometry primitives: site preparation, candidate grids, equipment/clearance
polygons, rotation handling and the placement-validity predicate.

Pure geometry — depends only on numpy + shapely.
"""

import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.validation import explain_validity


def create_site(vertices):
    """Build the site geometry from one polygon or several disjoint zones.

    Accepts either a bare ring ``[(x, y), ...]`` or a list of such rings
    ``[[(x, y), ...], [(x, y), ...]]`` — multi-zone sites (e.g. a plot split by a
    road) are unioned into a single MultiPolygon, which every downstream consumer
    (bounds, ``contains``, plotting) already handles.
    """
    rings = _as_ring_list(vertices)
    polys = []
    for i, ring in enumerate(rings):
        poly = Polygon(ring)
        if not poly.is_valid:
            raise ValueError(
                f"Invalid site polygon #{i + 1}: {explain_validity(poly)}. "
                "Check the vertex order — vertices must trace the boundary "
                "without the edges crossing each other."
            )
        polys.append(poly)

    site = polys[0] if len(polys) == 1 else unary_union(polys)
    if site.is_empty or site.area <= 0:
        raise ValueError("Site polygon has zero area")
    return site


def _as_ring_list(vertices):
    """Normalise site input to a list of rings (each a list of (x, y))."""
    if not vertices:
        raise ValueError("site_vertices is empty")
    first = vertices[0]
    # A bare ring looks like [(x, y), ...]; detect the leading (x, y) pair.
    if (
        isinstance(first, (tuple, list))
        and len(first) == 2
        and all(isinstance(c, (int, float)) for c in first)
    ):
        return [list(vertices)]
    return [list(v) for v in vertices]


def prepare_site(site, config):
    """Apply the inward setback and subtract the restricted zones.

    Returns ``(usable_site, non_buildable_polys, restricted_polys)``. The
    restricted polygons are returned as well as subtracted because they are a
    *hard* exclusion: ``is_valid_placement`` additionally forbids the clearance
    envelope from reaching into them, which subtracting from the site alone
    cannot express (that only constrains the footprint).
    """
    restricted_polys = [Polygon(z) for z in config["zones"]["restricted"]]
    usable_site = site.buffer(-config["setback"])
    for zone_poly in restricted_polys:
        usable_site = usable_site.difference(zone_poly)
    non_buildable_polys = [Polygon(z) for z in config["zones"]["non_buildable"]]
    return usable_site, non_buildable_polys, restricted_polys


def create_candidate_grid(site, resolution):
    """Regular candidate grid over the site's bounding box.

    Returns an empty list for degenerate (empty / zero-area) sites instead of
    raising on NaN bounds — callers treat "no candidates" as "nothing fits".
    """
    if site.is_empty or resolution <= 0:
        return []
    minx, miny, maxx, maxy = site.bounds
    if not all(np.isfinite([minx, miny, maxx, maxy])):
        return []
    xs = np.arange(minx, maxx, resolution)
    ys = np.arange(miny, maxy, resolution)
    return [(x, y) for x in xs for y in ys]


def create_equipment_polygon(x, y, width, height):
    return box(x, y, x + width, y + height)


def create_clearance_polygon(footprint, clearance):
    minx, miny, maxx, maxy = footprint.bounds
    return box(
        minx - clearance["left"],
        miny - clearance["back"],
        maxx + clearance["right"],
        maxy + clearance["front"],
    )


# Overlap smaller than this (m^2) is treated as flush contact, not an incursion,
# so a clearance sitting exactly on a restricted boundary is still allowed.
MIN_OVERLAP_AREA = 1e-9


def is_valid_placement(candidate_fp, candidate_cl, site, non_buildable, placed,
                       restricted=()):
    if not site.contains(candidate_fp):
        return False
    for zone in non_buildable:
        if candidate_fp.intersects(zone):
            return False
    # Restricted zones are hard exclusions: the clearance envelope may not
    # encroach either, only touch the boundary.
    for zone in restricted:
        if candidate_cl.intersects(zone) and \
                candidate_cl.intersection(zone).area > MIN_OVERLAP_AREA:
            return False
    for obj in placed:
        if candidate_fp.intersects(obj["clearance_zone"]):
            return False
        if candidate_cl.intersects(obj["footprint"]):
            return False
    return True


# The four discrete orientations a container may take (degrees, CCW).
ORIENTATIONS = (0, 90, 180, 270)


def get_oriented_dimensions(w, h, cl_dict, angle):
    """Return (width, height, clearance) for a container rotated by ``angle``
    (one of 0/90/180/270 degrees).

    For a rectangular footprint, 0/180 share the (w, h) bounding box and 90/270
    share (h, w) — but the *clearance* sides differ for every angle because the
    per-side clearances (front/back/left/right) are asymmetric. This is what
    makes 180° meaningful: it lets a unit face its small "back" clearance toward
    a neighbour (back-to-back packing) instead of its large "front".

    Each 90° step advances the clearance values along the cycle
    front -> right -> back -> left -> front.
    """
    k = (int(angle) // 90) % 4
    rw, rh = (w, h) if k % 2 == 0 else (h, w)
    cl = dict(cl_dict)
    for _ in range(k):
        cl = {
            "front": cl["left"],
            "right": cl["front"],
            "back":  cl["right"],
            "left":  cl["back"],
        }
    return rw, rh, cl


def get_rotated_dimensions(w, h, cl_dict, rotated):
    """Backward-compatible boolean wrapper: False -> 0°, True -> 90°.

    ``rotated`` may also be passed an angle (0/90/180/270) directly.
    """
    if rotated in (0, 90, 180, 270):
        angle = rotated
    else:
        angle = 90 if rotated else 0
    return get_oriented_dimensions(w, h, cl_dict, angle)