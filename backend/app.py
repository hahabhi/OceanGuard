#!/usr/bin/env python3
"""
OceanGuard FastAPI Backend
Real-time coastal hazard reporting and monitoring system
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
from datetime import datetime, timezone, timedelta
import json
import asyncio
import os
from collections import defaultdict

# Import our ML pipeline
from services.ingest import ProcessingPipeline

# Timezone utility functions
def get_current_timestamp():
    """Get current timestamp in ISO format with timezone info"""
    return datetime.now(timezone.utc).isoformat()

def get_local_timestamp():
    """Get current local timestamp in ISO format"""
    return datetime.now().isoformat()

app = FastAPI(
    title="OceanGuard API",
    description="Coastal Hazard Monitoring & Emergency Response System",
    version="1.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize ML pipeline
pipeline = ProcessingPipeline()

# Real-time event broadcasting
class EventBroadcaster:
    def __init__(self):
        self.connections = set()
    
    def add_connection(self, queue):
        self.connections.add(queue)
    
    def remove_connection(self, queue):
        self.connections.discard(queue)
    
    async def broadcast(self, event_type: str, data: dict):
        """Broadcast event to all connected clients"""
        if not self.connections:
            return
            
        event_data = {
            "type": event_type,
            "data": data,
            "timestamp": get_current_timestamp()
        }
        
        message = f"data: {json.dumps(event_data)}\n\n"
        
        # Send to all connected clients
        disconnected = set()
        for queue in self.connections.copy():
            try:
                await queue.put(message)
            except Exception:
                disconnected.add(queue)
        
        # Remove disconnected clients
        for queue in disconnected:
            self.connections.discard(queue)

# Global event broadcaster
broadcaster = EventBroadcaster()

# Request/Response Models
class UserRegistration(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    address: str
    emergency_contact: Optional[str] = None
    role: str = "citizen"

class UserResponse(BaseModel):
    id: int
    name: str
    phone: str
    email: Optional[str]
    role: str
    picture: Optional[str] = None
    session_id: str
    registered_at: str

class CitizenReport(BaseModel):
    text: str
    lat: float
    lon: float
    media_path: Optional[str] = None
    media_urls: Optional[list] = None  # For frontend compatibility
    has_media: Optional[bool] = False  # Whether report includes media
    media_verified: Optional[bool] = False  # Whether media has been verified by image classifier
    media_confidence: Optional[float] = None  # Confidence score from image classifier (future)
    source: str = "citizen"
    user_id: Optional[int] = None
    user_name: Optional[str] = None

class ReportResponse(BaseModel):
    id: int
    message: str
    status: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    user_name: Optional[str] = None
    processing_result: Optional[dict] = None

class HazardEvent(BaseModel):
    id: int
    hazard_type: str
    confidence: float
    severity: int
    status: str
    centroid_lat: float
    centroid_lon: float
    created_at: str
    updated_at: str
    evidence_json: Optional[dict] = None

class LoRaSOS(BaseModel):
    device_id: str
    lat: float
    lon: float
    battery_level: Optional[float] = None
    signal_strength: Optional[float] = None

class VolunteerRegistration(BaseModel):
    name: str
    email: str
    phone: str
    skills: List[str]
    availability: dict
    location: dict
    emergency_contact: Optional[str] = None
    preferred_languages: List[str] = []

class VolunteerTask(BaseModel):
    title: str
    description: str
    location: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    urgency: str = "medium"
    estimated_duration: str
    required_skills: List[str]
    max_volunteers: int = 1
    deadline: Optional[str] = None

# Database helper
def get_db_connection():
    import os
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, 'hazard.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def check_incois_correlation(report_timestamp, citizen_hazard_type, conn):
    """
    Check if citizen report correlates with recent INCOIS bulletins
    Returns correlation info with confidence boost/penalty
    """
    from datetime import datetime, timedelta
    
    cursor = conn.cursor()
    
    # Parse report timestamp
    try:
        if isinstance(report_timestamp, str):
            # Handle different timestamp formats
            if 'T' in report_timestamp:
                report_time = datetime.fromisoformat(report_timestamp.replace('Z', '+00:00'))
            else:
                report_time = datetime.fromisoformat(report_timestamp)
        else:
            report_time = report_timestamp
    except Exception as e:
        print(f"Warning: Could not parse timestamp {report_timestamp}: {e}")
        # If timestamp parsing fails, assume no correlation
        return {'correlation': 0, 'boost': 0.0, 'type': 'none', 'matching_bulletins': 0}
    
    # Look for INCOIS bulletins within 48 hours before report
    # Extended to 72 hours to account for older bulletins in the system
    time_window_start = report_time - timedelta(hours=72)
    time_window_end = report_time + timedelta(hours=6)  # Allow some future bulletins
    
    # Query recent bulletins with better timestamp handling
    cursor.execute("""
        SELECT hazard_type, severity, description, issued_at
        FROM raw_bulletins 
        WHERE datetime(issued_at) BETWEEN datetime(?) AND datetime(?)
        ORDER BY issued_at DESC
        LIMIT 20
    """, (time_window_start.strftime('%Y-%m-%d %H:%M:%S'), 
          time_window_end.strftime('%Y-%m-%d %H:%M:%S')))
    
    recent_bulletins = cursor.fetchall()
    
    print(f"🔍 Checking INCOIS correlation for {citizen_hazard_type}")
    print(f"   Time window: {time_window_start.strftime('%Y-%m-%d %H:%M')} to {time_window_end.strftime('%Y-%m-%d %H:%M')}")
    print(f"   Found {len(recent_bulletins)} bulletins in window")
    
    if not recent_bulletins:
        return {'correlation': 0, 'boost': 0.0, 'type': 'none', 'matching_bulletins': 0}
    
    # Analyze correlation
    matching_bulletins = []
    conflicting_bulletins = []
    
    # Hazard type mapping for broader matching (updated for 5 main hazard types)
    hazard_groups = {
        'flood': ['flood', 'tsunami', 'tides'],
        'tsunami': ['tsunami', 'flood', 'earthquake'],
        'tides': ['tides', 'flood', 'tsunami'],
        'earthquake': ['earthquake', 'tsunami', 'landslide'],
        'landslide': ['landslide', 'earthquake'],
        'emergency': ['tsunami', 'earthquake', 'flood', 'landslide']
    }
    
    related_types = hazard_groups.get(citizen_hazard_type, [citizen_hazard_type])
    
    for bulletin in recent_bulletins:
        bulletin_type = bulletin['hazard_type'].lower()
        
        # Check for matching hazard types
        if bulletin_type in related_types or citizen_hazard_type in [bulletin_type]:
            matching_bulletins.append(bulletin)
            print(f"   ✅ Match: {bulletin_type} (severity {bulletin['severity']})")
        # Check for contradicting conditions (e.g., calm weather vs earthquake/tsunami report)
        elif citizen_hazard_type in ['emergency', 'earthquake', 'tsunami'] and bulletin_type in ['calm', 'clear']:
            conflicting_bulletins.append(bulletin)
    
    # Calculate correlation score and confidence boost
    if matching_bulletins:
        # Positive correlation - boost confidence
        num_matches = len(matching_bulletins)
        avg_severity = sum(b['severity'] for b in matching_bulletins) / num_matches
        
        # Base boost of 15-30% depending on severity and number of matches (GOVERNMENT DATA)
        base_boost = 0.15 + (avg_severity - 1) * 0.03  # 15% + up to 12% for severity
        match_bonus = min(0.05, num_matches * 0.02)    # Up to 5% for multiple matches
        
        total_boost = min(0.30, base_boost + match_bonus)  # Cap at 30% (OFFICIAL DATA)
        
        correlation_type = 'confirmation'
        if avg_severity >= 4:
            correlation_type = 'high_severity_confirmation'
        
        return {
            'correlation': 1,
            'boost': total_boost,
            'type': correlation_type,
            'matching_bulletins': num_matches,
            'avg_severity': avg_severity
        }
    
    elif conflicting_bulletins:
        # Negative correlation - reduce confidence
        penalty = -0.15  # 15% penalty for contradicting official data
        
        return {
            'correlation': -1,
            'boost': penalty,
            'type': 'contradiction',
            'matching_bulletins': len(conflicting_bulletins)
        }
    
    else:
        # No correlation
        return {'correlation': 0, 'boost': 0.0, 'type': 'none', 'matching_bulletins': 0}


def check_social_media_correlation(timestamp, hazard_type, location_keywords, conn):
    """
    Check for correlation with social media news/posts about the same hazard.
    This function will be implemented to integrate with social media APIs.
    
    Args:
        timestamp: When the report was made
        hazard_type: Type of hazard (flood, cyclone, etc.)
        location_keywords: Location terms from the report
        conn: Database connection
    
    Returns:
        dict: {
            'correlation': -1/0/1 (negative/none/positive),
            'boost': confidence boost amount,
            'type': 'confirmation'/'contradiction'/'none',
            'matching_posts': number of matching social media posts,
            'sources': list of social media sources found
        }
    """
    # TODO: Implement social media API integration
    # For now, return placeholder based on hazard type popularity
    
    # Simulate social media correlation based on hazard severity
    # (In real implementation, this would query Twitter/Facebook APIs)
    popular_hazards = ['flood', 'earthquake', 'tsunami', 'landslide', 'emergency']
    
    if hazard_type.lower() in popular_hazards:
        # Simulate finding social media posts for major hazards
        simulated_posts = 3 if hazard_type == 'emergency' else 2
        base_boost = 0.05 + (simulated_posts - 1) * 0.015  # 5-8% base
        severity_bonus = 0.02 if hazard_type in ['emergency', 'tsunami'] else 0.01
        total_boost = min(0.10, base_boost + severity_bonus)  # Cap at 10%
        
        return {
            'correlation': 1,
            'boost': total_boost,
            'type': 'confirmation',
            'matching_posts': simulated_posts,
            'sources': ['Twitter', 'Facebook'] if simulated_posts > 1 else ['Twitter']
        }
    else:
        # No social media coverage for minor hazards
        return {
            'correlation': 0,
            'boost': 0.0,
            'type': 'none',
            'matching_posts': 0,
            'sources': []
        }


@app.get("/")
async def root():
    """Health check and API info"""
    return {
        "status": "🌊 OceanGuard API Online",
        "version": "1.0.0",
        "features": [
            "Citizen report submission",
            "Real-time ML processing",
            "Hazard event fusion",
            "LoRa SOS emergency detection",
            "Real-time SSE events"
        ]
    }

@app.get("/api/events")
async def stream_events():
    """Server-Sent Events endpoint for real-time updates"""
    async def event_generator():
        queue = asyncio.Queue()
        broadcaster.add_connection(queue)
        
        try:
            # Send initial connection event
            initial_event = {
                "type": "connected",
                "data": {"message": "Connected to OceanGuard real-time events"},
                "timestamp": get_current_timestamp()
            }
            yield f"data: {json.dumps(initial_event)}\n\n"
            
            # Stream events
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield message
                except asyncio.TimeoutError:
                    # Send keepalive
                    keepalive = {
                        "type": "keepalive",
                        "data": {"timestamp": get_current_timestamp()},
                        "timestamp": get_current_timestamp()
                    }
                    yield f"data: {json.dumps(keepalive)}\n\n"
        except Exception as e:
            print(f"SSE client disconnected: {e}")
        finally:
            broadcaster.remove_connection(queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )


@app.post("/api/users/register", response_model=UserResponse)
async def register_user(user: UserRegistration):
    """Register a new user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create users table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT UNIQUE,
                address TEXT,
                emergency_contact TEXT,
                role TEXT DEFAULT 'citizen',
                picture TEXT,
                session_id TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Add picture column if it doesn't exist (for migration)
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN picture TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass
        
        # Generate session ID
        import uuid
        session_id = str(uuid.uuid4())
        
        # Insert user
        cursor.execute("""
            INSERT INTO users (name, phone, email, address, emergency_contact, role, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user.name,
            user.phone,
            user.email,
            user.address,
            user.emergency_contact,
            user.role,
            session_id
        ))
        
        user_id = cursor.lastrowid
        
        # Get the registered user data
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user_data = cursor.fetchone()
        
        conn.commit()
        conn.close()
        
        return UserResponse(
            id=user_data['id'],
            name=user_data['name'],
            phone=user_data['phone'],
            email=user_data['email'],
            role=user_data['role'],
            session_id=user_data['session_id'],
            registered_at=user_data['registered_at']
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@app.post("/api/migrate")
async def migrate_database():
    """Add missing columns to database tables"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Add user_id and user_name columns to raw_reports if they don't exist
        try:
            cursor.execute("ALTER TABLE raw_reports ADD COLUMN user_id INTEGER")
        except:
            pass  # Column already exists
            
        try:
            cursor.execute("ALTER TABLE raw_reports ADD COLUMN user_name TEXT")
        except:
            pass  # Column already exists
        
        conn.commit()
        conn.close()
        
        return {"message": "Database migration completed successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")

@app.post("/api/reports", response_model=ReportResponse)
async def submit_report(report: CitizenReport, background_tasks: BackgroundTasks):
    """Submit a new citizen report and process it through ML pipeline"""
    
    try:
        # Handle media files - convert media_urls to media_path for storage
        media_path = None
        has_media = False
        if report.media_urls and len(report.media_urls) > 0:
            # Store first media file path, or concatenate multiple with commas
            media_path = ','.join(report.media_urls)
            has_media = True
        elif report.media_path:
            media_path = report.media_path
            has_media = True
        
        # Set has_media from report if explicitly provided
        if hasattr(report, 'has_media') and report.has_media is not None:
            has_media = report.has_media
            
        # Store in database first
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO raw_reports (source, text, lat, lon, ts, media_path, has_media, media_verified, processed, user_id, user_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.source,
            report.text,
            report.lat,
            report.lon,
            datetime.now(timezone.utc).isoformat(),
            media_path,
            has_media,
            report.media_verified or False,  # Default to False if not provided
            False,
            report.user_id,
            report.user_name
        ))
        
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Broadcast new report event
        await broadcaster.broadcast("new_report", {
            "id": report_id,
            "lat": report.lat,
            "lon": report.lon,
            "user_name": report.user_name,
            "source": report.source,
            "text": report.text[:100] + "..." if len(report.text) > 100 else report.text
        })
        
        # Process through ML pipeline in background
        background_tasks.add_task(process_report_async, report_id)
        
        return ReportResponse(
            id=report_id,
            message="Report submitted successfully and queued for processing",
            status="received",
            lat=report.lat,
            lon=report.lon,
            user_name=report.user_name,
            processing_result=None
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit report: {str(e)}")

async def process_report_async(report_id: int):
    """Background task to process report through PROGRESSIVE CONFIDENCE ML pipeline"""
    try:
        # Use the proper ML pipeline with progressive confidence
        from services.ingest import ProcessingPipeline
        
        pipeline = ProcessingPipeline()
        success = pipeline.process_single_report(report_id)
        
        if success:
            print(f"✅ Progressive ML pipeline completed for report {report_id}")
        else:
            print(f"❌ Progressive ML pipeline failed for report {report_id}")
        
        # Broadcast ML processing completion
        await broadcaster.broadcast("report_processed", {
            "report_id": report_id,
            "processed": success,
            "result": "Progressive ML analysis completed" if success else "Progressive ML analysis failed"
        })
        
    except Exception as e:
        print(f"❌ Error in progressive ML processing for report {report_id}: {str(e)}")
        # Fallback to marking as unprocessed so it can be retried
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE raw_reports SET processed = 0 WHERE id = ?", (report_id,))
        conn.commit()
        conn.close()

@app.get("/api/hazards", response_model=List[HazardEvent])
async def get_hazards(limit: int = 50, status: Optional[str] = None):
    """Get current hazard events"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM hazard_events"
    params = []
    
    if status:
        query += " WHERE status = ?"
        params.append(status)
    
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    events = cursor.fetchall()
    conn.close()
    
    return [
        HazardEvent(
            id=event["id"],
            hazard_type=event["hazard_type"],
            confidence=event["confidence"],
            severity=event["severity"],
            status=event["status"],
            centroid_lat=event["centroid_lat"],
            centroid_lon=event["centroid_lon"],
            created_at=event["created_at"],
            updated_at=event["updated_at"],
            evidence_json=json.loads(event["evidence_json"]) if event["evidence_json"] else None
        )
        for event in events
    ]

@app.get("/api/hazards/approved")
async def get_approved_hazards(limit: int = 50):
    """Get approved hazard events"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM hazard_events 
        WHERE status = 'approved' 
        ORDER BY created_at DESC 
        LIMIT ?
    """, (limit,))
    events = cursor.fetchall()
    conn.close()
    
    hazards = []
    for event in events:
        hazard = {
            "id": event["id"],
            "type": event["hazard_type"],
            "confidence": event["confidence"],
            "confidence_level": "high" if event["confidence"] > 0.7 else "medium" if event["confidence"] > 0.4 else "low",
            "severity": event["severity"],
            "status": event["status"],
            "location": f"{event['centroid_lat']:.4f}, {event['centroid_lon']:.4f}",
            "centroid_lat": event["centroid_lat"],
            "centroid_lon": event["centroid_lon"],
            "created_at": event["created_at"],
            "updated_at": event["updated_at"],
            "validated_at": event["updated_at"],  # Use updated_at as validation time
            "title": f"{event['hazard_type'].title()} Alert"
        }
        
        # Parse evidence for additional details
        if event["evidence_json"]:
            try:
                evidence = json.loads(event["evidence_json"])
                if evidence.get("reports"):
                    hazard["report_count"] = len(evidence["reports"])
            except:
                pass
                
        hazards.append(hazard)
    
    return {"hazards": hazards}

@app.get("/api/hazards/pending")
async def get_pending_hazards(limit: int = 50):
    """Get pending hazard events that need validation"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM hazard_events 
        WHERE status IN ('pending', 'review', 'unvalidated') OR status IS NULL
        ORDER BY created_at DESC 
        LIMIT ?
    """, (limit,))
    events = cursor.fetchall()
    conn.close()
    
    return [
        HazardEvent(
            id=event["id"],
            hazard_type=event["hazard_type"],
            confidence=event["confidence"],
            severity=event["severity"],
            status=event["status"] or "pending",
            centroid_lat=event["centroid_lat"],
            centroid_lon=event["centroid_lon"],
            created_at=event["created_at"],
            updated_at=event["updated_at"],
            evidence_json=json.loads(event["evidence_json"]) if event["evidence_json"] else None
        )
        for event in events
    ]

@app.get("/api/hazards/{hazard_id}")
async def get_hazard_details(hazard_id: int):
    """Get detailed information about a specific hazard event"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM hazard_events WHERE id = ?", (hazard_id,))
    event = cursor.fetchone()
    
    if not event:
        raise HTTPException(status_code=404, detail="Hazard event not found")
    
    # Get evidence/source reports
    evidence = {}
    if event["evidence_json"]:
        try:
            evidence = json.loads(event["evidence_json"])
        except:
            evidence = {"raw_evidence": event["evidence_json"]}
    
    conn.close()
    
    return {
        "event": dict(event),
        "evidence": evidence
    }

