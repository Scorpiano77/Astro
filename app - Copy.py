from flask import Flask, render_template, request, Response, jsonify
import swisseph as swe
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
import csv
import io
import threading
from contextlib import contextmanager

app = Flask(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
         "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
SIGN_INDEX = {name: i for i, name in enumerate(SIGNS)}

NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni",
    "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha",
    "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha",
    "Purva Bhadrapada", "Uttara Bhadrapada", "Revati"
]

NAKSHATRA_LORDS = [
    "Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu",
    "Jupiter", "Saturn", "Mercury", "Ketu", "Venus", "Sun",
    "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury",
    "Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu",
    "Jupiter", "Saturn", "Mercury"
]

HOUSE_LORDS = [
    "Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury",
    "Venus", "Mars", "Jupiter", "Saturn", "Saturn", "Jupiter"
]

PLANET_MAP = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mars": swe.MARS,
    "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER,
    "Venus": swe.VENUS,
    "Saturn": swe.SATURN,
    "Rahu": swe.MEAN_NODE,
    "Uranus": getattr(swe, "URANUS", None),
    "Neptune": getattr(swe, "NEPTUNE", None),
}

# Filter out None-valued planets (in case some constants missing)
PLANET_MAP = {k: v for k, v in PLANET_MAP.items() if v is not None}

PANAPHARA_WINDOWS = [(2.5, 5.0), (10.0, 12.5), (17.5, 20.0), (25.0, 27.5)]
APOKLIMA_WINDOWS = [(5.0, 7.5), (12.5, 15.0), (20.0, 22.5), (27.5, 30.0)]

POPULAR_CITIES = [
    "New York, USA", "London, UK", "Tokyo, Japan", "Mumbai, India",
    "Delhi, India", "Bangalore, India", "Chennai, India", "Kolkata, India",
    "Singapore", "Dubai, UAE", "Sydney, Australia", "Toronto, Canada"
]

# ============================================================================
# SWEP/SIDEREAL HELPERS & THREAD LOCK
# ============================================================================

# Because swisseph sidereal mode is global, serialize access with a lock.
swe_lock = threading.Lock()

# Candidate SIDM constant names per user-friendly ayanamsa name.
AYANAMSA_CANDIDATES = {
    "KP_old": [
        "SIDM_KP_OLD", "SIDM_KP", "SIDM_KRISHNAMURTI",
        "SE_SIDM_KP_OLD", "SE_SIDM_KP", "SE_SIDM_KRISHNAMURTI"
    ],
    "Lahiri": [
        "SIDM_LAHIRI", "SIDM_DELUCE_LAHIRI", "SE_SIDM_LAHIRI", "SIDM_DEFAULT"
    ],
    "Fagan-Bradley": [
        "SIDM_FAGAN_BRADLEY", "SIDM_FAGAN", "SE_SIDM_FAGAN_BRADLEY"
    ],
    "Tropical": [
        # Some builds use SIDM_0 or SIDM_TROPICAL; try several names.
        "SIDM_0", "SIDM_TROPICAL", "SE_SIDM_TROPICAL"
    ]
}

def set_ayanamsa_by_name(name):
    """
    Try to set swisseph sidereal mode according to a friendly name.
    Returns (True, used_constant_name) on success, (False, message) on failure.
    """
    if not name:
        return False, "No ayanamsa name provided"

    name = str(name)
    if name not in AYANAMSA_CANDIDATES:
        return False, f"Unknown ayanamsa: {name}"

    candidates = AYANAMSA_CANDIDATES[name]
    for cand in candidates:
        if hasattr(swe, cand):
            mode = getattr(swe, cand)
            try:
                swe.set_sid_mode(mode)
                return True, cand
            except Exception as ex:
                # try next candidate
                continue

    # If none of the named constants exist, return failure
    return False, f"No swisseph SIDM constant available for '{name}' on this system"

@contextmanager
def use_ayanamsa(name):
    """Context manager that acquires swe_lock and sets ayanamsa (if available)."""
    with swe_lock:
        ok, msg = set_ayanamsa_by_name(name)
        yield (ok, msg)
        # Note: we do not attempt to restore prior SIDM mode; swisseph typically
        # expects a single global sidereal mode. If you need per-request isolation,
        # run computations in separate processes.

# ============================================================================
# HELPER FUNCTIONS (ASTRO & DATETIME)
# ============================================================================

def wrap360(deg):
    """Wrap angle to [0, 360)"""
    return deg % 360.0

def body_lon_sid(dt_utc, body_id):
    """Get sidereal longitude of a body at given UTC datetime"""
    jd = swe.julday(dt_utc.year, dt_utc.month, dt_utc.day,
                    dt_utc.hour + dt_utc.minute/60.0 + dt_utc.second/3600.0)
    result = swe.calc_ut(jd, body_id)
    lon_trop = result[0][0]
    ayan = swe.get_ayanamsa_ut(jd)
    lon_sid = wrap360(lon_trop - ayan)
    return lon_sid

def sign_and_deg(lon_sid):
    """Convert sidereal longitude to (sign_index, degree_in_sign)"""
    lon_sid = wrap360(lon_sid)
    sign_idx = int(lon_sid / 30.0)
    deg = lon_sid - (sign_idx * 30.0)
    return sign_idx, deg

def calculate_ascendant(dt_utc, lat, lon):
    """Calculate sidereal ascendant"""
    jd = swe.julday(dt_utc.year, dt_utc.month, dt_utc.day,
                    dt_utc.hour + dt_utc.minute/60.0 + dt_utc.second/3600.0)
    cusps, ascmc = swe.houses(jd, lat, lon, b'P')
    asc_trop = ascmc[0]
    ayan = swe.get_ayanamsa_ut(jd)
    asc_sid = wrap360(asc_trop - ayan)
    return asc_sid

def get_nakshatra_from_longitude(lon_sid):
    """Get nakshatra index, name, and lord from sidereal longitude"""
    lon_sid = wrap360(lon_sid)
    nak_idx = int(lon_sid / (360.0 / 27.0))
    nak_name = NAKSHATRAS[nak_idx]
    nak_lord = NAKSHATRA_LORDS[nak_idx]
    return nak_idx, nak_name, nak_lord

def calculate_navamsa_sign(lon_sid):
    """Calculate D9 (Navamsa) sign index from sidereal longitude"""
    lon_sid = wrap360(lon_sid)
    sign_idx = int(lon_sid / 30.0)
    deg_in_sign = lon_sid - (sign_idx * 30.0)
    navamsa_pada = int(deg_in_sign / 3.333333333)
    
    if sign_idx % 4 == 0:  # Movable
        d9_sign = (navamsa_pada) % 12
    elif sign_idx % 4 == 1:  # Fixed
        d9_sign = (navamsa_pada + 9) % 12
    elif sign_idx % 4 == 2:  # Dual
        d9_sign = (navamsa_pada + 6) % 12
    else:  # Movable
        d9_sign = (navamsa_pada + 3) % 12
    
    return d9_sign

def degree_in_panaphara_window(deg):
    """Check if degree is in any Panaphara window"""
    for a, b in PANAPHARA_WINDOWS:
        if a <= deg <= b:
            return True
    return False

def degree_in_apoklima_window(deg):
    """Check if degree is in any Apoklima window"""
    for a, b in APOKLIMA_WINDOWS:
        if a <= deg <= b:
            return True
    return False

def dms_short(deg):
    """Format degree as DD°MM'"""
    d = int(deg)
    m = int((deg - d) * 60)
    return f"{d:02d}°{m:02d}'"

