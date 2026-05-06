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


## v17 RatingsHistory Sn. header fix

Fixes Redlands/Knight ratings history pages where the ratings table header uses `Sn.` instead of `Season`.


## v18 RatingsHistory hard-anchor parser

Fixes ratings import by:
- anchoring directly on the Sn./Type/A/SPD/.../OVR table header
- parsing cell-by-cell rows from that header only
- detecting player name from class/height/title instead of nav tabs
- logging first parsed row in Render logs


## v19 rating growth baseline fix

Fixes ratings/OVR growth baseline:
- previous behavior could use the first/oldest row, often `Season End`
- new behavior uses the `Season Start` row from the lowest numbered season
- fallback order: Recruiting/Signed, then earliest row in lowest season


## v20 decision engine

Adds:
- /decision dashboard
- Connects ratings history to season BPR by player name
- Expected BPR from best role score
- Impact Gap = Actual BPR - Expected BPR
- Labels: Overperformer / Underperformer / As Expected / Import ratings

Current Expected BPR formula:
  (Best Role Score - 50) / 4

This is transparent and should later be replaced by a learned regression once enough player-season data exists.


## v21 human-only decision engine filter

Decision Engine now supports filtering to only games against non-Sim AI opponents.

Default behavior:
- Human-coached opponents only

Toggle available on /decision page.


## v22 decision engine error fix

Fixes internal server error on /decision caused by referencing non-existent Game fields.
Human-only filtering now uses away_team/home_team and away_coach/home_coach correctly.


## v23 password protection

Adds Basic Auth protection to the entire app.

Required Render environment variables:
- APP_USERNAME
- APP_PASSWORD

If either variable is missing, the app denies access.


## v24 authenticated WhatIfSports sync

Adds:
- Authenticated WIS fetch layer
- Supports WIS_COOKIE or WIS_USERNAME/WIS_PASSWORD from Render env vars
- /sync page
- Sync All Configured Teams
- Sync individual GameLog URL
- Parser fetch now uses authenticated session automatically

Recommended Render env vars:
- WIS_COOKIE (preferred) OR WIS_USERNAME + WIS_PASSWORD
- WIS_GAMELOG_PHELAN_ECSU
- WIS_GAMELOG_TARKANIAN_CSUEASTBAY
- WIS_GAMELOG_KNIGHT_REDLANDS

Known defaults:
- ECSU/Phelan defaults to tid=14473
- Redlands/Knight defaults to tid=13652
- CSU Eastbay/Tarkanian must be set once unless a default URL is later added


## v25 ratings sync + rating colors

Adds:
- Sync All Ratings
- Sync individual team ratings from TeamProfile/Ratings.aspx
- hardcoded CSU Eastbay tid=12670
- color-aware RatingsHistory import
- PlayerRatingSnapshot color fields with automatic SQLite migration

Color detection is best-effort based on WIS HTML classes/styles. If WIS does not expose colors in HTML, values import normally and color fields stay blank.


## v26 ratings sync diagnostics

Fixes/diagnoses Ratings Sync:
- More aggressive Team Ratings page player-id detection
- Shows Last Ratings Sync Result on /ratings
- Logs TEAM RATINGS PARSER DEBUG with player count and sample players

If sync still imports nothing, copy the Last Ratings Sync Result and Render log lines:
TEAM RATINGS PARSER DEBUG
SYNC RATINGS ALL RESULT


## v27 ratings sync import fix

Fixes Sync All Ratings error:
- name 'parse_team_ratings_url' is not defined

Adds /sync-diagnostics to verify parser functions are loaded.


## v28 multi-team page fix

Fixes ECSU-only leftovers:
- /players now accepts selected team and world
- Game Ratings page has team selector
- Decision Engine normalizes world from selected team
- Season page links to correct team Game Ratings
