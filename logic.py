"""
FINISIO CLEANS - Core Business Logic
All platform rules, commission calculations, subscription management,
job workflow transitions, and validation.

Phase 1.5 additions:
  - create_user accepts signup_country, signup_city, signup_ip
  - list_users / get_user automatically include those fields (admin can see them)
"""

import uuid
import hashlib
import json
from datetime import datetime, timedelta
import db
import auth

# ---------------------------------------------------------
#  CONSTANTS
# ---------------------------------------------------------

PLATFORM_COMMISSION_PCT = 40.0
CLEANER_SHARE_PCT = 60.0

SUBSCRIPTION_PLANS = {
    "basic":    {"price_scr": 1200, "hours": 4,  "label": "Basic"},
    "standard": {"price_scr": 1500, "hours": 6,  "label": "Standard"},
    "premium":  {"price_scr": 2000, "hours": 10, "label": "Premium"},
}

# Valid job status transitions (what each status can move to)
VALID_TRANSITIONS = {
    "pending":     {"assigned", "cancelled"},
    "assigned":    {"accepted", "cancelled"},
    "accepted":    {"in_progress", "cancelled"},
    "in_progress": {"completed", "disputed"},
    "completed":   {"disputed"},
    "disputed":    {"completed", "cancelled"},
    "cancelled":   set(),
}


# ---------------------------------------------------------
#  UTILITIES
# ---------------------------------------------------------

def new_id():
    return str(uuid.uuid4())


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def hash_password(password: str) -> str:
    """LEGACY sha256 hash — kept for backwards compatibility only.
    All new passwords use auth.hash_password_bcrypt() instead."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    """LEGACY verify — auth.verify_password() handles both bcrypt and sha256."""
    return hash_password(plain) == hashed


def to_json(obj) -> str:
    return json.dumps(obj) if obj is not None else "[]"


def from_json(text) -> list:
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


# ---------------------------------------------------------
#  USERS
# ---------------------------------------------------------

def create_user(name, email, role, phone=None, address=None, password=None,
                signup_country=None, signup_city=None, signup_ip=None):
    """Create a user. If role=cleaner, also creates a cleaner profile.
    New users are hashed with bcrypt (strong). Old sha256 users get
    auto-upgraded next time they log in.
    Phase 1.5: signup location captured (country/city from frontend, IP from server)."""
    if role not in ("customer", "cleaner", "admin"):
        raise ValueError("role must be customer, cleaner, or admin")

    # Check email uniqueness
    existing = db.fetchone("SELECT id FROM users WHERE email=?", (email,))
    if existing:
        raise ValueError(f"Email already registered: {email}")

    uid = new_id()

    # Hash with bcrypt (new users always get the strong hash)
    if password:
        pw_hash = auth.hash_password_bcrypt(password)
    else:
        pw_hash = auth.hash_password_bcrypt(new_id())

    # Trim/sanitise location strings — accept None happily
    sc = (signup_country or "").strip() or None
    si = (signup_city or "").strip() or None
    sip = (signup_ip or "").strip() or None

    db.execute(
        """INSERT INTO users
           (id,name,email,role,phone,address,password_hash,
            signup_country,signup_city,signup_ip,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, name, email, role, phone, address, pw_hash, sc, si, sip, now_iso())
    )

    # Auto-create cleaner profile
    if role == "cleaner":
        db.execute(
            "INSERT INTO cleaner_profiles (user_id, created_at) VALUES (?,?)",
            (uid, now_iso())
        )

    return get_user(uid)


def get_user(user_id):
    user = db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
    if user:
        user.pop("password_hash", None)  # never expose hash
    return user


def get_user_by_email(email):
    return db.fetchone(
        "SELECT * FROM users WHERE email=?", (email,)
    )


def list_users(role=None):
    if role:
        rows = db.fetchall("SELECT * FROM users WHERE role=? ORDER BY created_at DESC", (role,))
    else:
        rows = db.fetchall("SELECT * FROM users ORDER BY created_at DESC")
    for r in rows:
        r.pop("password_hash", None)
    return rows


# ---------------------------------------------------------
#  CLEANERS
# ---------------------------------------------------------

