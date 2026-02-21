"""
Muhurtha Calculator — Auspicious Timing Finder
Finds windows during a chosen day when all 5 Muhurtha rules pass simultaneously.
Completely independent of natal chart. Transit chart only.

v2: Monthly Scanner — scans all days in a month for all activities simultaneously.
"""

from flask import Flask, render_template, request, jsonify, send_file, session
import swisseph as swe
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from contextlib import contextmanager
import threading
import calendar
import json

app = Flask(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SIGNS = [
    "Aries","Taurus","Gemini","Cancer","Leo","Virgo",
    "Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"
]

NAKSHATRAS = [
    "Ashwini","Bharani","Krittika","Rohini","Mrigashira","Ardra",
    "Punarvasu","Pushya","Ashlesha","Magha","Purva Phalguni","Uttara Phalguni",
    "Hasta","Chitra","Swati","Vishakha","Anuradha","Jyeshtha",
    "Mula","Purva Ashadha","Uttara Ashadha","Shravana","Dhanishtha","Shatabhisha",
    "Purva Bhadrapada","Uttara Bhadrapada","Revati"
]

NAKSHATRA_LORDS = [
    "Ketu","Venus","Sun","Moon","Mars","Rahu",
    "Jupiter","Saturn","Mercury","Ketu","Venus","Sun",
    "Moon","Mars","Rahu","Jupiter","Saturn","Mercury",
    "Ketu","Venus","Sun","Moon","Mars","Rahu",
    "Jupiter","Saturn","Mercury"
]

# Sign lord (KP / Vedic)
SIGN_LORDS = {
    0: "Mars",    # Aries
    1: "Venus",   # Taurus
    2: "Mercury", # Gemini
    3: "Moon",    # Cancer
    4: "Sun",     # Leo
    5: "Mercury", # Virgo
    6: "Venus",   # Libra
    7: "Mars",    # Scorpio
    8: "Jupiter", # Sagittarius
    9: "Saturn",  # Capricorn
    10: "Saturn", # Aquarius
    11: "Jupiter" # Pisces
}

PLANET_IDS = {
    "Sun":     swe.SUN,
    "Moon":    swe.MOON,
    "Mars":    swe.MARS,
    "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER,
    "Venus":   swe.VENUS,
    "Saturn":  swe.SATURN,
    "Rahu":    swe.MEAN_NODE,
}

# Kaaraka presets
KAARAKA_PRESETS = [
    {"label": "Travel",               "icon": "✈️",  "planets": ["Moon", "Mercury"]},
    {"label": "Finance / Investment", "icon": "💰",  "planets": ["Jupiter", "Venus"]},
    {"label": "Job Application",      "icon": "💼",  "planets": ["Sun", "Mercury"]},
    {"label": "Hard Work / Toil",     "icon": "⚒️",  "planets": ["Saturn"]},
    {"label": "Construction",         "icon": "🏗️",  "planets": ["Saturn", "Mars"]},
    {"label": "Marriage",             "icon": "💍",  "planets": ["Venus"]},
    {"label": "Health / Recovery",    "icon": "🏥",  "planets": ["Sun", "Moon"]},
    {"label": "Legal Matters",        "icon": "⚖️",  "planets": ["Saturn", "Mars"]},
    {"label": "Education / Study",    "icon": "📚",  "planets": ["Mercury", "Jupiter"]},
    {"label": "Custom",               "icon": "⚙️",  "planets": []},
]

ALL_PLANETS = ["Sun","Moon","Mars","Mercury","Jupiter","Venus","Saturn","Rahu"]

# Thread safety for swisseph
swe_lock = threading.RLock()

def set_kp_ayanamsa():
    for name in ["SIDM_KP_OLD","SIDM_KP","SIDM_KRISHNAMURTI"]:
        if hasattr(swe, name):
            swe.set_sid_mode(getattr(swe, name))
            return
    swe.set_sid_mode(swe.SIDM_KRISHNAMURTI)

# ── Astro helpers ─────────────────────────────────────────────────────────────

def dt_to_jd(dt_utc):
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day,
                      dt_utc.hour + dt_utc.minute/60.0 + dt_utc.second/3600.0)

def wrap360(deg):
    return deg % 360.0

def sign_of(lon):
    return int(wrap360(lon) / 30.0)