def window_str(a, b):
    """Format window as DD°MM' - DD°MM'"""
    return f"{dms_short(a)} - {dms_short(b)}"

def fmt_dt(dt):
    """Format datetime as YYYY-MM-DD HH:MM"""
    return dt.strftime("%Y-%m-%d %H:%M")

def get_location_info(place_name):
    """Get latitude, longitude, and timezone for a place"""
    geolocator = Nominatim(user_agent="astro_transit_app")
    location = geolocator.geocode(place_name)
    
    if not location:
        raise ValueError(f"Could not find location: {place_name}")
    
    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lat=location.latitude, lng=location.longitude)
    
    if not tz_name:
        raise ValueError(f"Could not determine timezone for: {place_name}")
    
    return {
        "place": place_name,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": tz_name
    }

def find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
    """Find all intervals where is_true_fn returns True"""
    intervals = []
    current = start_utc
    in_interval = False
    interval_start = None
    
    while current <= end_utc:
        if is_true_fn(current):
            if not in_interval:
                in_interval = True
                interval_start = current
        else:
            if in_interval:
                in_interval = False
                intervals.append((interval_start, current - timedelta(seconds=step_seconds)))
        
        current += timedelta(seconds=step_seconds)
    
    if in_interval:
        intervals.append((interval_start, end_utc))
    
    # Refine intervals
    refined_intervals = []
    for start, end in intervals:
        refined_start = start
        refined_end = end
        
        # Refine start
        test_time = start - timedelta(seconds=step_seconds)
        while test_time >= start_utc:
            if not is_true_fn(test_time):
                break
            refined_start = test_time
            test_time -= timedelta(seconds=refine_to_seconds)
        
        # Refine end
        test_time = end + timedelta(seconds=step_seconds)
        while test_time <= end_utc:
            if not is_true_fn(test_time):
                break
            refined_end = test_time
            test_time += timedelta(seconds=refine_to_seconds)
        
        refined_intervals.append((refined_start, refined_end))
    
    return refined_intervals

# ============================================================================
# RULE COMPUTATION FUNCTIONS
# ============================================================================

def compute_rule1_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #1: Jupiter/Venus/2L in Panaphara houses (2/5/8/11) + Panaphara degrees"""
    rows = []
    panaphara_house_signs = set(natal_chart["PanapharaHouseSigns"])
    second_lord = natal_chart.get("SecondLord")
    
    bodies_to_check = [
        ("Jupiter", swe.JUPITER),
        ("Venus", swe.VENUS),
    ]
    
    if second_lord and second_lord in PLANET_MAP:
        bodies_to_check.append((second_lord, PLANET_MAP[second_lord]))
    
    for body_name, body_id in bodies_to_check:
        for a, b in PANAPHARA_WINDOWS:
            for sign_idx in sorted(panaphara_house_signs):
                def is_true_fn(dt_utc, _a=a, _b=b, _sidx=sign_idx, _bid=body_id):
                    lon = body_lon_sid(dt_utc, _bid)
                    sidx, deg = sign_and_deg(lon)
                    return (sidx == _sidx) and (_a <= deg <= _b)

                for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                    s_loc = s_utc.astimezone(tz)
                    e_loc = e_utc.astimezone(tz)
                    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                    house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                    
                    rows.append({
                        "category": "Money",
                        "rule": "Rule #1",
                        "body": body_name,
                        "start": s_loc,
                        "end": e_loc,
                        "start_str": fmt_dt(s_loc),
                        "end_str": fmt_dt(e_loc),
                        "sign": SIGNS[sign_idx],
                        "house": house_num,
                        "window": window_str(a, b),
                        "description": f"{body_name} in Panaphara house ({house_num}) + Panaphara degree → Money",
                    })
    
    return rows

def compute_rule2_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #2: Moon in PP planet nakshatra + Panaphara degree"""
    rows = []
    
    panaphara_planets = natal_chart.get("PanapharaPlanets", [])
    if not panaphara_planets:
        return rows
    
    pp_planet_names = set()
    for pp in panaphara_planets:
        pp_planet_names.add(pp["name"])
    
    for planet_name in pp_planet_names:
        nak_indices = [i for i, lord in enumerate(NAKSHATRA_LORDS) if lord == planet_name]
        
        for nak_idx in nak_indices:
            nak_name = NAKSHATRAS[nak_idx]
            nak_start_lon = nak_idx * (360.0 / 27.0)
            nak_end_lon = (nak_idx + 1) * (360.0 / 27.0)
            
            for a, b in PANAPHARA_WINDOWS:
                def is_true_fn(dt_utc, _nak_start=nak_start_lon, _nak_end=nak_end_lon, _a=a, _b=b):
                    moon_lon = body_lon_sid(dt_utc, swe.MOON)
                    moon_lon = wrap360(moon_lon)
                    in_nakshatra = (_nak_start <= moon_lon < _nak_end)
                    _, moon_deg = sign_and_deg(moon_lon)
                    in_panaphara_deg = (_a <= moon_deg <= _b)
                    return in_nakshatra and in_panaphara_deg
                
                for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                    s_loc = s_utc.astimezone(tz)
                    e_loc = e_utc.astimezone(tz)
                    moon_lon_start = body_lon_sid(s_utc, swe.MOON)
                    moon_sidx, moon_deg = sign_and_deg(moon_lon_start)
                    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                    house_num = ((moon_sidx - natal_asc_idx) % 12) + 1
                    
                    rows.append({
                        "category": "Money",
                        "rule": "Rule #2",
                        "body": "Moon",
                        "start": s_loc,
                        "end": e_loc,
                        "start_str": fmt_dt(s_loc),
                        "end_str": fmt_dt(e_loc),
                        "sign": SIGNS[moon_sidx],
                        "house": house_num,
                        "window": window_str(a, b),
                        "description": f"Moon in {nak_name} (owned by {planet_name} - PP planet) + Panaphara degree → Money",
                    })
    
    return rows

def compute_rule3_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #3: D9 Dispositor of 2L in Panaphara houses + degrees"""
    rows = []
    
    d9_info = natal_chart.get("D9_SecondLord_Dispositor")
    if not d9_info:
        return rows
    
    dispositor_name = d9_info["dispositor"]
    if dispositor_name not in PLANET_MAP:
        return rows
    
    dispositor_id = PLANET_MAP[dispositor_name]
    panaphara_house_signs = set(natal_chart["PanapharaHouseSigns"])
    
    for a, b in PANAPHARA_WINDOWS:
        for sign_idx in sorted(panaphara_house_signs):
            def is_true_fn(dt_utc, _a=a, _b=b, _sidx=sign_idx):
                lon = body_lon_sid(dt_utc, dispositor_id)
                sidx, deg = sign_and_deg(lon)
                return (sidx == _sidx) and (_a <= deg <= _b)

            for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                s_loc = s_utc.astimezone(tz)
                e_loc = e_utc.astimezone(tz)
                natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                
                rows.append({
                    "category": "Money",
                    "rule": "Rule #3",
                    "body": dispositor_name,
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": SIGNS[sign_idx],
                    "house": house_num,
                    "window": window_str(a, b),
                    "description": f"{dispositor_name} (D9 dispositor of 2L) in Panaphara house ({house_num}) + degree → Money",
                })
    
    return rows

