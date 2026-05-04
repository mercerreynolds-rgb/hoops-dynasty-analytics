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
