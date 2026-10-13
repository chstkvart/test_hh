import json
import logging
import os
import re
import threading
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE_URL = os.environ.get("SOURCE_URL", "https://test.ooobs.ru/api/orders")
PORT = int(os.environ.get("PORT", "8000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync")

_lock = threading.Lock()
_last_result = None


def fetch_orders(retries=3):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(SOURCE_URL, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("orders payload is not a list")
            return data
        except Exception as e:
            last_err = e
            log.warning("fetch attempt %s/%s failed: %s", attempt, retries, e)
    raise last_err


def normalize_phone(raw):
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    return None


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return float("-inf")


def to_amount(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def sync(orders):
    best = {}
    invalid = 0
    for o in orders:
        if not isinstance(o, dict):
            invalid += 1
            continue
        phone = normalize_phone(o.get("phone"))
        if phone is None:
            invalid += 1
            continue
        key = (to_amount(o.get("amount")), parse_ts(o.get("created_at")))
        cur = best.get(phone)
        if cur is None or key > cur[0]:
            best[phone] = (key, o)

    clients = []
    for phone, (_, o) in best.items():
        amount = o.get("amount")
        if isinstance(amount, float) and amount.is_integer():
            amount = int(amount)
        clients.append({
            "name": o.get("name") or "",
            "phone": phone,
            "email": o.get("email") or "",
            "amount": amount,
        })

    received = len(orders)
    unique = len(clients)
    return {
        "clients": clients,
        "stats": {
            "received": received,
            "unique": unique,
            "duplicates_merged": received - invalid - unique,
            "invalid_dropped": invalid,
        },
    }


def run_sync():
    global _last_result
    with _lock:
        try:
            _last_result = sync(fetch_orders())
            log.info("sync ok: %s", _last_result["stats"])
        except Exception as e:
            log.error("sync failed: %s", e)
            if _last_result is None:
                raise
        return _last_result


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/health":
            return self._send(200, {"status": "ok"})
        if path in ("/", "/result", "/clients", "/sync", "/api/clients"):
            try:
                return self._send(200, run_sync())
            except Exception as e:
                return self._send(502, {"error": f"source unavailable: {e}"})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    log.info("listening on :%s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
