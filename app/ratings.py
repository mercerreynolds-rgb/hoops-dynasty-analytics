from __future__ import annotations


def safe_div(n: float, d: float) -> float:
    return n / d if d else 0.0


def calculate_box_ratings(row: dict) -> dict:
    # First-pass transparent Hoops Dynasty box BPR model.
    #
    # BoxOBPR:
    #   scoring + playmaking + offensive rebounds - turnovers, normalized by minutes
    #
    # BoxDBPR:
    #   steals + blocks + defensive rebounds - fouls, normalized by minutes
    #
    # Later:
    #   OBPR/DBPR should blend box ratings with lineup/on-off and possession impact.
    minutes = row.get("minutes", 0) or 0
    pts = row.get("pts", 0) or 0
    ast = row.get("ast", 0) or 0
    orb = row.get("orb", 0) or 0
    tov = row.get("tov", 0) or 0
    reb = row.get("reb", 0) or 0
    stl = row.get("stl", 0) or 0
    blk = row.get("blk", 0) or 0
    pf = row.get("pf", 0) or 0

    dreb = max(reb - orb, 0)

    box_off_value = pts + (0.70 * ast) + (0.50 * orb) - (1.00 * tov)
    box_def_value = (1.20 * stl) + (1.00 * blk) + (0.60 * dreb) - (0.30 * pf)

    # Scale per 20 minutes to make outputs easier to read.
    box_obpr = safe_div(box_off_value, minutes) * 20
    box_dbpr = safe_div(box_def_value, minutes) * 20

    # MVP: OBPR/DBPR equal box versions until possession/on-off model is added.
    obpr = box_obpr
    dbpr = box_dbpr

    return {
        "box_obpr": round(box_obpr, 3),
        "box_dbpr": round(box_dbpr, 3),
        "obpr": round(obpr, 3),
        "dbpr": round(dbpr, 3),
        "bpr": round(obpr + dbpr, 3),
    }