def deg_in_sign(lon):
    return wrap360(lon) % 30.0

def planet_lon(dt_utc, planet_id):
    with swe_lock:
        set_kp_ayanamsa()
        jd = dt_to_jd(dt_utc)
        result = swe.calc_ut(jd, planet_id)
        trop = result[0][0]
        ayan = swe.get_ayanamsa_ut(jd)
        return wrap360(trop - ayan)

def ketu_lon(dt_utc):
    return wrap360(planet_lon(dt_utc, swe.MEAN_NODE) + 180.0)

def ascendant_lon(dt_utc, lat, lon_deg):
    with swe_lock:
        set_kp_ayanamsa()
        jd = dt_to_jd(dt_utc)
        cusps, ascmc = swe.houses(jd, lat, lon_deg, b'P')
        trop = ascmc[0]
        ayan = swe.get_ayanamsa_ut(jd)
        return wrap360(trop - ayan)

def nakshatra_lord_of(lon):
    nak_idx = int(wrap360(lon) / (360.0/27.0))
    return NAKSHATRA_LORDS[nak_idx], NAKSHATRAS[nak_idx]

def dms(deg):
    d = int(deg)
    m = int((deg - d) * 60)
    return f"{d:02d}°{m:02d}'"

def house_from_lagna(planet_sign_idx, lagna_sign_idx):
    return ((planet_sign_idx - lagna_sign_idx) % 12) + 1

def shorter_arc(sign_a, sign_b):
    fwd = ((sign_b - sign_a) % 12) + 1
    bwd = ((sign_a - sign_b) % 12) + 1
    return min(fwd, bwd)

def is_kendra_or_11(house):
    return house in (1, 4, 7, 10, 11)

def is_bad(house):
    return house in (6, 8, 12)

# ── Transit chart snapshot ────────────────────────────────────────────────────

def get_transit_chart(dt_utc, lat, lon_deg):
    asc_lon = ascendant_lon(dt_utc, lat, lon_deg)
    chart = {
        "asc_lon":  asc_lon,
        "asc_sign": sign_of(asc_lon),
        "asc_deg":  deg_in_sign(asc_lon),
    }
    for name, pid in PLANET_IDS.items():
        lon = planet_lon(dt_utc, pid)
        chart[name] = {"lon": lon, "sign": sign_of(lon), "deg": deg_in_sign(lon)}
    k_lon = ketu_lon(dt_utc)
    chart["Ketu"] = {"lon": k_lon, "sign": sign_of(k_lon), "deg": deg_in_sign(k_lon)}
    return chart

def get_planet_sign(chart, planet_name):
    if planet_name in chart:
        return chart[planet_name]["sign"]
    return None

# ── Rule checkers ─────────────────────────────────────────────────────────────

def check_rule(chart, lord_name, rule_num):
    asc_sign = chart["asc_sign"]
    lord_sign = get_planet_sign(chart, lord_name)
    if lord_sign is None:
        return False, None, f"{lord_name} not computed"
    house = house_from_lagna(lord_sign, asc_sign)
    passed = is_kendra_or_11(house) and not is_bad(house)
    status = "✅" if passed else "❌"
    detail = f"{lord_name} in {SIGNS[lord_sign]} (H{house}) {status}"
    return passed, house, detail

def check_rule4(chart, kaaraka_planets):
    asc_sign = chart["asc_sign"]
    details = []
    all_pass = True
    for pname in kaaraka_planets:
        psign = get_planet_sign(chart, pname)
        if psign is None:
            details.append(f"{pname}: not found ❌")
            all_pass = False
            continue
        house = house_from_lagna(psign, asc_sign)
        passed = is_kendra_or_11(house) and not is_bad(house)
        if not passed:
            all_pass = False
        status = "✅" if passed else "❌"
        details.append(f"{pname} in {SIGNS[psign]} (H{house}) {status}")
    return all_pass, details

