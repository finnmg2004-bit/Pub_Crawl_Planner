"""Pub crawl planner pipeline: TinyFish Agent -> Search -> Fetch -> Agent."""
import glob
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from tinyfish import AgentConfig, TinyFish


def load_key():
    if os.environ.get("TINYFISH_API_KEY"):
        return os.environ["TINYFISH_API_KEY"]
    files = glob.glob(os.path.expanduser("~/API_Key/*"))
    if not files:
        raise RuntimeError("Set TINYFISH_API_KEY or put the key in ~/API_Key/")
    return open(files[0]).read().strip()


client = TinyFish(api_key=load_key())

# Words that count as evidence a pub meets a requirement.
SYNONYMS = {
    "lunch": ["lunch", "kitchen", "food served", "food", "sandwich", "burger", "pizza"],
    "food": ["food", "kitchen", "menu", "dinner", "lunch"],
    "tea": ["afternoon tea", "tea", "coffee", "cream tea"],
    "sunday roast": ["sunday roast", "roast"],
    "roast": ["sunday roast", "roast"],
    "vegan": ["vegan", "plant-based"],
    "vegetarian": ["vegetarian", "vegan"],
    "beer garden": ["beer garden", "garden", "terrace"],
    "live music": ["live music", "gig", "band"],
    "quiz": ["quiz"],
    "craft beer": ["craft beer", "ipa", "cask", "real ale"],
    "dog friendly": ["dog friendly", "dog-friendly", "dogs welcome"],
}

SKIP_DOMAINS = ("google.", "facebook.com", "instagram.com", "tiktok.com", "youtube.com")

PUB_SCHEMA = {
    "type": "object",
    "properties": {
        "pubs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "address": {"type": "string"},
                    "rating": {"type": "number"},
                },
                "required": ["name", "address"],
            },
        }
    },
    "required": ["pubs"],
}

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "total_minutes": {"type": "number"},
        "total_distance": {"type": "string"},
        "legs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "minutes": {"type": "number"},
                    "distance": {"type": "string"},
                },
                "required": ["to", "minutes"],
            },
        },
    },
    "required": ["legs"],
}


def agent_find_pubs(area, count, log, retry=False):
    """TinyFish Agent: browse live Google Maps for pubs near an area."""
    url = f"https://www.google.com/maps/search/pubs+near+{quote(area)}"
    log(f"🤖 Agent: browsing Google Maps for pubs near {area}…")
    r = client.agent.run(
        url=url,
        goal=(
            f"List the first {count} pubs or bars shown in the results with their name, "
            "street address and star rating. Skip sponsored/ad results."
        ),
        output_schema=PUB_SCHEMA,
        agent_config=AgentConfig(max_duration_seconds=90),
    )
    pubs = (r.result or {}).get("pubs", []) if getattr(r.status, "value", r.status) == "COMPLETED" else []
    if not pubs and not retry:
        log(f"🤖 Agent: nothing found near {area}, retrying with a broader search…")
        return agent_find_pubs(area.split(",")[0] + " London", count, log, retry=True)
    log(f"🤖 Agent: found {len(pubs)} pubs near {area}")
    for p in pubs:
        p["area"] = area
    return pubs


def search_pub_site(pub, area):
    """TinyFish Search: find the pub's own website / menu page."""
    q = f"{pub['name']} pub {area} menu food opening hours"
    res = client.search.query(q, location="GB", language="en")
    for item in res.results:
        if not any(d in item.url for d in SKIP_DOMAINS):
            return item.url, item.snippet or ""
    return None, ""


def evidence_for(text, requirement):
    """Return a short quote from the page proving the requirement, or None."""
    words = SYNONYMS.get(requirement.lower(), [requirement.lower()])
    for w in words:
        m = re.search(r"\b" + re.escape(w) + r"\b", text, re.I)
        if m:
            i = m.start()
            snippet = text[max(0, i - 60): i + 90]
            return re.sub(r"\s+", " ", snippet).strip()
    return None


def hours_snippet(text):
    m = re.search(r"(kitchen|food)[^\n]{0,40}?\d{1,2}(:\d\d)?\s?(am|pm)[^\n]{0,60}", text, re.I)
    return re.sub(r"\s+", " ", m.group(0)).strip()[:120] if m else ""


def gmaps_link(stops):
    return "https://www.google.com/maps/dir/" + "/".join(quote(s) for s in stops) + "/data=!4m2!4m1!3e2"


