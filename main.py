import os
import hmac
import hashlib
from io import StringIO
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId
from datetime import datetime, timezone
import csv
import requests

from database import db, create_document, get_documents
from schemas import User, Venue, AvailabilitySlot, Booking, Payment, Review

APP_NAME = "SportEase"
PRIMARY = "#00C853"
SECONDARY = "#212121"
ACCENT = "#2196F3"

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helpers

def oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def now_utc():
    return datetime.now(timezone.utc)


# Models for API
class BrandConfig(BaseModel):
    name: str = APP_NAME
    primary: str = PRIMARY
    secondary: str = SECONDARY
    accent: str = ACCENT
    fonts: dict = {"heading": "Poppins", "body": "Inter"}


# Routes
@app.get("/")
def root():
    return {"app": APP_NAME, "status": "ok"}


@app.get("/api/brand", response_model=BrandConfig)
def get_brand():
    return BrandConfig()


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:20]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# Users
@app.post("/api/users", response_model=dict)
def upsert_user(user: User):
    if db is None:
        raise HTTPException(500, "Database not configured")
    users = db["user"]
    existing = users.find_one({"email": user.email})
    payload = user.model_dump(exclude_unset=True)
    payload["updated_at"] = now_utc()
    if not existing:
        payload["created_at"] = now_utc()
        res = users.insert_one(payload)
        return {"id": str(res.inserted_id)}
    else:
        users.update_one({"_id": existing["_id"]}, {"$set": payload})
        return {"id": str(existing["_id"])}


@app.get("/api/users/{user_id}")
def get_user(user_id: str):
    u = db["user"].find_one({"_id": oid(user_id)})
    if not u:
        raise HTTPException(404, "User not found")
    u["id"] = str(u.pop("_id"))
    return u


# Venues
@app.get("/api/venues")
def list_venues(
    sport: Optional[str] = None,
    city: Optional[str] = None,
    q: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    seeded_only: Optional[bool] = None,
    limit: int = 20,
    skip: int = 0,
):
    if db is None:
        raise HTTPException(500, "Database not configured")
    filt = {}
    if sport:
        filt["sports"] = sport
    if city:
        filt["address"] = {"$regex": city, "$options": "i"}
    if q:
        filt["name"] = {"$regex": q, "$options": "i"}
    if seeded_only is True:
        filt["isSeeded"] = True
    if min_price is not None or max_price is not None:
        pr = {}
        if min_price is not None:
            pr["$gte"] = min_price
        if max_price is not None:
            pr["$lte"] = max_price
        filt["pricePerHour"] = pr
    cursor = db["venue"].find(filt).skip(skip).limit(limit)
    data = []
    for v in cursor:
        v["id"] = str(v.pop("_id"))
        data.append(v)
    return {"items": data, "count": len(data)}


@app.get("/api/venues/{venue_id}")
def get_venue(venue_id: str):
    v = db["venue"].find_one({"_id": oid(venue_id)})
    if not v:
        raise HTTPException(404, "Venue not found")
    v["id"] = str(v.pop("_id"))
    return v


class AddVenue(BaseModel):
    ownerId: str
    name: str
    address: str
    lat: float
    lng: float
    sports: List[str]
    images: List[str] = []
    pricePerHour: float
    amenities: List[str] = []


@app.post("/api/owner/venues")
def owner_add_venue(payload: AddVenue, x_user_id: Optional[str] = Query(None, alias="userId")):
    # Simple owner auth check: caller userId must equal payload.ownerId
    if not x_user_id or x_user_id != payload.ownerId:
        raise HTTPException(403, "Forbidden")
    v = payload.model_dump()
    v["created_at"] = now_utc()
    v["isSeeded"] = False
    res = db["venue"].insert_one(v)
    return {"id": str(res.inserted_id)}


@app.get("/api/owner/venues")
def owner_list_venues(userId: str):
    items = []
    for v in db["venue"].find({"ownerId": userId}):
        v["id"] = str(v.pop("_id"))
        items.append(v)
    return {"items": items}