def compute_rule4_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #4: Venus and Uranus both in Panaphara houses + degrees (Crorepati Yoga)"""
    rows = []
    
    panaphara_house_signs = set(natal_chart["PanapharaHouseSigns"])
    
    for a, b in PANAPHARA_WINDOWS:
        for sign_idx_v in sorted(panaphara_house_signs):
            for sign_idx_u in sorted(panaphara_house_signs):
                def is_true_fn(dt_utc, _a=a, _b=b, _sidx_v=sign_idx_v, _sidx_u=sign_idx_u):
                    venus_lon = body_lon_sid(dt_utc, swe.VENUS)
                    uranus_lon = body_lon_sid(dt_utc, swe.URANUS) if hasattr(swe, "URANUS") else None
                    v_sidx, v_deg = sign_and_deg(venus_lon)
                    u_sidx, u_deg = sign_and_deg(uranus_lon) if uranus_lon is not None else (None, None)
                    
                    venus_ok = (v_sidx == _sidx_v) and (_a <= v_deg <= _b)
                    uranus_ok = (u_sidx == _sidx_u) and (_a <= u_deg <= _b) if uranus_lon is not None else False
                    
                    return venus_ok and uranus_ok

                for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                    s_loc = s_utc.astimezone(tz)
                    e_loc = e_utc.astimezone(tz)
                    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                    house_v = ((sign_idx_v - natal_asc_idx) % 12) + 1
                    house_u = ((sign_idx_u - natal_asc_idx) % 12) + 1
                    
                    rows.append({
                        "category": "Money",
                        "rule": "Rule #4",
                        "body": "Venus/Uranus",
                        "start": s_loc,
                        "end": e_loc,
                        "start_str": fmt_dt(s_loc),
                        "end_str": fmt_dt(e_loc),
                        "sign": f"{SIGNS[sign_idx_v]} / {SIGNS[sign_idx_u]}",
                        "house": f"{house_v} / {house_u}",
                        "window": window_str(a, b),
                        "description": f"Venus in {house_v}H + Uranus in {house_u}H (both Panaphara + degree) → Crorepati Yoga",
                    })
    
    return rows

def compute_rule5_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #5: 5L and 9L in same sign OR mutual aspect (7th from each other)"""
    rows = []
    
    fifth_lord = natal_chart.get("FifthLord")
    ninth_lord = natal_chart.get("NinthLord")
    
    if not fifth_lord or not ninth_lord:
        return rows
    
    if fifth_lord not in PLANET_MAP or ninth_lord not in PLANET_MAP:
        return rows
    
    fifth_lord_id = PLANET_MAP[fifth_lord]
    ninth_lord_id = PLANET_MAP[ninth_lord]
    
    # Same sign
    for sign_idx in range(12):
        def is_true_fn(dt_utc, _sidx=sign_idx):
            lon5 = body_lon_sid(dt_utc, fifth_lord_id)
            lon9 = body_lon_sid(dt_utc, ninth_lord_id)
            s5, _ = sign_and_deg(lon5)
            s9, _ = sign_and_deg(lon9)
            return s5 == _sidx and s9 == _sidx
        
        for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
            s_loc = s_utc.astimezone(tz)
            e_loc = e_utc.astimezone(tz)
            natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
            house_num = ((sign_idx - natal_asc_idx) % 12) + 1
            
            rows.append({
                "category": "Money",
                "rule": "Rule #5",
                "body": f"{fifth_lord}/{ninth_lord}",
                "start": s_loc,
                "end": e_loc,
                "start_str": fmt_dt(s_loc),
                "end_str": fmt_dt(e_loc),
                "sign": SIGNS[sign_idx],
                "house": house_num,
                "window": "Same Sign",
                "description": f"5L ({fifth_lord}) and 9L ({ninth_lord}) in same sign ({SIGNS[sign_idx]}) → Money",
            })
    
    # Mutual 7th aspect
    for sign_idx_5 in range(12):
        sign_idx_9 = (sign_idx_5 + 6) % 12
        
        def is_true_fn(dt_utc, _s5=sign_idx_5, _s9=sign_idx_9):
            lon5 = body_lon_sid(dt_utc, fifth_lord_id)
            lon9 = body_lon_sid(dt_utc, ninth_lord_id)
            s5, _ = sign_and_deg(lon5)
            s9, _ = sign_and_deg(lon9)
            return s5 == _s5 and s9 == _s9
        
        for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
            s_loc = s_utc.astimezone(tz)
            e_loc = e_utc.astimezone(tz)
            natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
            house5 = ((sign_idx_5 - natal_asc_idx) % 12) + 1
            house9 = ((sign_idx_9 - natal_asc_idx) % 12) + 1
            
            rows.append({
                "category": "Money",
                "rule": "Rule #5",
                "body": f"{fifth_lord}/{ninth_lord}",
                "start": s_loc,
                "end": e_loc,
                "start_str": fmt_dt(s_loc),
                "end_str": fmt_dt(e_loc),
                "sign": f"{SIGNS[sign_idx_5]} / {SIGNS[sign_idx_9]}",
                "house": f"{house5} / {house9}",
                "window": "7th Aspect",
                "description": f"5L ({fifth_lord}) and 9L ({ninth_lord}) in mutual 7th aspect → Money",
            })
    
    return rows

def compute_rule6_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #6: 2L in Panaphara degree (any sign)"""
    rows = []
    
    second_lord = natal_chart.get("SecondLord")
    if not second_lord or second_lord not in PLANET_MAP:
        return rows
    
    second_lord_id = PLANET_MAP[second_lord]
    
    for a, b in PANAPHARA_WINDOWS:
        for sign_idx in range(12):
            def is_true_fn(dt_utc, _a=a, _b=b, _sidx=sign_idx):
                lon = body_lon_sid(dt_utc, second_lord_id)
                sidx, deg = sign_and_deg(lon)
                return (sidx == _sidx) and (_a <= deg <= _b)

            for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                s_loc = s_utc.astimezone(tz)
                e_loc = e_utc.astimezone(tz)
                natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                
                rows.append({
                    "category": "Money",
                    "rule": "Rule #6",
                    "body": second_lord,
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": SIGNS[sign_idx],
                    "house": house_num,
                    "window": window_str(a, b),
                    "description": f"2L ({second_lord}) in Panaphara degree → Money",
                })
    
    return rows