def update_cleaner_profile(user_id, service_areas=None, skills=None,
                            experience_years=None, id_document_url=None):
    """Update cleaner profile details."""
    user = db.fetchone("SELECT * FROM users WHERE id=? AND role='cleaner'", (user_id,))
    if not user:
        raise ValueError("Cleaner not found")

    updates = []
    params = []
    if service_areas is not None:
        updates.append("service_areas=?"); params.append(to_json(service_areas))
    if skills is not None:
        updates.append("skills=?");        params.append(to_json(skills))
    if experience_years is not None:
        updates.append("experience_years=?"); params.append(experience_years)
    if id_document_url is not None:
        updates.append("id_document_url=?"); params.append(id_document_url)

    if updates:
        params.append(user_id)
        db.execute(f"UPDATE cleaner_profiles SET {','.join(updates)} WHERE user_id=?", params)

    return get_cleaner_profile(user_id)


def approve_cleaner(user_id, admin_id, approve: bool):
    """Admin approves or rejects a cleaner application."""
    admin = db.fetchone("SELECT * FROM users WHERE id=? AND role='admin'", (admin_id,))
    if not admin:
        raise ValueError("Only admins can approve cleaners")

    status = "approved" if approve else "rejected"
    approved_at = now_iso() if approve else None

    db.execute(
        "UPDATE cleaner_profiles SET approved_status=?, approved_at=? WHERE user_id=?",
        (status, approved_at, user_id)
    )
    db.execute(
        "UPDATE users SET verification_status=? WHERE id=?",
        ("verified" if approve else "rejected", user_id)
    )
    return get_cleaner_profile(user_id)


def get_cleaner_profile(user_id):
    profile = db.fetchone("SELECT * FROM cleaner_profiles WHERE user_id=?", (user_id,))
    if profile:
        profile["service_areas"] = from_json(profile.get("service_areas"))
        profile["skills"]        = from_json(profile.get("skills"))
    return profile


def list_cleaners(approved_only=True):
    sql = """
        SELECT u.id, u.name, u.email, u.phone, u.verification_status,
               u.signup_country, u.signup_city, u.signup_ip,
               cp.approved_status, cp.service_areas, cp.skills,
               cp.rating, cp.total_jobs_completed, cp.experience_years
        FROM users u
        JOIN cleaner_profiles cp ON u.id = cp.user_id
    """
    if approved_only:
        sql += " WHERE cp.approved_status='approved'"
    sql += " ORDER BY cp.rating DESC"
    rows = db.fetchall(sql)
    for r in rows:
        r["service_areas"] = from_json(r.get("service_areas"))
        r["skills"]        = from_json(r.get("skills"))
    return rows


def update_cleaner_rating(cleaner_id):
    """Recalculate cleaner's average rating from all reviews."""
    result = db.fetchone(
        "SELECT AVG(rating) as avg_r, COUNT(*) as cnt FROM reviews WHERE cleaner_id=?",
        (cleaner_id,)
    )
    avg = round(result["avg_r"] or 0, 2)
    db.execute(
        "UPDATE cleaner_profiles SET rating=? WHERE user_id=?",
        (avg, cleaner_id)
    )
    return avg


# ---------------------------------------------------------
#  SUBSCRIPTIONS
# ---------------------------------------------------------

def create_subscription(customer_id, plan_type):
    """Create a new monthly subscription for a customer."""
    if plan_type not in SUBSCRIPTION_PLANS:
        raise ValueError(f"Unknown plan: {plan_type}. Choose: {list(SUBSCRIPTION_PLANS.keys())}")

    customer = db.fetchone("SELECT * FROM users WHERE id=? AND role='customer'", (customer_id,))
    if not customer:
        raise ValueError("Customer not found")

    db.execute(
        "UPDATE subscriptions SET status='cancelled' WHERE customer_id=? AND status='active'",
        (customer_id,)
    )

    plan = SUBSCRIPTION_PLANS[plan_type]
    sub_id = new_id()
    renewal = (datetime.utcnow() + timedelta(days=30)).date().isoformat()

    db.execute(
        """INSERT INTO subscriptions
           (id, customer_id, plan_type, price_scr, hours_allocated, hours_used,
            status, renewal_date, created_at)
           VALUES (?,?,?,?,?,0,'active',?,?)""",
        (sub_id, customer_id, plan_type, plan["price_scr"], plan["hours"], renewal, now_iso())
    )
    return get_subscription(sub_id)


def get_subscription(sub_id):
    return db.fetchone("SELECT * FROM subscriptions WHERE id=?", (sub_id,))


