import tkinter as tk
from tkinter import scrolledtext
import threading
import re
import time
import json
import asyncio
from bleak import BleakScanner, BleakClient

from google import genai
from google.genai import types

# ==========================================
# 🔑 API & BLUETOOTH SETUP
# ==========================================
# Grab your API key and plug it in here!
API_KEY = "AIzaSyD022BXnOZRASGtZiqTBMZk0EBVrg3Qto4"

# These need to exactly match what we put in your ESP32 C++ code
BLE_DEVICE_NAME = "ESP32_HEALTH"
BLE_CHAR_UUID = "abcd1234-5678-1234-5678-abcdef123456"

# ==========================================
# ⚙️ SENSOR CALIBRATION
# ==========================================
# Use this to fix inaccurate temperature readings from the hardware.
# If the ESP32 sends 40, and you put -2.0 here, the app will use 38.0 everywhere.
TEMP_OFFSET = +4.0

# ==========================================
# 🧠 JEEVAN BOT AI CONFIG
# ==========================================
# Let's wake up the Gemini API client
client = genai.Client(api_key=API_KEY)

# Setting up Jeevan Bot's brain with the exact rules you asked for
chat_session = client.chats.create(
    model="gemini-2.5-flash", # Using the 2.5 Flash model
    config=types.GenerateContentConfig(
        system_instruction=(
            "You are a helpful medical wellness assistant. "
            "Use the provided patient sensor data to give general wellness suggestions only. "
            "Also do not take the temperature data too seriously as its not very precise. "
            "Do not claim diagnosis but give answers. Feel free to use emojis. "
            "Give concise answers. "
            "give some yoga suggestions and if i need to go to hospital or not. "
        )
    )
)

# ==========================================
# 🎨 UI COLOR PALETTE
# ==========================================
# Keeping that sleek, modern dark mode look
BG_COLOR = "#0D1117"     
PANEL_BG = "#161B22"     
TEXT_COLOR = "#E6EDF3"   
ACCENT_COLOR = "#39D353" # Nice bright green when things are live
LOCKED_COLOR = "#FF7B72" # Red/Orange for when we freeze the screen
ENTRY_BG = "#0D1117"     

# ==========================================
# 💾 SENSOR DATA MEMORY
# ==========================================
data_locked = False

# This is what holds the invisible background data streaming from Bluetooth
live_sensor_data = {
    "spo2": "--",
    "heart_rate": "--",
    "temp": "--",
    "aqi": "Scanning BLE..."
}

# This is what the screen (and Jeevan Bot) actually looks at
active_sensor_data = live_sensor_data.copy()

# ==========================================
# 📡 BLUETOOTH (BLE) ENGINE
# ==========================================
def ble_notification_handler(sender, data):
    # Whenever the ESP32 shouts out new data, this function catches it instantly!
    try:
        # Clean up the raw bytes into a normal string
        line = data.decode('utf-8').strip()
        
        # Check if it looks like our JSON dictionary {"temp":..., "hr":...}
        if line.startswith("{") and line.endswith("}"):
            parsed_data = json.loads(line)
            
            # Apply our temperature offset if temp data exists!
            if "temp" in parsed_data:
                try:
                    raw_temp = float(parsed_data["temp"])
                    # Add the offset and round it to 1 decimal place so it looks clean
                    adjusted_temp = round(raw_temp + TEMP_OFFSET, 1)
                    live_sensor_data["temp"] = adjusted_temp
                except ValueError:
                    pass # Skip if the temp isn't a valid number
            
            # Safely update the rest of our live data dictionary
            live_sensor_data["heart_rate"] = parsed_data.get("hr", live_sensor_data["heart_rate"])
            live_sensor_data["spo2"] = parsed_data.get("spo2", live_sensor_data["spo2"])
            live_sensor_data["aqi"] = parsed_data.get("air", live_sensor_data["aqi"])
            
    except Exception as e:
        print(f"Oops, couldn't read that BLE data frame: {e}")