def compute_rule7_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #7: Lucky Days - Apoklima lords (3L/6L/9L/12L) in apoklima houses"""
    rows = []
    
    lucky_planets = natal_chart.get("LuckyPlanets", [])
    extremely_lucky_planets = natal_chart.get("ExtremelyLuckyPlanets", [])
    
    apoklima_house_signs = set(natal_chart["ApoklimaHouseSigns"])
    
    # Lucky planets (in apoklima house, not in apoklima degree)
    for planet_info in lucky_planets:
        planet_name = planet_info["name"]
        if planet_name not in PLANET_MAP:
            continue
        
        planet_id = PLANET_MAP[planet_name]
        
        for sign_idx in sorted(apoklima_house_signs):
            def is_true_fn(dt_utc, _sidx=sign_idx, _pid=planet_id):
                lon = body_lon_sid(dt_utc, _pid)
                sidx, deg = sign_and_deg(lon)
                in_sign = (sidx == _sidx)
                not_in_apoklima_deg = not degree_in_apoklima_window(deg)
                return in_sign and not_in_apoklima_deg

            for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                s_loc = s_utc.astimezone(tz)
                e_loc = e_utc.astimezone(tz)
                natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                
                rows.append({
                    "category": "Money",
                    "rule": "Rule #7 (Lucky)",
                    "body": planet_name,
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": SIGNS[sign_idx],
                    "house": house_num,
                    "window": "Apoklima House",
                    "description": f"{planet_name} ({planet_info['lord_type']}) in Apoklima house ({house_num}) → Lucky Day",
                })
    
    # Extremely lucky planets (in apoklima house + apoklima degree)
    for planet_info in extremely_lucky_planets:
        planet_name = planet_info["name"]
        if planet_name not in PLANET_MAP:
            continue
        
        planet_id = PLANET_MAP[planet_name]
        
        for a, b in APOKLIMA_WINDOWS:
            for sign_idx in sorted(apoklima_house_signs):
                def is_true_fn(dt_utc, _a=a, _b=b, _sidx=sign_idx, _pid=planet_id):
                    lon = body_lon_sid(dt_utc, _pid)
                    sidx, deg = sign_and_deg(lon)
                    return (sidx == _sidx) and (_a <= deg <= _b)

                for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                    s_loc = s_utc.astimezone(tz)
                    e_loc = e_utc.astimezone(tz)
                    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                    house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                    
                    rows.append({
                        "category": "Money",
                        "rule": "Rule #7 (Extremely Lucky)",
                        "body": planet_name,
                        "start": s_loc,
                        "end": e_loc,
                        "start_str": fmt_dt(s_loc),
                        "end_str": fmt_dt(e_loc),
                        "sign": SIGNS[sign_idx],
                        "house": house_num,
                        "window": window_str(a, b),
                        "description": f"{planet_name} ({planet_info['lord_type']}) in Apoklima house ({house_num}) + Apoklima degree → Extremely Lucky Day",
                    })
    
    return rows

def compute_rule8_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Money Rule #8: Transit planet touches natal PP planet degree (±1° orb)"""
    rows = []
    
    panaphara_planets = natal_chart.get("PanapharaPlanets", [])
    if not panaphara_planets:
        return rows
    
    ORB = 1.0
    
    for pp_info in panaphara_planets:
        pp_name = pp_info["name"]
        pp_sign_idx = pp_info["sign_index"]
        pp_deg = pp_info["degree"]
        pp_sign = pp_info["sign"]
        
        min_deg = pp_deg - ORB
        max_deg = pp_deg + ORB
        
        # Check all transiting planets
        for transit_name, transit_id in PLANET_MAP.items():
            def is_true_fn(dt_utc, _pp_sign=pp_sign_idx, _min=min_deg, _max=max_deg, _tid=transit_id):
                lon = body_lon_sid(dt_utc, _tid)
                sidx, deg = sign_and_deg(lon)
                if sidx != _pp_sign:
                    return False
                return (_min <= deg <= _max)

            for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                s_loc = s_utc.astimezone(tz)
                e_loc = e_utc.astimezone(tz)
                natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                house_num = ((pp_sign_idx - natal_asc_idx) % 12) + 1
                
                rows.append({
                    "category": "Money",
                    "rule": "Rule #8",
                    "body": transit_name,
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": pp_sign,
                    "house": house_num,
                    "window": f"{dms_short(pp_deg)} ±1°",
                    "description": f"{transit_name} touches natal {pp_name} degree ({dms_short(pp_deg)}) in {pp_sign} → Money",
                })
    
    return rows

def compute_loss1_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Loss #1: Saturn/Venus/Ketu in natal apoklima houses (3/6/9/12) + apoklima degrees → Loss"""
    rows = []
    apoklima_house_signs = set(natal_chart["ApoklimaHouseSigns"])
    
    bodies_to_check = [
        ("Saturn", swe.SATURN),
        ("Venus", swe.VENUS),
    ]
    
    for body_name, body_id in bodies_to_check:
        for a, b in APOKLIMA_WINDOWS:
            for sign_idx in sorted(apoklima_house_signs):
                def is_true_fn(dt_utc, _a=a, _b=b, _sidx=sign_idx, _bid=body_id):
                    lon = body_lon_sid(dt_utc, _bid)
                    sidx, deg = sign_and_deg(lon)
                    return (sidx == _sidx) and (_a <= deg <= _b)

                for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                    s_loc = s_utc.astimezone(tz)
                    e_loc = e_utc.astimezone(tz)
                    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                    house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                    
                    rows.append({
                        "category": "Loss",
                        "rule": "Loss #1",
                        "body": body_name,
                        "start": s_loc,
                        "end": e_loc,
                        "start_str": fmt_dt(s_loc),
                        "end_str": fmt_dt(e_loc),
                        "sign": SIGNS[sign_idx],
                        "house": house_num,
                        "window": window_str(a, b),
                        "description": f"{body_name} in Apoklima house ({house_num}) + Apoklima degree → Loss",
                    })
    
    # Ketu
    for a, b in APOKLIMA_WINDOWS:
        for sign_idx in sorted(apoklima_house_signs):
            def is_true_fn(dt_utc, _a=a, _b=b, _sidx=sign_idx):
                rahu_lon = body_lon_sid(dt_utc, swe.MEAN_NODE)
                ketu_lon = wrap360(rahu_lon + 180.0)
                sidx, deg = sign_and_deg(ketu_lon)
                return (sidx == _sidx) and (_a <= deg <= _b)

            for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                s_loc = s_utc.astimezone(tz)
                e_loc = e_utc.astimezone(tz)
                natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                house_num = ((sign_idx - natal_asc_idx) % 12) + 1
                
                rows.append({
                    "category": "Loss",
                    "rule": "Loss #1",
                    "body": "Ketu",
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": SIGNS[sign_idx],
                    "house": house_num,
                    "window": window_str(a, b),
                    "description": f"Ketu in Apoklima house ({house_num}) + Apoklima degree → Loss",
                })
    
    return rows

def compute_loss2_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Loss #2: Sun in natal 6L/8L/12L nakshatras + apoklima degrees → Loss"""
    rows = []
    
    sixth_lord = natal_chart.get("SixthLord")
    eighth_lord = natal_chart.get("EighthLord")
    twelfth_lord = natal_chart.get("TwelfthLord")
    
    malefic_lords = set()
    if sixth_lord:
        malefic_lords.add(sixth_lord)
    if eighth_lord:
        malefic_lords.add(eighth_lord)
    if twelfth_lord:
        malefic_lords.add(twelfth_lord)
    
    if not malefic_lords:
        return rows
    
    for lord_name in malefic_lords:
        nak_indices = [i for i, nak_lord in enumerate(NAKSHATRA_LORDS) if nak_lord == lord_name]
        
        for nak_idx in nak_indices:
            nak_name = NAKSHATRAS[nak_idx]
            nak_start_lon = nak_idx * (360.0 / 27.0)
            nak_end_lon = (nak_idx + 1) * (360.0 / 27.0)
            
            for a, b in APOKLIMA_WINDOWS:
                def is_true_fn(dt_utc, _nak_start=nak_start_lon, _nak_end=nak_end_lon, _a=a, _b=b):
                    sun_lon = body_lon_sid(dt_utc, swe.SUN)
                    sun_lon = wrap360(sun_lon)
                    in_nakshatra = (_nak_start <= sun_lon < _nak_end)
                    _, sun_deg = sign_and_deg(sun_lon)
                    in_apoklima_deg = (_a <= sun_deg <= _b)
                    return in_nakshatra and in_apoklima_deg
                
                for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
                    s_loc = s_utc.astimezone(tz)
                    e_loc = e_utc.astimezone(tz)
                    sun_lon_start = body_lon_sid(s_utc, swe.SUN)
                    sun_sidx, sun_deg = sign_and_deg(sun_lon_start)
                    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
                    house_num = ((sun_sidx - natal_asc_idx) % 12) + 1
                    
                    lord_type = ""
                    if lord_name == sixth_lord:
                        lord_type = "6L"
                    if lord_name == eighth_lord:
                        lord_type = "8L" if not lord_type else lord_type + "/8L"
                    if lord_name == twelfth_lord:
                        lord_type = "12L" if not lord_type else lord_type + "/12L"
                    
                    rows.append({
                        "category": "Loss",
                        "rule": "Loss #2",
                        "body": "Sun",
                        "start": s_loc,
                        "end": e_loc,
                        "start_str": fmt_dt(s_loc),
                        "end_str": fmt_dt(e_loc),
                        "sign": SIGNS[sun_sidx],
                        "house": house_num,
                        "window": window_str(a, b),
                        "description": f"Sun in {nak_name} (owned by {lord_name} - {lord_type}) + Apoklima degree → Loss",
                    })
    
    return rows