def get_active_subscription(customer_id):
    return db.fetchone(
        "SELECT * FROM subscriptions WHERE customer_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
        (customer_id,)
    )


def deduct_subscription_hours(sub_id, hours):
    """Deduct hours from a subscription. Raises if insufficient balance."""
    sub = get_subscription(sub_id)
    if not sub:
        raise ValueError("Subscription not found")
    remaining = sub["hours_allocated"] - sub["hours_used"]
    if hours > remaining:
        raise ValueError(f"Insufficient subscription hours. Remaining: {remaining}h, requested: {hours}h")
    db.execute(
        "UPDATE subscriptions SET hours_used = hours_used + ? WHERE id=?",
        (hours, sub_id)
    )


def renew_subscriptions():
    """Check and auto-renew expired subscriptions (run daily via cron/scheduler)."""
    today = datetime.utcnow().date().isoformat()
    expired = db.fetchall(
        "SELECT * FROM subscriptions WHERE status='active' AND renewal_date <= ?", (today,)
    )
    renewed = []
    for sub in expired:
        new_renewal = (datetime.utcnow() + timedelta(days=30)).date().isoformat()
        db.execute(
            "UPDATE subscriptions SET hours_used=0, renewal_date=? WHERE id=?",
            (new_renewal, sub["id"])
        )
        renewed.append(sub["id"])
    return renewed


# ---------------------------------------------------------
#  BOOKINGS
# ---------------------------------------------------------

