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
