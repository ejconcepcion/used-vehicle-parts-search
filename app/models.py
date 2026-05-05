"""ORM models for vehicles, part-resale estimates, eBay-price cache, and run log."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Vehicle(Base):
    __tablename__ = "vehicle"

    id = Column(Integer, primary_key=True)
    vin = Column(String, unique=True, nullable=False, index=True)
    year = Column(Integer, nullable=True)
    make = Column(String, nullable=True, index=True)
    model = Column(String, nullable=True, index=True)
    yard_name = Column(String, nullable=True)
    yard_address = Column(String, nullable=True)
    row_number = Column(String, nullable=True)
    date_added_to_yard = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    detail_url = Column(String, nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    estimated_total_value = Column(Float, default=0.0, index=True)   # net total (after fees & shipping)
    gross_total_value = Column(Float, default=0.0)                   # gross total (raw eBay medians)

    parts = relationship(
        "PartEstimate",
        back_populates="vehicle",
        cascade="all, delete-orphan",
    )
    top_sold_parts = relationship(
        "TopSoldPart",
        back_populates="vehicle",
        cascade="all, delete-orphan",
    )


class PartEstimate(Base):
    __tablename__ = "part_estimate"

    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicle.id"), nullable=False, index=True)
    part_name = Column(String, nullable=False)
    ebay_query = Column(String, nullable=False)
    median_price_usd = Column(Float, nullable=True)   # gross eBay sold median
    net_value_usd = Column(Float, nullable=True)       # after eBay fees, payment fees, shipping
    shipping_est_usd = Column(Float, nullable=True)    # estimated shipping used in net calc
    sample_size = Column(Integer, default=0)
    queried_at = Column(DateTime, default=datetime.utcnow)

    vehicle = relationship("Vehicle", back_populates="parts")

    __table_args__ = (
        UniqueConstraint("vehicle_id", "part_name", name="uq_vehicle_part"),
    )


class EbayPriceCache(Base):
    """Caches eBay sold-listing medians by query string. Avoids re-querying."""

    __tablename__ = "ebay_price_cache"

    id = Column(Integer, primary_key=True)
    query = Column(String, unique=True, nullable=False, index=True)
    median_price_usd = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    queried_at = Column(DateTime, default=datetime.utcnow)
    raw_prices_json = Column(Text, nullable=True)


class TopSoldPart(Base):
    """Top recently-sold eBay listings for a vehicle (fetched by year/make/model)."""

    __tablename__ = "top_sold_part"

    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicle.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    price_usd = Column(Float, nullable=True)
    url = Column(String, nullable=True)
    sold_date_str = Column(String, nullable=True)
    queried_at = Column(DateTime, default=datetime.utcnow)

    vehicle = relationship("Vehicle", back_populates="top_sold_parts")


class SearchRun(Base):
    """Audit log: one row per pipeline run."""

    __tablename__ = "search_run"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    vehicles_seen = Column(Integer, default=0)
    vehicles_matched = Column(Integer, default=0)
    parts_queried = Column(Integer, default=0)
    error = Column(Text, nullable=True)