@app.get("/api/raw-reports")
async def get_raw_reports(limit: int = 100, processed: Optional[bool] = None):
    """Get raw citizen reports for map display - shows all reports by default"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT id, source, text, lat, lon, ts, nlp_type, nlp_conf, credibility, user_name, media_path FROM raw_reports"
    params = []
    
    if processed is not None:
        query += " WHERE processed = ?"
        params.append(processed)
    
    query += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    reports = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": report["id"],
            "source": report["source"],
            "text": report["text"],
            "lat": report["lat"],
            "lon": report["lon"],
            "timestamp": report["ts"],
            "hazard_type": report["nlp_type"] or "pending",
            "confidence": report["nlp_conf"] or 0.0,
            "credibility": report["credibility"] or 0.0,
            "user_name": report["user_name"] or "Anonymous",
            "media_path": report["media_path"],
            "image_url": f"/api/media/{report['media_path']}" if report["media_path"] else None
        }
        for report in reports
    ]

@app.get("/api/incois-bulletins")
async def get_incois_bulletins(limit: int = 50):
    """Get INCOIS bulletins for display"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, issued_at, hazard_type, severity, description 
        FROM raw_bulletins 
        ORDER BY issued_at DESC 
        LIMIT ?
    """, (limit,))
    
    bulletins = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": bulletin["id"],
            "issued_at": bulletin["issued_at"],
            "hazard_type": bulletin["hazard_type"],
            "severity": bulletin["severity"],
            "description": bulletin["description"]
        }
        for bulletin in bulletins
    ]

@app.post("/api/lora/sos")
async def lora_emergency(sos_data: LoRaSOS, background_tasks: BackgroundTasks):
    """Handle LoRa SOS emergency beacon"""
    
    try:
        # Create emergency report
        emergency_text = f"EMERGENCY SOS from LoRa device {sos_data.device_id}. Immediate assistance required!"
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO raw_reports (source, text, lat, lon, ts, media_path, processed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "lora_sos",
            emergency_text,
            sos_data.lat,
            sos_data.lon,
            datetime.now(timezone.utc).isoformat(),
            f"lora_device_{sos_data.device_id}",
            False
        ))
        
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Priority processing for emergency
        background_tasks.add_task(process_emergency_async, report_id, sos_data.device_id)
        
        return {
            "status": "emergency_received",
            "report_id": report_id,
            "message": f"Emergency SOS from device {sos_data.device_id} received and prioritized",
            "coordinates": {"lat": sos_data.lat, "lon": sos_data.lon}
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process SOS: {str(e)}")

async def process_emergency_async(report_id: int, device_id: str):
    """High-priority processing for emergency SOS"""
    try:
        # Process immediately with emergency classification
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get the report
        cursor.execute("SELECT * FROM raw_reports WHERE id = ?", (report_id,))
        report = cursor.fetchone()
        
        if report:
            # Emergency classification
            cursor.execute("""
                UPDATE raw_reports 
                SET processed = 1, nlp_type = 'emergency', nlp_conf = 0.99, credibility = 0.95
                WHERE id = ?
            """, (report_id,))
            
            # Create immediate hazard event
            from datetime import datetime, timezone
            current_time = datetime.now(timezone.utc).isoformat()
            
            cursor.execute("""
                INSERT INTO hazard_events 
                (hazard_type, confidence, severity, status, centroid_lat, centroid_lon, 
                 evidence_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'emergency', 0.99, 5, 'emergency', report['lat'], report['lon'],
                json.dumps({"device_id": device_id, "source_reports": [report_id]}),
                current_time, current_time
            ))
            
            hazard_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            print(f"🚨 EMERGENCY processed: Device {device_id}, Report {report_id}, Hazard {hazard_id}")
            
            # Broadcast emergency
            await broadcaster.broadcast("emergency_alert", {
                "device_id": device_id,
                "hazard_id": hazard_id,
                "lat": report['lat'],
                "lon": report['lon'],
                "message": f"EMERGENCY SOS from device {device_id}"
            })
        
    except Exception as e:
        print(f"❌ Error processing emergency {report_id}: {e}")