def check_rule5(chart):
    asc_sign = chart["asc_sign"]
    asc_lon  = chart["asc_lon"]
    lagna_lord = SIGN_LORDS[asc_sign]
    nak_lord, nak_name = nakshatra_lord_of(asc_lon)
    ll_sign  = get_planet_sign(chart, lagna_lord)
    nl_sign  = get_planet_sign(chart, nak_lord)
    if ll_sign is None or nl_sign is None:
        return "avoid", "Unknown", "Could not compute"
    arc = shorter_arc(ll_sign, nl_sign)
    if arc in (1, 4, 7, 10):
        quality, label = "excellent", f"Kendra ({arc}) 🌟"
    elif arc in (2, 3, 11, 12):
        quality, label = "ok", f"Just OK ({arc}) ⚠️"
    else:
        quality, label = "avoid", f"Avoid ({arc}) ❌"
    detail = (f"Lagna lord {lagna_lord} in {SIGNS[ll_sign]} | "
              f"Nak lord ({nak_name}) {nak_lord} in {SIGNS[nl_sign]} | "
              f"Arc={arc} → {label}")
    return quality, label, detail

# ── Grade a moment ────────────────────────────────────────────────────────────

GRADE_MAP = {
    (5, "excellent"): ("🌟 Excellent Muhurtha", 5),
    (5, "ok"):        ("✨ Very Good Muhurtha",  4),
    (4, "excellent"): ("✅ Good Muhurtha",        3),
    (4, "ok"):        ("✅ Good Muhurtha",        3),
    (3, "excellent"): ("⚠️ Acceptable",           2),
    (3, "ok"):        ("⚠️ Acceptable",           2),
}

def grade_moment(rules_pass_count, r5_quality):
    key = (rules_pass_count, r5_quality)
    if key in GRADE_MAP:
        return GRADE_MAP[key]
    if rules_pass_count >= 4:
        return ("✅ Good Muhurtha", 3)
    if rules_pass_count == 3:
        return ("⚠️ Acceptable", 2)
    return ("❌ Avoid", 1)

def check_ketu_in_lagna(chart):
    asc_sign = chart["asc_sign"]
    ketu_sign = chart["Ketu"]["sign"]
    in_lagna = (ketu_sign == asc_sign)
    detail = (f"⚠️ Ketu in Lagna ({SIGNS[asc_sign]}) → Avoid"
              if in_lagna else
              f"Ketu in {SIGNS[ketu_sign]} (H{house_from_lagna(ketu_sign, asc_sign)}) — OK")
    return in_lagna, detail


def evaluate_moment(dt_utc, lat, lon_deg, kaaraka_planets):
    chart = get_transit_chart(dt_utc, lat, lon_deg)
    asc_sign = chart["asc_sign"]
    asc_deg  = chart["asc_deg"]
    lagna_lord = SIGN_LORDS[asc_sign]
    third_sign    = (asc_sign + 2) % 12
    eleventh_sign = (asc_sign + 10) % 12
    third_lord    = SIGN_LORDS[third_sign]
    eleventh_lord = SIGN_LORDS[eleventh_sign]

    ketu_in_lagna, ketu_detail = check_ketu_in_lagna(chart)
    if ketu_in_lagna:
        r4_pass, r4_details = check_rule4(chart, kaaraka_planets)
        r5_quality, r5_label, r5_detail = check_rule5(chart)
        return {
            "dt_utc": dt_utc,
            "asc_sign": SIGNS[asc_sign],
            "asc_deg":  dms(asc_deg),
            "lagna_lord": lagna_lord,
            "third_lord": third_lord,
            "eleventh_lord": eleventh_lord,
            "ketu_in_lagna": True,
            "ketu_detail": ketu_detail,
            "r1": {"pass": False, "detail": f"[Ketu override] {check_rule(chart, lagna_lord, 1)[2]}"},
            "r2": {"pass": False, "detail": f"[Ketu override] {check_rule(chart, third_lord, 2)[2]}"},
            "r3": {"pass": False, "detail": f"[Ketu override] {check_rule(chart, eleventh_lord, 3)[2]}"},
            "r4": {"pass": r4_pass, "details": r4_details},
            "r5": {"pass": False, "quality": r5_quality, "label": r5_label, "detail": r5_detail},
            "rules_pass": 0,
            "grade": "🚫 Avoid — Ketu in Lagna",
            "score": 0,
        }

    r1_pass, r1_house, r1_detail = check_rule(chart, lagna_lord, 1)
    r2_pass, r2_house, r2_detail = check_rule(chart, third_lord, 2)
    r3_pass, r3_house, r3_detail = check_rule(chart, eleventh_lord, 3)
    r4_pass, r4_details          = check_rule4(chart, kaaraka_planets)
    r5_quality, r5_label, r5_detail = check_rule5(chart)
    r5_pass = r5_quality in ("excellent", "ok")

    rules_pass = sum([r1_pass, r2_pass, r3_pass, r4_pass, r5_pass])
    grade, score = grade_moment(rules_pass, r5_quality)

    return {
        "dt_utc": dt_utc,
        "asc_sign": SIGNS[asc_sign],
        "asc_deg":  dms(asc_deg),
        "lagna_lord": lagna_lord,
        "third_lord": third_lord,
        "eleventh_lord": eleventh_lord,
        "ketu_in_lagna": False,
        "ketu_detail": ketu_detail,
        "r1": {"pass": r1_pass, "detail": r1_detail},
        "r2": {"pass": r2_pass, "detail": r2_detail},
        "r3": {"pass": r3_pass, "detail": r3_detail},
        "r4": {"pass": r4_pass, "details": r4_details},
        "r5": {"pass": r5_pass, "quality": r5_quality, "label": r5_label, "detail": r5_detail},
        "rules_pass": rules_pass,
        "grade": grade,
        "score": score,
    }