def agent_walking_route(stops, log):
    """TinyFish Agent: read real walking times for each leg from Google Maps."""
    log("🤖 Agent: reading walking times for the route on Google Maps…")
    r = client.agent.run(
        url=gmaps_link(stops),
        goal=(
            "This is a Google Maps walking directions page with multiple stops. Do not change "
            "the stops. Read the walking time in minutes and distance for each leg in order, "
            "plus the total time and distance, and return them straight away."
        ),
        output_schema=ROUTE_SCHEMA,
        agent_config=AgentConfig(max_duration_seconds=100),
    )
    if getattr(r.status, "value", r.status) != "COMPLETED":
        log("🤖 Agent: couldn't read walking times in time - use the Google Maps link below")
    return (r.result or {}) if getattr(r.status, "value", r.status) == "COMPLETED" else {}


NOMINATIM = "https://nominatim.openstreetmap.org/search?format=json&"


def fetch_json(urls):
    """TinyFish Fetch on JSON endpoints; returns {url: parsed json}."""
    import json
    resp = client.fetch.get_contents(urls, format="html", per_url_timeout_ms=20000)
    out = {}
    for res in resp.results:
        text = res.text if isinstance(res.text, str) else str(res.text)
        text = re.sub(r"^\s*<[^>]+>|<[^>]+>\s*$", "", text.strip())
        try:
            out[res.url] = json.loads(text)
        except ValueError:
            out[res.url] = []
    return out


def walk_minutes(a, b):
    """Rough walking time: straight-line distance x 1.3 for streets, at 80 m/min."""
    import math
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1]) * math.cos(math.radians(a[0]))
    metres = 6371000 * math.hypot(dlat, dlon) * 1.3
    return max(1, round(metres / 80)), metres


def plan(start, end, n_pubs, requirements, log=print):
    reqs = [r.strip() for r in requirements.split(",") if r.strip()]

    # 1. Fetch: geocode start/end on OpenStreetMap.
    log("📄 Fetch: finding your start and finish on OpenStreetMap…")
    urls = [NOMINATIM + "limit=1&q=" + quote(start), NOMINATIM + "limit=1&q=" + quote(end)]
    geo = fetch_json(urls)
    try:
        a = (float(geo[urls[0]][0]["lat"]), float(geo[urls[0]][0]["lon"]))
        b = (float(geo[urls[1]][0]["lat"]), float(geo[urls[1]][0]["lon"]))
    except (KeyError, IndexError):
        raise RuntimeError("Couldn't find the start or finish - try adding the city, e.g. 'Camden Town, London'")

    # 2. Fetch: every pub in the box between start and finish (padded ~400m).
    pad = 0.004
    box = f"{min(a[1], b[1]) - pad},{max(a[0], b[0]) + pad},{max(a[1], b[1]) + pad},{min(a[0], b[0]) - pad}"
    log("📄 Fetch: pulling every pub between start and finish from OpenStreetMap…")
    pubs_url = NOMINATIM + f"q=pub&viewbox={box}&bounded=1&limit=40&extratags=1"
    raw = [x for x in fetch_json([pubs_url]).get(pubs_url, []) if x.get("type") in ("pub", "bar", "biergarten")]
    if not raw:
        raise RuntimeError("No pubs found between those places - try places further apart")
    candidates, seen = [], set()
    vx, vy = b[0] - a[0], b[1] - a[1]
    seg = vx * vx + vy * vy or 1e-9
    for x in raw:
        name = x.get("name") or x["display_name"].split(",")[0]
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        lat, lon = float(x["lat"]), float(x["lon"])
        t = ((lat - a[0]) * vx + (lon - a[1]) * vy) / seg  # 0 = at start, 1 = at finish
        px, py = a[0] + t * vx, a[1] + t * vy
        tags = x.get("extratags") or {}
        candidates.append({
            "name": name, "lat": lat, "lon": lon, "t": t,
            "detour": ((lat - px) ** 2 + (lon - py) ** 2) ** 0.5,
            "address": ", ".join(x["display_name"].split(",")[1:3]).strip(),
            "website": tags.get("website") or tags.get("contact:website"),
            "osm_tags": " ".join(f"{k} {v}" for k, v in tags.items()).replace("_", " ").replace(":", " "),
        })
    # Keep the pubs closest to the straight line, spread along the route.
    candidates.sort(key=lambda p: p["detour"])
    candidates = candidates[: max(8, n_pubs * 3)]
    log(f"🍺 Found {len(raw)} pubs, shortlisted {len(candidates)} closest to your route")

    # 3. Search: websites for pubs OpenStreetMap doesn't have one for.
    missing = [p for p in candidates if not p["website"]]
    if missing:
        log(f"🔎 Search: finding websites for {len(missing)} pubs…")
        with ThreadPoolExecutor(8) as ex:
            sites = list(ex.map(lambda p: search_pub_site(p, end), missing))
        for p, (url, snip) in zip(missing, sites):
            p["website"], p["snippet"] = url, snip

    # 4. Fetch: read live pub websites and check requirements.
    urls = [p["website"] for p in candidates if p["website"]][:10]
    log(f"📄 Fetch: reading {len(urls)} pub websites to check {', '.join(reqs) or 'details'}…")
    resp = client.fetch.get_contents(urls, format="markdown", per_url_timeout_ms=20000)
    pages = {res.url: res.text if isinstance(res.text, str) else str(res.text) for res in resp.results}
    for p in candidates:
        text = pages.get(p["website"], "") + "\n" + p.get("snippet", "") + "\n" + p["osm_tags"]
        p["matches"] = {r: evidence_for(text, r) for r in reqs}
        p["score"] = sum(1 for v in p["matches"].values() if v) * 10 - p["detour"] * 1000
        p["hours"] = hours_snippet(text)

    chosen = []
    for r in reqs:
        best = max((p for p in candidates if p["matches"].get(r) and p not in chosen),
                   key=lambda p: p["score"], default=None)
        if best and len(chosen) < n_pubs:
            chosen.append(best)
    for p in sorted(candidates, key=lambda p: -p["score"]):
        if len(chosen) >= n_pubs:
            break
        if p not in chosen:
            chosen.append(p)
    chosen.sort(key=lambda p: p["t"])  # order along the route
    log(f"✅ Picked {len(chosen)} pubs: {', '.join(p['name'] for p in chosen)}")

    # Estimated walking times (exact ones available via the Agent button).
    prev, total_min, total_m = a, 0, 0
    for p in chosen:
        mins, m = walk_minutes(prev, (p["lat"], p["lon"]))
        p["walk"] = {"minutes": mins, "distance": f"~{m / 1000:.1f} km"}
        total_min, total_m, prev = total_min + mins, total_m + m, (p["lat"], p["lon"])
    mins, m = walk_minutes(prev, b)
    stops = [start] + [f"{p['lat']},{p['lon']}" for p in chosen] + [end]
    return {
        "start": start, "end": end, "requirements": reqs, "pubs": chosen,
        "final_leg": {"minutes": mins, "distance": f"~{m / 1000:.1f} km"},
        "total_minutes": total_min + mins, "total_distance": f"~{(total_m + m) / 1000:.1f} km",
        "maps_link": gmaps_link(stops), "stops": stops, "estimated": True,
    }


