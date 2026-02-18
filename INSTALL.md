# Quick Start Installation Guide

## For Your Local Machine or VPS

### Step 1: Extract Files
```bash
# Extract the astro-transit-app folder to your desired location
cd /path/to/astro-transit-app
```

### Step 2: Create Virtual Environment (Recommended)
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Run the Application
```bash
python app.py
```

### Step 5: Access the Application
Open your browser and go to:
```
http://localhost:5001
```

## For Production Deployment (Optional)

### Using Gunicorn (Linux/Mac)
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5001 app:app
```

### Using Nginx as Reverse Proxy
1. Install Nginx
2. Configure as reverse proxy to localhost:5001
3. Set up SSL certificate (Let's Encrypt recommended)

### Environment Variables
```bash
export FLASK_ENV=production
export FLASK_APP=app.py
```

## Testing the Installation

1. **Homepage Test**: Should see the input form with glass-morphism design
2. **Sample Calculation**:
   - Birth Date: 1990-01-01
   - Birth Time: 12:00
   - Birth Place: Mumbai, India
   - Transit Range: 2025-02-01 to 2025-02-07
   - Transit Place: Same as birth place
   - Click "Calculate Transits"

3. **Expected Result**: Results page showing natal chart and transit events

## Common Issues

### Issue: ModuleNotFoundError
**Solution**: Make sure you're in the virtual environment and all packages are installed

### Issue: Location not found
**Solution**: Try more specific location names (e.g., "New York, USA" instead of just "New York")

### Issue: Port 5001 already in use
**Solution**: Change the port in app.py:
```python
app.run(debug=True, port=5002)  # Changed to 5002
```

### Issue: Slow calculations
**Solution**: 
- Reduce the transit date range
- Disable some rules temporarily
- Use fewer rules for testing

## Verification Checklist

- [ ] Python 3.9+ installed
- [ ] Virtual environment created and activated
- [ ] All dependencies installed successfully
- [ ] App starts without errors
- [ ] Homepage loads in browser
- [ ] Sample calculation completes successfully
- [ ] Results page displays correctly
- [ ] CSV download works

## Next Steps

1. Read the full README.md for detailed documentation
2. Customize the UI by editing templates/
3. Add your own astrological rules in app.py
4. Consider setting up a production server for 24/7 access

---

**Need Help?**
Check the console output for detailed error messages. Most issues are related to:
- Missing dependencies
- Incorrect location names
- Timezone issues (solved automatically by the app)