def evaluate_moment_multi(dt_utc, lat, lon_deg, all_kaaraka_list):
    """
    Evaluate a single moment for MULTIPLE kaaraka sets simultaneously.
    Computes the transit chart once, then tests each activity.
    Returns dict: {activity_label: result_dict}
    """
    try:
        chart = get_transit_chart(dt_utc, lat, lon_deg)
    except Exception:
        return None

    asc_sign = chart["asc_sign"]
    asc_deg  = chart["asc_deg"]
    lagna_lord = SIGN_LORDS[asc_sign]
    third_sign    = (asc_sign + 2) % 12
    eleventh_sign = (asc_sign + 10) % 12
    third_lord    = SIGN_LORDS[third_sign]
    eleventh_lord = SIGN_LORDS[eleventh_sign]

    ketu_in_lagna, ketu_detail = check_ketu_in_lagna(chart)

    # Pre-compute rules 1/2/3/5 (same for all activities)
    if ketu_in_lagna:
        r1_pass = r2_pass = r3_pass = False
        r1_detail = r2_detail = r3_detail = "[Ketu in Lagna]"
    else:
        r1_pass, _, r1_detail = check_rule(chart, lagna_lord, 1)
        r2_pass, _, r2_detail = check_rule(chart, third_lord, 2)
        r3_pass, _, r3_detail = check_rule(chart, eleventh_lord, 3)

    r5_quality, r5_label, r5_detail = check_rule5(chart)
    r5_pass = r5_quality in ("excellent", "ok") and not ketu_in_lagna

    results = {}
    for label, kaaraka_planets in all_kaaraka_list:
        if ketu_in_lagna:
            r4_pass, r4_details = check_rule4(chart, kaaraka_planets)
            results[label] = {
                "dt_utc": dt_utc,
                "asc_sign": SIGNS[asc_sign],
                "asc_deg": dms(asc_deg),
                "lagna_lord": lagna_lord,
                "ketu_in_lagna": True,
                "rules_pass": 0,
                "grade": "🚫 Avoid — Ketu in Lagna",
                "score": 0,
                "r5_quality": r5_quality,
            }
        else:
            r4_pass, r4_details = check_rule4(chart, kaaraka_planets)
            rules_pass = sum([r1_pass, r2_pass, r3_pass, r4_pass, r5_pass])
            grade, score = grade_moment(rules_pass, r5_quality)
            results[label] = {
                "dt_utc": dt_utc,
                "asc_sign": SIGNS[asc_sign],
                "asc_deg": dms(asc_deg),
                "lagna_lord": lagna_lord,
                "ketu_in_lagna": False,
                "rules_pass": rules_pass,
                "grade": grade,
                "score": score,
                "r5_quality": r5_quality,
                "r1_detail": r1_detail,
                "r2_detail": r2_detail,
                "r3_detail": r3_detail,
                "r4_details": r4_details,
                "r5_detail": r5_detail,
                "r5_label": r5_label,
            }
    return results


# ── Day Scanner ───────────────────────────────────────────────────────────────

