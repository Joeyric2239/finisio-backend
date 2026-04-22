"""
FINISIO CLEANS — Seed Script
Populates the database with realistic sample data for testing.

Run:  python3 seed.py
"""

import db
import logic

def seed():
    db.init_db()
    print("\n[SEED] Seeding database...\n")

    # ── ADMIN ────────────────────────────────────────────
    admin = logic.create_user(
        name="Platform Admin", email="admin@finisiocleans.sc",
        role="admin", phone="+248 250 0001", password="admin123"
    )
    print(f"  ✓ Admin:    {admin['name']} ({admin['id'][:8]}…)")

    # ── CUSTOMERS ────────────────────────────────────────
    sarah = logic.create_user(
        name="Sarah Mitchell", email="sarah@email.sc",
        role="customer", phone="+248 254 1234",
        address="Mont Fleuri, Mahé", password="customer123"
    )
    thomas = logic.create_user(
        name="Thomas Koa", email="thomas@email.sc",
        role="customer", phone="+248 254 5678",
        address="Beau Vallon, Mahé", password="customer123"
    )
    print(f"  ✓ Customer: {sarah['name']} ({sarah['id'][:8]}…)")
    print(f"  ✓ Customer: {thomas['name']} ({thomas['id'][:8]}…)")

    # ── CLEANERS ─────────────────────────────────────────
    marie = logic.create_user(
        name="Marie Dupont", email="marie@email.sc",
        role="cleaner", phone="+248 253 1111", password="cleaner123"
    )
    logic.update_cleaner_profile(
        marie["id"],
        service_areas=["Victoria", "North Mahé", "Beau Vallon"],
        skills=["home_deep", "post_construction", "carpet_cleaning"],
        experience_years=4,
    )
    logic.approve_cleaner(marie["id"], admin["id"], approve=True)

    jean = logic.create_user(
        name="Jean-Paul Kobia", email="jean@email.sc",
        role="cleaner", phone="+248 253 2222", password="cleaner123"
    )
    logic.update_cleaner_profile(
        jean["id"],
        service_areas=["Praslin", "La Digue"],
        skills=["home_deep", "post_construction"],
        experience_years=6,
    )
    logic.approve_cleaner(jean["id"], admin["id"], approve=True)

    anna = logic.create_user(
        name="Anna Joseph", email="anna@email.sc",
        role="cleaner", phone="+248 253 3333", password="cleaner123"
    )
    logic.update_cleaner_profile(
        anna["id"],
        service_areas=["South Mahé", "Anse Royale"],
        skills=["home_deep"],
        experience_years=2,
    )
    # Anna is still pending
    print(f"  ✓ Cleaner:  {marie['name']} (approved) ({marie['id'][:8]}…)")
    print(f"  ✓ Cleaner:  {jean['name']} (approved) ({jean['id'][:8]}…)")
    print(f"  ✓ Cleaner:  {anna['name']} (pending)  ({anna['id'][:8]}…)")

    # ── SUBSCRIPTIONS ────────────────────────────────────
    sub_sarah = logic.create_subscription(sarah["id"], "standard")
    sub_thomas = logic.create_subscription(thomas["id"], "premium")
    print(f"\n  ✓ Subscription: {sarah['name']} → Standard (SCR 1,500 | 6h/mo)")
    print(f"  ✓ Subscription: {thomas['name']} → Premium (SCR 2,000 | 10h/mo)")

    # ── BOOKINGS ─────────────────────────────────────────
    # Completed booking: Sarah + Marie
    b1 = logic.create_booking(
        customer_id    = sarah["id"],
        service_type   = "home_deep",
        booking_type   = "subscription",
        scheduled_date = "2026-04-15",
        scheduled_time = "09:00",
        address        = "Mont Fleuri, Mahé",
        notes          = "Please focus on kitchen and master bedroom",
        hours_booked   = 4,
        subscription_id= sub_sarah["id"],
    )
    logic.assign_cleaner(b1["id"], marie["id"], admin["id"])
    logic.update_job_status(b1["id"], "accepted",    marie["id"],  "Cleaner accepted")
    logic.update_job_status(b1["id"], "in_progress", marie["id"],  "Job started")
    logic.update_job_status(b1["id"], "completed",   marie["id"],  "Job completed")
    p1 = logic.create_payment(b1["id"], sarah["id"], 1500, "bank_transfer", "REF-SC-0415")
    logic.confirm_payment(p1["id"], admin["id"], "REF-SC-0415")
    logic.submit_review(b1["id"], sarah["id"], 5, "Marie was fantastic! Very thorough.")
    print(f"\n  ✓ Booking:  {b1['id'][:8]}… Home Deep | Sarah → Marie | COMPLETED + PAID + REVIEWED")

    # Assigned booking: Thomas + Jean
    b2 = logic.create_booking(
        customer_id    = thomas["id"],
        service_type   = "post_construction",
        booking_type   = "subscription",
        scheduled_date = "2026-04-28",
        scheduled_time = "08:00",
        address        = "Praslin",
        notes          = "New villa build — heavy dust and debris",
        hours_booked   = 6,
        subscription_id= sub_thomas["id"],
    )
    logic.assign_cleaner(b2["id"], jean["id"], admin["id"])
    print(f"  ✓ Booking:  {b2['id'][:8]}… Post-Construction | Thomas → Jean | ASSIGNED")

    # Pending booking (no cleaner yet)
    b3 = logic.create_booking(
        customer_id    = sarah["id"],
        service_type   = "home_deep",
        booking_type   = "subscription",
        scheduled_date = "2026-04-30",
        scheduled_time = "10:00",
        address        = "Mont Fleuri, Mahé",
        hours_booked   = 2,
        subscription_id= sub_sarah["id"],
    )
    print(f"  ✓ Booking:  {b3['id'][:8]}… Home Deep | Sarah | PENDING (needs cleaner)")

    # One-time booking
    b4 = logic.create_booking(
        customer_id  = thomas["id"],
        service_type = "home_deep",
        booking_type = "one_time",
        scheduled_date = "2026-05-03",
        scheduled_time = "09:00",
        address      = "Beau Vallon, Mahé",
        hours_booked = 3,
    )
    print(f"  ✓ Booking:  {b4['id'][:8]}… Home Deep | Thomas | ONE-TIME | PENDING")

    # ── MESSAGES ─────────────────────────────────────────
    logic.send_message(sarah["id"], marie["id"], "Hi Marie, just confirming tomorrow at 9am?", b1["id"])
    logic.send_message(marie["id"], sarah["id"], "Yes confirmed! I'll focus on kitchen + bedroom.", b1["id"])
    print(f"\n  ✓ Sample messages seeded")

    # ── SUMMARY ──────────────────────────────────────────
    analytics = logic.get_analytics()
    print("\n" + "─"*48)
    print("  DATABASE SUMMARY")
    print("─"*48)
    print(f"  Users:         {analytics['users']['total']} ({analytics['users']['customers']} customers, {analytics['users']['cleaners']} cleaners)")
    print(f"  Bookings:      {analytics['bookings']['total']} total")
    print(f"  Subscriptions: {analytics['subscriptions']['total_active']} active")
    print(f"  Platform Rev:  SCR {analytics['revenue']['total_platform']:,.0f}")
    print(f"  Cleaner Pay:   SCR {analytics['revenue']['total_cleaner_payouts']:,.0f}")
    print("─"*48)

    print(f"\n  [SEED] Done. Admin login: admin@finisiocleans.sc / admin123")
    print(f"  [SEED] DB file: finisio.db\n")

    # Print key IDs for testing
    print("  KEY IDs FOR API TESTING:")
    print(f"  Admin ID:      {admin['id']}")
    print(f"  Sarah ID:      {sarah['id']}")
    print(f"  Thomas ID:     {thomas['id']}")
    print(f"  Marie ID:      {marie['id']}")
    print(f"  Jean ID:       {jean['id']}")
    print(f"  Anna ID:       {anna['id']}")
    print(f"  Sub Sarah ID:  {sub_sarah['id']}")
    print(f"  Booking 1 ID:  {b1['id']}")
    print(f"  Booking 2 ID:  {b2['id']}")
    print(f"  Booking 3 ID:  {b3['id']}")


if __name__ == "__main__":
    seed()
