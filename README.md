# 📡 GSM Modem SMS API

A lightweight REST API built with **FastAPI** that lets you send and receive SMS messages through any GSM modem using AT commands.

---

## 🛠️ Requirements

- Python 3.11+
- A GSM USB modem (e.g. Huawei E173, ZTE MF190, SIM800 module)
- Active SIM card with SMS enabled

---

## ⚙️ Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Find your modem port
#    Windows — run this to find your COM port:
python -c "import serial.tools.list_ports; [print(p) for p in serial.tools.list_ports.comports()]"
#    Linux:
ls /dev/ttyUSB*

# 3. Set your modem port in main.py
MODEM_PORT = "COM4"           # Windows (use the port found above)
# MODEM_PORT = "/dev/ttyUSB0" # Linux

# 3. Run the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> **No modem?** The API automatically starts in **DEMO mode** with sample data.

---

## 📖 API Endpoints

### `GET /` — Info

Returns available endpoints.

---

### `POST /sms/send` — Send SMS

**Request body:**

```json
{
  "to": "+8801XXXXXXXXX",
  "message": "Hello from SMS API!"
}
```

**Response:**

```json
{
  "success": true,
  "to": "+8801XXXXXXXXX",
  "message": "Hello from SMS API!",
  "modem_response": "+CMGS: 5\r\nOK",
  "sent_at": "2024-06-01T10:30:00Z"
}
```

---

### `GET /sms/inbox` — List Messages

Optional query param: `?status=ALL` (default)

Options: `ALL`, `REC UNREAD`, `REC READ`, `STO UNSENT`, `STO SENT`

**Response:**

```json
[
  {
    "index": 0,
    "status": "REC UNREAD",
    "sender": "+8801700000001",
    "timestamp": "24/01/01,10:00:00+24",
    "message": "Hello!"
  }
]
```

---

### `GET /sms/inbox/{index}` — Read One Message

```
GET /sms/inbox/0
```

---

### `DELETE /sms/{index}` — Delete Message

```
DELETE /sms/0
```

**Response:**

```json
{ "success": true, "deleted_index": 0 }
```

---

### `GET /modem/status` — Modem Info

**Response:**

```json
{
  "connected": true,
  "port": "/dev/ttyUSB0",
  "operator": "GrameenPhone",
  "imei": "123456789012345",
  "signal": { "rssi": 18, "dbm": -77, "raw": "+CSQ: 18,0" },
  "mode": "live"
}
```

---

## 🧪 Test with curl

```bash
# Send SMS
curl -X POST http://localhost:8000/sms/send \
  -H "Content-Type: application/json" \
  -d '{"to": "+8801XXXXXXXXX", "message": "Hello!"}'

# Get inbox
curl http://localhost:8000/sms/inbox

# Get unread only
curl "http://localhost:8000/sms/inbox?status=REC%20UNREAD"

# Read message index 0
curl http://localhost:8000/sms/inbox/0

# Delete message index 0
curl -X DELETE http://localhost:8000/sms/0

# Modem status
curl http://localhost:8000/modem/status
```

---

## 🐧 Linux: Find your modem port

```bash
# List USB serial devices
ls /dev/ttyUSB*

# Or check dmesg after plugging in
dmesg | grep tty
```

## 🪟 Windows: Find COM port

Open **Device Manager → Ports (COM & LPT)** — look for your modem.

---

## 📝 Interactive Docs

Visit **http://localhost:8000/docs** for the full Swagger UI.

---

## 🔒 Tips

- Use international format for numbers: `+8801XXXXXXXXX`
- Standard SMS is max **160 characters**; longer messages are split automatically
- Signal `rssi > 10` (dBm > -93) is needed for reliable delivery
- Run with `--host 0.0.0.0` to expose on your local network
