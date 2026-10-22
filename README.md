# 🍺 Pub Crawl Planner — built with TinyFish

Edit: this submission won me second prize at the UCL AI society, Tiny Fish build night

Give it a start, a finish, how many pubs, and what you want (lunch, tea, Sunday roast, beer garden…).
It plans a walking pub crawl using **live web data**, and checks each pub's own website for the food and drink you asked for.

Built at the TinyFish × UCL AI Society Build Night.

## How TinyFish is used (4 endpoints)

| Step | Endpoint | What it does |
|---|---|---|
| 1 | **Fetch** | Geocodes your start and finish, then pulls every pub between them from OpenStreetMap (about 1s) |
| 2 | **Search** | Finds a website for any pub OpenStreetMap doesn't have one for |
| 3 | **Fetch** | Reads each pub's live website and checks for your requirements (lunch, tea, roast…), showing the quote it found as evidence |
| 4 | **Agent** | Optional button: opens Google Maps walking directions for the route and reads the exact walking time for each leg |
| 5 | **Monitor** | Optional button: sets up daily monitors on each pub's website, to catch menu, kitchen-hours or event changes |

Output: a results table (walk time, ✓/✗ for each requirement with a quote from the site) plus a one-click Google Maps walking route. A plan takes about 5 seconds; exact walking times via the Agent take 1–2 minutes.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export TINYFISH_API_KEY=your_key_here
.venv/bin/python app.py
```

Open http://localhost:8000. A plan takes about 5 seconds.