def compute_loss3_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Loss #3: Moon in 6th nakshatra from natal Moon OR Sun in 6th nakshatra from natal Sun → Loss"""
    rows = []
    
    # Moon's 6th nakshatra
    natal_moon_lon = natal_chart["Moon"]["longitude"]
    natal_moon_nak_idx = int(natal_moon_lon / (360.0 / 27.0))
    sixth_nak_from_moon = (natal_moon_nak_idx + 5) % 27  # 6th nakshatra (0-indexed, so +5)
    sixth_nak_name_moon = NAKSHATRAS[sixth_nak_from_moon]
    
    nak_start_lon = sixth_nak_from_moon * (360.0 / 27.0)
    nak_end_lon = (sixth_nak_from_moon + 1) * (360.0 / 27.0)
    
    def is_moon_in_sixth_nak(dt_utc, _nak_start=nak_start_lon, _nak_end=nak_end_lon):
        moon_lon = body_lon_sid(dt_utc, swe.MOON)
        moon_lon = wrap360(moon_lon)
        return (_nak_start <= moon_lon < _nak_end)
    
    for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_moon_in_sixth_nak, step_seconds, refine_to_seconds):
        s_loc = s_utc.astimezone(tz)
        e_loc = e_utc.astimezone(tz)
        moon_lon_start = body_lon_sid(s_utc, swe.MOON)
        moon_sidx, moon_deg = sign_and_deg(moon_lon_start)
        natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
        house_num = ((moon_sidx - natal_asc_idx) % 12) + 1
        
        natal_moon_nak_name = natal_chart["Moon"]["nakshatra"]
        
        rows.append({
            "category": "Loss",
            "rule": "Loss #3",
            "body": "Moon",
            "start": s_loc,
            "end": e_loc,
            "start_str": fmt_dt(s_loc),
            "end_str": fmt_dt(e_loc),
            "sign": SIGNS[moon_sidx],
            "house": house_num,
            "window": "Full Nakshatra",
            "description": f"Moon in {sixth_nak_name_moon} (6th nakshatra from natal Moon's {natal_moon_nak_name}) → Loss",
        })
    
    # Sun's 6th nakshatra
    natal_sun_lon = natal_chart["Sun"]["longitude"]
    natal_sun_nak_idx = int(natal_sun_lon / (360.0 / 27.0))
    sixth_nak_from_sun = (natal_sun_nak_idx + 5) % 27  # 6th nakshatra (0-indexed, so +5)
    sixth_nak_name_sun = NAKSHATRAS[sixth_nak_from_sun]
    
    nak_start_lon_sun = sixth_nak_from_sun * (360.0 / 27.0)
    nak_end_lon_sun = (sixth_nak_from_sun + 1) * (360.0 / 27.0)
    
    def is_sun_in_sixth_nak(dt_utc, _nak_start=nak_start_lon_sun, _nak_end=nak_end_lon_sun):
        sun_lon = body_lon_sid(dt_utc, swe.SUN)
        sun_lon = wrap360(sun_lon)
        return (_nak_start <= sun_lon < _nak_end)
    
    for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_sun_in_sixth_nak, step_seconds, refine_to_seconds):
        s_loc = s_utc.astimezone(tz)
        e_loc = e_utc.astimezone(tz)
        sun_lon_start = body_lon_sid(s_utc, swe.SUN)
        sun_sidx, sun_deg = sign_and_deg(sun_lon_start)
        natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
        house_num = ((sun_sidx - natal_asc_idx) % 12) + 1
        
        natal_sun_nak_name = natal_chart["Sun"]["nakshatra"]
        
        rows.append({
            "category": "Loss",
            "rule": "Loss #3",
            "body": "Sun",
            "start": s_loc,
            "end": e_loc,
            "start_str": fmt_dt(s_loc),
            "end_str": fmt_dt(e_loc),
            "sign": SIGNS[sun_sidx],
            "house": house_num,
            "window": "Full Nakshatra",
            "description": f"Sun in {sixth_nak_name_sun} (6th nakshatra from natal Sun's {natal_sun_nak_name}) → Loss",
        })
    
    return rows

def compute_loss4_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Loss #4: Moon conjunct natal Neptune degree (±1° orb) → Loss"""
    rows = []
    
    natal_neptune_deg = natal_chart["Neptune"]["degree"]
    natal_neptune_sign_idx = natal_chart["Neptune"]["sign_index"]
    natal_neptune_sign = natal_chart["Neptune"]["sign"]
    
    ORB = 1.0
    min_deg = natal_neptune_deg - ORB
    max_deg = natal_neptune_deg + ORB
    
    def is_true_fn(dt_utc, _natal_sign_idx=natal_neptune_sign_idx, _min_deg=min_deg, _max_deg=max_deg):
        moon_lon = body_lon_sid(dt_utc, swe.MOON)
        sidx, deg = sign_and_deg(moon_lon)
        if sidx != _natal_sign_idx:
            return False
        return (_min_deg <= deg <= _max_deg)
    
    for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
        s_loc = s_utc.astimezone(tz)
        e_loc = e_utc.astimezone(tz)
        moon_lon_start = body_lon_sid(s_utc, swe.MOON)
        moon_sidx, moon_deg = sign_and_deg(moon_lon_start)
        natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
        house_num = ((moon_sidx - natal_asc_idx) % 12) + 1
        
        rows.append({
            "category": "Loss",
            "rule": "Loss #4",
            "body": "Moon",
            "start": s_loc,
            "end": e_loc,
            "start_str": fmt_dt(s_loc),
            "end_str": fmt_dt(e_loc),
            "sign": natal_neptune_sign,
            "house": house_num,
            "window": f"{dms_short(natal_neptune_deg)} ±1°",
            "description": f"Moon conjunct Natal Neptune degree ({dms_short(natal_neptune_deg)}) in {natal_neptune_sign} → Loss",
        })
    
    return rows

