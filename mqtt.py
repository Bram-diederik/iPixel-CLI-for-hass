#!/usr/bin/env python3
import os
import json
import time
import threading
import subprocess
import websocket
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# -----------------------------
# Load .env config
# -----------------------------
load_dotenv()
MQTT_HOST = os.getenv("MQTT_HOST", "homeassisant")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
SSL_ENABLED = os.getenv("SSL_ENABLED", "False").lower() == "true"
DEVICE_MAC = os.getenv("DEVICE_MAC")
IPIXELCLI = os.getenv("IPIXELCLI","python ./ipixelcli.py")
WS_PORT =  os.getenv("WS_PORT","8765");





# -----------------------------
# Constants / Defaults
# -----------------------------
DEVICE_ID_SAFE = DEVICE_MAC.replace(":", "").lower()
BASE_TOPIC = f"ipixel/{DEVICE_ID_SAFE}"
CMD_TOPIC = BASE_TOPIC + "/set"
STATE_TOPIC = BASE_TOPIC + "/state"
LAST_TEXT_TOPIC = BASE_TOPIC + "/last_text"
WS_URL = f"ws://127.0.0.1:{WS_PORT}"

TOPICS = {
    "power": f"{BASE_TOPIC}/power",
    "brightness": f"{BASE_TOPIC}/brightness",
    "speed": f"{BASE_TOPIC}/speed",
    "animation": f"{BASE_TOPIC}/animation",
    "color": f"{BASE_TOPIC}/color",
    "font": f"{BASE_TOPIC}/font",
    "matrix_height": f"{BASE_TOPIC}/matrix_height",
    "font_size": f"{BASE_TOPIC}/font_size",
    "font_offset_x": f"{BASE_TOPIC}/font_offset_x",
    "font_offset_y": f"{BASE_TOPIC}/font_offset_y",
    "send_text": f"{BASE_TOPIC}/send_text",
    "clear": f"{BASE_TOPIC}/clear",
}

DEFAULTS = {
    "power": False,
    "brightness": 80,
    "speed": 75,
    "animation": 1,
    "color": "ffffff",
    "font": "gnufont",
    "matrix_height": 16,
    "font_size": None,
    "font_offset_x": 0,
    "font_offset_y": 0,
}


current_states = DEFAULTS.copy()
last_text = ""
ws = None
client = None

# -----------------------------
# WebSocket helpers
# -----------------------------
def start_server_once():
    cmd = f"{IPIXELCLI} -a {DEVICE_MAC} --host 127.0.0.1 --server -p 8765 &"
    subprocess.Popen(cmd, shell=True)
    time.sleep(2)

def ws_connect():
    global ws
    if ws:
        try:
            ws.send(json.dumps({"command": "ping"}))
            return ws
        except Exception:
            try:
                ws.close()
            except:
                pass
            ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect(WS_URL, timeout=5)
        print("[WS] Connected")
        threading.Thread(target=ws_receive_thread, daemon=True).start()
        return ws
    except Exception as e:
        print("[WS] Connect failed:", e)
        ws = None
        return None

def ensure_server():
    if not ws_connect():
        start_server_once()
        ws_connect()

def ws_send(payload):
    global ws
    try:
        if not ws:
            ws_connect()
        if not ws:
            print("[WS] Cannot send, no connection")
            return
        ws.send(json.dumps(payload))
        cmd = payload.get("command")
        if cmd == "send_text":
            print(f"[WS] Sent text: {payload['params'][0]}")
        else:
            print(f"[WS] Sent command: {cmd} | params: {payload.get('params')}")
    except Exception as e:
        print("[WS] Send failed:", e)

def ws_receive_thread():
    global ws
    while True:
        if not ws:
            time.sleep(1)
            continue
        try:
            ws.settimeout(1)
            msg = ws.recv()
            if isinstance(msg, bytes):
                continue
            try:
                data = json.loads(msg)
                print("[WS] Received JSON:", data)
            except Exception:
                continue
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as e:
            print("[WS] Receive loop ended:", e)
            time.sleep(2)

# -----------------------------
# LED helpers
# -----------------------------
def send_text_to_led(text, **kwargs):
    if not text:
        return
    payload = {"command": "send_text", "params": [text]}
    for k in ["color", "speed", "animation", "font", "matrix_height"]:
        if k in kwargs and kwargs[k] is not None:
            payload["params"].append(f"{k}={kwargs[k]}")
    ws_send(payload)

def clear_generated_texts():
    ws_send({"command": "clear", "params": []})

def send_led_off():
    ws_send({"command": "led_off", "params": []})
def send_led_on():
    ws_send({"command": "led_on", "params": []})