async def run_ble_client():
    print(f"Looking around for {BLE_DEVICE_NAME}...")
    
    # Scan the airwaves for your specific ESP32
    device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME, timeout=10.0)
    
    if not device:
        print("❌ Couldn't find the ESP32. Is it plugged in and turned on?")
        live_sensor_data["aqi"] = "Device Not Found"
        return

    print(f"✅ Found it at {device.address}! Hooking up now...")
    live_sensor_data["aqi"] = "Connecting..."

    # Time to connect and start listening
    try:
        async with BleakClient(device) as client:
            print("We're in! Listening to the sensor stream...")
            
            # Tell the ESP32 we want to hear every time that specific UUID changes
            await client.start_notify(BLE_CHAR_UUID, ble_notification_handler)
            
            # Keep this thread alive forever so it doesn't hang up the phone
            while True:
                await asyncio.sleep(1)
                
    except Exception as e:
        print(f"Lost connection to the ESP32: {e}")
        live_sensor_data["aqi"] = "BLE Disconnected"

def start_background_loop(loop):
    # This just gives our async Bluetooth scanner its own safe space to run
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_ble_client())

# ==========================================
# 🤖 JEEVAN BOT CHAT LOGIC
# ==========================================
def ask_gemini(user_msg):
    # Bundle up the absolute latest sensor readings and hand them to Jeevan
    vitals_context = (
        f"[Current Vitals - SpO2: {active_sensor_data['spo2']}%, "
        f"HR: {active_sensor_data['heart_rate']} bpm, "
        f"Temp: {active_sensor_data['temp']} C, "
        f"AQI: {active_sensor_data['aqi']}]\n\n"
        f"User: {user_msg}"
    )

    # Let's give it 3 tries just in case Google's servers are being slow
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = chat_session.send_message(vitals_context)
            return response.text
        except Exception as e:
            # If we hit a traffic jam (503 error), wait 2 seconds and try again
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                if attempt < max_retries - 1:
                    time.sleep(2) 
                    continue      
            return f"API Error: {e}"

def display_message(sender, text):
    # This smart little function handles bold text and emojis so Tkinter doesn't freak out
    chatbox.insert(tk.END, f"\n{sender}: ", "bold")
    parts = re.split(r'(\*\*.*?\*\*)', text)
    
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            # It's bold! Chop off the asterisks and apply the bold font
            clean_text = part[2:-2]
            chatbox.insert(tk.END, clean_text, "bold")
        else:
            # Regular text (emojis work perfectly here natively on Windows)
            chatbox.insert(tk.END, part, "normal")
            
    chatbox.insert(tk.END, "\n")
    chatbox.see(tk.END) # Auto-scroll to the bottom!

def send_message():
    # Grab what the user typed
    msg = user_entry.get().strip()
    if not msg:
        return

    # Slap it on the screen and clear the entry box
    display_message("You", msg)
    user_entry.delete(0, tk.END)

    # Ask Jeevan in the background so the app doesn't freeze while thinking
    threading.Thread(
        target=get_bot_reply,
        args=(msg,),
        daemon=True
    ).start()

def get_bot_reply(msg):
    # Get the answer and print it out!
    reply = ask_gemini(msg)
    display_message("Jeevan Bot", reply)

# ==========================================
# 🖥️ GUI / WINDOW LOGIC
# ==========================================
def toggle_lock(event=None):
    # This flips the switch when you press the backslash key
    global data_locked
    data_locked = not data_locked
    
    if data_locked:
        # Screen is frozen
        lock_status_label.config(text="🔒 STATUS: LOCKED", fg=LOCKED_COLOR)
        lock_btn.config(text="Unlock Data (\\)", bg="#8B0000", activebackground="#A52A2A")
    else:
        # Screen is live
        lock_status_label.config(text="🔓 STATUS: LIVE", fg=ACCENT_COLOR)
        lock_btn.config(text="Lock Data (\\)", bg="#238636", activebackground="#2EA043")
        update_sensor_display(force=True) # Instantly catch up to the live data

def update_sensor_display(force=False):
    # If the screen isn't locked, copy the invisible live data over to the active screen
    if not data_locked or force:
        active_sensor_data.update(live_sensor_data)
        
        vitals_text.set(
            f"SpO2: {active_sensor_data['spo2']} %\n\n"
            f"Heart Rate: {active_sensor_data['heart_rate']} bpm\n\n"
            f"Temp: {active_sensor_data['temp']} C\n\n"
            f"AQI: {active_sensor_data['aqi']}"
        )

    # Loop this check every 1 second
    if not force:
        root.after(1000, update_sensor_display)

