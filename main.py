"""
SMS Gateway API
---------------
A FastAPI-based REST API that sends and receives SMS via a GSM modem.

Endpoints:
  POST /sms/send          - Send an SMS
  GET  /sms/inbox         - Read all inbox messages
  GET  /sms/inbox/{index} - Read a specific message
  DELETE /sms/{index}     - Delete a message
  GET  /modem/status      - Modem info & signal strength
  GET  /docs              - Auto-generated API docs (Swagger UI)
"""

import asyncio
import logging
import serial
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ─── Config ────────────────────────────────────────────────────────────────────
MODEM_PORT = "/dev/ttyUSB0"   # Linux: /dev/ttyUSB0  |  Windows: COM3
MODEM_BAUD = 115200
LOG_LEVEL  = logging.DEBUG

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sms-api")

# ─── Modem helper ──────────────────────────────────────────────────────────────
class Modem:
    """Thin wrapper around a serial GSM modem using raw AT commands."""

    def __init__(self, port: str, baud: int):
        self.port = port
        self.baud = baud
        self._ser: Optional[serial.Serial] = None

    # ── connection ──────────────────────────────────────────────────────────────
    def connect(self):
        try:
            self._ser = serial.Serial(
                self.port, self.baud, timeout=5, write_timeout=5
            )
            time.sleep(1)
            self._flush()
            log.info(f"Modem connected on {self.port}")
            self._init()
        except serial.SerialException as e:
            log.warning(f"Could not open modem ({e}). Running in DEMO mode.")
            self._ser = None

    def disconnect(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            log.info("Modem disconnected")

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    # ── low-level AT ────────────────────────────────────────────────────────────
    def _flush(self):
        if self._ser:
            self._ser.reset_input_buffer()

    def _at(self, cmd: str, delay: float = 0.5, raw: bool = False) -> str:
        if not self.connected:
            raise RuntimeError("Modem not connected")
        self._ser.write((cmd + "\r\n").encode())
        time.sleep(delay)
        resp = self._ser.read(self._ser.in_waiting or 1).decode(errors="replace")
        log.debug(f"AT << {cmd!r}  >>  {resp!r}")
        if not raw and "ERROR" in resp:
            raise RuntimeError(f"AT error for '{cmd}': {resp.strip()}")
        return resp

    def _init(self):
        self._at("AT")           # echo check
        self._at("ATE0")         # disable echo
        self._at("AT+CMGF=1")   # text mode
        self._at('AT+CSCS="GSM"')  # character set

    # ── SMS operations ──────────────────────────────────────────────────────────
    def send_sms(self, number: str, message: str) -> str:
        """Send an SMS. Returns modem response (includes message reference)."""
        self._at(f'AT+CMGS="{number}"', delay=1.0)   # wait for > prompt
        self._ser.write(message.encode() + bytes([26]))  # Ctrl-Z sends it
        # Wait up to 15s for +CMGS: or ERROR response
        deadline = time.time() + 15
        resp = ""
        while time.time() < deadline:
            time.sleep(0.5)
            chunk = self._ser.read(self._ser.in_waiting or 1).decode(errors="replace")
            resp += chunk
            if "+CMGS:" in resp or "ERROR" in resp or "OK" in resp:
                break
        log.debug(f"SMS send response: {resp!r}")
        if "ERROR" in resp:
            raise RuntimeError(f"Failed to send: {resp.strip()}")
        if "+CMGS:" not in resp:
            raise RuntimeError(f"No confirmation received: {resp.strip()!r}")
        return resp.strip()

    def list_messages(self, status: str = "ALL") -> list[dict]:
        """List messages from SIM storage."""
        resp = self._at(f'AT+CMGL="{status}"', delay=1.5)
        return _parse_cmgl(resp)

    def read_message(self, index: int) -> dict:
        """Read one message by SIM index."""
        resp = self._at(f"AT+CMGR={index}", delay=0.5)
        msgs = _parse_cmgr(resp, index)
        if not msgs:
            raise RuntimeError(f"No message at index {index}")
        return msgs[0]

    def delete_message(self, index: int):
        self._at(f"AT+CMGD={index}")

    def signal_quality(self) -> dict:
        resp = self._at("AT+CSQ", raw=True)
        # +CSQ: rssi,ber
        for line in resp.splitlines():
            if "+CSQ:" in line:
                parts = line.split(":")[1].strip().split(",")
                rssi = int(parts[0])
                dbm  = -113 + rssi * 2 if rssi < 99 else None
                return {"rssi": rssi, "dbm": dbm, "raw": line.strip()}
        return {"rssi": -1, "dbm": None, "raw": resp.strip()}

    def operator(self) -> str:
        resp = self._at('AT+COPS?', raw=True)
        for line in resp.splitlines():
            if "+COPS:" in line:
                parts = line.split(",")
                if len(parts) >= 3:
                    return parts[2].strip().strip('"')
        return "Unknown"

    def imei(self) -> str:
        resp = self._at("AT+GSN", raw=True)
        for line in resp.splitlines():
            line = line.strip()
            if line.isdigit() and len(line) >= 15:
                return line
        return "Unknown"


# ─── AT response parsers ───────────────────────────────────────────────────────
def _parse_cmgl(raw: str) -> list[dict]:
    messages = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("+CMGL:"):
            # +CMGL: index,"status","sender",,"timestamp"
            meta = line[len("+CMGL:"):].split(",", 4)
            idx    = int(meta[0].strip())
            status = meta[1].strip().strip('"')
            sender = meta[2].strip().strip('"') if len(meta) > 2 else ""
            ts     = meta[4].strip().strip('"') if len(meta) > 4 else ""
            body = lines[i + 1].strip() if i + 1 < len(lines) else ""
            messages.append({"index": idx, "status": status,
                              "sender": sender, "timestamp": ts, "message": body})
            i += 2
        else:
            i += 1
    return messages


def _parse_cmgr(raw: str, index: int) -> list[dict]:
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("+CMGR:"):
            meta = line[len("+CMGR:"):].split(",", 4)
            status = meta[0].strip().strip('"')
            sender = meta[1].strip().strip('"') if len(meta) > 1 else ""
            ts     = meta[3].strip().strip('"') if len(meta) > 3 else ""
            body   = lines[i + 1].strip() if i + 1 < len(lines) else ""
            return [{"index": index, "status": status,
                     "sender": sender, "timestamp": ts, "message": body}]
    return []


# ─── Demo mode (no modem) ──────────────────────────────────────────────────────
DEMO_INBOX = [
    {"index": 0, "status": "REC UNREAD", "sender": "+8801700000001",
     "timestamp": "24/01/01,10:00:00+24", "message": "Hello from demo!"},
    {"index": 1, "status": "REC READ",   "sender": "+8801700000002",
     "timestamp": "24/01/01,11:30:00+24", "message": "Test message 2"},
]

class DemoModem(Modem):
    """Simulates a modem when no hardware is present."""

    def connect(self):
        log.info("🟡 DEMO MODE — no real modem, using simulated responses")
        self._ser = None   # stays None, but we override every method

    @property
    def connected(self) -> bool:
        return True  # always "connected" in demo

    def send_sms(self, number, message):
        log.info(f"[DEMO] SMS → {number}: {message}")
        return "+CMGS: 1\r\nOK"

    def list_messages(self, status="ALL"):
        return DEMO_INBOX if status in ("ALL", "REC UNREAD", "REC READ") else []

    def read_message(self, index):
        for m in DEMO_INBOX:
            if m["index"] == index:
                return m
        raise RuntimeError(f"No message at index {index}")

    def delete_message(self, index):
        log.info(f"[DEMO] Delete message {index}")

    def signal_quality(self):
        return {"rssi": 18, "dbm": -77, "raw": "+CSQ: 18,0"}

    def operator(self):
        return "GrameenPhone (Demo)"

    def imei(self):
        return "123456789012345"


# ─── App lifecycle ─────────────────────────────────────────────────────────────
modem: Modem = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global modem
    # Try real modem first; fall back to demo
    try:
        m = Modem(MODEM_PORT, MODEM_BAUD)
        m.connect()
        if m.connected:
            modem = m
        else:
            raise RuntimeError("not connected")
    except Exception:
        modem = DemoModem(MODEM_PORT, MODEM_BAUD)
        modem.connect()
    yield
    if hasattr(modem, "disconnect"):
        modem.disconnect()


# ─── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="GSM Modem SMS API",
    description="Send & receive SMS messages through a GSM modem via REST.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Schemas ───────────────────────────────────────────────────────────────────
class SendRequest(BaseModel):
    to: str = Field(..., example="+8801XXXXXXXXX", description="Recipient phone number (international format)")
    message: str = Field(..., example="Hello!", description="SMS text body (max 160 chars for single SMS)")

class SendResponse(BaseModel):
    success: bool
    to: str
    message: str
    modem_response: str
    sent_at: str

class SmsMessage(BaseModel):
    index: int
    status: str
    sender: str
    timestamp: str
    message: str

class ModemStatus(BaseModel):
    connected: bool
    port: str
    operator: str
    imei: str
    signal: dict
    mode: str


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.post("/sms/send", response_model=SendResponse, tags=["SMS"])
async def send_sms(req: SendRequest):
    """Send an SMS to a phone number."""
    try:
        resp = await asyncio.to_thread(modem.send_sms, req.to, req.message)
        return SendResponse(
            success=True,
            to=req.to,
            message=req.message,
            modem_response=resp,
            sent_at=datetime.utcnow().isoformat() + "Z",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sms/inbox", response_model=list[SmsMessage], tags=["SMS"])
async def get_inbox(status: str = "ALL"):
    """
    Read messages from the modem SIM.

    - **status**: `ALL` | `REC UNREAD` | `REC READ` | `STO UNSENT` | `STO SENT`
    """
    try:
        msgs = await asyncio.to_thread(modem.list_messages, status)
        return msgs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sms/inbox/{index}", response_model=SmsMessage, tags=["SMS"])
async def get_message(index: int):
    """Read a single SMS by its SIM storage index."""
    try:
        return await asyncio.to_thread(modem.read_message, index)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/sms/{index}", tags=["SMS"])
async def delete_message(index: int):
    """Delete a message from SIM storage by index."""
    try:
        await asyncio.to_thread(modem.delete_message, index)
        return {"success": True, "deleted_index": index}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/modem/status", response_model=ModemStatus, tags=["Modem"])
async def modem_status():
    """Get modem connection info, operator, IMEI and signal strength."""
    try:
        sig  = await asyncio.to_thread(modem.signal_quality)
        op   = await asyncio.to_thread(modem.operator)
        imei = await asyncio.to_thread(modem.imei)
        mode = "demo" if isinstance(modem, DemoModem) else "live"
        return ModemStatus(
            connected=modem.connected,
            port=modem.port,
            operator=op,
            imei=imei,
            signal=sig,
            mode=mode,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", tags=["Info"])
def root():
    return {
        "name": "GSM SMS API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": [
            "POST /sms/send",
            "GET  /sms/inbox",
            "GET  /sms/inbox/{index}",
            "DELETE /sms/{index}",
            "GET  /modem/status",
        ],
    }