def scan_day(date_str, lat, lon_deg, tz_name, kaaraka_planets, min_rules=3):
    tz = ZoneInfo(tz_name)
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, tzinfo=tz)
    day_end = day_start + timedelta(hours=24)

    STEP = timedelta(minutes=2)
    windows = []
    current_window = None

    dt = day_start
    while dt < day_end:
        dt_utc = dt.astimezone(timezone.utc)
        try:
            result = evaluate_moment(dt_utc, lat, lon_deg, kaaraka_planets)
        except Exception as e:
            dt += STEP
            continue

        passes = result["rules_pass"] >= min_rules

        if passes:
            if current_window is None:
                current_window = {
                    "start": dt,
                    "start_utc": dt_utc,
                    "best_score": result["score"],
                    "best_grade": result["grade"],
                    "best_result": result,
                    "scores": [result["score"]],
                }
            else:
                current_window["scores"].append(result["score"])
                if result["score"] > current_window["best_score"]:
                    current_window["best_score"] = result["score"]
                    current_window["best_grade"] = result["grade"]
                    current_window["best_result"] = result
        else:
            if current_window is not None:
                current_window["end"] = dt
                current_window["end_utc"] = dt_utc
                _finalize_window(current_window, tz)
                windows.append(current_window)
                current_window = None

        dt += STEP

    if current_window is not None:
        current_window["end"] = day_end
        current_window["end_utc"] = day_end.astimezone(timezone.utc)
        _finalize_window(current_window, tz)
        windows.append(current_window)

    return windows


def _finalize_window(w, tz):
    fmt = "%I:%M %p"
    w["start_str"] = w["start"].strftime(fmt)
    w["end_str"]   = w["end"].strftime(fmt)
    duration_mins  = int((w["end"] - w["start"]).total_seconds() / 60)
    w["duration"]  = f"{duration_mins} min" if duration_mins < 60 else f"{duration_mins//60}h {duration_mins%60}m"
    avg = sum(w["scores"]) / len(w["scores"])
    w["avg_score"] = round(avg, 2)
    r = w["best_result"]
    w["asc_sign"]      = r["asc_sign"]
    w["asc_deg"]       = r["asc_deg"]
    w["lagna_lord"]    = r["lagna_lord"]
    w["grade"]         = r["grade"]
    w["rules_pass"]    = r["rules_pass"]
    w["r1"]            = r["r1"]
    w["r2"]            = r["r2"]
    w["r3"]            = r["r3"]
    w["r4"]            = r["r4"]
    w["r5"]            = r["r5"]


# ── Monthly Scanner ───────────────────────────────────────────────────────────

def scan_month(year, month, lat, lon_deg, tz_name, custom_planets=None, top_n=5):
    """
    Scan ALL days in a month for ALL activity presets simultaneously.
    Returns dict: {activity_label: [top_n window dicts sorted by score]}
    Only keeps windows with score >= 4 (Excellent or Very Good).
    """
    tz = ZoneInfo(tz_name)
    num_days = calendar.monthrange(year, month)[1]
    STEP = timedelta(minutes=2)
    MIN_SCORE = 4  # Only Excellent (5) and Very Good (4)

    # Build list of (label, planets) for all presets
    all_kaaraka = []
    for preset in KAARAKA_PRESETS:
        if preset["label"] == "Custom":
            planets = custom_planets or []
            if planets:
                all_kaaraka.append(("Custom", planets))
        else:
            all_kaaraka.append((preset["label"], preset["planets"]))

    # Per-activity: track open windows and closed windows
    activity_windows = {label: [] for label, _ in all_kaaraka}
    activity_current = {label: None for label, _ in all_kaaraka}

    for day in range(1, num_days + 1):
        day_start = datetime(year, month, day, 0, 0, 0, tzinfo=tz)
        day_end = day_start + timedelta(hours=24)
        dt = day_start

        while dt < day_end:
            dt_utc = dt.astimezone(timezone.utc)
            multi_results = evaluate_moment_multi(dt_utc, lat, lon_deg, all_kaaraka)

            if multi_results is None:
                dt += STEP
                continue

            for label, result in multi_results.items():
                passes = result["score"] >= MIN_SCORE
                current = activity_current[label]

                if passes:
                    if current is None:
                        activity_current[label] = {
                            "start": dt,
                            "start_utc": dt_utc,
                            "best_score": result["score"],
                            "best_grade": result["grade"],
                            "best_result": result,
                            "scores": [result["score"]],
                            "day": day,
                        }
                    else:
                        current["scores"].append(result["score"])
                        if result["score"] > current["best_score"]:
                            current["best_score"] = result["score"]
                            current["best_grade"] = result["grade"]
                            current["best_result"] = result
                else:
                    if current is not None:
                        current["end"] = dt
                        current["end_utc"] = dt_utc
                        _finalize_month_window(current, tz, year, month)
                        activity_windows[label].append(current)
                        activity_current[label] = None

            dt += STEP

        # Close any open windows at end of day
        for label, current in activity_current.items():
            if current is not None and current.get("day") == day:
                current["end"] = day_end
                current["end_utc"] = day_end.astimezone(timezone.utc)
                _finalize_month_window(current, tz, year, month)
                activity_windows[label].append(current)
                activity_current[label] = None

    # Sort and pick top N per activity
    result_by_activity = {}
    for label, windows in activity_windows.items():
        sorted_windows = sorted(windows, key=lambda w: (-w["best_score"], -w.get("duration_mins", 0)))
        result_by_activity[label] = sorted_windows[:top_n]

    return result_by_activity


