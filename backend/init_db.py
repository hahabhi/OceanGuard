#!/usr/bin/env python3
"""
Initialize OceanGuard Database
Creates all necessary tables for the application
"""

import sqlite3
from datetime import datetime

def init_database():
    """Initialize the database with all required tables"""
    conn = sqlite3.connect('oceanguard.db')
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            address TEXT,
            emergency_contact TEXT,
            role TEXT DEFAULT 'citizen',
            picture TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create raw_reports table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            text TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            media_path TEXT,
            processed BOOLEAN DEFAULT FALSE,
            user_id INTEGER,
            user_name TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    
    # Create hazard_events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hazard_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hazard_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            confidence_score REAL DEFAULT 0.0,
            incois_contribution REAL DEFAULT 0.0,
            citizen_contribution REAL DEFAULT 0.0,
            social_media_contribution REAL DEFAULT 0.0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            validated BOOLEAN DEFAULT FALSE,
            status TEXT DEFAULT 'pending'
        )
    """)
    
    # Create volunteer_registrations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS volunteer_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            address TEXT NOT NULL,
            emergency_contact TEXT,
            skills TEXT,
            availability TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Insert some sample data
    sample_hazards = [
        ('Storm Surge', 'high', 12.9716, 77.5946, 0.85, 0.6, 0.3, 0.1, datetime.now(), True, 'validated'),
        ('Coastal Erosion', 'medium', 12.9116, 77.6648, 0.72, 0.4, 0.5, 0.1, datetime.now(), True, 'validated'),
        ('High Waves', 'low', 13.0827, 80.2707, 0.68, 0.7, 0.2, 0.1, datetime.now(), False, 'pending'),
    ]
    
    cursor.executemany("""
        INSERT OR IGNORE INTO hazard_events 
        (hazard_type, severity, lat, lon, confidence_score, incois_contribution, 
         citizen_contribution, social_media_contribution, timestamp, validated, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sample_hazards)
    
    # Insert sample users
    sample_users = [
        ('Admin User', 'admin@oceanguard.com', '+91-9876543210', 'Chennai, India', '+91-9876543211', 'admin', 'https://via.placeholder.com/100x100?text=Admin'),
        ('Citizen User', 'citizen@example.com', '+91-9876543212', 'Mumbai, India', '+91-9876543213', 'citizen', 'https://via.placeholder.com/100x100?text=Citizen'),
    ]
    
    cursor.executemany("""
        INSERT OR IGNORE INTO users (name, email, phone, address, emergency_contact, role, picture)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, sample_users)
    
    conn.commit()
    conn.close()
    print("Database initialized successfully!")
    print("Created tables: users, raw_reports, hazard_events, volunteer_registrations")
    print("Inserted sample data")

if __name__ == "__main__":
    init_database()