# Availability & Slots
@app.get("/api/venues/{venue_id}/slots")
def get_slots(venue_id: str, date: str):
    items = []
    for s in db["availabilityslot"].find({"venueId": venue_id, "date": date}).sort("startTime"):
        s["id"] = str(s.pop("_id"))
        items.append(s)
    return {"items": items}


class ReservePayload(BaseModel):
    userId: str
    slotIds: List[str]


@app.post("/api/slots/reserve")
def reserve_slots(payload: ReservePayload):
    # Mark slots as reserved if they are available
    ids = [oid(sid) for sid in payload.slotIds]
    res = db["availabilityslot"].update_many(
        {"_id": {"$in": ids}, "status": "available"},
        {"$set": {"status": "reserved"}}
    )
    if res.modified_count != len(ids):
        raise HTTPException(409, "One or more slots are not available")
    return {"reserved": payload.slotIds}


# Booking + Razorpay
class CreateBookingPayload(BaseModel):
    userId: str
    venueId: str
    slotIds: List[str]


@app.post("/api/bookings/create")
def create_booking(payload: CreateBookingPayload):
    # Calculate price
    venue = db["venue"].find_one({"_id": oid(payload.venueId)})
    if not venue:
        raise HTTPException(404, "Venue not found")
    price_per_hour = float(venue.get("pricePerHour", 0))
    hours = max(1, len(payload.slotIds))
    total_amount = round(price_per_hour * hours, 2)

    # Ensure all slots are reserved (or available)
    ids = [oid(i) for i in payload.slotIds]
    slots = list(db["availabilityslot"].find({"_id": {"$in": ids}}))
    if len(slots) != len(ids):
        raise HTTPException(400, "Invalid slots")
    invalid = [s for s in slots if s.get("status") not in ("available", "reserved")]
    if invalid:
        raise HTTPException(409, "Some slots are not bookable")

    # Mark them reserved
    db["availabilityslot"].update_many({"_id": {"$in": ids}, "status": "available"}, {"$set": {"status": "reserved"}})

    booking_doc = {
        "userId": payload.userId,
        "venueId": payload.venueId,
        "slotIds": payload.slotIds,
        "totalAmount": total_amount,
        "commission": round(total_amount * 0.1, 2),
        "paymentStatus": "pending",
        "status": "pending",
        "created_at": now_utc(),
    }
    bres = db["booking"].insert_one(booking_doc)
    booking_id = str(bres.inserted_id)

    # Razorpay order (amount in paise)
    order = None
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        try:
            resp = requests.post(
                "https://api.razorpay.com/v1/orders",
                auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
                json={
                    "amount": int(total_amount * 100),
                    "currency": "INR",
                    "receipt": booking_id,
                    "payment_capture": 1,
                    "notes": {"bookingId": booking_id, "venueId": payload.venueId},
                },
                timeout=10,
            )
            resp.raise_for_status()
            order = resp.json()
        except Exception:
            # Fallback: create a fake order for dev
            order = {"id": f"order_{booking_id}", "amount": int(total_amount * 100), "currency": "INR"}
    else:
        order = {"id": f"order_{booking_id}", "amount": int(total_amount * 100), "currency": "INR"}

    pay_doc = {
        "bookingId": booking_id,
        "amount": total_amount,
        "gateway": "razorpay",
        "orderId": order["id"],
        "status": "created",
        "created_at": now_utc(),
    }
    db["payment"].insert_one(pay_doc)

    return {
        "bookingId": booking_id,
        "totalAmount": total_amount,
        "order": order,
        "razorpayKeyId": RAZORPAY_KEY_ID,
    }


class RazorpayConfirmPayload(BaseModel):
    bookingId: str
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: Optional[str] = None


