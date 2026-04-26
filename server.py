"""
FINISIO CLEANS - HTTP API Server
Pure Python stdlib HTTP server. Zero external dependencies.

Run:  python3 server.py
      python3 server.py --port 9000
"""

import json
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import os
import db
import logic

PORT = 8000

# ---------------------------------------------------------
#  RESPONSE HELPERS
# ---------------------------------------------------------

def ok(data, status=200):
    return status, {"ok": True, "data": data}

def created(data):
    return ok(data, 201)

def err(message, status=400):
    return status, {"ok": False, "error": message}

def not_found(resource="Resource"):
    return err(f"{resource} not found", 404)

def forbidden():
    return err("Forbidden", 403)


# ---------------------------------------------------------
#  ROUTER  (pattern -> handler map)
# ---------------------------------------------------------

ROUTES = []

def route(method, pattern):
    """Decorator to register a route."""
    def decorator(fn):
        ROUTES.append((method.upper(), re.compile(f"^{pattern}$"), fn))
        return fn
    return decorator


# ---------------------------------------------------------
#  ROUTE HANDLERS
# ---------------------------------------------------------

# -- HEALTH ----------------------------------------------

@route("GET", r"/health")
def health(body, params, **_):
    return ok({"status": "ok", "service": "Finisio Cleans API", "version": "1.0.0"})


# -- USERS ------------------------------------------------