@app.get("/api/reports/{report_id}")
async def get_report_status(report_id: int):
    """Check processing status of a specific report"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, source, text, lat, lon, ts, processed, 
               nlp_type, nlp_conf, credibility, group_id
        FROM raw_reports WHERE id = ?
    """, (report_id,))
    
    report = cursor.fetchone()
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    # Check if it's been fused into a hazard event
    hazard_event = None
    if report["group_id"]:
        cursor.execute("""
            SELECT id, hazard_type, confidence, status 
            FROM hazard_events 
            WHERE evidence_json LIKE ?
        """, (f'%"group_id": {report["group_id"]}%',))
        hazard_event = cursor.fetchone()
    
    conn.close()
    
    return {
        "report": dict(report),
        "processing_status": "completed" if report["processed"] else "pending",
        "ml_results": {
            "classified_as": report["nlp_type"],
            "confidence": report["nlp_conf"],
            "credibility": report["credibility"],
            "group_id": report["group_id"]
        },
        "hazard_event": dict(hazard_event) if hazard_event else None
    }

# Citizen-specific endpoints
@app.get("/api/citizen/hazard-feed")
async def get_citizen_hazard_feed():
    """Get real-time hazard feed for citizens"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get recent hazards with mock social media data
    cursor.execute("""
        SELECT 
            id,
            hazard_type as type,
            severity,
            centroid_lat as lat,
            centroid_lon as lon,
            created_at as timestamp,
            confidence,
            status
        FROM hazard_events 
        WHERE created_at >= datetime('now', '-7 days')
        ORDER BY created_at DESC
        LIMIT 20
    """)
    
    hazards = []
    for row in cursor.fetchall():
        hazard = dict(row)
        # Add mock additional data
        hazard.update({
            "location": f"Coastal Region ({hazard['lat']:.2f}, {hazard['lon']:.2f})",
            "description": f"Detected {hazard['type']} with {hazard['severity']} severity level",
            "source": "INCOIS + Citizen Reports",
            "confidence": int(hazard["confidence"] * 100)
        })
        hazards.append(hazard)
    
    conn.close()
    return hazards

@app.get("/api/citizen/my-reports")
async def get_user_reports():
    """Get reports submitted by the current user"""
    # For now, return sample reports
    # In production, filter by authenticated user
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get recent user reports (for demonstration, get all recent reports)
    cursor.execute("""
        SELECT id, source, text, lat, lon, ts as timestamp, processed, nlp_type, nlp_conf
        FROM raw_reports 
        WHERE source = 'citizen'
        ORDER BY ts DESC 
        LIMIT 10
    """)
    
    reports = []
    for row in cursor.fetchall():
        report = dict(row)
        reports.append(report)
    
    conn.close()
    return reports

@app.post("/api/citizen/submit-report")
async def submit_citizen_report(background_tasks: BackgroundTasks, report_data: dict):
    """Submit a new citizen report and process through ML pipeline"""
    try:
        # Extract report data
        location = report_data.get('location', '')
        description = report_data.get('description', '')
        hazard_type = report_data.get('hazard_type', '')
        severity = report_data.get('severity', 'low')
        media_files = report_data.get('media_files', [])
        
        # Get coordinates
        lat = report_data.get('lat')
        lon = report_data.get('lon')
        
        if not lat or not lon:
            raise HTTPException(status_code=400, detail="Location coordinates are required")
            
        # Create report text
        report_text = f"Citizen report: {hazard_type} - {description} at {location}"
        
        # Submit to ML pipeline
        report_result = await submit_report(
            CitizenReport(
                text=report_text,
                lat=float(lat),
                lon=float(lon),
                user_name=report_data.get('user_name', 'Anonymous'),
                media_urls=media_files or []
            ),
            background_tasks
        )
        
        return {
            "success": True,
            "report_id": report_result.id,
            "message": "Report submitted successfully and sent for processing",
            "processing_status": "queued"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit report: {str(e)}")

@app.get("/api/citizen/notifications")
async def get_user_notifications():
    """Get notifications for the current user"""
    # Mock notifications for demonstration
    return [
        {
            "id": 1,
            "title": "High Wave Alert",
            "message": "High waves detected in your area. Please exercise caution.",
            "timestamp": "2025-09-17T14:30:00",
            "read": False,
            "severity": "high"
        },
        {
            "id": 2,
            "title": "Weather Update",
            "message": "Moderate weather conditions expected for next 24 hours.",
            "timestamp": "2025-09-17T12:00:00",
            "read": True,
            "severity": "moderate"
        }
    ]

# Volunteer endpoints
@app.post("/api/volunteer/register")
async def register_volunteer(volunteer_data: VolunteerRegistration):
    """Register a new volunteer"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create volunteers table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS volunteers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT,
                skills TEXT,
                availability TEXT,
                location TEXT,
                emergency_contact TEXT,
                preferred_languages TEXT,
                status TEXT DEFAULT 'active',
                rating REAL DEFAULT 0.0,
                tasks_completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert volunteer data
        cursor.execute("""
            INSERT INTO volunteers (name, email, phone, skills, availability, location, 
                                  emergency_contact, preferred_languages)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            volunteer_data.name,
            volunteer_data.email,
            volunteer_data.phone,
            json.dumps(volunteer_data.skills),
            json.dumps(volunteer_data.availability),
            json.dumps(volunteer_data.location),
            volunteer_data.emergency_contact,
            json.dumps(volunteer_data.preferred_languages)
        ))
        
        conn.commit()
        volunteer_id = cursor.lastrowid
        conn.close()
        
        # Broadcast new volunteer registration
        await broadcaster.broadcast("volunteer_registered", {
            "volunteer_id": volunteer_id,
            "name": volunteer_data.name,
            "skills": volunteer_data.skills
        })
        
        return {
            "success": True,
            "volunteer_id": volunteer_id,
            "message": "Volunteer registration successful",
            "data": {
                "id": volunteer_id,
                "name": volunteer_data.name,
                "email": volunteer_data.email,
                "skills": volunteer_data.skills
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Volunteer registration failed: {str(e)}")

@app.get("/api/volunteer/profile/{volunteer_id}")
async def get_volunteer_profile(volunteer_id: int):
    """Get volunteer profile information"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM volunteers WHERE id = ?", (volunteer_id,))
        volunteer = cursor.fetchone()
        
        if not volunteer:
            raise HTTPException(status_code=404, detail="Volunteer not found")
        
        # Get completed tasks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS volunteer_task_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                volunteer_id INTEGER,
                status TEXT DEFAULT 'assigned',
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                notes TEXT
            )
        """)
        
        cursor.execute("""
            SELECT COUNT(*) FROM volunteer_task_assignments 
            WHERE volunteer_id = ? AND status = 'completed'
        """, (volunteer_id,))
        completed_tasks = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "id": volunteer["id"],
            "name": volunteer["name"],
            "email": volunteer["email"],
            "phone": volunteer["phone"],
            "skills": json.loads(volunteer["skills"]) if volunteer["skills"] else [],
            "availability": json.loads(volunteer["availability"]) if volunteer["availability"] else {},
            "location": json.loads(volunteer["location"]) if volunteer["location"] else {},
            "status": volunteer["status"],
            "rating": volunteer["rating"],
            "tasks_completed": completed_tasks,
            "created_at": volunteer["created_at"]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get volunteer profile: {str(e)}")

@app.get("/api/volunteer/tasks")
async def get_volunteer_tasks(volunteer_id: Optional[int] = None, status: str = "open"):
    """Get available volunteer tasks"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create volunteer_tasks table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS volunteer_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                location TEXT,
                lat REAL,
                lon REAL,
                urgency TEXT DEFAULT 'medium',
                estimated_duration TEXT,
                required_skills TEXT,
                max_volunteers INTEGER DEFAULT 1,
                current_volunteers INTEGER DEFAULT 0,
                status TEXT DEFAULT 'open',
                deadline TIMESTAMP,
                created_by TEXT DEFAULT 'system',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert some sample tasks if table is empty
        cursor.execute("SELECT COUNT(*) FROM volunteer_tasks")
        if cursor.fetchone()[0] == 0:
            sample_tasks = [
                ("Emergency Response - Coastal Evacuation", 
                 "Assist with evacuation procedures in high-risk coastal areas during cyclone warning",
                 "Marina Beach, Chennai", 13.0827, 80.2707, "high", "6 hours", 
                 json.dumps(["first_aid", "communication", "crowd_management"]), 5),
                ("Data Collection - Storm Damage Assessment",
                 "Document and assess infrastructure damage post-storm for recovery planning",
                 "Cuddalore District", 11.7480, 79.7714, "medium", "4 hours",
                 json.dumps(["documentation", "photography", "basic_assessment"]), 3),
                ("Community Outreach - Safety Awareness",
                 "Conduct safety awareness sessions in fishing communities about coastal hazards",
                 "Nagapattinam", 10.7672, 79.8420, "low", "3 hours",
                 json.dumps(["communication", "local_language", "presentation"]), 2),
                ("Environmental Monitoring - Water Quality Check",
                 "Collect water samples and monitor environmental conditions after storm events",
                 "Puducherry Coast", 11.9416, 79.8083, "medium", "2 hours",
                 json.dumps(["environmental_science", "sample_collection", "data_recording"]), 2)
            ]
            
            for task in sample_tasks:
                cursor.execute("""
                    INSERT INTO volunteer_tasks 
                    (title, description, location, lat, lon, urgency, estimated_duration, required_skills, max_volunteers)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, task)
            conn.commit()
        
        # Get tasks based on filters
        query = "SELECT * FROM volunteer_tasks WHERE status = ?"
        params = [status]
        
        cursor.execute(query + " ORDER BY urgency DESC, created_at DESC", params)
        tasks = cursor.fetchall()
        
        conn.close()
        
        return [
            {
                "id": task["id"],
                "title": task["title"],
                "description": task["description"],
                "location": task["location"],
                "lat": task["lat"],
                "lon": task["lon"],
                "urgency": task["urgency"],
                "estimated_duration": task["estimated_duration"],
                "required_skills": json.loads(task["required_skills"]) if task["required_skills"] else [],
                "max_volunteers": task["max_volunteers"],
                "current_volunteers": task["current_volunteers"],
                "status": task["status"],
                "deadline": task["deadline"],
                "created_at": task["created_at"]
            }
            for task in tasks
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get volunteer tasks: {str(e)}")

@app.post("/api/volunteer/tasks/{task_id}/apply")
async def apply_for_task(task_id: int, application_data: dict):
    """Apply for a volunteer task"""
    try:
        volunteer_id = application_data.get("volunteer_id")
        if not volunteer_id:
            raise HTTPException(status_code=400, detail="Volunteer ID required")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if task exists and is open
        cursor.execute("SELECT * FROM volunteer_tasks WHERE id = ? AND status = 'open'", (task_id,))
        task = cursor.fetchone()
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found or not available")
        
        # Check if volunteer already applied
        cursor.execute("""
            SELECT * FROM volunteer_task_assignments 
            WHERE task_id = ? AND volunteer_id = ?
        """, (task_id, volunteer_id))
        
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Already applied for this task")
        
        # Check if task is at capacity
        if task["current_volunteers"] >= task["max_volunteers"]:
            raise HTTPException(status_code=400, detail="Task is at full capacity")
        
        # Create assignment
        cursor.execute("""
            INSERT INTO volunteer_task_assignments (task_id, volunteer_id, status)
            VALUES (?, ?, 'assigned')
        """, (task_id, volunteer_id))
        
        # Update task volunteer count
        cursor.execute("""
            UPDATE volunteer_tasks 
            SET current_volunteers = current_volunteers + 1,
                status = CASE WHEN current_volunteers + 1 >= max_volunteers THEN 'assigned' ELSE 'open' END
            WHERE id = ?
        """, (task_id,))
        
        conn.commit()
        assignment_id = cursor.lastrowid
        conn.close()
        
        # Broadcast task assignment
        await broadcaster.broadcast("task_assigned", {
            "task_id": task_id,
            "volunteer_id": volunteer_id,
            "assignment_id": assignment_id
        })
        
        return {
            "success": True,
            "assignment_id": assignment_id,
            "message": "Successfully applied for task",
            "task_id": task_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to apply for task: {str(e)}")

@app.patch("/api/volunteer/tasks/{task_id}/complete")
async def complete_task(task_id: int, completion_data: dict):
    """Mark a volunteer task as completed"""
    try:
        volunteer_id = completion_data.get("volunteer_id")
        notes = completion_data.get("notes", "")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update assignment status
        cursor.execute("""
            UPDATE volunteer_task_assignments 
            SET status = 'completed', completed_at = ?, notes = ?
            WHERE task_id = ? AND volunteer_id = ?
        """, (get_current_timestamp(), notes, task_id, volunteer_id))
        
        # Update volunteer stats
        cursor.execute("""
            UPDATE volunteers 
            SET tasks_completed = tasks_completed + 1,
                updated_at = ?
            WHERE id = ?
        """, (get_current_timestamp(), volunteer_id))
        
        conn.commit()
        conn.close()
        
        # Broadcast completion
        await broadcaster.broadcast("task_completed", {
            "task_id": task_id,
            "volunteer_id": volunteer_id,
            "completed_at": get_current_timestamp()
        })
        
        return {
            "success": True,
            "message": "Task marked as completed",
            "completed_at": get_current_timestamp()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to complete task: {str(e)}")

@app.get("/api/volunteer/{volunteer_id}/assignments")
async def get_volunteer_assignments(volunteer_id: int):
    """Get all task assignments for a volunteer"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT vta.*, vt.title, vt.description, vt.location, vt.urgency, vt.estimated_duration
            FROM volunteer_task_assignments vta
            JOIN volunteer_tasks vt ON vta.task_id = vt.id
            WHERE vta.volunteer_id = ?
            ORDER BY vta.assigned_at DESC
        """, (volunteer_id,))
        
        assignments = cursor.fetchall()
        conn.close()
        
        return [
            {
                "assignment_id": assignment["id"],
                "task_id": assignment["task_id"],
                "title": assignment["title"],
                "description": assignment["description"],
                "location": assignment["location"],
                "urgency": assignment["urgency"],
                "estimated_duration": assignment["estimated_duration"],
                "status": assignment["status"],
                "assigned_at": assignment["assigned_at"],
                "completed_at": assignment["completed_at"],
                "notes": assignment["notes"]
            }
            for assignment in assignments
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get volunteer assignments: {str(e)}")

# Admin volunteer management endpoints
@app.get("/api/admin/volunteers")
async def get_all_volunteers():
    """Get all registered volunteers for admin management"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.*, 
                   (SELECT COUNT(*) FROM volunteer_task_assignments vta 
                    WHERE vta.volunteer_id = v.id AND vta.status = 'completed') as completed_tasks,
                   (SELECT COUNT(*) FROM volunteer_task_assignments vta 
                    WHERE vta.volunteer_id = v.id AND vta.status = 'assigned') as active_tasks
            FROM volunteers v
            ORDER BY v.created_at DESC
        """)
        
        volunteers = cursor.fetchall()
        conn.close()
        
        return [
            {
                "id": volunteer["id"],
                "name": volunteer["name"],
                "email": volunteer["email"],
                "phone": volunteer["phone"],
                "skills": json.loads(volunteer["skills"]) if volunteer["skills"] else [],
                "status": volunteer["status"],
                "rating": volunteer["rating"],
                "tasks_completed": volunteer["completed_tasks"],
                "active_tasks": volunteer["active_tasks"],
                "created_at": volunteer["created_at"],
                "location": json.loads(volunteer["location"]) if volunteer["location"] else {}
            }
            for volunteer in volunteers
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get volunteers: {str(e)}")

@app.post("/api/admin/volunteer-tasks")
async def create_volunteer_task(task_data: VolunteerTask):
    """Create a new volunteer task (admin only)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO volunteer_tasks 
            (title, description, location, lat, lon, urgency, estimated_duration, 
             required_skills, max_volunteers, deadline, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_data.title,
            task_data.description,
            task_data.location,
            task_data.lat,
            task_data.lon,
            task_data.urgency,
            task_data.estimated_duration,
            json.dumps(task_data.required_skills),
            task_data.max_volunteers,
            task_data.deadline,
            "admin"
        ))
        
        conn.commit()
        task_id = cursor.lastrowid
        conn.close()
        
        # Broadcast new task creation
        await broadcaster.broadcast("new_volunteer_task", {
            "task_id": task_id,
            "title": task_data.title,
            "urgency": task_data.urgency,
            "location": task_data.location
        })
        
        return {
            "success": True,
            "task_id": task_id,
            "message": "Volunteer task created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create volunteer task: {str(e)}")

