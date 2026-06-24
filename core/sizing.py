"""System sizing model.

Turns a placed layout (BESS count, MVS count) plus per-unit ratings into total
plant MW / MWh and a storage-duration classification (2H / 3H / 4H). Pure and
UI-free, so the notebook and the app size systems identically.
"""

# Canonical storage durations (hours) used for classification.
DURATION_CLASSES = (2, 3, 4)


def classify_duration(duration_h, classes=DURATION_CLASSES):
    """Map a continuous duration (MWh / MW) to the nearest canonical class.

    Returns ``(class_hours, label)`` e.g. ``(4, "4H")``. For a zero/undefined
    duration returns ``(None, "N/A")``.
    """
    if not duration_h or duration_h <= 0:
        return None, "N/A"
    nearest = min(classes, key=lambda h: abs(h - duration_h))
    return nearest, f"{nearest}H"


def size_system(bess_count, mvs_count, bess_unit_mwh, mvs_unit_mw,
                target_mw=None, target_mwh=None, classes=DURATION_CLASSES):
    """Compute plant-level sizing from a placed layout.

    Parameters
    ----------
    bess_count, mvs_count : int
        Units actually placed by the layout engine.
    bess_unit_mwh : float
        Energy per BESS container (MWh).
    mvs_unit_mw : float
        Power capacity per MVS station (MW).
    target_mw, target_mwh : float | None
        Optional design targets; if given, the achieved-vs-target gap and a
        boolean "met" flag are included.

    Returns
    -------
    dict with: total_mw, total_mwh, duration_h, duration_class, duration_label,
    and (when targets are supplied) mw_gap / mwh_gap / mw_target_met /
    mwh_target_met.
    """
    total_mwh = bess_count * bess_unit_mwh
    total_mw  = mvs_count * mvs_unit_mw
    duration_h = (total_mwh / total_mw) if total_mw > 0 else 0.0
    duration_class, duration_label = classify_duration(duration_h, classes)

    out = {
        "total_mw":        total_mw,
        "total_mwh":       total_mwh,
        "duration_h":      duration_h,
        "duration_class":  duration_class,
        "duration_label":  duration_label,
    }

    if target_mw is not None:
        out["mw_gap"] = total_mw - target_mw
        out["mw_target_met"] = total_mw >= target_mw
    if target_mwh is not None:
        out["mwh_gap"] = total_mwh - target_mwh
        out["mwh_target_met"] = total_mwh >= target_mwh

    return out