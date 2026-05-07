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


## v29 decision filter fix

Fixes Decision Engine game filter:
- replaces checkbox with explicit dropdown: Human coaches only / All games
- unchecked checkbox issue removed
- auto-falls back to All games when selected team has zero human-coached games
- shows human-game and all-game counts


## v31 True Potential Engine
Adds /potential and /potential/{player_id}.
Uses baseline colors from lowest-season Season Start when detected; otherwise infers color from observed growth.
Growth rules: green +30, blue +20, black +10, yellow +5, red +0.
Flags green PE/LP outlier watch.


## v32 WE potential fix

Updates True Potential Engine:
- WE is always black
- WE is not capped by normal color-potential rules
- WE projected with average +20 career growth for now
- lower starting WE gets a slightly slower temporary projection
- archive data should replace this assumption later


## v33 DU/FT projection fix

Adds DU and FT back into:
- projected peak calculations
- remaining growth calculations
- potential projection tables
- projected role scoring inputs


## v34 actual WIS color parser

Maps WhatIfSports potential CSS classes:
- potential_veryhigh -> green
- potential_high -> blue
- potential_average -> black
- potential_low -> yellow
- potential_verylow -> red


## v35 Potential DU/FT type fix

Fixes /potential internal server error caused by FT letter grades like D+ being treated as numbers.
- DU is treated numerically when possible.
- FT grades are mapped to numeric values for projections.
- Detail page shows original DU/FT display values.


## v36 exclude FT from total rating projection

FT remains imported, displayed, and projected, but is excluded from:
- projected total
- remaining growth total
- projected OVR index / total player rating style calculations


## v37 actual color + projection fix

Fixes:
- color extraction now reads potential_* classes from td and nested elements
- adds HTML row fallback to capture colors even if header matching fails
- Projected OVR now equals current OVR + total remaining non-FT growth
- removes bad avg_remaining * 10 projection inflation


## v38 cap all projected ratings at 100

Updates True Potential Engine:
- every projected rating is capped at 100
- WE remains special: always black and not color-growth-capped
- but WE projected value still cannot exceed 100


## v39 raw potential class parser

Fixes potential color capture by reading raw RatingsHistory table rows like:
<td class="center potential_average">67</td>

The parser now:
- extracts potential_* classes directly from row cells
- maps them back onto parsed Season/Type snapshots
- logs detected_color_cells in RATINGS PARSER DEBUG


## v40 PlayerHistory color endpoint fix

Fixes actual potential color capture by switching RatingsHistory imports from:
- /hd/PlayerProfile/RatingsHistory.aspx

to:
- /hd/PlayerHistory/RatingsHistory.aspx

The PlayerHistory endpoint is the one visible in Chrome screenshots and reliably exposes potential_* CSS classes.


## v41 PlayerHistory no-OVR ratings fix

Fixes PlayerHistory ratings sync returning 0 rows:
- PlayerHistory endpoint can omit OVR column
- parser now supports both with-OVR and no-OVR row shapes
- when OVR is absent, it calculates OVR as sum of numeric ratings excluding FT


## v42 revert ratings sync to PlayerProfile

Reverts ratings sync back to:
- /hd/PlayerProfile/RatingsHistory.aspx?tid=...&pid=...

Reason:
- PlayerProfile has the stable numeric/OVR table structure.
- PlayerHistory exposed a different table shape and caused zero-row imports.
- We keep the improved potential_* class parsing and fallback inference.


## v43 potential ceiling model fix

Reframes Potential correctly:
- Ceiling = first Season Start rating + baseline color growth rule
- Current ratings do not determine ceiling
- Current ratings only calculate realized/remaining development
- all projected ratings cap at 100
- FT displayed but excluded from total projected OVR


## v44 manual baseline color overrides

Adds manual color overrides for the Potential Engine:
- overrides are stored per player/rating
- manual colors take priority over detected/inferred colors
- useful when WIS color classes are not captured reliably
- Potential board shows manual/detected/inferred/fixed counts


## v45 deterministic potential parser + verifier
Adds DOM-first RatingsHistory parser and /potential-debug/{player_id}.


## v46 rendered DOM ratings sync

Adds Playwright/headless Chromium rendered DOM fetching:
- /sync-ratings-all-rendered
- rendered Potential Debug mode
- uses browser-rendered DOM to capture potential_* classes that raw requests may miss

Render build command should include:
  pip install -r requirements.txt && playwright install chromium

If Render needs system packages, use a Docker deploy or add Playwright dependencies per Render logs.


## v47 empirical growth engine foundation

Adds:
- /growth-model empirical growth dashboard
- growth samples from imported Ratings History
- inferred realized-growth bands
- /archive roster import page
- generic archive roster player-link crawler

This starts the archive-driven path:
baseline ratings -> realized career growth -> empirical expectations.