# -----------------------------
# MQTT helpers
# -----------------------------
def publish_states():
    for k, v in current_states.items():
        t = TOPICS.get(k)
        if t:
            client.publish(f"{t}/state", str(v), retain=True)
    client.publish(STATE_TOPIC, json.dumps(current_states), retain=True)
    client.publish(LAST_TEXT_TOPIC, last_text, retain=True)

def handle_set_payload(key, payload):
    global last_text
    payload = payload.decode() if isinstance(payload, bytes) else str(payload)
    print(f"[MQTT] Set {key} -> {payload}")

    try:
        data = json.loads(payload)
    except Exception:
        data = payload

    # Handle JSON object sent to base command topic
    if key == "set" and isinstance(data, dict):
        print(f"[MQTT] Processing JSON command: {data}")
        
        # Format 1: Direct WebSocket command (command/params structure)
        if "command" in data and "params" in data:
            print(f"[MQTT] Detected WebSocket command format: {data['command']}")
            
            # Handle simple on/off commands
            if data["command"] == "led_on":
                send_led_on()
                print("[MQTT] Display turned ON")
            
            elif data["command"] == "led_off":
                send_led_off()
                print("[MQTT] Display turned OFF")
            
            # Extract text from params if it's a send_text command
            elif data["command"] == "send_text" and data["params"]:
                text_param = data["params"][0]
                if text_param.startswith("text="):
                    last_text = text_param[5:]  # Remove "text=" prefix
                else:
                    last_text = text_param
                
                print(f"[MQTT] Extracted text: {last_text}")
                
                # Send the command directly to WebSocket
                ensure_server()
                ws_send(data)  # Send the exact WebSocket command
            
        # Format 2: Simple parameter format (send_text, color, speed, etc.)
        elif "send_text" in data:
            last_text = data["send_text"]
            print(f"[MQTT] Text to display: {last_text}")
            
            # Update all provided states
            for k, v in data.items():
                if k in current_states:
                    current_states[k] = v
                    print(f"[MQTT] Updated {k} = {v}")

            # Build params for WebSocket command
            params = [last_text]
            param_keys = ["color", "speed", "animation", "font", "matrix_height"]
            
            for k in param_keys:
                if k in data and data[k] is not None:
                    params.append(f"{k}={data[k]}")
                elif current_states.get(k) is not None:
                    params.append(f"{k}={current_states[k]}")
            
            print(f"[MQTT] Sending text with params: {params}")
            
            # Ensure display is on and send text
            ensure_server()
            ws_send({"command": "send_text", "params": params})

    # Handle individual topic sets (existing functionality)
    elif key == "send_text":
        last_text = data
        if current_states.get("power"):
            ensure_server()
            ws_send({"command": "send_text", "params": [last_text]})

    elif key == "power":
        current_states["power"] = str(data).upper() in ("ON", "1", "TRUE")
        ensure_server()
        if current_states["power"]:
            ws_send({"command": "send_text", "params": [" "]})  # Turn on with space
        else:
            ws_send({"command": "clear", "params": []})  # Turn off with clear

    # Handle other individual parameters
    elif key in current_states:
        current_states[key] = data
        print(f"[MQTT] Updated {key} = {data}")

    publish_states()


def on_connect(client, userdata, flags, rc):
    print("[MQTT] Connected, subscribing topics...")
    # Subscribe to base command topic and subtopics
    client.subscribe(CMD_TOPIC)
    client.subscribe(f"{CMD_TOPIC}/#")
    # Subscribe to individual topics
    for t in TOPICS.values():
        client.subscribe(t + "/set")
    client.subscribe(TOPICS["send_text"])
    publish_states()

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload
    print(f"[MQTT] Received {topic} -> {payload}")
    
    # Extract the key from topic
    if topic == CMD_TOPIC:
        # This is the base command topic "ipixel/6554874a3e63/set"
        handle_set_payload("set", payload)
        return
    elif topic.startswith(CMD_TOPIC + "/"):
        # Subtopics like "ipixel/6554874a3e63/set/send_text"
        key = topic[len(CMD_TOPIC)+1:]
        handle_set_payload(key, payload)
        return
        
    # Handle individual set topics (existing)
    if topic == TOPICS["send_text"]:
        handle_set_payload("send_text", payload)
        return
        
    for key, t in TOPICS.items():
        if topic == t + "/set":
            handle_set_payload(key, payload)
            return

# -----------------------------
# MAIN
# -----------------------------
def mqtt_start():
    global client
    client = mqtt.Client(client_id=f"ipixel_{DEVICE_ID_SAFE}")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    if SSL_ENABLED:
        client.tls_set()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()
    print("[MQTT] Wrapper started")

def periodic_tasks():
    ensure_server()
    while True:
        publish_states()
        time.sleep(10)

if __name__ == "__main__":
    print("Starting iPixel MQTT Wrapper...")
    mqtt_start()
    threading.Thread(target=periodic_tasks, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
        client.loop_stop()
        client.disconnect()