def _finalize_month_window(w, tz, year, month):
    fmt_time = "%I:%M %p"
    fmt_date = "%a, %d %b"
    w["start_str"]  = w["start"].strftime(fmt_time)
    w["end_str"]    = w["end"].strftime(fmt_time)
    w["date_str"]   = w["start"].strftime(fmt_date)
    w["date_full"]  = w["start"].strftime("%Y-%m-%d")
    duration_mins   = int((w["end"] - w["start"]).total_seconds() / 60)
    w["duration_mins"] = duration_mins
    w["duration"]   = f"{duration_mins} min" if duration_mins < 60 else f"{duration_mins//60}h {duration_mins%60}m"
    avg = sum(w["scores"]) / len(w["scores"])
    w["avg_score"]  = round(avg, 2)
    r = w["best_result"]
    w["asc_sign"]   = r["asc_sign"]
    w["lagna_lord"] = r["lagna_lord"]
    w["grade"]      = r["grade"]
    w["rules_pass"] = r["rules_pass"]


# ── Geocoding ─────────────────────────────────────────────────────────────────

tf = TimezoneFinder()

def get_location(place_name):
    try:
        geolocator = Nominatim(user_agent="muhurtha_app", timeout=10)
        loc = geolocator.geocode(place_name)
        if loc:
            tz_name = tf.timezone_at(lat=loc.latitude, lng=loc.longitude)
            return {"lat": loc.latitude, "lon": loc.longitude,
                    "tz": tz_name, "display": loc.address}
    except Exception:
        pass
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("muhurtha_index.html",
                           kaaraka_presets=KAARAKA_PRESETS,
                           all_planets=ALL_PLANETS)

@app.route("/compute", methods=["POST"])
def compute():
    date_str        = request.form.get("date")
    place_name      = request.form.get("place")
    kaaraka_label   = request.form.get("kaaraka_label", "Custom")
    kaaraka_p1      = request.form.get("kaaraka_p1", "").strip()
    kaaraka_p2      = request.form.get("kaaraka_p2", "").strip()
    kaaraka_p3      = request.form.get("kaaraka_p3", "").strip()
    min_rules       = int(request.form.get("min_rules", 3))
    manual_lat      = request.form.get("manual_lat", "").strip()
    manual_lon      = request.form.get("manual_lon", "").strip()

    kaaraka_planets = [p for p in [kaaraka_p1, kaaraka_p2, kaaraka_p3] if p and p != "None"]

    if manual_lat and manual_lon:
        try:
            lat = float(manual_lat)
            lon_deg = float(manual_lon)
            tz_name = tf.timezone_at(lat=lat, lng=lon_deg) or "UTC"
            location = {"lat": lat, "lon": lon_deg, "tz": tz_name, "display": place_name}
        except Exception as e:
            return render_template("muhurtha_error.html", error=str(e))
    else:
        location = get_location(place_name)
        if not location:
            return render_template("muhurtha_error.html",
                                   error=f"Could not geocode '{place_name}'. Try entering coordinates manually.")

    try:
        windows = scan_day(
            date_str, location["lat"], location["lon"],
            location["tz"], kaaraka_planets, min_rules
        )
        windows.sort(key=lambda w: (-w["best_score"], w["start"]))
        return render_template("muhurtha_results.html",
                               windows=windows,
                               date=date_str,
                               place=location["display"],
                               tz=location["tz"],
                               kaaraka_label=kaaraka_label,
                               kaaraka_planets=kaaraka_planets,
                               min_rules=min_rules)
    except Exception as e:
        import traceback
        return render_template("muhurtha_error.html", error=str(e) + "\n" + traceback.format_exc())


