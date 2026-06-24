"""Geometry primitives: site preparation, candidate grids, equipment/clearance
polygons, rotation handling and the placement-validity predicate.

Pure geometry — depends only on numpy + shapely.
"""

import numpy as np
from shapely.geometry import Polygon, box


def create_site(vertices):
    site = Polygon(vertices)
    if not site.is_valid:
        raise ValueError("Invalid polygon: check vertex order")
    return site


def prepare_site(site, config):
    """Apply the inward setback, subtract restricted zones, and return the
    usable buildable polygon together with the list of non-buildable polygons.
    """
    usable_site = site.buffer(-config["setback"])
    for zone in config["zones"]["restricted"]:
        zone_poly = Polygon(zone)
        usable_site = usable_site.difference(zone_poly)
    non_buildable_polys = [Polygon(z) for z in config["zones"]["non_buildable"]]
    return usable_site, non_buildable_polys


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


def is_valid_placement(candidate_fp, candidate_cl, site, non_buildable, placed):
    if not site.contains(candidate_fp):
        return False
    for zone in non_buildable:
        if candidate_fp.intersects(zone):
            return False
    for obj in placed:
        if candidate_fp.intersects(obj["clearance_zone"]):
            return False
        if candidate_cl.intersects(obj["footprint"]):
            return False
    return True


def get_rotated_dimensions(w, h, cl_dict, rotated):
    if not rotated:
        return w, h, cl_dict
    return h, w, {
        "front": cl_dict["left"],
        "back": cl_dict["right"],
        "left": cl_dict["back"],
        "right": cl_dict["front"],
    }