def create_booking(customer_id, service_type, booking_type,
                   scheduled_date, scheduled_time=None, address=None,
                   notes=None, media_urls=None, hours_booked=None,
                   subscription_id=None):
    """Create a new booking. Goes straight to 'pending' queue for admin."""

    customer = db.fetchone("SELECT * FROM users WHERE id=? AND role='customer'", (customer_id,))
    if not customer:
        raise ValueError("Customer not found")

    if service_type not in ("home_deep", "post_construction"):
        raise ValueError("service_type must be home_deep or post_construction")

    if booking_type not in ("subscription", "one_time"):
        raise ValueError("booking_type must be subscription or one_time")

    if booking_type == "subscription":
        if not subscription_id:
            active_sub = get_active_subscription(customer_id)
            if not active_sub:
                raise ValueError("No active subscription found. Please subscribe first.")
            subscription_id = active_sub["id"]
        if hours_booked:
            deduct_subscription_hours(subscription_id, hours_booked)

    amount = _calculate_booking_amount(service_type, booking_type, hours_booked, subscription_id)

    booking_id = new_id()
    db.execute(
        """INSERT INTO bookings
           (id, customer_id, subscription_id, service_type, booking_type,
            status, scheduled_date, scheduled_time, address, notes,
            media_urls, hours_booked, amount_scr, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (booking_id, customer_id, subscription_id, service_type, booking_type,
         "pending", scheduled_date, scheduled_time, address, notes,
         to_json(media_urls or []), hours_booked, amount, now_iso(), now_iso())
    )

    _log_status_change(booking_id, None, "pending", customer_id, "Booking created")

    return get_booking(booking_id)


def _calculate_booking_amount(service_type, booking_type, hours, sub_id):
    """Determine the charge for a booking."""
    if booking_type == "subscription" and sub_id:
        sub = get_subscription(sub_id)
        if sub:
            return 0.0
    rates = {"home_deep": 300, "post_construction": 380}
    rate = rates.get(service_type, 300)
    return rate * (hours or 1)


def get_booking(booking_id):
    booking = db.fetchone("SELECT * FROM bookings WHERE id=?", (booking_id,))
    if booking:
        booking["media_urls"] = from_json(booking.get("media_urls"))
    return booking


def get_customer_bookings(customer_id, status=None):
    if status:
        rows = db.fetchall(
            "SELECT * FROM bookings WHERE customer_id=? AND status=? ORDER BY created_at DESC",
            (customer_id, status)
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM bookings WHERE customer_id=? ORDER BY created_at DESC",
            (customer_id,)
        )
    for r in rows:
        r["media_urls"] = from_json(r.get("media_urls"))
    return rows


def get_cleaner_jobs(cleaner_id, status=None):
    if status:
        rows = db.fetchall(
            "SELECT * FROM bookings WHERE cleaner_id=? AND status=? ORDER BY scheduled_date",
            (cleaner_id, status)
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM bookings WHERE cleaner_id=? ORDER BY scheduled_date DESC",
            (cleaner_id,)
        )
    for r in rows:
        r["media_urls"] = from_json(r.get("media_urls"))
    return rows


def list_bookings(status=None, limit=50, offset=0):
    """Admin: list all bookings with optional filter."""
    if status:
        rows = db.fetchall(
            "SELECT * FROM bookings WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset)
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM bookings ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
    for r in rows:
        r["media_urls"] = from_json(r.get("media_urls"))
    return rows


def assign_cleaner(booking_id, cleaner_id, admin_id):
    """Admin manually assigns a cleaner to a booking."""
    admin = db.fetchone("SELECT * FROM users WHERE id=? AND role='admin'", (admin_id,))
    if not admin:
        raise ValueError("Only admins can assign cleaners")

    booking = get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")
    if booking["status"] == "completed":
        raise ValueError(f"Cannot reassign a completed booking. Current: {booking['status']}")

    cleaner = db.fetchone(
        "SELECT cp.approved_status FROM cleaner_profiles cp WHERE cp.user_id=?",
        (cleaner_id,)
    )
    if not cleaner or cleaner["approved_status"] != "approved":
        raise ValueError("Cleaner is not approved")

    db.execute(
        "UPDATE bookings SET cleaner_id=?, status='assigned', updated_at=? WHERE id=?",
        (cleaner_id, now_iso(), booking_id)
    )
    _log_status_change(booking_id, "pending", "assigned", admin_id, f"Cleaner {cleaner_id} assigned")
    return get_booking(booking_id)


def update_job_status(booking_id, new_status, changed_by_id, note=None):
    """Advance a job through the workflow. Enforces valid transitions."""
    booking = get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")

    current = booking["status"]
    if new_status not in VALID_TRANSITIONS.get(current, set()):
        allowed = list(VALID_TRANSITIONS.get(current, set()))
        raise ValueError(
            f"Cannot move from '{current}' to '{new_status}'. Allowed: {allowed}"
        )

    db.execute(
        "UPDATE bookings SET status=?, updated_at=? WHERE id=?",
        (new_status, now_iso(), booking_id)
    )
    _log_status_change(booking_id, current, new_status, changed_by_id, note)

    if new_status == "completed" and booking.get("cleaner_id"):
        db.execute(
            "UPDATE cleaner_profiles SET total_jobs_completed = total_jobs_completed + 1 WHERE user_id=?",
            (booking["cleaner_id"],)
        )

    return get_booking(booking_id)


def _log_status_change(booking_id, old_status, new_status, changed_by, note):
    db.execute(
        """INSERT INTO booking_status_log
           (id, booking_id, old_status, new_status, changed_by, note, changed_at)
           VALUES (?,?,?,?,?,?,?)""",
        (new_id(), booking_id, old_status, new_status, changed_by, note, now_iso())
    )


def get_booking_history(booking_id):
    return db.fetchall(
        "SELECT * FROM booking_status_log WHERE booking_id=? ORDER BY changed_at",
        (booking_id,)
    )


# ---------------------------------------------------------
#  PAYMENTS
# ---------------------------------------------------------

def create_payment(booking_id, customer_id, amount_scr, payment_method="bank_transfer", reference_no=None):
    """Record an expected payment (status=pending until admin confirms)."""
    booking = get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")

    pay_id = new_id()
    db.execute(
        """INSERT INTO payments
           (id, booking_id, customer_id, amount_scr, payment_method, status, reference_no, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (pay_id, booking_id, customer_id, amount_scr, payment_method, "pending", reference_no, now_iso())
    )
    return get_payment(pay_id)


def confirm_payment(payment_id, admin_id, reference_no=None):
    """Admin manually confirms a payment and triggers commission calculation."""
    admin = db.fetchone("SELECT * FROM users WHERE id=? AND role='admin'", (admin_id,))
    if not admin:
        raise ValueError("Only admins can confirm payments")

    payment = get_payment(payment_id)
    if not payment:
        raise ValueError("Payment not found")
    if payment["status"] == "confirmed":
        raise ValueError("Payment already confirmed")

    db.execute(
        """UPDATE payments
           SET status='confirmed', confirmed_by=?, confirmed_at=?, reference_no=COALESCE(?,reference_no)
           WHERE id=?""",
        (admin_id, now_iso(), reference_no, payment_id)
    )

    commission = calculate_commission(payment["booking_id"], payment_id)

    return {"payment": get_payment(payment_id), "commission": commission}


def reject_payment(payment_id, admin_id):
    admin = db.fetchone("SELECT * FROM users WHERE id=? AND role='admin'", (admin_id,))
    if not admin:
        raise ValueError("Only admins can reject payments")
    db.execute(
        "UPDATE payments SET status='rejected' WHERE id=?", (payment_id,)
    )
    return get_payment(payment_id)


def get_payment(payment_id):
    return db.fetchone("SELECT * FROM payments WHERE id=?", (payment_id,))


def list_payments(status=None):
    if status:
        return db.fetchall("SELECT * FROM payments WHERE status=? ORDER BY created_at DESC", (status,))
    return db.fetchall("SELECT * FROM payments ORDER BY created_at DESC")


# ---------------------------------------------------------
#  COMMISSION
# ---------------------------------------------------------

def calculate_commission(booking_id, payment_id=None):
    """
    Calculate and store the 40/60 commission split for a booking.
    Platform takes 40%, cleaner receives 60%.
    """
    booking = get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")

    payment = None
    if payment_id:
        payment = get_payment(payment_id)
    else:
        payment = db.fetchone(
            "SELECT * FROM payments WHERE booking_id=? AND status='confirmed' LIMIT 1",
            (booking_id,)
        )

    total = payment["amount_scr"] if payment else (booking.get("amount_scr") or 0)

    platform_share = round(total * (PLATFORM_COMMISSION_PCT / 100), 2)
    cleaner_share  = round(total * (CLEANER_SHARE_PCT / 100), 2)

    existing = db.fetchone("SELECT * FROM commissions WHERE booking_id=?", (booking_id,))
    if existing:
        db.execute(
            """UPDATE commissions
               SET total_amount=?, platform_share=?, cleaner_share=?,
                   payment_id=COALESCE(?,payment_id)
               WHERE booking_id=?""",
            (total, platform_share, cleaner_share, payment_id, booking_id)
        )
        return db.fetchone("SELECT * FROM commissions WHERE booking_id=?", (booking_id,))

    comm_id = new_id()
    db.execute(
        """INSERT INTO commissions
           (id, booking_id, payment_id, total_amount, platform_pct, cleaner_pct,
            platform_share, cleaner_share, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (comm_id, booking_id, payment_id, total,
         PLATFORM_COMMISSION_PCT, CLEANER_SHARE_PCT,
         platform_share, cleaner_share, "pending", now_iso())
    )
    return db.fetchone("SELECT * FROM commissions WHERE id=?", (comm_id,))


def settle_commission(booking_id, admin_id):
    """Mark commission as settled after cleaner is paid."""
    db.execute(
        "UPDATE commissions SET status='settled', settled_at=? WHERE booking_id=?",
        (now_iso(), booking_id)
    )
    return db.fetchone("SELECT * FROM commissions WHERE booking_id=?", (booking_id,))


def get_commission_summary():
    """Admin analytics: total revenue, commissions, payouts."""
    return db.fetchone("""
        SELECT
            COUNT(*)                              AS total_bookings,
            COALESCE(SUM(total_amount),0)         AS total_revenue,
            COALESCE(SUM(platform_share),0)       AS total_platform,
            COALESCE(SUM(cleaner_share),0)        AS total_cleaner_payouts,
            COUNT(CASE WHEN status='settled' THEN 1 END) AS settled_count,
            COUNT(CASE WHEN status='pending' THEN 1 END) AS pending_count
        FROM commissions
    """)


# ---------------------------------------------------------
#  REVIEWS
# ---------------------------------------------------------

def submit_review(booking_id, customer_id, rating, comment=None):
    """Customer submits a review for a completed job."""
    booking = get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")
    if booking["customer_id"] != customer_id:
        raise ValueError("You can only review your own bookings")
    if booking["status"] != "completed":
        raise ValueError("Can only review completed bookings")
    if not booking.get("cleaner_id"):
        raise ValueError("No cleaner was assigned to this booking")

    existing = db.fetchone("SELECT id FROM reviews WHERE booking_id=?", (booking_id,))
    if existing:
        raise ValueError("Review already submitted for this booking")

    review_id = new_id()
    db.execute(
        """INSERT INTO reviews (id, booking_id, customer_id, cleaner_id, rating, comment, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (review_id, booking_id, customer_id, booking["cleaner_id"], rating, comment, now_iso())
    )
    update_cleaner_rating(booking["cleaner_id"])
    return db.fetchone("SELECT * FROM reviews WHERE id=?", (review_id,))


# ---------------------------------------------------------
#  MESSAGES
# ---------------------------------------------------------

def send_message(sender_id, receiver_id, message_text, booking_id=None):
    msg_id = new_id()
    db.execute(
        """INSERT INTO messages (id, sender_id, receiver_id, booking_id, message_text, sent_at)
           VALUES (?,?,?,?,?,?)""",
        (msg_id, sender_id, receiver_id, booking_id, message_text, now_iso())
    )
    return db.fetchone("SELECT * FROM messages WHERE id=?", (msg_id,))


def get_conversation(user_a, user_b, booking_id=None):
    if booking_id:
        return db.fetchall(
            """SELECT * FROM messages
               WHERE booking_id=? AND (
                   (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
               ) ORDER BY sent_at""",
            (booking_id, user_a, user_b, user_b, user_a)
        )
    return db.fetchall(
        """SELECT * FROM messages WHERE
           (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
           ORDER BY sent_at""",
        (user_a, user_b, user_b, user_a)
    )


def mark_messages_read(receiver_id, sender_id):
    db.execute(
        "UPDATE messages SET is_read=1 WHERE receiver_id=? AND sender_id=?",
        (receiver_id, sender_id)
    )


# ---------------------------------------------------------
#  DISPUTES
# ---------------------------------------------------------

def raise_dispute(booking_id, raised_by, description):
    booking = get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")
    dispute_id = new_id()
    db.execute(
        """INSERT INTO disputes (id, booking_id, raised_by, description, created_at)
           VALUES (?,?,?,?,?)""",
        (dispute_id, booking_id, raised_by, description, now_iso())
    )
    update_job_status(booking_id, "disputed", raised_by, f"Dispute raised: {description[:60]}")
    return db.fetchone("SELECT * FROM disputes WHERE id=?", (dispute_id,))


def resolve_dispute(dispute_id, admin_id, resolution):
    db.execute(
        """UPDATE disputes SET status='resolved', resolution=?, resolved_by=?, resolved_at=?
           WHERE id=?""",
        (resolution, admin_id, now_iso(), dispute_id)
    )
    return db.fetchone("SELECT * FROM disputes WHERE id=?", (dispute_id,))


# ---------------------------------------------------------
#  ANALYTICS (Admin)
# ---------------------------------------------------------

def get_analytics():
    """High-level platform analytics for admin dashboard."""
    bookings = db.fetchone("""
        SELECT
            COUNT(*)  AS total,
            COUNT(CASE WHEN status='pending'     THEN 1 END) AS pending,
            COUNT(CASE WHEN status='assigned'    THEN 1 END) AS assigned,
            COUNT(CASE WHEN status='in_progress' THEN 1 END) AS in_progress,
            COUNT(CASE WHEN status='completed'   THEN 1 END) AS completed,
            COUNT(CASE WHEN status='cancelled'   THEN 1 END) AS cancelled
        FROM bookings
    """)

    users = db.fetchone("""
        SELECT
            COUNT(*)  AS total,
            COUNT(CASE WHEN role='customer' THEN 1 END) AS customers,
            COUNT(CASE WHEN role='cleaner'  THEN 1 END) AS cleaners,
            COUNT(CASE WHEN role='admin'    THEN 1 END) AS admins
        FROM users
    """)

    subscriptions = db.fetchone("""
        SELECT
            COUNT(*) AS total_active,
            COUNT(CASE WHEN plan_type='basic'    THEN 1 END) AS basic,
            COUNT(CASE WHEN plan_type='standard' THEN 1 END) AS standard,
            COUNT(CASE WHEN plan_type='premium'  THEN 1 END) AS premium,
            COALESCE(SUM(price_scr),0) AS monthly_subscription_revenue
        FROM subscriptions WHERE status='active'
    """)

    revenue = get_commission_summary()

    return {
        "bookings":      dict(bookings),
        "users":         dict(users),
        "subscriptions": dict(subscriptions),
        "revenue":       dict(revenue),
    }