def exact_route(result, log=print):
    """TinyFish Agent: read exact walking times from Google Maps for a finished plan."""
    route = agent_walking_route(result["stops"], log)
    legs = route.get("legs", [])
    if not legs:
        return None
    for i, p in enumerate(result["pubs"]):
        if i < len(legs):
            p["walk"] = legs[i]
    if len(legs) > len(result["pubs"]):
        result["final_leg"] = legs[len(result["pubs"])]
    result.update(total_minutes=route.get("total_minutes"), total_distance=route.get("total_distance"),
                  estimated=False)
    return result


def create_monitors(pubs, requirements):
    """TinyFish Monitor: watch each pub's website daily for menu / hours / event changes."""
    import json
    import urllib.request
    made = []
    for p in pubs:
        if not p.get("website"):
            continue
        body = {
            "type": "fetch",
            "name": f"Pub crawl: {p['name']}"[:100],
            "config": {"url": p["website"], "format": "markdown"},
            "schedule_cron": "CRON_TZ=Europe/London 0 10 * * *",
            "purpose": (f"Changes to food or drink at {p['name']}: kitchen or opening hours, "
                        f"menu changes ({', '.join(requirements) or 'food'}), closures, "
                        "or new events like quiz nights or live music."),
        }
        req = urllib.request.Request(
            "https://agent.tinyfish.ai/v1/monitors", data=json.dumps(body).encode(), method="POST",
            headers={"X-API-Key": load_key(), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        made.append({"pub": p["name"], "url": p["website"], "id": data.get("id") or data.get("monitor", {}).get("id")})
    return made


if __name__ == "__main__":
    import json
    print(json.dumps(plan("UCL, Gower Street, London", "Camden Town, London", 3, "lunch, tea"), indent=1, default=str))
