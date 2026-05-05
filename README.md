# Hoops Dynasty Analytics v11

Adds bulk import from a WhatIfSports Team Game Log page and multi-team support.

Tracked teams:
- E. Connecticut St. — Phelan
- CSU, Eastbay — Tarkanian
- Redlands — Knight

## Deploy notes

After deploying v11:
1. Reset database.
2. Paste a Game Log URL into Bulk Import.
3. Go to Diagnostics.
4. Go to Season and choose a tracked team.

Game Log URL format:
https://www.whatifsports.com/hd/TeamProfile/GameLog.aspx?tid=14473


## v12 player detail fix

Fixes internal server error on individual player pages by replacing the player detail template with a multi-team safe version and URL-encoding player links.


## v13 advanced metrics

Adds to season/player pages:
- Usage %
- True Shooting %
- On-court ORtg / DRtg / Net Rating
- Net On/Off
- Simple player role tags


## v14 ratings history

Adds:
- RatingsHistory.aspx importer
- Player rating snapshots table
- Growth from first snapshot to current
- Role scores from ratings
- Ratings dashboard and player rating detail pages

Color/potential projection is not yet automated because the RatingsHistory page gives historical values, not original color tags. That can be added next with manual color input.


## v15 Redlands/Knight boxscore parser fix

Adds hybrid box-score parsing:
- compact player rows: `c Norman Brown 18 4-10 ...`
- cell-by-cell player rows used by some other pages

This fixes Redlands games importing a shell but zero stat rows.


## v16 ratings import fix

Adds robust RatingsHistory parsing for both cell-by-cell and compact table output.
Also logs RATINGS PARSER DEBUG and RATINGS IMPORT RESULT in Render logs.
