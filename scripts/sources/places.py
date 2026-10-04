"""Google Places API (New) - only for reviews (Stage 7, Ahmad's choice).

Why: without a Google login, Maps in Playwright shows a "limited view" with no reviews. We never log in and never
bypass it. The official API gives up to 5 reviews per place (Google picks them; we sort lowest rating first).

Key: GOOGLE_PLACES_API_KEY in .env. Limit: places_api.max_calls_per_day in config/policy.yaml, counted with
events (to stay inside Google's free monthly usage). Reviewer names are dropped at once, never stored.
The key is never printed.
"""
from __future__ import annotations

import httpx

import db

SEARCH = "https://places.googleapis.com/v1/places:searchText"
DETAILS = "https://places.googleapis.com/v1/places/{}"
TIMEOUT = 20.0
DEFAULT_MAX_CALLS = 30
EVENT = "places_call"


class PlacesError(db.DeskError):
    pass


def api_key() -> str | None:
    return db.load_env().get("GOOGLE_PLACES_API_KEY") or None


def calls_left(desk: db.Desk) -> int:
    cap = int((desk.policy.get("places_api") or {}).get("max_calls_per_day") or DEFAULT_MAX_CALLS)
    since = db.start_of_today_utc(desk.tz, desk.now()).isoformat(timespec="seconds")
    return cap - desk.store.count_events(EVENT, since)


def _call(client: httpx.Client, method: str, url: str, key: str, mask: str, json: dict | None = None) -> dict:
    try:
        resp = client.request(method, url, json=json, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask})
    except httpx.HTTPError as exc:
        raise PlacesError(f"cannot reach Google Places API ({type(exc).__name__})") from exc
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise PlacesError(f"Places API error HTTP {resp.status_code}: {msg.replace(key, '***')[:200]}")
    return resp.json()


def _same_business(a: str, b: str) -> bool:
    ka, kb = db.name_key(a or ""), db.name_key(b or "")
    return bool(ka and kb) and (db.similar_names(ka, kb) or ka in kb or kb in ka)


def reviews_for(desk: db.Desk, name: str, address: str | None, key: str,
                transport: httpx.BaseTransport | None = None) -> dict:
    """Finds the place by name + address and returns {rating, review_count, reviews:[{stars,date,approx,text}]}.
    Two API calls (search = place id only, details = rating + reviews). Each call is counted."""
    if calls_left(desk) < 2:
        raise db.CapReached("daily Places API limit reached (places_api.max_calls_per_day in config/policy.yaml)")
    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        desk.store.add_event(EVENT, "role:places", None, {"kind": "search", "name": name})
        found = _call(client, "POST", SEARCH, key, "places.id,places.displayName",
                      json={"textQuery": f"{name} {address or ''}".strip(), "maxResultCount": 1})
        places = found.get("places") or []
        if not places:
            return {"reviews": [], "note": "place not found in Places API"}
        place = places[0]
        if not _same_business(name, (place.get("displayName") or {}).get("text", "")):
            return {"reviews": [], "note": "Places API found a different business; reviews not used"}
        desk.store.add_event(EVENT, "role:places", None, {"kind": "details", "name": name})
        data = _call(client, "GET", DETAILS.format(place["id"]), key, "rating,userRatingCount,reviews")
    reviews = []
    for r in data.get("reviews") or []:            # authorAttribution (reviewer name) is ignored on purpose
        text = ((r.get("originalText") or {}).get("text") or (r.get("text") or {}).get("text") or "").strip()
        if not text:
            continue
        reviews.append({"stars": r.get("rating"), "date": (r.get("publishTime") or "")[:10] or None,
                        "approx": False, "text": " ".join(text.split())})
    reviews.sort(key=lambda r: r["stars"] if r["stars"] is not None else 9)
    return {"rating": data.get("rating"), "review_count": data.get("userRatingCount"), "reviews": reviews}