def compute_loss5_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Loss #5: Natal 6L and 8L in 6/8 relationship (sign-only) → EXPENSE"""
    rows = []
    
    sixth_lord = natal_chart.get("SixthLord")
    eighth_lord = natal_chart.get("EighthLord")
    
    if not sixth_lord or not eighth_lord:
        return rows
    
    if sixth_lord not in PLANET_MAP or eighth_lord not in PLANET_MAP:
        return rows
    
    sixth_lord_id = PLANET_MAP[sixth_lord]
    eighth_lord_id = PLANET_MAP[eighth_lord]
    
    def is_true_fn(dt_utc):
        lon6 = body_lon_sid(dt_utc, sixth_lord_id)
        lon8 = body_lon_sid(dt_utc, eighth_lord_id)
        s6, _ = sign_and_deg(lon6)
        s8, _ = sign_and_deg(lon8)
        
        # Calculate house positions
        house_6_to_8 = ((s8 - s6) % 12) + 1
        house_8_to_6 = ((s6 - s8) % 12) + 1
        
        # Check if they are in 6/8 relationship
        return (house_6_to_8 == 6 and house_8_to_6 == 8) or (house_6_to_8 == 8 and house_8_to_6 == 6)
    
    for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
        s_loc = s_utc.astimezone(tz)
        e_loc = e_utc.astimezone(tz)
        
        lon6_s = body_lon_sid(s_utc, sixth_lord_id)
        lon8_s = body_lon_sid(s_utc, eighth_lord_id)
        s6_idx, _ = sign_and_deg(lon6_s)
        s8_idx, _ = sign_and_deg(lon8_s)
        
        natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
        house6 = ((s6_idx - natal_asc_idx) % 12) + 1
        house8 = ((s8_idx - natal_asc_idx) % 12) + 1
        
        rows.append({
            "category": "Expense",
            "rule": "Loss #5",
            "body": f"{sixth_lord}/{eighth_lord}",
            "start": s_loc,
            "end": e_loc,
            "start_str": fmt_dt(s_loc),
            "end_str": fmt_dt(e_loc),
            "sign": f"{SIGNS[s6_idx]} / {SIGNS[s8_idx]}",
            "house": f"{house6} / {house8}",
            "window": "6/8 Relation",
            "description": f"6L ({sixth_lord}) and 8L ({eighth_lord}) in 6/8 relationship → EXPENSE",
        })
    
    return rows

def compute_loss6_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    """Loss #6: Sun in natal 3/6/8/12 houses → Loss (Exception: PP planet nakshatras → Money)"""
    rows = []
    
    natal_asc_idx = natal_chart["Ascendant"]["sign_index"]
    
    # 3/6/8/12 house signs
    dusthana_signs = [
        (natal_asc_idx + 2) % 12,  # 3rd house
        (natal_asc_idx + 5) % 12,  # 6th house
        (natal_asc_idx + 7) % 12,  # 8th house
        (natal_asc_idx + 11) % 12, # 12th house
    ]
    
    # PP planet nakshatras (exception) - excluding Swati
    panaphara_planets = natal_chart.get("PanapharaPlanets", [])
    second_lord = natal_chart.get("SecondLord")
    fifth_lord = natal_chart.get("FifthLord")
    ninth_lord = natal_chart.get("NinthLord")
    
    pp_planet_names = set()
    for pp in panaphara_planets:
        pp_planet_names.add(pp["name"])
    if second_lord:
        pp_planet_names.add(second_lord)
    if fifth_lord:
        pp_planet_names.add(fifth_lord)
    if ninth_lord:
        pp_planet_names.add(ninth_lord)
    
    pp_nak_indices = set()
    for planet_name in pp_planet_names:
        nak_indices = [i for i, lord in enumerate(NAKSHATRA_LORDS) if lord == planet_name]
        pp_nak_indices.update(nak_indices)
    
    # Exclude Swati (14th nakshatra, index 14)
    swati_idx = 14
    if swati_idx in pp_nak_indices:
        pp_nak_indices.remove(swati_idx)
    
    for sign_idx in dusthana_signs:
        def is_true_fn(dt_utc, _sidx=sign_idx):
            sun_lon = body_lon_sid(dt_utc, swe.SUN)
            sidx, _ = sign_and_deg(sun_lon)
            return sidx == _sidx
        
        for s_utc, e_utc in find_true_intervals(start_utc, end_utc, is_true_fn, step_seconds, refine_to_seconds):
            s_loc = s_utc.astimezone(tz)
            e_loc = e_utc.astimezone(tz)
            sun_lon_start = body_lon_sid(s_utc, swe.SUN)
            sun_sidx, sun_deg = sign_and_deg(sun_lon_start)
            house_num = ((sun_sidx - natal_asc_idx) % 12) + 1
            
            # Check if Sun is in PP planet nakshatra
            sun_nak_idx = int(sun_lon_start / (360.0 / 27.0))
            sun_nak_name = NAKSHATRAS[sun_nak_idx]
            sun_nak_lord = NAKSHATRA_LORDS[sun_nak_idx]
            
            if sun_nak_idx in pp_nak_indices:
                # Exception: Money
                rows.append({
                    "category": "Money",
                    "rule": "Loss #6 (Exception)",
                    "body": "Sun",
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": SIGNS[sun_sidx],
                    "house": house_num,
                    "window": "Full Sign",
                    "description": f"Sun in {house_num}H ({SIGNS[sun_sidx]}) in {sun_nak_name} (owned by {sun_nak_lord} - PP planet) → Money",
                })
            else:
                # Loss
                rows.append({
                    "category": "Loss",
                    "rule": "Loss #6",
                    "body": "Sun",
                    "start": s_loc,
                    "end": e_loc,
                    "start_str": fmt_dt(s_loc),
                    "end_str": fmt_dt(e_loc),
                    "sign": SIGNS[sun_sidx],
                    "house": house_num,
                    "window": "Full Sign",
                    "description": f"Sun in {house_num}H ({SIGNS[sun_sidx]}) in {sun_nak_name} → Loss",
                })
    
    return rows