@app.route("/monthly")
def monthly():
    """Monthly scanner page."""
    return render_template("muhurtha_monthly.html",
                           kaaraka_presets=KAARAKA_PRESETS,
                           all_planets=ALL_PLANETS)


@app.route("/scan_month", methods=["POST"])
def scan_month_route():
    year        = int(request.form.get("year"))
    month       = int(request.form.get("month"))
    place_name  = request.form.get("place")
    manual_lat  = request.form.get("manual_lat", "").strip()
    manual_lon  = request.form.get("manual_lon", "").strip()
    custom_p1   = request.form.get("custom_p1", "").strip()
    custom_p2   = request.form.get("custom_p2", "").strip()
    custom_p3   = request.form.get("custom_p3", "").strip()
    custom_planets = [p for p in [custom_p1, custom_p2, custom_p3] if p and p != "None"]

    if manual_lat and manual_lon:
        try:
            lat = float(manual_lat)
            lon_deg = float(manual_lon)
            tz_name = tf.timezone_at(lat=lat, lng=lon_deg) or "UTC"
            location = {"lat": lat, "lon": lon_deg, "tz": tz_name, "display": place_name}
        except Exception as e:
            return render_template("muhurtha_error.html", error=str(e))
    else:
        location = get_location(place_name)
        if not location:
            return render_template("muhurtha_error.html",
                                   error=f"Could not geocode '{place_name}'.")

    try:
        results = scan_month(
            year, month,
            location["lat"], location["lon"],
            location["tz"],
            custom_planets=custom_planets,
            top_n=5
        )
        month_name = calendar.month_name[month]
        num_days = calendar.monthrange(year, month)[1]

        # Attach icon/label metadata
        presets_meta = {p["label"]: p for p in KAARAKA_PRESETS}

        # Serialise results into session for Excel export
        serialisable = {}
        for label, slots in results.items():
            serialisable[label] = []
            for w in slots:
                sw = {k: v for k, v in w.items()
                      if k not in ("start", "end", "start_utc", "end_utc", "best_result", "scores")}
                serialisable[label].append(sw)

        session["last_monthly_results"] = json.dumps(serialisable)
        session["last_monthly_meta"] = json.dumps({
            "year": year, "month": month, "month_name": month_name,
            "place": location["display"], "tz": location["tz"],
            "custom_planets": custom_planets,
        })

        return render_template("muhurtha_monthly_results.html",
                               results=results,
                               presets_meta=presets_meta,
                               year=year,
                               month=month,
                               month_name=month_name,
                               num_days=num_days,
                               place=location["display"],
                               tz=location["tz"],
                               custom_planets=custom_planets)
    except Exception as e:
        import traceback
        return render_template("muhurtha_error.html", error=str(e) + "\n" + traceback.format_exc())


@app.route("/export_excel", methods=["GET"])
def export_excel():
    """Generate and serve the Excel export from the last monthly scan stored in session."""
    from muhurtha_excel import generate_excel

    raw_results = session.get("last_monthly_results")
    raw_meta    = session.get("last_monthly_meta")

    if not raw_results or not raw_meta:
        return render_template("muhurtha_error.html",
                               error="No scan results found. Please run a monthly scan first, then click Export.")

    results = json.loads(raw_results)
    meta    = json.loads(raw_meta)

    stream = generate_excel(
        results=results,
        month_name=meta["month_name"],
        year=meta["year"],
        place=meta["place"],
        tz=meta["tz"],
        custom_planets=meta["custom_planets"],
    )

    filename = f"Muhurtha_{meta['month_name']}_{meta['year']}.xlsx"
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    app.secret_key = "muhurtha_secret_key_2026"
    app.run(debug=True, port=5002)