@app.get("/api/admin/volunteer-tasks")
async def get_all_volunteer_tasks():
    """Get all volunteer tasks for admin management"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT vt.*,
                   (SELECT COUNT(*) FROM volunteer_task_assignments vta 
                    WHERE vta.task_id = vt.id) as total_applications,
                   (SELECT COUNT(*) FROM volunteer_task_assignments vta 
                    WHERE vta.task_id = vt.id AND vta.status = 'completed') as completed_assignments
            FROM volunteer_tasks vt
            ORDER BY vt.created_at DESC
        """)
        
        tasks = cursor.fetchall()
        conn.close()
        
        return [
            {
                "id": task["id"],
                "title": task["title"],
                "description": task["description"],
                "location": task["location"],
                "lat": task["lat"],
                "lon": task["lon"],
                "urgency": task["urgency"],
                "estimated_duration": task["estimated_duration"],
                "required_skills": json.loads(task["required_skills"]) if task["required_skills"] else [],
                "max_volunteers": task["max_volunteers"],
                "current_volunteers": task["current_volunteers"],
                "status": task["status"],
                "deadline": task["deadline"],
                "created_at": task["created_at"],
                "total_applications": task["total_applications"],
                "completed_assignments": task["completed_assignments"]
            }
            for task in tasks
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get volunteer tasks: {str(e)}")

@app.post("/api/admin/volunteers/{volunteer_id}/assign-task")
async def assign_task_to_volunteer(volunteer_id: int, assignment_data: dict):
    """Assign a specific task to a volunteer (admin only)"""
    try:
        task_id = assignment_data.get("task_id")
        if not task_id:
            raise HTTPException(status_code=400, detail="Task ID required")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if assignment already exists
        cursor.execute("""
            SELECT * FROM volunteer_task_assignments 
            WHERE task_id = ? AND volunteer_id = ?
        """, (task_id, volunteer_id))
        
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Assignment already exists")
        
        # Create assignment
        cursor.execute("""
            INSERT INTO volunteer_task_assignments (task_id, volunteer_id, status)
            VALUES (?, ?, 'assigned')
        """, (task_id, volunteer_id))
        
        # Update task volunteer count
        cursor.execute("""
            UPDATE volunteer_tasks 
            SET current_volunteers = current_volunteers + 1
            WHERE id = ?
        """, (task_id,))
        
        conn.commit()
        assignment_id = cursor.lastrowid
        conn.close()
        
        # Broadcast assignment
        await broadcaster.broadcast("admin_task_assigned", {
            "task_id": task_id,
            "volunteer_id": volunteer_id,
            "assignment_id": assignment_id,
            "assigned_by": "admin"
        })
        
        return {
            "success": True,
            "assignment_id": assignment_id,
            "message": "Task assigned successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to assign task: {str(e)}")

# Enhanced notification endpoints
@app.get("/api/notifications")
async def get_notifications(user_type: str = "citizen", user_id: Optional[int] = None):
    """Get notifications for users"""
    try:
        # Mock notifications with different types
        notifications = [
            {
                "id": 1,
                "title": "🌊 High Wave Alert",
                "message": "High waves (3-4m) detected near Marina Beach. Avoid coastal areas.",
                "type": "hazard_alert",
                "severity": "high",
                "timestamp": "2025-01-17T14:30:00",
                "read": False,
                "location": "Marina Beach, Chennai",
                "action_required": True
            },
            {
                "id": 2,
                "title": "✅ Report Processed",
                "message": "Your hazard report has been analyzed and confirmed. Thank you for keeping the community safe!",
                "type": "report_update",
                "severity": "info",
                "timestamp": "2025-01-17T13:45:00",
                "read": True,
                "action_required": False
            },
            {
                "id": 3,
                "title": "🤝 New Volunteer Opportunity",
                "message": "Emergency response needed in Cuddalore. Your skills match this urgent task.",
                "type": "volunteer_opportunity",
                "severity": "medium",
                "timestamp": "2025-01-17T12:20:00",
                "read": False,
                "action_required": True
            },
            {
                "id": 4,
                "title": "⚠️ Weather Advisory",
                "message": "Cyclone warning issued for Tamil Nadu coast. Monitor updates regularly.",
                "type": "weather_advisory",
                "severity": "high",
                "timestamp": "2025-01-17T10:00:00",
                "read": False,
                "location": "Tamil Nadu Coast",
                "action_required": False
            }
        ]
        
        # Filter by user type if needed
        if user_type == "volunteer":
            # Add volunteer-specific notifications
            notifications.extend([
                {
                    "id": 5,
                    "title": "📋 Task Assignment",
                    "message": "You've been assigned to 'Emergency Response - Coastal Evacuation' task.",
                    "type": "task_assignment",
                    "severity": "high",
                    "timestamp": "2025-01-17T09:30:00",
                    "read": False,
                    "action_required": True
                }
            ])
        
        return notifications
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get notifications: {str(e)}")

@app.patch("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int):
    """Mark a notification as read"""
    return {
        "success": True,
        "notification_id": notification_id,
        "message": "Notification marked as read"
    }

@app.post("/api/notifications/send")
async def send_notification(notification_data: dict):
    """Send a notification to users (admin only)"""
    try:
        # This would integrate with push notification services
        # For now, just broadcast via SSE
        await broadcaster.broadcast("new_notification", {
            "title": notification_data.get("title"),
            "message": notification_data.get("message"),
            "type": notification_data.get("type", "general"),
            "severity": notification_data.get("severity", "info"),
            "timestamp": get_current_timestamp()
        })
        
        return {
            "success": True,
            "message": "Notification sent successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send notification: {str(e)}")

# Admin API endpoints
@app.get("/api/stats")
async def get_admin_stats():
    """Get admin dashboard statistics"""
    try:
        conn = get_db_connection()
        
        # Get total counts
        total_reports = conn.execute("SELECT COUNT(*) FROM raw_reports").fetchone()[0]
        total_hazards = conn.execute("SELECT COUNT(*) FROM hazard_events").fetchone()[0]
        total_bulletins = conn.execute("SELECT COUNT(*) FROM raw_bulletins").fetchone()[0]
        
        # Get pending validation count
        pending_validation = conn.execute(
            "SELECT COUNT(*) FROM hazard_events WHERE status = 'pending' OR status = 'detected'"
        ).fetchone()[0]
        
        # Get recent activity (last 24 hours)
        recent_reports = conn.execute(
            "SELECT COUNT(*) FROM raw_reports WHERE datetime(ts) > datetime('now', '-1 day')"
        ).fetchone()[0]
        
        recent_hazards = conn.execute(
            "SELECT COUNT(*) FROM hazard_events WHERE datetime(created_at) > datetime('now', '-1 day')"
        ).fetchone()[0]
        
        # Get confidence distribution
        confidence_dist = conn.execute("""
            SELECT 
                CASE 
                    WHEN confidence >= 0.8 THEN 'high'
                    WHEN confidence >= 0.5 THEN 'medium'
                    ELSE 'low'
                END as confidence_level,
                COUNT(*) as count
            FROM hazard_events 
            GROUP BY confidence_level
        """).fetchall()
        
        conn.close()
        
        return {
            "total_reports": total_reports,
            "total_hazards": total_hazards,
            "total_bulletins": total_bulletins,
            "pending_validation": pending_validation,
            "recent_activity": {
                "reports_24h": recent_reports,
                "hazards_24h": recent_hazards
            },
            "confidence_distribution": {row[0]: row[1] for row in confidence_dist},
            "system_status": "operational",
            "last_updated": get_current_timestamp()
        }
        
    except Exception as e:
        print(f"Error getting admin stats: {e}")
        return {
            "total_reports": 0,
            "total_hazards": 0,
            "total_bulletins": 0,
            "pending_validation": 0,
            "recent_activity": {"reports_24h": 0, "hazards_24h": 0},
            "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
            "system_status": "error",
            "last_updated": get_current_timestamp()
        }

@app.post("/api/admin/hazards/{hazard_id}/validate")
async def validate_hazard(hazard_id: int, validation_data: dict):
    """Validate a hazard event with confidence adjustment"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current hazard data
        cursor.execute("SELECT * FROM hazard_events WHERE id = ?", (hazard_id,))
        hazard = cursor.fetchone()
        
        if not hazard:
            raise HTTPException(status_code=404, detail="Hazard not found")
        
        status = validation_data.get('status', 'validated')
        current_confidence = hazard['confidence'] or 0.5
        
        # Adjust confidence based on validation
        if status == 'approved':
            # Boost confidence for approved events (admin validation adds credibility)
            confidence_boost = 0.2  # 20% boost
            new_confidence = min(current_confidence + confidence_boost, 1.0)
            
            # Update both status and confidence
            cursor.execute("""
                UPDATE hazard_events 
                SET status = ?, confidence = ?, updated_at = ?
                WHERE id = ?
            """, (status, new_confidence, get_current_timestamp(), hazard_id))
            
        elif status == 'rejected':
            # Reduce confidence for rejected events
            confidence_reduction = 0.3  # 30% reduction
            new_confidence = max(current_confidence - confidence_reduction, 0.0)
            
            cursor.execute("""
                UPDATE hazard_events 
                SET status = ?, confidence = ?, updated_at = ?
                WHERE id = ?
            """, (status, new_confidence, get_current_timestamp(), hazard_id))
        else:
            # Just update status for other cases
            cursor.execute("""
                UPDATE hazard_events 
                SET status = ?, updated_at = ?
                WHERE id = ?
            """, (status, get_current_timestamp(), hazard_id))
        
        conn.commit()
        conn.close()
        
        # Broadcast validation event
        await broadcaster.broadcast("hazard_validated", {
            "hazard_id": hazard_id,
            "status": status,
            "confidence_updated": status in ['approved', 'rejected'],
            "validated_by": "admin"
        })
        
        return {
            "message": "Hazard validated successfully", 
            "hazard_id": hazard_id,
            "status": status,
            "confidence_updated": status in ['approved', 'rejected']
        }
        
    except Exception as e:
        print(f"Error validating hazard: {e}")
        raise HTTPException(status_code=500, detail="Failed to validate hazard")

@app.get("/api/admin/users")
async def get_users():
    """Get user list for admin management"""
    # Since authentication was removed, return mock user data
    return [
        {
            "id": 1,
            "name": "Admin User",
            "email": "admin@oceanguard.com",
            "role": "admin",
            "reports_count": 0,
            "last_active": get_current_timestamp()
        },
        {
            "id": 2,
            "name": "Citizen User",
            "email": "citizen@example.com", 
            "role": "citizen",
            "reports_count": 5,
            "last_active": get_current_timestamp()
        }
    ]

# Media file serving endpoint
@app.get("/api/media/{filename}")
async def serve_media_file(filename: str):
    """Serve media files uploaded with reports"""
    try:
        # Define media directory (you might want to configure this)
        media_dir = os.path.join(os.path.dirname(__file__), "media")
        
        # Security: Check if file exists and is within media directory
        file_path = os.path.join(media_dir, filename)
        
        # Prevent directory traversal attacks
        if not os.path.commonpath([media_dir, file_path]) == media_dir:
            raise HTTPException(status_code=400, detail="Invalid file path")
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Media file not found")
        
        return FileResponse(file_path)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error serving media file: {str(e)}")

# Static files for development (media uploads)
# Create media directory if it doesn't exist
media_directory = os.path.join(os.path.dirname(__file__), "media")
if not os.path.exists(media_directory):
    os.makedirs(media_directory)

# Mount static files
app.mount("/media", StaticFiles(directory=media_directory), name="media")

if __name__ == "__main__":
    import uvicorn
    print("🌊 Starting OceanGuard API Server...")
    print("   Visit: http://127.0.0.1:8000")
    print("   API Docs: http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)