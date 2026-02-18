# Astro Transit Calculator

A beautiful, modern web application for calculating astrological transits based on Vedic/KP astrology principles.

## Features

- ✨ **Eye-catching glass-morphism UI** with dark cosmic theme
- 🔮 **Multiple Ayanamsa support** (KP Old, Lahiri, Fagan-Bradley, Tropical)
- 💰 **8 Money Rules** for identifying wealth-generating transits
- 📉 **6 Loss Rules** for identifying challenging periods
- 📊 **Detailed natal chart** with planetary positions and nakshatras
- 📥 **CSV export** of all calculated transits
- 🌍 **Automatic timezone** detection via geolocation
- 📱 **Fully responsive** design

## Installation

### Prerequisites
- Python 3.9+
- pip

### Setup

1. **Install dependencies:**
```bash
pip install Flask==3.0.0 pyswisseph==2.10.3.2 geopy==2.4.1 timezonefinder==6.5.0
```

Or use the requirements file:
```bash
pip install -r requirements.txt
```

2. **Run the application:**
```bash
python app.py
```

3. **Access the application:**
Open your browser and navigate to:
```
http://localhost:5001
```

## File Structure

```
astro-transit-app/
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── templates/
│   ├── index.html         # Input form page
│   ├── results.html       # Results display page
│   └── error.html         # Error handling page
└── README.md              # This file
```

## Key Fixes Implemented

### 1. **Indentation Error Fix**
The `/compute` route had an indentation error that prevented the server from starting. This has been corrected.

### 2. **Natal Planets Dictionary Fix**
Added filtering to separate planet dictionaries from other data structures in the natal chart:
```python
natal_planets = {k: v for k, v in natal_chart.items() if isinstance(v, dict)}
```
This prevents the Jinja template error when iterating over natal chart data.

### 3. **Modern UI Design**
- Glass-morphism effect with backdrop blur
- Cosmic gradient background (dark purple to black)
- Custom checkbox styling
- Hover effects and transitions
- FontAwesome icons throughout
- Responsive grid layouts

## Usage

### 1. Enter Birth Details
- Date, time, and location of birth
- Supports autocomplete for popular cities

### 2. Set Transit Range
- Start and end dates for transit calculation
- Optional different location for transit (defaults to birth place)

### 3. Configure Settings
- Choose ayanamsa (sidereal system)
- Enable/disable specific money and loss rules

### 4. View Results
- See natal chart with all planetary positions
- Review calculated transit events in a sortable table
- Download results as CSV

## Money Rules

1. **Rule #1**: Jupiter/Venus/2L in Panaphara houses + Panaphara degrees
2. **Rule #2**: Moon in PP planet nakshatra + Panaphara degree
3. **Rule #3**: D9 Dispositor of 2L in Panaphara houses + degrees
4. **Rule #4**: Venus and Uranus in Panaphara (Crorepati Yoga)
5. **Rule #5**: 5L and 9L in same sign or mutual 7th aspect
6. **Rule #6**: 2L in Panaphara degree (any sign)
7. **Rule #7**: Lucky Days (Apoklima lords in Apoklima houses)
8. **Rule #8**: Transit planet touches natal PP planet degree (±1°)

## Loss Rules

1. **Loss #1**: Saturn/Venus/Ketu in Apoklima houses + Apoklima degrees
2. **Loss #2**: Sun in 6L/8L/12L nakshatras + Apoklima degrees
3. **Loss #3**: Moon/Sun in 6th nakshatra from natal position
4. **Loss #4**: Moon conjunct natal Neptune degree (±1°)
5. **Loss #5**: 6L and 8L in 6/8 relationship → EXPENSE
6. **Loss #6**: Sun in 3/6/8/12 houses (with PP nakshatra exception)

## Technical Details

### Astronomical Calculations
- Uses Swiss Ephemeris (pyswisseph) for precise planetary positions
- Supports multiple ayanamsa systems
- Thread-safe ayanamsa handling with context managers
- Sidereal zodiac calculations

### House Systems
- Placidus house system for ascendant calculation
- Panaphara houses: 2, 5, 8, 11
- Apoklima houses: 3, 6, 9, 12
- Degree windows for enhanced precision

### Transit Algorithm
- Hourly step scan for efficiency
- Minute-level refinement for precision
- Interval merging for continuous periods
- Timezone-aware calculations

## Customization

### Adding New Rules
To add custom rules, create a new function in `app.py`:

```python
def compute_custom_rule_rows(start_utc, end_utc, tz, natal_chart, step_seconds, refine_to_seconds):
    rows = []
    # Your logic here
    return rows
```

Then integrate it in `compute_all_rows()`.

### Styling
All styles are embedded in the HTML templates using Tailwind CSS via CDN. Modify the `<style>` sections to customize:
- Colors (gradient backgrounds, borders)
- Glass-morphism effects (backdrop-filter)
- Typography (fonts, sizes)

## Troubleshooting

### "dict object has no attribute" Error
This was caused by iterating over non-dictionary items in the natal chart. Fixed by filtering for dict-type values only.

### Network/Location Errors
The app uses Nominatim for geocoding. Ensure you have internet access for location lookup. Popular cities are pre-configured.

### Swiss Ephemeris Data
The pyswisseph library includes basic ephemeris data. For extended date ranges, you may need to download additional ephemeris files.

### Ayanamsa Not Found
If an ayanamsa isn't available in your pyswisseph build, the app will log a warning and fall back to the default mode. Check console output.

## Browser Compatibility

- ✅ Chrome 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Edge 90+

Note: Glass-morphism effects (backdrop-filter) may not work on older browsers.

## Performance Notes

- Transit calculations are CPU-intensive
- Expect 10-30 seconds for 7-day range calculations
- 30+ day ranges may take several minutes
- Consider caching results for repeated queries

## Credits

- **Swiss Ephemeris**: Astrodienst AG
- **Geopy**: GeoPy contributors
- **Tailwind CSS**: Tailwind Labs
- **Font Awesome**: Fonticons, Inc.

## License

This project is provided as-is for educational and personal use.

## Support

For issues or questions:
1. Check the console output for error details
2. Verify all dependencies are installed correctly
3. Ensure birth/transit locations are correctly spelled
4. Try with fewer enabled rules if calculations are slow

---

**Version**: 1.0.0  
**Last Updated**: January 2025