@app.post("/api/payments/razorpay/confirm")
def confirm_payment(payload: RazorpayConfirmPayload):
    # Optional signature verify
    if payload.razorpay_signature and RAZORPAY_KEY_SECRET:
        msg = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode()
        dig = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()
        if dig != payload.razorpay_signature:
            raise HTTPException(400, "Signature mismatch")

    # Update booking and slots
    booking = db["booking"].find_one({"_id": oid(payload.bookingId)})
    if not booking:
        raise HTTPException(404, "Booking not found")

    db["booking"].update_one({"_id": oid(payload.bookingId)}, {"$set": {"paymentStatus": "paid", "status": "confirmed", "bookingCode": payload.razorpay_payment_id}})

    # Mark slots as booked
    slot_ids = [oid(s) for s in booking.get("slotIds", [])]
    db["availabilityslot"].update_many({"_id": {"$in": slot_ids}}, {"$set": {"status": "booked"}})

    # Update payment
    db["payment"].update_one({"bookingId": payload.bookingId}, {"$set": {"status": "paid", "transactionId": payload.razorpay_payment_id}})

    return {"status": "confirmed"}


@app.get("/api/bookings")
def list_bookings(userId: Optional[str] = None, ownerId: Optional[str] = None, role: Optional[str] = None):
    filt = {}
    if userId:
        filt["userId"] = userId
    if ownerId:
        # fetch venue ids for this owner
        v_ids = [str(v["_id"]) for v in db["venue"].find({"ownerId": ownerId}, {"_id": 1})]
        filt["venueId"] = {"$in": v_ids}
    items = []
    for b in db["booking"].find(filt).sort("created_at", -1):
        b["id"] = str(b.pop("_id"))
        items.append(b)
    return {"items": items}


# Reviews
class AddReview(BaseModel):
    userId: str
    venueId: str
    rating: int
    comment: Optional[str] = None


@app.post("/api/reviews")
def add_review(payload: AddReview):
    # allow only if user has a confirmed booking for this venue
    has = db["booking"].find_one({"userId": payload.userId, "venueId": payload.venueId, "status": "confirmed"})
    if not has:
        raise HTTPException(403, "Not allowed to review")
    doc = payload.model_dump()
    doc["created_at"] = now_utc()
    res = db["review"].insert_one(doc)
    return {"id": str(res.inserted_id)}


@app.get("/api/venues/{venue_id}/reviews")
def list_reviews(venue_id: str, limit: int = 20):
    items = []
    for r in db["review"].find({"venueId": venue_id}).sort("created_at", -1).limit(limit):
        r["id"] = str(r.pop("_id"))
        items.append(r)
    return {"items": items}


# AI-like suggestions (deterministic placeholder)
@app.get("/api/suggestions")
def suggestions(userId: Optional[str] = None, city: Optional[str] = None, sport: Optional[str] = None, limit: int = 5):
    filt = {}
    if city:
        filt["address"] = {"$regex": city, "$options": "i"}
    if sport:
        filt["sports"] = sport
    items = []
    for v in db["venue"].find(filt).sort("rating", -1).limit(limit):
        v["id"] = str(v.pop("_id"))
        items.append(v)
    return {"items": items}


# Owner CSV export for payouts/bookings
@app.get("/api/owner/export")
def owner_export(ownerId: str):
    # Gather bookings and payments for owner
    v_ids = [str(v["_id"]) for v in db["venue"].find({"ownerId": ownerId}, {"_id": 1})]
    cur = db["booking"].find({"venueId": {"$in": v_ids}})
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["bookingId", "venueId", "userId", "amount", "commission", "status", "created_at"])
    for b in cur:
        writer.writerow([
            str(b.get("_id")), b.get("venueId"), b.get("userId"), b.get("totalAmount"), b.get("commission"), b.get("status"), b.get("created_at")
        ])
    csv_data = output.getvalue()
    return Response(content=csv_data, media_type='text/csv', headers={"Content-Disposition": "attachment; filename=owner_export.csv"})


