"""
SportEase Database Schemas (MongoDB via Pydantic)

Each Pydantic model maps to one MongoDB collection using the lowercase class name.
Example: class User -> collection "user"
"""
from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal
from datetime import datetime

Role = Literal["player", "owner", "admin"]

class User(BaseModel):
    id: Optional[str] = Field(default=None, description="Document _id as string")
    name: str = Field(...)
    email: EmailStr
    phone: Optional[str] = None
    role: Role = Field("player")
    profilePic: Optional[str] = None
    walletBalance: float = 0.0
    createdAt: Optional[datetime] = None

class Venue(BaseModel):
    id: Optional[str] = None
    ownerId: str
    name: str
    address: str
    lat: float
    lng: float
    sports: List[str] = []
    images: List[str] = []
    pricePerHour: float
    amenities: List[str] = []
    rating: float = 0.0
    availabilityRules: Optional[dict] = None
    createdAt: Optional[datetime] = None
    isSeeded: Optional[bool] = False

class AvailabilitySlot(BaseModel):
    id: Optional[str] = None
    venueId: str
    date: str  # YYYY-MM-DD
    startTime: str  # HH:MM
    endTime: str  # HH:MM
    status: Literal["available", "reserved", "booked", "blocked"] = "available"

class Booking(BaseModel):
    id: Optional[str] = None
    userId: str
    venueId: str
    slotIds: List[str]
    totalAmount: float
    commission: float = 0.0
    paymentStatus: Literal["pending", "paid", "failed", "refunded"] = "pending"
    status: Literal["pending", "confirmed", "cancelled"] = "pending"
    bookingCode: Optional[str] = None
    qrUrl: Optional[str] = None
    createdAt: Optional[datetime] = None

class Payment(BaseModel):
    id: Optional[str] = None
    bookingId: str
    amount: float
    gateway: Literal["razorpay"] = "razorpay"
    transactionId: Optional[str] = None
    orderId: Optional[str] = None
    status: Literal["created", "paid", "failed"] = "created"
    createdAt: Optional[datetime] = None

class Review(BaseModel):
    id: Optional[str] = None
    userId: str
    venueId: str
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = None
    createdAt: Optional[datetime] = None

class PromoCode(BaseModel):
    id: Optional[str] = None
    code: str
    description: Optional[str] = None
    discountPercent: float = Field(ge=0, le=100)
    active: bool = True

class Payout(BaseModel):
    id: Optional[str] = None
    ownerId: str
    amount: float
    periodStart: str
    periodEnd: str
    status: Literal["pending", "processed"] = "pending"

class Notification(BaseModel):
    id: Optional[str] = None
    userId: str
    title: str
    body: str
    scheduledFor: Optional[datetime] = None
    sent: bool = False
