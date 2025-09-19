from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import datetime

Base = declarative_base()

class RawReport(Base):
    __tablename__ = 'raw_reports'
    id = Column(Integer, primary_key=True)
    source = Column(String)
    text = Column(Text)
    lat = Column(Float)
    lon = Column(Float)
    ts = Column(DateTime, default=datetime.datetime.utcnow)
    media_path = Column(String, nullable=True)
    has_media = Column(Boolean, default=False)  # Whether report includes media
    media_verified = Column(Boolean, default=False)  # Whether media has been verified by image classifier
    media_confidence = Column(Float, nullable=True)  # Confidence score from image classifier
    processed = Column(Boolean, default=False)
    nlp_type = Column(String, nullable=True)
    nlp_conf = Column(Float, nullable=True)
    credibility = Column(Float, nullable=True)
    group_id = Column(Integer, nullable=True)

class RawBulletin(Base):
    __tablename__ = 'raw_bulletins'
    id = Column(Integer, primary_key=True)
    issued_at = Column(DateTime, default=datetime.datetime.utcnow)
    hazard_type = Column(String)
    severity = Column(Integer)
    description = Column(Text)

class HazardEvent(Base):
    __tablename__ = 'hazard_events'
    id = Column(Integer, primary_key=True)
    hazard_type = Column(String)
    confidence = Column(Float)
    severity = Column(Integer)
    status = Column(String)
    centroid_lat = Column(Float)
    centroid_lon = Column(Float)
    evidence_json = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class AdminValidation(Base):
    __tablename__ = 'admin_validations'
    id = Column(Integer, primary_key=True)
    hazard_id = Column(Integer, ForeignKey('hazard_events.id'))
    action = Column(String)
    notes = Column(Text)
    admin_id = Column(String)
    ts = Column(DateTime, default=datetime.datetime.utcnow)
    hazard_event = relationship('HazardEvent')