def compute_all_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds, enabled_rules):
    """Compute all transit rows based on enabled rules"""
    rows = []
    
    # Money Rules
    if enabled_rules.get("enable_rule1"):
        rows.extend(compute_rule1_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule2"):
        rows.extend(compute_rule2_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule3"):
        rows.extend(compute_rule3_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule4"):
        rows.extend(compute_rule4_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule5"):
        rows.extend(compute_rule5_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule6"):
        rows.extend(compute_rule6_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule7"):
        rows.extend(compute_rule7_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_rule8"):
        rows.extend(compute_rule8_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    # Loss Rules
    if enabled_rules.get("enable_loss1"):
        rows.extend(compute_loss1_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_loss2"):
        rows.extend(compute_loss2_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_loss3"):
        rows.extend(compute_loss3_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_loss4"):
        rows.extend(compute_loss4_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_loss5"):
        rows.extend(compute_loss5_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    if enabled_rules.get("enable_loss6"):
        rows.extend(compute_loss6_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds))
    
    # Sort by start time
    rows.sort(key=lambda r: r["start"])
    
    return rows

# ============================================================================
# NATAL CHART COMPUTATION
# ============================================================================

def compute_natal_chart(birth_dt_utc, birth_lat, birth_lon):
    """Compute natal chart with all required information"""
    
    # Calculate ascendant
    asc_lon = calculate_ascendant(birth_dt_utc, birth_lat, birth_lon)
    asc_sign_idx, asc_deg = sign_and_deg(asc_lon)
    
    natal_chart = {
        "Ascendant": {
            "longitude": asc_lon,
            "sign_index": asc_sign_idx,
            "sign": SIGNS[asc_sign_idx],
            "degree": asc_deg,
        }
    }
    
    # Calculate planetary positions
    for planet_name, planet_id in PLANET_MAP.items():
        if planet_name == "Rahu":
            lon = body_lon_sid(birth_dt_utc, planet_id)
            sign_idx, deg = sign_and_deg(lon)
            nak_idx, nak_name, nak_lord = get_nakshatra_from_longitude(lon)
            d9_sign_idx = calculate_navamsa_sign(lon)
            
            natal_chart[planet_name] = {
                "longitude": lon,
                "sign_index": sign_idx,
                "sign": SIGNS[sign_idx],
                "degree": deg,
                "nakshatra": nak_name,
                "nakshatra_lord": nak_lord,
                "d9_sign_index": d9_sign_idx,
                "d9_sign": SIGNS[d9_sign_idx],
            }
            
            # Calculate Ketu (opposite of Rahu)
            ketu_lon = wrap360(lon + 180.0)
            ketu_sign_idx, ketu_deg = sign_and_deg(ketu_lon)
            ketu_nak_idx, ketu_nak_name, ketu_nak_lord = get_nakshatra_from_longitude(ketu_lon)
            ketu_d9_sign_idx = calculate_navamsa_sign(ketu_lon)
            
            natal_chart["Ketu"] = {
                "longitude": ketu_lon,
                "sign_index": ketu_sign_idx,
                "sign": SIGNS[ketu_sign_idx],
                "degree": ketu_deg,
                "nakshatra": ketu_nak_name,
                "nakshatra_lord": ketu_nak_lord,
                "d9_sign_index": ketu_d9_sign_idx,
                "d9_sign": SIGNS[ketu_d9_sign_idx],
            }
        else:
            lon = body_lon_sid(birth_dt_utc, planet_id)
            sign_idx, deg = sign_and_deg(lon)
            nak_idx, nak_name, nak_lord = get_nakshatra_from_longitude(lon)
            d9_sign_idx = calculate_navamsa_sign(lon)
            
            natal_chart[planet_name] = {
                "longitude": lon,
                "sign_index": sign_idx,
                "sign": SIGNS[sign_idx],
                "degree": deg,
                "nakshatra": nak_name,
                "nakshatra_lord": nak_lord,
                "d9_sign_index": d9_sign_idx,
                "d9_sign": SIGNS[d9_sign_idx],
            }
    
    # Calculate house lords
    natal_chart["SecondLord"] = HOUSE_LORDS[(asc_sign_idx + 1) % 12]
    natal_chart["ThirdLord"] = HOUSE_LORDS[(asc_sign_idx + 2) % 12]
    natal_chart["FifthLord"] = HOUSE_LORDS[(asc_sign_idx + 4) % 12]
    natal_chart["SixthLord"] = HOUSE_LORDS[(asc_sign_idx + 5) % 12]
    natal_chart["EighthLord"] = HOUSE_LORDS[(asc_sign_idx + 7) % 12]
    natal_chart["NinthLord"] = HOUSE_LORDS[(asc_sign_idx + 8) % 12]
    natal_chart["TwelfthLord"] = HOUSE_LORDS[(asc_sign_idx + 11) % 12]
    
    # Calculate Panaphara houses (2, 5, 8, 11)
    panaphara_house_signs = [
        (asc_sign_idx + 1) % 12,  # 2nd house
        (asc_sign_idx + 4) % 12,  # 5th house
        (asc_sign_idx + 7) % 12,  # 8th house
        (asc_sign_idx + 10) % 12, # 11th house
    ]
    natal_chart["PanapharaHouseSigns"] = panaphara_house_signs
    
    # Calculate Apoklima houses (3, 6, 9, 12)
    apoklima_house_signs = [
        (asc_sign_idx + 2) % 12,  # 3rd house
        (asc_sign_idx + 5) % 12,  # 6th house
        (asc_sign_idx + 8) % 12,  # 9th house
        (asc_sign_idx + 11) % 12, # 12th house
    ]
    natal_chart["ApoklimaHouseSigns"] = apoklima_house_signs
    
    # Find Panaphara planets (planets in Panaphara houses + Panaphara degrees)
    panaphara_planets = []
    for planet_name in ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]:
        if planet_name not in natal_chart:
            continue
        planet_info = natal_chart[planet_name]
        planet_sign_idx = planet_info["sign_index"]
        planet_deg = planet_info["degree"]
        
        if planet_sign_idx in panaphara_house_signs and degree_in_panaphara_window(planet_deg):
            house_num = ((planet_sign_idx - asc_sign_idx) % 12) + 1
            panaphara_planets.append({
                "name": planet_name,
                "sign_index": planet_sign_idx,
                "sign": planet_info["sign"],
                "degree": planet_deg,
                "house": house_num,
            })
    
    natal_chart["PanapharaPlanets"] = panaphara_planets
    
    # Calculate D9 dispositor of 2L
    second_lord = natal_chart["SecondLord"]
    if second_lord in natal_chart:
        second_lord_d9_sign = natal_chart[second_lord]["d9_sign_index"]
        d9_dispositor = HOUSE_LORDS[second_lord_d9_sign]
        natal_chart["D9_SecondLord_Dispositor"] = {
            "second_lord": second_lord,
            "d9_sign": SIGNS[second_lord_d9_sign],
            "dispositor": d9_dispositor,
        }
    
    # Calculate Lucky Planets (3L, 6L, 9L, 12L)
    third_lord = natal_chart["ThirdLord"]
    sixth_lord = natal_chart["SixthLord"]
    ninth_lord = natal_chart["NinthLord"]
    twelfth_lord = natal_chart["TwelfthLord"]
    
    lucky_planets = []
    extremely_lucky_planets = []
    
    for lord_name, lord_type in [(third_lord, "3L"), (sixth_lord, "6L"), (ninth_lord, "9L"), (twelfth_lord, "12L")]:
        if lord_name in natal_chart:
            lord_info = natal_chart[lord_name]
            lord_sign_idx = lord_info["sign_index"]
            lord_deg = lord_info["degree"]
            
            if lord_sign_idx in apoklima_house_signs:
                if degree_in_apoklima_window(lord_deg):
                    extremely_lucky_planets.append({
                        "name": lord_name,
                        "lord_type": lord_type,
                        "sign_index": lord_sign_idx,
                        "sign": lord_info["sign"],
                        "degree": lord_deg,
                    })
                else:
                    lucky_planets.append({
                        "name": lord_name,
                        "lord_type": lord_type,
                        "sign_index": lord_sign_idx,
                        "sign": lord_info["sign"],
                        "degree": lord_deg,
                    })
    
    natal_chart["LuckyPlanets"] = lucky_planets
    natal_chart["ExtremelyLuckyPlanets"] = extremely_lucky_planets
    
    return natal_chart

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def index():
    ayanamsa_options = list(AYANAMSA_CANDIDATES.keys())
    default_ayanamsa = "KP_old" if "KP_old" in ayanamsa_options else ayanamsa_options[0]
    return render_template('index.html',
                           popular_cities=POPULAR_CITIES,
                           ayanamsa_options=ayanamsa_options,
                           default_ayanamsa=default_ayanamsa)

@app.route('/compute', methods=['POST'])
def compute():
    try:
        # Get form data
        birth_date = request.form.get('birth_date')
        birth_time = request.form.get('birth_time')
        birth_place = request.form.get('birth_place')
        
        start_date = request.form.get('start_date')
        start_time = request.form.get('start_time')
        end_date = request.form.get('end_date')
        end_time = request.form.get('end_time')
        
        transit_place = request.form.get('transit_place')

        # Ayanamsa selection
        selected_ayanamsa = request.form.get('ayanamsa')

        # Get enabled rules
        enabled_rules = {
            "enable_rule1": request.form.get('enable_rule1') == 'on',
            "enable_rule2": request.form.get('enable_rule2') == 'on',
            "enable_rule3": request.form.get('enable_rule3') == 'on',
            "enable_rule4": request.form.get('enable_rule4') == 'on',
            "enable_rule5": request.form.get('enable_rule5') == 'on',
            "enable_rule6": request.form.get('enable_rule6') == 'on',
            "enable_rule7": request.form.get('enable_rule7') == 'on',
            "enable_rule8": request.form.get('enable_rule8') == 'on',
            "enable_loss1": request.form.get('enable_loss1') == 'on',
            "enable_loss2": request.form.get('enable_loss2') == 'on',
            "enable_loss3": request.form.get('enable_loss3') == 'on',
            "enable_loss4": request.form.get('enable_loss4') == 'on',
            "enable_loss5": request.form.get('enable_loss5') == 'on',
            "enable_loss6": request.form.get('enable_loss6') == 'on',
        }
        
        # Get location info
        birth_location = get_location_info(birth_place)
        transit_location = get_location_info(transit_place)
        
        # Parse birth datetime
        birth_dt_str = f"{birth_date} {birth_time}"
        birth_tz = ZoneInfo(birth_location['timezone'])
        birth_dt_local = datetime.strptime(birth_dt_str, "%Y-%m-%d %H:%M")
        birth_dt_local = birth_dt_local.replace(tzinfo=birth_tz)
        birth_dt_utc = birth_dt_local.astimezone(timezone.utc)
        
        # Parse transit datetime range
        start_dt_str = f"{start_date} {start_time}"
        end_dt_str = f"{end_date} {end_time}"
        transit_tz = ZoneInfo(transit_location['timezone'])
        start_dt_local = datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M")
        end_dt_local = datetime.strptime(end_dt_str, "%Y-%m-%d %H:%M")
        start_dt_local = start_dt_local.replace(tzinfo=transit_tz)
        end_dt_local = end_dt_local.replace(tzinfo=transit_tz)
        start_dt_utc = start_dt_local.astimezone(timezone.utc)
        end_dt_utc = end_dt_local.astimezone(timezone.utc)
        
        # Compute natal chart and transit rows under ayanamsa context (serialized)
        step_seconds = 3600  # 1 hour
        refine_to_seconds = 60  # 1 minute
        
        with use_ayanamsa(selected_ayanamsa) as (ok, msg):
            if not ok:
                # proceed but log/notify - falling back to whatever swisseph currently uses
                print(f"Warning: could not set ayanamsa '{selected_ayanamsa}' -> {msg}")
            else:
                print(f"Ayanamsa set to swisseph constant: {msg}")
            
            natal_chart = compute_natal_chart(
                birth_dt_utc,
                birth_location['latitude'],
                birth_location['longitude']
            )
            
            rows = compute_all_rows(
                start_dt_utc,
                end_dt_utc,
                transit_tz,
                natal_chart,
                step_seconds,
                refine_to_seconds,
                enabled_rules
            )
        
        # Filter natal chart for template to avoid iterating over non-dict items
        natal_planets = {k: v for k, v in natal_chart.items() if isinstance(v, dict)}

        return render_template('results.html', 
                             rows=rows, 
                             natal_chart=natal_chart,
                             natal_planets=natal_planets,
                             birth_info={
                                 'date': birth_date,
                                 'time': birth_time,
                                 'place': birth_place,
                             },
                             transit_info={
                                 'start_date': start_date,
                                 'start_time': start_time,
                                 'end_date': end_date,
                                 'end_time': end_time,
                                 'place': transit_place,
                                 'timezone': transit_location['timezone']
                             },
                             enabled_rules=enabled_rules,
                             selected_ayanamsa=selected_ayanamsa)
    
    except Exception as e:
        return render_template('error.html', error=str(e))


@app.route('/download_csv', methods=['POST'])
def download_csv():
    try:
        # Get form data (same as compute route)
        birth_date = request.form.get('birth_date')
        birth_time = request.form.get('birth_time')
        birth_place = request.form.get('birth_place')
        
        start_date = request.form.get('start_date')
        start_time = request.form.get('start_time')
        end_date = request.form.get('end_date')
        end_time = request.form.get('end_time')
        
        transit_place = request.form.get('transit_place')
        selected_ayanamsa = request.form.get('ayanamsa')
        
        # Get enabled rules
        enabled_rules = {
            "enable_rule1": request.form.get('enable_rule1') == 'on',
            "enable_rule2": request.form.get('enable_rule2') == 'on',
            "enable_rule3": request.form.get('enable_rule3') == 'on',
            "enable_rule4": request.form.get('enable_rule4') == 'on',
            "enable_rule5": request.form.get('enable_rule5') == 'on',
            "enable_rule6": request.form.get('enable_rule6') == 'on',
            "enable_rule7": request.form.get('enable_rule7') == 'on',
            "enable_rule8": request.form.get('enable_rule8') == 'on',
            "enable_loss1": request.form.get('enable_loss1') == 'on',
            "enable_loss2": request.form.get('enable_loss2') == 'on',
            "enable_loss3": request.form.get('enable_loss3') == 'on',
            "enable_loss4": request.form.get('enable_loss4') == 'on',
            "enable_loss5": request.form.get('enable_loss5') == 'on',
            "enable_loss6": request.form.get('enable_loss6') == 'on',
        }
        
        # Get location info
        birth_location = get_location_info(birth_place)
        transit_location = get_location_info(transit_place)
        
        # Parse birth datetime
        birth_dt_str = f"{birth_date} {birth_time}"
        birth_tz = ZoneInfo(birth_location['timezone'])
        birth_dt_local = datetime.strptime(birth_dt_str, "%Y-%m-%d %H:%M")
        birth_dt_local = birth_dt_local.replace(tzinfo=birth_tz)
        birth_dt_utc = birth_dt_local.astimezone(timezone.utc)
        
        # Parse transit datetime range
        start_dt_str = f"{start_date} {start_time}"
        end_dt_str = f"{end_date} {end_time}"
        transit_tz = ZoneInfo(transit_location['timezone'])
        start_dt_local = datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M")
        end_dt_local = datetime.strptime(end_dt_str, "%Y-%m-%d %H:%M")
        start_dt_local = start_dt_local.replace(tzinfo=transit_tz)
        end_dt_local = end_dt_local.replace(tzinfo=transit_tz)
        start_dt_utc = start_dt_local.astimezone(timezone.utc)
        end_dt_utc = end_dt_local.astimezone(timezone.utc)
        
        # Compute natal chart and rows under ayanamsa context
        step_seconds = 3600  # 1 hour
        refine_to_seconds = 60  # 1 minute
        
        with use_ayanamsa(selected_ayanamsa) as (ok, msg):
            if not ok:
                print(f"Warning: could not set ayanamsa '{selected_ayanamsa}' -> {msg}")
            else:
                print(f"Ayanamsa set to swisseph constant: {msg}")
            
            natal_chart = compute_natal_chart(
                birth_dt_utc,
                birth_location['latitude'],
                birth_location['longitude']
            )
            
            rows = compute_all_rows(
                start_dt_utc,
                end_dt_utc,
                transit_tz,
                natal_chart,
                step_seconds,
                refine_to_seconds,
                enabled_rules
            )
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Category', 'Rule', 'Body', 'Start', 'End', 'Sign', 'House', 'Window', 'Description'])
        
        # Write rows
        for row in rows:
            writer.writerow([
                row['category'],
                row['rule'],
                row['body'],
                row['start_str'],
                row['end_str'],
                row['sign'],
                row['house'],
                row['window'],
                row['description'],
            ])
        
        # Create response
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=transit_results.csv'}
        )
    
    except Exception as e:
        return render_template('error.html', error=str(e))

if __name__ == "__main__":
    app.run(debug=True, port=5001)
