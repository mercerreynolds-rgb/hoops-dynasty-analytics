# Hoops Dynasty Analytics

A web app for importing WhatIfSports Hoops Dynasty game pages and calculating starter player impact metrics.

## Local run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Render deploy

Use these settings:

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Python version:

```text
3.11.9
```


## v2 parser fix

Handles WhatIfSports player rows where position and player name are joined, e.g. `cGeorge Nicoll`, and captures lineup/substitution blocks.


## v3 fix

Adds reset database button and automatically re-imports blank duplicate games created by older parser versions.


## v4 parser/debug fix

Adds robust fetch debugging, better boxscore fallback parsing, and parser count logs.


## v5 parser fix

Parses WhatIfSports HTML cell-by-cell instead of assuming full rows are extracted as single text lines.


## v6 ECSU true BPR

Adds:
- MY_TEAM config for E. Connecticut St. in Phelan
- lineup segment table
- possession/on-off engine
- PlayerImpact table
- true OBPR/DBPR blend for ECSU players only

After deploying v6, reset database and re-import games so the new tables populate.


## v7 season stats

Adds:
- /season dashboard
- minutes-weighted season player ratings
- season on/off summaries
- most-used lineup rankings
- player detail/game-log pages
