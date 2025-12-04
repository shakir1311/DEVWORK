# Installing Required Libraries

You're getting a compilation error because the required libraries are not installed. Choose your development environment:

## Option 1: Arduino IDE (Recommended for Beginners)

### Steps:

1. **Open Arduino IDE**

2. **Install PubSubClient Library:**
   - Go to: `Tools` → `Manage Libraries...`
   - Search for: `PubSubClient`
   - Install: `PubSubClient by Nick O'Leary` (version 2.8.x or later)

3. **Install ArduinoJson Library:**
   - In the same Library Manager
   - Search for: `ArduinoJson`
   - Install: `ArduinoJson by Benoit Blanchon` (version 6.x)
   - **Important:** Make sure to install version 6.x, NOT version 7.x (API is different)

4. **Verify Installation:**
   - Go to: `Sketch` → `Include Library`
   - You should see both `PubSubClient` and `ArduinoJson` in the list

5. **Compile:**
   - Open the `.ino` file
   - Click `Verify` (✓) button
   - Should compile without errors now

---

## Option 2: PlatformIO

### If PlatformIO is NOT installed:

**Install PlatformIO:**
```bash
# Install via pip
pip install platformio

# Or install PlatformIO IDE (VS Code extension)
# https://platformio.org/install/ide?install=vscode
```

### If PlatformIO IS installed:

**Install libraries automatically:**
```bash
cd "/Users/shakirzareen/My Drive/Abertay/research/DEV/IOT/esp32_ecg_mqtt_consumer"
pio lib install
```

This will automatically install all libraries listed in `platformio.ini`.

**Or install manually:**
```bash
pio lib install "knolleary/PubSubClient@^2.8"
pio lib install "bblanchon/ArduinoJson@^6.21.3"
```

---

## Quick Fix (Arduino IDE)

If you're using Arduino IDE, the fastest way:

1. Open Arduino IDE
2. `Tools` → `Manage Libraries...`
3. Search and install:
   - **PubSubClient** (by Nick O'Leary)
   - **ArduinoJson** (by Benoit Blanchon) - **Version 6.x only!**
4. Compile again

---

## Troubleshooting

### "Library not found" after installation:
- Restart Arduino IDE
- Check: `File` → `Preferences` → `Sketchbook location`
- Libraries should be in: `[Sketchbook]/libraries/`

### Wrong ArduinoJson version:
- Uninstall ArduinoJson 7.x if installed
- Install ArduinoJson 6.21.3 specifically
- The code uses v6 API which is incompatible with v7

### PlatformIO not found:
- Install PlatformIO first (see above)
- Or use Arduino IDE instead (easier for beginners)

---

## Verify Installation

After installing, try compiling. You should see:
```
Sketch uses XXXXX bytes (XX%) of program storage space.
```

No errors = libraries installed correctly! ✅