# ==========================================
# 🏗️ BUILDING THE ACTUAL WINDOW
# ==========================================
root = tk.Tk()
root.title("Jeevan Bot Vitals Monitor")
root.geometry("950x600")
root.configure(bg=BG_COLOR)

# Bind that handy backslash key
root.bind('<backslash>', toggle_lock)

# --- THE LEFT SIDE (Sensor Readouts) ---
left_frame = tk.Frame(root, bg=PANEL_BG, width=280, highlightbackground="#30363D", highlightthickness=1)
left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 5), pady=10)
left_frame.pack_propagate(False)

title = tk.Label(
    left_frame,
    text="🏥 Live Vitals",
    font=("Segoe UI", 18, "bold"),
    bg=PANEL_BG,
    fg=TEXT_COLOR
)
title.pack(pady=(20, 5))

lock_status_label = tk.Label(
    left_frame,
    text="🔓 STATUS: LIVE",
    font=("Segoe UI", 12, "bold"),
    bg=PANEL_BG,
    fg=ACCENT_COLOR
)
lock_status_label.pack(pady=(0, 10))

lock_btn = tk.Button(
    left_frame,
    text="Lock Data (\\)",
    font=("Segoe UI", 10, "bold"),
    bg="#238636",
    fg="#FFFFFF",
    activebackground="#2EA043",
    activeforeground="#FFFFFF",
    relief="flat",
    cursor="hand2",
    command=toggle_lock
)
lock_btn.pack(pady=(0, 20), ipadx=10, ipady=3)

vitals_text = tk.StringVar()

vitals_label = tk.Label(
    left_frame,
    textvariable=vitals_text,
    font=("Consolas", 15, "bold"),
    bg=PANEL_BG,
    fg=TEXT_COLOR,
    justify="left"
)
vitals_label.pack(padx=20, anchor="w")

# --- THE RIGHT SIDE (Chat interface) ---
right_frame = tk.Frame(root, bg=BG_COLOR)
right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 10), pady=10)

chatbox = scrolledtext.ScrolledText(
    right_frame,
    wrap=tk.WORD,
    font=("Segoe UI", 12),
    bg=PANEL_BG,
    fg=TEXT_COLOR,
    insertbackground=TEXT_COLOR,
    highlightthickness=0,
    borderwidth=1,
    relief="solid"
)
chatbox.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

# Teaching Tkinter what bold text should actually look like
chatbox.tag_configure("bold", font=("Segoe UI", 12, "bold"))
chatbox.tag_configure("normal", font=("Segoe UI", 12))

# Jeevan's intro message!
display_message("Jeevan Bot", "Hello! I am scanning for the ESP32_HEALTH BLE signal...\n--------------------------------------------------")

bottom_frame = tk.Frame(right_frame, bg=BG_COLOR)
bottom_frame.pack(fill=tk.X)

user_entry = tk.Entry(
    bottom_frame,
    font=("Segoe UI", 13),
    bg=ENTRY_BG,
    fg=TEXT_COLOR,
    insertbackground=TEXT_COLOR,
    relief="solid",
    borderwidth=1
)
user_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 10))

send_btn = tk.Button(
    bottom_frame,
    text="Send",
    font=("Segoe UI", 11, "bold"),
    bg="#238636",
    fg="#FFFFFF",
    activebackground="#2EA043",
    activeforeground="#FFFFFF",
    relief="flat",
    cursor="hand2",
    command=send_message
)
send_btn.pack(side=tk.RIGHT, ipadx=15, ipady=4)

# Let users press Enter to send messages
user_entry.bind("<Return>", lambda e: send_message())

# ==========================================
# 🚀 FIRE IT UP
# ==========================================
# 1. Start the invisible Bluetooth listener in its own thread
ble_loop = asyncio.new_event_loop()
bt_thread = threading.Thread(target=start_background_loop, args=(ble_loop,), daemon=True)
bt_thread.start()

# 2. Start the screen update loop
update_sensor_display()

# 3. Open the window!
root.mainloop()