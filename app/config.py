TRACKED_TEAMS = [
    {"team": "E. Connecticut St.", "world": "Phelan"},
    {"team": "CSU, Eastbay", "world": "Tarkanian"},
    {"team": "Redlands", "world": "Knight"},
]

DEFAULT_TEAM = "E. Connecticut St."
DEFAULT_WORLD = "Phelan"

# Backward compatibility with earlier app versions.
MY_TEAM = DEFAULT_TEAM
WORLD = DEFAULT_WORLD


# v24 sync targets. Set these Render env vars to your TeamProfile/GameLog.aspx URLs.
SYNC_TARGETS = [
    {
        "label": "ECSU / Phelan",
        "team": "E. Connecticut St.",
        "world": "Phelan",
        "env": "WIS_GAMELOG_PHELAN_ECSU",
        "default_url": "https://www.whatifsports.com/hd/TeamProfile/GameLog.aspx?tid=14473",
    },
    {
        "label": "CSU Eastbay / Tarkanian",
        "team": "CSU, Eastbay",
        "world": "Tarkanian",
        "env": "WIS_GAMELOG_TARKANIAN_CSUEASTBAY",
        "default_url": "https://www.whatifsports.com/hd/TeamProfile/GameLog.aspx?tid=12670",
    },
    {
        "label": "Redlands / Knight",
        "team": "Redlands",
        "world": "Knight",
        "env": "WIS_GAMELOG_KNIGHT_REDLANDS",
        "default_url": "https://www.whatifsports.com/hd/TeamProfile/GameLog.aspx?tid=13652",
    },
]


RATINGS_SYNC_TARGETS = [
    {
        "label": "ECSU / Phelan",
        "team": "E. Connecticut St.",
        "world": "Phelan",
        "tid": "14473",
        "url": "https://www.whatifsports.com/hd/TeamProfile/Ratings.aspx?tid=14473",
    },
    {
        "label": "CSU Eastbay / Tarkanian",
        "team": "CSU, Eastbay",
        "world": "Tarkanian",
        "tid": "12670",
        "url": "https://www.whatifsports.com/hd/TeamProfile/Ratings.aspx?tid=12670",
    },
    {
        "label": "Redlands / Knight",
        "team": "Redlands",
        "world": "Knight",
        "tid": "13652",
        "url": "https://www.whatifsports.com/hd/TeamProfile/Ratings.aspx?tid=13652",
    },
]