@route("POST", r"/api/users")
def create_user(body, params, **_):
    required = ["name", "email", "role"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    try:
        user = logic.create_user(
            name     = body["name"],
            email    = body["email"],
            role     = body["role"],
            phone    = body.get("phone"),
            address  = body.get("address"),
            password = body.get("password"),
        )
        return created(user)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/users")
def list_users(body, params, **_):
    role = params.get("role", [None])[0]
    return ok(logic.list_users(role))


@route("GET", r"/api/users/(?P<user_id>[^/]+)")
def get_user(body, params, user_id, **_):
    user = logic.get_user(user_id)
    if not user:
        return not_found("User")
    return ok(user)


@route("PATCH", r"/api/users/(?P<user_id>[^/]+)")
def update_user(body, params, user_id, **_):
    user = logic.get_user(user_id)
    if not user:
        return not_found("User")
    updatable = ["name", "phone", "address"]
    sets, vals = [], []
    for f in updatable:
        if f in body:
            sets.append(f"{f}=?")
            vals.append(body[f])
    if sets:
        vals.append(user_id)
        db.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
    return ok(logic.get_user(user_id))


# -- CLEANERS ---------------------------------------------

@route("GET", r"/api/cleaners")
def list_cleaners(body, params, **_):
    approved_only = params.get("approved_only", ["true"])[0].lower() != "false"
    return ok(logic.list_cleaners(approved_only))


@route("GET", r"/api/cleaners/(?P<user_id>[^/]+)")
def get_cleaner(body, params, user_id, **_):
    profile = logic.get_cleaner_profile(user_id)
    if not profile:
        return not_found("Cleaner")
    return ok(profile)


@route("PATCH", r"/api/cleaners/(?P<user_id>[^/]+)")
def update_cleaner(body, params, user_id, **_):
    try:
        profile = logic.update_cleaner_profile(
            user_id          = user_id,
            service_areas    = body.get("service_areas"),
            skills           = body.get("skills"),
            experience_years = body.get("experience_years"),
            id_document_url  = body.get("id_document_url"),
        )
        return ok(profile)
    except ValueError as e:
        return err(str(e))


@route("POST", r"/api/cleaners/(?P<user_id>[^/]+)/approve")
def approve_cleaner(body, params, user_id, **_):
    admin_id = body.get("admin_id")
    approve  = body.get("approve", True)
    if not admin_id:
        return err("admin_id is required")
    try:
        result = logic.approve_cleaner(user_id, admin_id, approve)
        return ok(result)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/cleaners/(?P<cleaner_id>[^/]+)/jobs")
def get_cleaner_jobs(body, params, cleaner_id, **_):
    status = params.get("status", [None])[0]
    jobs = logic.get_cleaner_jobs(cleaner_id, status)
    return ok(jobs)


# -- SUBSCRIPTIONS ----------------------------------------

@route("POST", r"/api/subscriptions")
def create_subscription(body, params, **_):
    required = ["customer_id", "plan_type"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    try:
        sub = logic.create_subscription(body["customer_id"], body["plan_type"])
        return created(sub)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/subscriptions/(?P<sub_id>[^/]+)")
def get_subscription(body, params, sub_id, **_):
    sub = logic.get_subscription(sub_id)
    if not sub:
        return not_found("Subscription")
    return ok(sub)


@route("GET", r"/api/customers/(?P<customer_id>[^/]+)/subscription")
def get_customer_subscription(body, params, customer_id, **_):
    sub = logic.get_active_subscription(customer_id)
    if not sub:
        return not_found("Active subscription")
    return ok(sub)


@route("POST", r"/api/subscriptions/renew")
def renew_subscriptions(body, params, **_):
    renewed = logic.renew_subscriptions()
    return ok({"renewed_count": len(renewed), "renewed_ids": renewed})


# -- BOOKINGS ---------------------------------------------

@route("POST", r"/api/bookings")
def create_booking(body, params, **_):
    required = ["customer_id", "service_type", "booking_type", "scheduled_date"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    try:
        booking = logic.create_booking(**{
            "customer_id":     body["customer_id"],
            "service_type":    body["service_type"],
            "booking_type":    body["booking_type"],
            "scheduled_date":  body["scheduled_date"],
            "scheduled_time":  body.get("scheduled_time"),
            "address":         body.get("address"),
            "notes":           body.get("notes"),
            "media_urls":      body.get("media_urls"),
            "hours_booked":    body.get("hours_booked"),
            "subscription_id": body.get("subscription_id"),
        })
        return created(booking)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/bookings")
def list_bookings(body, params, **_):
    status = params.get("status", [None])[0]
    limit  = int(params.get("limit", [50])[0])
    offset = int(params.get("offset", [0])[0])
    return ok(logic.list_bookings(status, limit, offset))


@route("GET", r"/api/bookings/(?P<booking_id>[^/]+)")
def get_booking(body, params, booking_id, **_):
    booking = logic.get_booking(booking_id)
    if not booking:
        return not_found("Booking")
    return ok(booking)


@route("GET", r"/api/bookings/(?P<booking_id>[^/]+)/history")
def get_booking_history(body, params, booking_id, **_):
    history = logic.get_booking_history(booking_id)
    return ok(history)


@route("POST", r"/api/bookings/(?P<booking_id>[^/]+)/assign")
def assign_cleaner(body, params, booking_id, **_):
    cleaner_id = body.get("cleaner_id")
    admin_id   = body.get("admin_id")
    if not cleaner_id or not admin_id:
        return err("cleaner_id and admin_id are required")
    try:
        booking = logic.assign_cleaner(booking_id, cleaner_id, admin_id)
        return ok(booking)
    except ValueError as e:
        return err(str(e))


@route("PATCH", r"/api/bookings/(?P<booking_id>[^/]+)/status")
def update_job_status(body, params, booking_id, **_):
    new_status = body.get("status")
    changed_by = body.get("changed_by")
    note       = body.get("note")
    if not new_status or not changed_by:
        return err("status and changed_by are required")
    try:
        booking = logic.update_job_status(booking_id, new_status, changed_by, note)
        return ok(booking)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/customers/(?P<customer_id>[^/]+)/bookings")
def get_customer_bookings(body, params, customer_id, **_):
    status = params.get("status", [None])[0]
    return ok(logic.get_customer_bookings(customer_id, status))


# -- PAYMENTS ---------------------------------------------

@route("POST", r"/api/payments")
def create_payment(body, params, **_):
    required = ["booking_id", "customer_id", "amount_scr"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    try:
        payment = logic.create_payment(
            booking_id     = body["booking_id"],
            customer_id    = body["customer_id"],
            amount_scr     = float(body["amount_scr"]),
            payment_method = body.get("payment_method", "bank_transfer"),
            reference_no   = body.get("reference_no"),
        )
        return created(payment)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/payments")
def list_payments(body, params, **_):
    status = params.get("status", [None])[0]
    return ok(logic.list_payments(status))


@route("GET", r"/api/payments/(?P<payment_id>[^/]+)")
def get_payment(body, params, payment_id, **_):
    payment = logic.get_payment(payment_id)
    if not payment:
        return not_found("Payment")
    return ok(payment)


@route("POST", r"/api/payments/(?P<payment_id>[^/]+)/confirm")
def confirm_payment(body, params, payment_id, **_):
    admin_id     = body.get("admin_id")
    reference_no = body.get("reference_no")
    if not admin_id:
        return err("admin_id is required")
    try:
        result = logic.confirm_payment(payment_id, admin_id, reference_no)
        return ok(result)
    except ValueError as e:
        return err(str(e))


@route("POST", r"/api/payments/(?P<payment_id>[^/]+)/reject")
def reject_payment(body, params, payment_id, **_):
    admin_id = body.get("admin_id")
    if not admin_id:
        return err("admin_id is required")
    try:
        return ok(logic.reject_payment(payment_id, admin_id))
    except ValueError as e:
        return err(str(e))


# -- COMMISSION -------------------------------------------

@route("POST", r"/api/commission/calculate")
def calculate_commission(body, params, **_):
    booking_id = body.get("booking_id")
    if not booking_id:
        return err("booking_id is required")
    try:
        commission = logic.calculate_commission(booking_id, body.get("payment_id"))
        return ok(commission)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/commission")
def list_commissions(body, params, **_):
    return ok(db.fetchall("SELECT * FROM commissions ORDER BY created_at DESC"))


@route("GET", r"/api/commission/summary")
def commission_summary(body, params, **_):
    return ok(logic.get_commission_summary())


@route("POST", r"/api/commission/(?P<booking_id>[^/]+)/settle")
def settle_commission(body, params, booking_id, **_):
    admin_id = body.get("admin_id")
    if not admin_id:
        return err("admin_id is required")
    try:
        result = logic.settle_commission(booking_id, admin_id)
        return ok(result)
    except ValueError as e:
        return err(str(e))


# -- REVIEWS ----------------------------------------------

@route("POST", r"/api/reviews")
def submit_review(body, params, **_):
    required = ["booking_id", "customer_id", "rating"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    rating = body["rating"]
    if not isinstance(rating, int) or rating < 1 or rating > 5:
        return err("rating must be an integer between 1 and 5")
    try:
        review = logic.submit_review(
            booking_id  = body["booking_id"],
            customer_id = body["customer_id"],
            rating      = rating,
            comment     = body.get("comment"),
        )
        return ok(review)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/reviews/cleaner/(?P<cleaner_id>[^/]+)")
def get_cleaner_reviews(body, params, cleaner_id, **_):
    reviews = db.fetchall(
        "SELECT * FROM reviews WHERE cleaner_id=? ORDER BY created_at DESC",
        (cleaner_id,)
    )
    return ok(reviews)


# -- MESSAGES ---------------------------------------------

@route("POST", r"/api/messages")
def send_message(body, params, **_):
    required = ["sender_id", "receiver_id", "message_text"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    msg = logic.send_message(
        sender_id    = body["sender_id"],
        receiver_id  = body["receiver_id"],
        message_text = body["message_text"],
        booking_id   = body.get("booking_id"),
    )
    return created(msg)


@route("GET", r"/api/messages")
def get_messages(body, params, **_):
    user_a     = params.get("user_a", [None])[0]
    user_b     = params.get("user_b", [None])[0]
    booking_id = params.get("booking_id", [None])[0]
    if not user_a or not user_b:
        return err("user_a and user_b query params required")
    msgs = logic.get_conversation(user_a, user_b, booking_id)
    return ok(msgs)


@route("POST", r"/api/messages/read")
def mark_read(body, params, **_):
    logic.mark_messages_read(body.get("receiver_id"), body.get("sender_id"))
    return ok({"marked_read": True})


# -- DISPUTES ---------------------------------------------

@route("POST", r"/api/disputes")
def raise_dispute(body, params, **_):
    required = ["booking_id", "raised_by", "description"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    try:
        dispute = logic.raise_dispute(body["booking_id"], body["raised_by"], body["description"])
        return created(dispute)
    except ValueError as e:
        return err(str(e))


@route("POST", r"/api/disputes/(?P<dispute_id>[^/]+)/resolve")
def resolve_dispute(body, params, dispute_id, **_):
    admin_id   = body.get("admin_id")
    resolution = body.get("resolution")
    if not admin_id or not resolution:
        return err("admin_id and resolution are required")
    result = logic.resolve_dispute(dispute_id, admin_id, resolution)
    return ok(result)


@route("GET", r"/api/disputes")
def list_disputes(body, params, **_):
    return ok(db.fetchall("SELECT * FROM disputes ORDER BY created_at DESC"))


# -- ADMIN ANALYTICS --------------------------------------

@route("GET", r"/api/admin/analytics")
def get_analytics(body, params, **_):
    return ok(logic.get_analytics())


# -- SUBSCRIPTION PLANS -----------------------------------

@route("GET", r"/api/plans")
def get_plans(body, params, **_):
    plans = [{"id": k, **v} for k, v in logic.SUBSCRIPTION_PLANS.items()]
    return ok(plans)


# -- CLOCK RECORDS ----------------------------------------

@route("POST", r"/api/clock")
def clock_action(body, params, **_):
    import uuid
    cleaner_id = body.get("cleaner_id")
    action     = body.get("action")   # "in" or "out"
    timestamp  = body.get("timestamp")
    date       = body.get("date")
    if not all([cleaner_id, action, timestamp, date]):
        return err("Missing required fields: cleaner_id, action, timestamp, date")
    existing = db.fetchone(
        "SELECT * FROM clock_records WHERE cleaner_id=? AND date=?",
        (cleaner_id, date)
    )
    if action == "in":
        if existing:
            db.execute(
                "UPDATE clock_records SET clock_in=?, clock_out=NULL WHERE cleaner_id=? AND date=?",
                (timestamp, cleaner_id, date)
            )
        else:
            db.execute(
                "INSERT INTO clock_records (id, cleaner_id, date, clock_in) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), cleaner_id, date, timestamp)
            )
    elif action == "out":
        if not existing:
            return err("Cannot clock out — no clock-in record found for today")
        db.execute(
            "UPDATE clock_records SET clock_out=? WHERE cleaner_id=? AND date=?",
            (timestamp, cleaner_id, date)
        )
    else:
        return err("action must be 'in' or 'out'")
    record = db.fetchone(
        "SELECT * FROM clock_records WHERE cleaner_id=? AND date=?",
        (cleaner_id, date)
    )
    return ok(record)


@route("GET", r"/api/clock")
def get_clock_records(body, params, **_):
    cleaner_id = params.get("cleaner_id", [None])[0]
    date       = params.get("date", [None])[0]
    if cleaner_id and date:
        record = db.fetchone(
            "SELECT * FROM clock_records WHERE cleaner_id=? AND date=?",
            (cleaner_id, date)
        )
        return ok(record)
    elif cleaner_id:
        records = db.fetchall(
            "SELECT * FROM clock_records WHERE cleaner_id=? ORDER BY date DESC",
            (cleaner_id,)
        )
        return ok(records)
    else:
        # Admin view — all cleaners with their names
        records = db.fetchall(
            "SELECT cr.*, u.name as cleaner_name, u.phone as cleaner_phone "
            "FROM clock_records cr "
            "JOIN users u ON cr.cleaner_id = u.id "
            "ORDER BY cr.date DESC, cr.clock_in DESC "
            "LIMIT 200"
        )
        return ok(records)


@route("PATCH", r"/api/clock/(?P<record_id>[^/]+)/approve")
def approve_clock(body, params, record_id, **_):
    approved_hours = body.get("approved_hours")
    admin_id       = body.get("admin_id")
    notes          = body.get("notes", "")
    if approved_hours is None or not admin_id:
        return err("approved_hours and admin_id are required")
    db.execute(
        "UPDATE clock_records SET approved=1, approved_hours=?, approved_by=?, notes=? WHERE id=?",
        (float(approved_hours), admin_id, notes, record_id)
    )
    record = db.fetchone("SELECT * FROM clock_records WHERE id=?", (record_id,))
    if not record:
        return not_found("Clock record")
    return ok(record)


@route("DELETE", r"/api/clock/(?P<record_id>[^/]+)")
def delete_clock_record(body, params, record_id, **_):
    """Admin can delete a clock record if entered in error."""
    db.execute("DELETE FROM clock_records WHERE id=?", (record_id,))
    return ok({"deleted": record_id})


# ---------------------------------------------------------
#  REQUEST HANDLER
# ---------------------------------------------------------

class FinisioHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path} -> {args[1]}")

    def _send(self, status_code, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        for route_method, pattern, handler in ROUTES:
            if route_method != method:
                continue
            m = pattern.match(path)
            if m:
                kwargs = m.groupdict()
                body = self._read_body()
                try:
                    status, payload = handler(body=body, params=params, **kwargs)
                    self._send(status, payload)
                except Exception as e:
                    traceback.print_exc()
                    self._send(500, {"ok": False, "error": "Internal server error", "detail": str(e)})
                return

        self._send(404, {"ok": False, "error": f"Route not found: {method} {path}"})

    def do_GET(self):    self._dispatch("GET")
    def do_POST(self):   self._dispatch("POST")
    def do_PATCH(self):  self._dispatch("PATCH")
    def do_DELETE(self): self._dispatch("DELETE")


# ---------------------------------------------------------
#  ENTRY POINT
# ---------------------------------------------------------

def main():
    global PORT
    PORT = int(os.environ.get("PORT", 8000))

    print("=" * 52)
    print("  FINISIO CLEANS - API Server")
    print("=" * 52)
    db.init_db()

    httpd = HTTPServer(("0.0.0.0", PORT), FinisioHandler)
    print(f"  Listening on  http://localhost:{PORT}")
    print(f"  API Docs      http://localhost:{PORT}/docs    (open in browser)")
    print(f"  Health        http://localhost:{PORT}/health")
    print("  Press Ctrl+C to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()