"""
FINISIO CLEANS - HTTP API Server
Pure Python stdlib HTTP server.

Phase 1 security: JWT auth, bcrypt passwords, /api/auth/login, locked /api/users.
Phase 1.5: Server-side client IP capture for signups + frontend country/city.

Run:  python3 server.py
      python3 server.py --port 9000
"""
import os
import json
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import db
import logic
import auth

PORT = int(os.environ.get("PORT", 8080))

# Set STRICT_AUTH=true on Railway to enforce JWT on all protected routes.
# Until then we run in compatibility mode — old clients keep working.
STRICT_AUTH = os.environ.get("STRICT_AUTH", "false").lower() == "true"


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

def forbidden(message="Forbidden"):
    return err(message, 403)

def unauthorized(message="Authentication required"):
    return err(message, 401)


# ---------------------------------------------------------
#  ROUTER  (pattern -> handler map)
# ---------------------------------------------------------

ROUTES = []

def route(method, pattern, auth_required=False, admin_only=False):
    """
    Decorator to register a route.

    auth_required=True  -> caller must send a valid JWT (in strict mode)
    admin_only=True     -> caller must be admin (always enforced when token is present)
    """
    def decorator(fn):
        ROUTES.append({
            "method": method.upper(),
            "pattern": re.compile(f"^{pattern}$"),
            "handler": fn,
            "auth_required": auth_required,
            "admin_only": admin_only,
        })
        return fn
    return decorator


# ---------------------------------------------------------
#  AUTH ROUTES
# ---------------------------------------------------------

@route("POST", r"/api/auth/login")
def login(body, params, **_):
    """
    Email + password login. Returns a JWT on success.
    Auto-upgrades sha256 password hashes to bcrypt on successful login.
    """
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return err("Email and password are required")

    user_row = db.fetchone("SELECT * FROM users WHERE LOWER(email)=?", (email,))
    if not user_row:
        return unauthorized("Invalid email or password")

    stored_hash = user_row.get("password_hash")
    if not auth.verify_password(password, stored_hash):
        return unauthorized("Invalid email or password")

    if auth.needs_upgrade(stored_hash):
        try:
            new_hash = auth.hash_password_bcrypt(password)
            db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (new_hash, user_row["id"])
            )
            print(f"[AUTH] Upgraded password hash to bcrypt for user {user_row['id']}")
        except Exception as e:
            print(f"[AUTH] Hash upgrade failed for {user_row['id']}: {e}")

    token = auth.issue_token(user_row["id"], user_row["role"])

    safe_user = {k: v for k, v in user_row.items() if k != "password_hash"}
    return ok({"token": token, "user": safe_user})


@route("POST", r"/api/auth/verify")
def verify_session(body, params, _auth=None, **_):
    """
    Verify a JWT is still valid. Returns the user if valid.
    """
    if not _auth:
        return unauthorized("Invalid or expired token")
    user = logic.get_user(_auth["sub"])
    if not user:
        return unauthorized("User no longer exists")
    return ok({"user": user, "role": _auth["role"]})


# ---------------------------------------------------------
#  HEALTH
# ---------------------------------------------------------

@route("GET", r"/health")
def health(body, params, **_):
    return ok({
        "status": "ok",
        "service": "Finisio Cleans API",
        "version": "1.5.0",
        "auth_mode": "strict" if STRICT_AUTH else "compatibility",
    })


# ---------------------------------------------------------
#  USERS
# ---------------------------------------------------------

