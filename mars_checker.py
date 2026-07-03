"""
Mars Trek screenshot classifier — core logic.

This module holds everything that talks to Gemini and decides
approved / rejected. Both the local batch script and the Discord bot
import from here, so there's exactly one place the prompt/rules live.
"""
import google.generativeai as genai
from PIL import Image
import json
import re
import os
import time
import io
from google.api_core.exceptions import ResourceExhausted

# ============================================================
# CONFIG
# ============================================================
API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Fallback: load from a local .env file if present (format: GEMINI_API_KEY=xxxx)
if not API_KEY and os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as env_file:
        for line in env_file:
            if line.strip().startswith("GEMINI_API_KEY"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    API_KEY = parts[1].strip().strip("'\"")
                    break

if not API_KEY:
    raise RuntimeError(
        "No Gemini API key found. Set the GEMINI_API_KEY environment "
        "variable, or add GEMINI_API_KEY=your-key to a .env file next to "
        "this script."
    )

DISTANCE_MIN = 600.13
DISTANCE_MAX = 620.13
# ============================================================

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash-lite")

PROMPT = """This is a NASA Solar System Trek screenshot showing a Mars 
surface measurement of Olympus Mons.

Carefully examine every part of the image and extract the following:

--- SECTION 1: BROWSER ADDRESS BAR (top of the screen) ---
Look at the URL in the browser address bar at the very top of the screenshot.
- Is the address bar visible at all? (address_bar_visible: true/false)
- If visible, find the x= and y= parameters in the URL and extract their values.
  Example URL: trek.nasa.gov/mars/#v=0.1&x=-120.289&y=18.720&z=4...
  url_x would be -120.289 and url_y would be 18.720
- If the address bar is NOT visible or x= / y= are not in the URL, set url_x and url_y to null.
- IMPORTANT: if url_x or url_y is exactly 0 or 0.0, treat it as null — it means the value was not found.

--- SECTION 2: LEFT SEARCH/INFO PANEL ---
Look at the left side panel of the screenshot.
- Is the panel showing information about Olympus Mons?
  It should display the text "Olympus Mons" as a heading/title. (olympus_mons_in_search_tab: true/false)
- If Olympus Mons info IS shown, extract the Latitude and Longitude values 
  from the panel. They appear as labelled fields like "Latitude: 18.65275889"
- If the panel is NOT showing Olympus Mons info (or is closed/hidden), 
  set latitude and longitude both to null.
- NOTE: latitude, longitude, and olympus_mons_in_search_tab are all from 
  the SAME panel section. If the panel is not showing Olympus Mons, 
  all three will be false/null.

--- SECTION 3: DISTANCE RESULT POPUP ---
Look for the Distance Result popup/dialog box in the screenshot.
- Extract the terrain distance value shown in km. (terrain_distance_km)
- If the popup is not visible or the value cannot be read, set to null.

--- SECTION 4: MARS MAP ---
Look at the Mars surface map on the right side of the screenshot.
- Is there a yellow/orange measurement line drawn across the volcano? 
  (measurement_line_visible: true/false)

Return ONLY this JSON. No explanation. No markdown. Raw JSON only:
{
  "address_bar_visible": <true or false>,
  "url_x": <decimal number from URL x= parameter, or null>,
  "url_y": <decimal number from URL y= parameter, or null>,
  "olympus_mons_in_search_tab": <true or false>,
  "latitude": <decimal number from left panel, or null>,
  "longitude": <decimal number from left panel, or null>,
  "terrain_distance_km": <decimal number or null>,
  "measurement_line_visible": <true or false>
}"""


# ============================================================
# CLASSIFY — checks ALL conditions, collects ALL failures
# ============================================================
def classify(extracted: dict) -> tuple:
    """
    Checks every condition independently and returns ALL failures found.
    Returns (decision, combined_reasons, combined_messages, failures_list)
    """
    dist         = extracted.get("terrain_distance_km")
    search       = extracted.get("olympus_mons_in_search_tab")
    line         = extracted.get("measurement_line_visible")
    addr_visible = extracted.get("address_bar_visible")
    url_x        = extracted.get("url_x")
    url_y        = extracted.get("url_y")

    # treat 0 or 0.0 as null for coordinates (model sometimes returns 0 when not found)
    if url_x == 0 or url_x == 0.0:
        url_x = None
    if url_y == 0 or url_y == 0.0:
        url_y = None

    failures = []

    # ── Check 1: Address bar and URL coordinates ─────────────
    if not addr_visible:
        failures.append({
            "reason":  "address_bar_not_visible",
            "message": "The browser address bar is not visible in your screenshot — "
                       "make sure the full browser window including the URL bar at "
                       "the top is captured in your screenshot."
        })
    elif url_x is None or url_y is None:
        failures.append({
            "reason":  "url_coordinates_not_found",
            "message": "The x and y coordinates could not be found in the address bar URL — "
                       "make sure the full NASA Trek URL is visible and not truncated."
        })

    # ── Check 2: Distance readable ───────────────────────────
    if dist is None:
        failures.append({
            "reason":  "distance_not_visible",
            "message": "We couldn't read the terrain distance — make sure the "
                       "Distance Result panel is fully visible and not cut off."
        })
    # ── Check 3: Distance in range (only if readable) ────────
    elif not (DISTANCE_MIN <= dist <= DISTANCE_MAX):
        failures.append({
            "reason":  "distance_out_of_range",
            "message": f"Your measured diameter is {dist} km, which is outside the "
                       f"accepted range of {DISTANCE_MIN}–{DISTANCE_MAX} km. "
                       "Redraw the line across the full width of Olympus Mons."
        })

    # ── Check 4: Search panel — Olympus Mons + lat/lon ───────
    if not search:
        failures.append({
            "reason":  "search_panel_not_showing_olympus_mons",
            "message": "Your screenshot does not show the Olympus Mons information "
                       "in the left search panel — this also means the Latitude and "
                       "Longitude values are not visible. Make sure you have searched "
                       "for Olympus Mons and its details are shown in the left panel."
        })

    # ── Check 5: Measurement line ────────────────────────────
    if not line:
        failures.append({
            "reason":  "measurement_line_not_visible",
            "message": "The measurement line isn't visible on the Mars map — "
                       "make sure the line drawn across Olympus Mons is clearly "
                       "shown in your screenshot."
        })

    # ── Final decision ────────────────────────────────────────
    if failures:
        all_reasons  = " | ".join(f["reason"]  for f in failures)
        all_messages = " | ".join(f["message"] for f in failures)
        return "rejected", all_reasons, all_messages, failures

    return "approved", None, None, []


# ============================================================
# INTERNAL: run the model on a PIL image and build the result dict
# ============================================================
def _run_model(image: Image.Image, filename: str) -> dict:
    max_retries = 5
    backoff     = 15
    raw         = ""

    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                [PROMPT, image],
                generation_config={"temperature": 0.0}
            )
            raw = response.text.strip()
            break
        except ResourceExhausted as e:
            if attempt == max_retries - 1:
                raise e
            print(f"    [Rate limit hit — waiting {backoff}s before retry...]")
            time.sleep(backoff)
            backoff *= 2

    cleaned = re.sub(r"```json|```", "", raw).strip()
    match   = re.search(r"\{.*\}", cleaned, re.DOTALL)

    base = {
        "file":              filename,
        "parse_success":     False,
        "address_bar_visible": None,
        "url_x":             None,
        "url_y":             None,
        "olympus_mons":      None,
        "latitude":          None,
        "longitude":         None,
        "terrain_distance_km": None,
        "measurement_line_visible": None,
        "decision":          "rejected",
        "all_reasons":       "model_parse_failed",
        "all_messages":      "Model did not return valid JSON.",
        "failure_count":     1,
        "raw_output":        raw[:300],
    }

    if not match:
        return base

    try:
        data = json.loads(match.group())

        if data.get("url_x") in [0, 0.0]:
            data["url_x"] = None
        if data.get("url_y") in [0, 0.0]:
            data["url_y"] = None

        decision, all_reasons, all_messages, failures = classify(data)

        return {
            "file":                    filename,
            "parse_success":           True,
            "address_bar_visible":     data.get("address_bar_visible"),
            "url_x":                   data.get("url_x"),
            "url_y":                   data.get("url_y"),
            "olympus_mons":            data.get("olympus_mons_in_search_tab"),
            "latitude":                data.get("latitude"),
            "longitude":               data.get("longitude"),
            "terrain_distance_km":     data.get("terrain_distance_km"),
            "measurement_line_visible": data.get("measurement_line_visible"),
            "decision":                decision,
            "all_reasons":             all_reasons  or "",
            "all_messages":            all_messages or "",
            "failure_count":           len(failures),
            "raw_output":              raw[:300],
        }

    except json.JSONDecodeError:
        base["all_reasons"]  = "json_parse_error"
        base["all_messages"] = "Could not parse model response as JSON."
        base["raw_output"]   = raw[:300]
        return base


# ============================================================
# PUBLIC ENTRY POINTS
# ============================================================
def process_image_path(path: str) -> dict:
    """Classify an image already saved on disk."""
    image = Image.open(path).convert("RGB")
    return _run_model(image, os.path.basename(path))


def process_image_bytes(image_bytes: bytes, filename: str = "uploaded_image.png") -> dict:
    """Classify raw image bytes (e.g. a Discord attachment download)."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return _run_model(image, filename)