# Seed data
SAMPLE_VENUES = [
    {
        "name": "Emerald Arena 5v5 Turf",
        "address": "Vadodara, Gujarat",
        "lat": 22.3072,
        "lng": 73.1812,
        "sports": ["football", "cricket"],
        "images": [],
        "pricePerHour": 1200,
        "amenities": ["Lights", "Parking", "Locker"],
    },
    {
        "name": "Charcoal Courts Badminton",
        "address": "Alkapuri, Vadodara",
        "lat": 22.3100,
        "lng": 73.1800,
        "sports": ["badminton"],
        "images": [],
        "pricePerHour": 500,
        "amenities": ["AC", "Pro Shop"],
    },
    {
        "name": "Blue Wave Swimming",
        "address": "Gotri, Vadodara",
        "lat": 22.3201,
        "lng": 73.1609,
        "sports": ["swimming"],
        "images": [],
        "pricePerHour": 300,
        "amenities": ["Coach", "Shower"],
    },
    {
        "name": "City Tennis Hub",
        "address": "Akota, Vadodara",
        "lat": 22.2999,
        "lng": 73.1702,
        "sports": ["tennis"],
        "images": [],
        "pricePerHour": 700,
        "amenities": ["Clay Court", "Lights"],
    },
    {
        "name": "Cricket Dome",
        "address": "Manjalpur, Vadodara",
        "lat": 22.2700,
        "lng": 73.2000,
        "sports": ["cricket"],
        "images": [],
        "pricePerHour": 900,
        "amenities": ["Pitch", "Bowling Machine"],
    },
    {
        "name": "Skate & Play Park",
        "address": "Vasna Road, Vadodara",
        "lat": 22.3105,
        "lng": 73.1501,
        "sports": ["skate"],
        "images": [],
        "pricePerHour": 400,
        "amenities": ["Rentals", "Coach"],
    },
    {
        "name": "Hoops Central",
        "address": "Karelibaug, Vadodara",
        "lat": 22.3302,
        "lng": 73.2003,
        "sports": ["basketball"],
        "images": [],
        "pricePerHour": 800,
        "amenities": ["Indoor", "Scoreboard"],
    },
    {
        "name": "Founding Partner Turf",
        "address": "Fatehgunj, Vadodara",
        "lat": 22.3205,
        "lng": 73.2009,
        "sports": ["football"],
        "images": [],
        "pricePerHour": 1100,
        "amenities": ["Lights", "Cafe"],
    },
]


@app.post("/api/seed")
def seed():
    # create two users (player, owner) and 8 venues
    owner = db["user"].find_one({"email": "owner@sportease.dev"})
    if not owner:
        owner_id = db["user"].insert_one({"name": "Owner One", "email": "owner@sportease.dev", "role": "owner", "created_at": now_utc()}).inserted_id
    else:
        owner_id = owner["_id"]

    player = db["user"].find_one({"email": "player@sportease.dev"})
    if not player:
        db["user"].insert_one({"name": "Player One", "email": "player@sportease.dev", "role": "player", "created_at": now_utc()})

    count = db["venue"].count_documents({"isSeeded": True})
    if count >= 8:
        return {"seeded": True, "venues": count}

    for v in SAMPLE_VENUES:
        doc = {**v, "ownerId": str(owner_id), "created_at": now_utc(), "isSeeded": True, "rating": 4.4}
        vid = db["venue"].insert_one(doc).inserted_id
        # add a few slots for today
        today = datetime.now().strftime("%Y-%m-%d")
        base = ["06:00-07:00", "07:00-08:00", "08:00-09:00", "17:00-18:00", "18:00-19:00", "19:00-20:00"]
        for rng in base:
            s, e = rng.split("-")
            db["availabilityslot"].insert_one({
                "venueId": str(vid), "date": today, "startTime": s, "endTime": e, "status": "available", "created_at": now_utc()
            })
    return {"seeded": True}