@route("POST", r"/api/users")
def create_user(body, params, _client_ip=None, **_):
    """Public — anyone can sign up. Captures location for audit trail."""
    required = ["name", "email", "role"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if body.get("role") == "admin":
        return forbidden("Admin accounts cannot be created via signup")
    try:
        # Server-captured IP always wins over what frontend sends —
        # frontend can lie, server can't be lied to.
        user = logic.create_user(
            name           = body["name"],
            email          = body["email"],
            role           = body["role"],
            phone          = body.get("phone"),
            address        = body.get("address"),
            password       = body.get("password"),
            signup_country = body.get("signup_country"),
            signup_city    = body.get("signup_city"),
            signup_ip      = _client_ip or body.get("signup_ip"),
        )
        return created(user)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/users", auth_required=True, admin_only=True)
def list_users(body, params, _auth=None, **_):
    """LOCKED DOWN — admin-only."""
    role = params.get("role", [None])[0]
    return ok(logic.list_users(role))


@route("GET", r"/api/users/by-email")
def get_user_by_email(body, params, _auth=None, **_):
    """
    Look up a single user by email — minimal info only.
    Used by Google Sign-In to check if an account already exists.
    """
    email = (params.get("email", [""])[0] or "").strip().lower()
    if not email:
        return err("email query param required")
    user = db.fetchone(
        "SELECT id, name, email, role, verification_status FROM users WHERE LOWER(email)=?",
        (email,)
    )
    if not user:
        return not_found("User")
    return ok(user)


@route("GET", r"/api/users/(?P<user_id>[^/]+)", auth_required=True)
def get_user(body, params, user_id, _auth=None, **_):
    """A user can fetch their own record. Admins can fetch anyone's."""
    if _auth and _auth["sub"] != user_id and _auth["role"] != "admin":
        return forbidden("You can only access your own user record")
    user = logic.get_user(user_id)
    if not user:
        return not_found("User")
    return ok(user)


@route("PATCH", r"/api/users/(?P<user_id>[^/]+)", auth_required=True)
def update_user(body, params, user_id, _auth=None, **_):
    """A user can update their own record. Admins can update anyone."""
    if _auth and _auth["sub"] != user_id and _auth["role"] != "admin":
        return forbidden("You can only update your own user record")
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


@route("PATCH", r"/api/cleaners/(?P<user_id>[^/]+)", auth_required=True)
def update_cleaner(body, params, user_id, _auth=None, **_):
    if _auth and _auth["sub"] != user_id and _auth["role"] != "admin":
        return forbidden("You can only update your own cleaner profile")
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


@route("POST", r"/api/cleaners/(?P<user_id>[^/]+)/approve", auth_required=True, admin_only=True)
def approve_cleaner(body, params, user_id, _auth=None, **_):
    admin_id = _auth["sub"] if _auth else body.get("admin_id")
    approve  = body.get("approve", True)
    if not admin_id:
        return err("admin_id is required")
    try:
        result = logic.approve_cleaner(user_id, admin_id, approve)
        return ok(result)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/cleaners/(?P<cleaner_id>[^/]+)/jobs", auth_required=True)
def get_cleaner_jobs(body, params, cleaner_id, _auth=None, **_):
    if _auth and _auth["sub"] != cleaner_id and _auth["role"] != "admin":
        return forbidden("You can only access your own jobs")
    status = params.get("status", [None])[0]
    jobs = logic.get_cleaner_jobs(cleaner_id, status)
    return ok(jobs)


# -- SUBSCRIPTIONS ----------------------------------------

@route("POST", r"/api/subscriptions", auth_required=True)
def create_subscription(body, params, _auth=None, **_):
    required = ["customer_id", "plan_type"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if _auth and _auth["sub"] != body["customer_id"] and _auth["role"] != "admin":
        return forbidden("You can only create subscriptions for yourself")
    try:
        sub = logic.create_subscription(body["customer_id"], body["plan_type"])
        return created(sub)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/subscriptions/(?P<sub_id>[^/]+)", auth_required=True)
def get_subscription(body, params, sub_id, **_):
    sub = logic.get_subscription(sub_id)
    if not sub:
        return not_found("Subscription")
    return ok(sub)


@route("GET", r"/api/customers/(?P<customer_id>[^/]+)/subscription", auth_required=True)
def get_customer_subscription(body, params, customer_id, _auth=None, **_):
    if _auth and _auth["sub"] != customer_id and _auth["role"] != "admin":
        return forbidden()
    sub = logic.get_active_subscription(customer_id)
    if not sub:
        return not_found("Active subscription")
    return ok(sub)


@route("POST", r"/api/subscriptions/renew", auth_required=True, admin_only=True)
def renew_subscriptions(body, params, **_):
    renewed = logic.renew_subscriptions()
    return ok({"renewed_count": len(renewed), "renewed_ids": renewed})


# -- BOOKINGS ---------------------------------------------

@route("POST", r"/api/bookings", auth_required=True)
def create_booking(body, params, _auth=None, **_):
    required = ["customer_id", "service_type", "booking_type", "scheduled_date"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if _auth and _auth["sub"] != body["customer_id"] and _auth["role"] != "admin":
        return forbidden("You can only book for yourself")
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


@route("GET", r"/api/bookings", auth_required=True, admin_only=True)
def list_bookings(body, params, **_):
    """Admin-only — used by admin dashboard to see all bookings."""
    status = params.get("status", [None])[0]
    limit  = int(params.get("limit", [50])[0])
    offset = int(params.get("offset", [0])[0])
    return ok(logic.list_bookings(status, limit, offset))


@route("GET", r"/api/bookings/(?P<booking_id>[^/]+)", auth_required=True)
def get_booking(body, params, booking_id, _auth=None, **_):
    booking = logic.get_booking(booking_id)
    if not booking:
        return not_found("Booking")
    if _auth and _auth["role"] != "admin":
        if _auth["sub"] not in (booking.get("customer_id"), booking.get("cleaner_id")):
            return forbidden()
    return ok(booking)


@route("GET", r"/api/bookings/(?P<booking_id>[^/]+)/history", auth_required=True)
def get_booking_history(body, params, booking_id, **_):
    history = logic.get_booking_history(booking_id)
    return ok(history)


@route("POST", r"/api/bookings/(?P<booking_id>[^/]+)/assign", auth_required=True, admin_only=True)
def assign_cleaner(body, params, booking_id, _auth=None, **_):
    cleaner_id = body.get("cleaner_id")
    admin_id   = _auth["sub"] if _auth else body.get("admin_id")
    if not cleaner_id or not admin_id:
        return err("cleaner_id and admin_id are required")
    try:
        booking = logic.assign_cleaner(booking_id, cleaner_id, admin_id)
        return ok(booking)
    except ValueError as e:
        return err(str(e))


@route("PATCH", r"/api/bookings/(?P<booking_id>[^/]+)/status", auth_required=True)
def update_job_status(body, params, booking_id, _auth=None, **_):
    new_status = body.get("status")
    changed_by = _auth["sub"] if _auth else body.get("changed_by")
    note       = body.get("note")
    if not new_status or not changed_by:
        return err("status and changed_by are required")
    try:
        booking = logic.update_job_status(booking_id, new_status, changed_by, note)
        return ok(booking)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/customers/(?P<customer_id>[^/]+)/bookings", auth_required=True)
def get_customer_bookings(body, params, customer_id, _auth=None, **_):
    if _auth and _auth["sub"] != customer_id and _auth["role"] != "admin":
        return forbidden("You can only access your own bookings")
    status = params.get("status", [None])[0]
    return ok(logic.get_customer_bookings(customer_id, status))


# -- PAYMENTS ---------------------------------------------

@route("POST", r"/api/payments", auth_required=True)
def create_payment(body, params, _auth=None, **_):
    required = ["booking_id", "customer_id", "amount_scr"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if _auth and _auth["sub"] != body["customer_id"] and _auth["role"] != "admin":
        return forbidden()
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


@route("GET", r"/api/payments", auth_required=True, admin_only=True)
def list_payments(body, params, **_):
    status = params.get("status", [None])[0]
    return ok(logic.list_payments(status))


@route("GET", r"/api/payments/(?P<payment_id>[^/]+)", auth_required=True)
def get_payment(body, params, payment_id, **_):
    payment = logic.get_payment(payment_id)
    if not payment:
        return not_found("Payment")
    return ok(payment)


@route("POST", r"/api/payments/(?P<payment_id>[^/]+)/confirm", auth_required=True, admin_only=True)
def confirm_payment(body, params, payment_id, _auth=None, **_):
    admin_id     = _auth["sub"] if _auth else body.get("admin_id")
    reference_no = body.get("reference_no")
    if not admin_id:
        return err("admin_id is required")
    try:
        result = logic.confirm_payment(payment_id, admin_id, reference_no)
        return ok(result)
    except ValueError as e:
        return err(str(e))


@route("POST", r"/api/payments/(?P<payment_id>[^/]+)/reject", auth_required=True, admin_only=True)
def reject_payment(body, params, payment_id, _auth=None, **_):
    admin_id = _auth["sub"] if _auth else body.get("admin_id")
    if not admin_id:
        return err("admin_id is required")
    try:
        return ok(logic.reject_payment(payment_id, admin_id))
    except ValueError as e:
        return err(str(e))


# -- COMMISSION -------------------------------------------

@route("POST", r"/api/commission/calculate", auth_required=True, admin_only=True)
def calculate_commission(body, params, **_):
    booking_id = body.get("booking_id")
    if not booking_id:
        return err("booking_id is required")
    try:
        commission = logic.calculate_commission(booking_id, body.get("payment_id"))
        return ok(commission)
    except ValueError as e:
        return err(str(e))


@route("GET", r"/api/commission", auth_required=True, admin_only=True)
def list_commissions(body, params, **_):
    return ok(db.fetchall("SELECT * FROM commissions ORDER BY created_at DESC"))


@route("GET", r"/api/commission/summary", auth_required=True, admin_only=True)
def commission_summary(body, params, **_):
    return ok(logic.get_commission_summary())


@route("POST", r"/api/commission/(?P<booking_id>[^/]+)/settle", auth_required=True, admin_only=True)
def settle_commission(body, params, booking_id, _auth=None, **_):
    admin_id = _auth["sub"] if _auth else body.get("admin_id")
    if not admin_id:
        return err("admin_id is required")
    try:
        result = logic.settle_commission(booking_id, admin_id)
        return ok(result)
    except ValueError as e:
        return err(str(e))


# -- REVIEWS ----------------------------------------------

@route("POST", r"/api/reviews", auth_required=True)
def submit_review(body, params, _auth=None, **_):
    required = ["booking_id", "customer_id", "rating"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if _auth and _auth["sub"] != body["customer_id"] and _auth["role"] != "admin":
        return forbidden()
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

@route("POST", r"/api/messages", auth_required=True)
def send_message(body, params, _auth=None, **_):
    required = ["sender_id", "receiver_id", "message_text"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if _auth and _auth["sub"] != body["sender_id"] and _auth["role"] != "admin":
        return forbidden("You can only send messages as yourself")
    msg = logic.send_message(
        sender_id    = body["sender_id"],
        receiver_id  = body["receiver_id"],
        message_text = body["message_text"],
        booking_id   = body.get("booking_id"),
    )
    return created(msg)


@route("GET", r"/api/messages", auth_required=True)
def get_messages(body, params, _auth=None, **_):
    user_a     = params.get("user_a", [None])[0]
    user_b     = params.get("user_b", [None])[0]
    booking_id = params.get("booking_id", [None])[0]
    if not user_a or not user_b:
        return err("user_a and user_b query params required")
    if _auth and _auth["sub"] not in (user_a, user_b) and _auth["role"] != "admin":
        return forbidden()
    msgs = logic.get_conversation(user_a, user_b, booking_id)
    return ok(msgs)


@route("POST", r"/api/messages/read", auth_required=True)
def mark_read(body, params, _auth=None, **_):
    if _auth and _auth["sub"] != body.get("receiver_id") and _auth["role"] != "admin":
        return forbidden()
    logic.mark_messages_read(body.get("receiver_id"), body.get("sender_id"))
    return ok({"marked_read": True})


# -- DISPUTES ---------------------------------------------

@route("POST", r"/api/disputes", auth_required=True)
def raise_dispute(body, params, _auth=None, **_):
    required = ["booking_id", "raised_by", "description"]
    for f in required:
        if f not in body:
            return err(f"Missing field: {f}")
    if _auth and _auth["sub"] != body["raised_by"] and _auth["role"] != "admin":
        return forbidden()
    try:
        dispute = logic.raise_dispute(body["booking_id"], body["raised_by"], body["description"])
        return created(dispute)
    except ValueError as e:
        return err(str(e))


@route("POST", r"/api/disputes/(?P<dispute_id>[^/]+)/resolve", auth_required=True, admin_only=True)
def resolve_dispute(body, params, dispute_id, _auth=None, **_):
    admin_id   = _auth["sub"] if _auth else body.get("admin_id")
    resolution = body.get("resolution")
    if not admin_id or not resolution:
        return err("admin_id and resolution are required")
    result = logic.resolve_dispute(dispute_id, admin_id, resolution)
    return ok(result)


@route("GET", r"/api/disputes", auth_required=True, admin_only=True)
def list_disputes(body, params, **_):
    return ok(db.fetchall("SELECT * FROM disputes ORDER BY created_at DESC"))


# -- ADMIN ANALYTICS --------------------------------------

@route("GET", r"/api/admin/analytics", auth_required=True, admin_only=True)
def get_analytics(body, params, **_):
    return ok(logic.get_analytics())


# -- SUBSCRIPTION PLANS -----------------------------------

@route("GET", r"/api/plans")
def get_plans(body, params, **_):
    plans = [{"id": k, **v} for k, v in logic.SUBSCRIPTION_PLANS.items()]
    return ok(plans)


# -- CLOCK RECORDS ----------------------------------------

@route("POST", r"/api/clock", auth_required=True)
def clock_action(body, params, _auth=None, **_):
    import uuid
    cleaner_id = body.get("cleaner_id")
    action     = body.get("action")
    timestamp  = body.get("timestamp")
    date       = body.get("date")
    if not all([cleaner_id, action, timestamp, date]):
        return err("Missing required fields: cleaner_id, action, timestamp, date")
    if _auth and _auth["sub"] != cleaner_id and _auth["role"] != "admin":
        return forbidden("You can only clock in/out as yourself")
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


@route("GET", r"/api/clock", auth_required=True)
def get_clock_records(body, params, _auth=None, **_):
    cleaner_id = params.get("cleaner_id", [None])[0]
    date       = params.get("date", [None])[0]
    if _auth and _auth["role"] != "admin":
        if not cleaner_id or cleaner_id != _auth["sub"]:
            return forbidden("You can only view your own clock records")
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
        records = db.fetchall(
            "SELECT cr.*, u.name as cleaner_name, u.phone as cleaner_phone "
            "FROM clock_records cr "
            "JOIN users u ON cr.cleaner_id = u.id "
            "ORDER BY cr.date DESC, cr.clock_in DESC "
            "LIMIT 200"
        )
        return ok(records)


@route("PATCH", r"/api/clock/(?P<record_id>[^/]+)/approve", auth_required=True, admin_only=True)
def approve_clock(body, params, record_id, _auth=None, **_):
    approved_hours = body.get("approved_hours")
    admin_id       = _auth["sub"] if _auth else body.get("admin_id")
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


@route("DELETE", r"/api/clock/(?P<record_id>[^/]+)", auth_required=True, admin_only=True)
def delete_clock_record(body, params, record_id, **_):
    db.execute("DELETE FROM clock_records WHERE id=?", (record_id,))
    return ok({"deleted": record_id})


# ---------------------------------------------------------
#  REQUEST HANDLER
# ---------------------------------------------------------

class FinisioHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        try:
            print(f"  {self.command} {self.path} -> {args[1]}")
        except Exception:
            pass

    def _send(self, status_code, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
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

    def _get_auth_payload(self):
        """Extract and verify JWT from Authorization header. Returns payload dict or None."""
        token = auth.extract_token_from_header(self.headers.get("Authorization"))
        return auth.verify_token(token)

    def _get_client_ip(self):
        """
        Extract the real client IP, accounting for Railway's reverse proxy.
        Order:
          1. X-Forwarded-For (Railway/Cloudflare/standard proxy header) — leftmost is original client
          2. X-Real-IP (some proxies use this)
          3. self.client_address (direct connection — only if no proxy)
        """
        # X-Forwarded-For can be a comma-separated list — first IP is the original
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            # Take the first IP, strip whitespace
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip
        xri = self.headers.get("X-Real-IP", "")
        if xri:
            return xri.strip()
        # Fallback — direct connection
        try:
            return self.client_address[0]
        except Exception:
            return None

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        for r in ROUTES:
            if r["method"] != method:
                continue
            m = r["pattern"].match(path)
            if not m:
                continue

            auth_payload = self._get_auth_payload()
            client_ip = self._get_client_ip()

            if r["auth_required"] and not auth_payload:
                if STRICT_AUTH:
                    self._send(401, {"ok": False, "error": "Authentication required"})
                    return

            if r["admin_only"]:
                if STRICT_AUTH and (not auth_payload or auth_payload.get("role") != "admin"):
                    self._send(403, {"ok": False, "error": "Admin access required"})
                    return
                if auth_payload and auth_payload.get("role") != "admin":
                    self._send(403, {"ok": False, "error": "Admin access required"})
                    return

            kwargs = m.groupdict()
            body   = self._read_body()
            try:
                status, payload = r["handler"](
                    body=body,
                    params=params,
                    _auth=auth_payload,
                    _client_ip=client_ip,
                    **kwargs
                )
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
    PORT = int(os.environ.get("PORT", 8080))

    print("=" * 52)
    print("  FINISIO CLEANS - API Server (v1.5.0)")
    print("=" * 52)
    db.init_db()

    print(f"  Auth mode:    {'STRICT' if STRICT_AUTH else 'COMPATIBILITY (old clients allowed)'}")
    print(f"  JWT secret:   {'CONFIGURED' if os.environ.get('JWT_SECRET') else 'MISSING — SET JWT_SECRET ON RAILWAY'}")
    print(f"  IP capture:   ENABLED (X-Forwarded-For aware)")

    httpd = HTTPServer(("0.0.0.0", PORT), FinisioHandler)
    print(f"  Listening on  http://localhost:{PORT}")
    print(f"  Health        http://localhost:{PORT}/health")
    print("  Press Ctrl+C to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
