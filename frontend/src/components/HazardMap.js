import React, { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Circle, useMap } from 'react-leaflet';
import L from 'leaflet';

// Fix for default markers in react-leaflet
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// Custom hazard icons (updated for 5 main hazard types)
const createHazardIcon = (type, status, confidence = 0.5) => {
  const colors = {
    flood: '#3498db',
    tsunami: '#e74c3c', 
    tides: '#16a085',
    earthquake: '#f39c12',
    landslide: '#8e44ad',
    emergency: '#c0392b'
  };

  const icons = {
    flood: '💧',
    tsunami: '🌊',
    tides: '�',
    earthquake: '⚡',
    landslide: '⛰️',
    emergency: '🚨'
  };

  const color = colors[type] || '#7f8c8d';
  const icon = icons[type] || '⚠️';
  
  // Progressive confidence sizing and opacity
  let size = 30;
  let opacity = 0.8;
  let pulseAnimation = '';
  
  if (confidence >= 0.8) {
    size = 40; // High confidence = larger
    opacity = 1.0;
    pulseAnimation = 'animation: pulse 2s infinite;';
  } else if (confidence >= 0.5) {
    size = 35; // Medium confidence
    opacity = 0.9;
  } else {
    size = 28; // Low confidence = smaller
    opacity = 0.7;
  }
  
  if (status === 'emergency') {
    size = Math.max(size, 45);
    pulseAnimation = 'animation: pulse 1s infinite;';
    opacity = 1.0;
  }

  return L.divIcon({
    html: `
      <div style="
        background: ${color};
        color: white;
        border-radius: 50%;
        width: ${size}px;
        height: ${size}px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: ${size * 0.6}px;
        border: 3px solid white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        opacity: ${opacity};
        ${pulseAnimation}
        position: relative;
      ">
        ${icon}
        <div style="
          position: absolute;
          bottom: -8px;
          right: -8px;
          background: ${confidence >= 0.8 ? '#27ae60' : confidence >= 0.5 ? '#f39c12' : '#e74c3c'};
          color: white;
          border-radius: 50%;
          width: 16px;
          height: 16px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 10px;
          font-weight: bold;
          border: 2px solid white;
        ">
          ${Math.round(confidence * 100)}
        </div>
      </div>
      <style>
        @keyframes pulse {
          0% { transform: scale(1); opacity: ${opacity}; }
          50% { transform: scale(1.1); opacity: 1; }
          100% { transform: scale(1); opacity: ${opacity}; }
        }
      </style>
    `,
    className: 'hazard-marker',
    iconSize: [size, size],
    iconAnchor: [size/2, size/2]
  });
};

// Custom user report icon
const createUserReportIcon = (isUserSubmission = false, isProcessed = true) => {
  // Green for user's own reports, red for unprocessed citizen reports, blue for processed
  let bgColor, badgeColor, badgeIcon;
  
  if (isUserSubmission) {
    // Green for user's own reports
    bgColor = 'linear-gradient(45deg, #4CAF50, #66BB6A)';
    badgeColor = '#2E7D32';
    badgeIcon = '📍';
  } else if (!isProcessed) {
    // Red for unprocessed citizen reports
    bgColor = 'linear-gradient(45deg, #F44336, #EF5350)';
    badgeColor = '#C62828';
    badgeIcon = '⚠️';
  } else {
    // Blue for processed citizen reports
    bgColor = 'linear-gradient(45deg, #2196F3, #42A5F5)';
    badgeColor = '#1565C0';
    badgeIcon = '👤';
  }
  
  return L.divIcon({
    html: `
      <div style="
        background: ${bgColor};
        border: 3px solid white;
        border-radius: 50%;
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        position: relative;
      ">
        <i class="fas fa-user" style="color: white; font-size: 10px;"></i>
        <div style="
          position: absolute;
          top: -8px;
          right: -8px;
          background: ${badgeColor};
          color: white;
          border-radius: 50%;
          width: 16px;
          height: 16px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 8px;
          font-weight: bold;
          border: 2px solid white;
        ">${badgeIcon}</div>
      </div>
    `,
    className: 'user-report-marker',
    iconSize: [24, 24],
    iconAnchor: [12, 12]
  });
};

// Component to fit map bounds to markers
const FitBounds = ({ hazardEvents, userReports }) => {
  const map = useMap();

  useEffect(() => {
    // Use unified marker data for bounds calculation
    const unifiedMarkers = createUnifiedMarkerData(hazardEvents, userReports);
    
    // Get valid coordinates for bounds
    const validMarkers = unifiedMarkers
      .filter(marker => 
        marker.lat && marker.lon &&
        marker.lat >= -90 && marker.lat <= 90 &&
        marker.lon >= -180 && marker.lon <= 180
      )
      .map(marker => [marker.lat, marker.lon]);
    
    if (validMarkers.length > 0) {
      const bounds = L.latLngBounds(validMarkers);
      map.fitBounds(bounds, { padding: [20, 20], maxZoom: 12 });
    } else {
      // Default to Indian Ocean region if no valid markers
      map.setView([10.0, 80.0], 6);
    }
  }, [hazardEvents, userReports, map]);

  return null;
};

// Get confidence level for styling
const getConfidenceLevel = (confidence) => {
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
};

// Create unique coordinate key for grouping
const getCoordinateKey = (lat, lon, precision = 3) => {
  return `${lat.toFixed(precision)}_${lon.toFixed(precision)}`;
};

// UNIFIED DEDUPLICATION SYSTEM
// This function creates a single unified list from all data sources and eliminates ALL duplicates
const createUnifiedMarkerData = (hazardEvents, userReports) => {
  const GRID_PRECISION = 3; // 3 decimal places = ~111m precision
  const coordinateGroups = new Map();
  
  // Process hazard events first (these have priority as they're ML-processed clusters)
  hazardEvents.forEach(event => {
    if (!event.centroid_lat || !event.centroid_lon) return;
    
    const key = getCoordinateKey(event.centroid_lat, event.centroid_lon, GRID_PRECISION);
    
    if (!coordinateGroups.has(key)) {
      coordinateGroups.set(key, {
        type: 'hazard_event',
        lat: event.centroid_lat,
        lon: event.centroid_lon,
        data: event,
        priority: 10, // Highest priority
        confidence: event.confidence || 0.5
      });
    }
  });
  
  // Process user reports - only add if no hazard event exists in same grid cell
  userReports.forEach(report => {
    if (!report.lat || !report.lon) return;
    
    const key = getCoordinateKey(report.lat, report.lon, GRID_PRECISION);
    
    // If no hazard event exists in this grid cell, add the user report
    if (!coordinateGroups.has(key)) {
      coordinateGroups.set(key, {
        type: 'user_report',
        lat: report.lat,
        lon: report.lon,
        data: report,
        priority: report.isUserSubmission ? 8 : 5, // User submissions get higher priority
        confidence: report.confidence || 0.3
      });
    } else {
      // If hazard event exists, only replace if this is a very recent user submission
      if (report.isUserSubmission) {
        try {
          const reportTime = new Date(report.timestamp);
          const thirtyMinutesAgo = new Date(Date.now() - 30 * 60 * 1000);
          
          if (reportTime > thirtyMinutesAgo) { // Last 30 minutes
            console.log('Prioritizing recent user submission over hazard event');
            coordinateGroups.set(key, {
              type: 'user_report',
              lat: report.lat,
              lon: report.lon,
              data: report,
              priority: 9, // Very high priority for recent user submissions
              confidence: report.confidence || 0.3
            });
          }
        } catch (error) {
          console.error('Error parsing report timestamp:', report.timestamp, error);
        }
      }
    }
  });
  
  // Convert map to array and sort by priority
  return Array.from(coordinateGroups.values())
    .sort((a, b) => b.priority - a.priority)
    .slice(0, 50); // Limit to 50 total markers to prevent overcrowding
};

// Get time ago string with proper timezone handling
// Helper function to format UTC timestamps consistently
const formatUTCTimestamp = (timestamp) => {
  if (!timestamp) return 'Unknown time';
  
  try {
    // If timestamp doesn't contain timezone info, treat it as UTC
    let dateString = timestamp;
    if (typeof timestamp === 'string' && !timestamp.includes('T') && !timestamp.includes('Z') && !timestamp.includes('+')) {
      // SQLite format without timezone - treat as UTC
      dateString = timestamp.replace(' ', 'T') + 'Z';
    }
    
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return 'Invalid date';
    return date.toLocaleString();
  } catch (error) {
    console.error('Error formatting timestamp:', timestamp, error);
    return 'Unknown time';
  }
};

const getTimeAgo = (dateString) => {
  if (!dateString) return 'Unknown time';
  
  try {
    const now = new Date();
    
    // If timestamp doesn't contain timezone info, treat it as UTC
    let processedDateString = dateString;
    if (typeof dateString === 'string' && !dateString.includes('T') && !dateString.includes('Z') && !dateString.includes('+')) {
      // SQLite format without timezone - treat as UTC
      processedDateString = dateString.replace(' ', 'T') + 'Z';
    }
    
    const eventTime = new Date(processedDateString);
    
    // Check if date is valid
    if (isNaN(eventTime.getTime())) {
      return 'Invalid date';
    }
    
    const diffMs = now - eventTime;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return eventTime.toLocaleDateString();
  } catch (error) {
    console.error('Error parsing date:', dateString, error);
    return 'Unknown time';
  }
};

const HazardMap = ({ hazardEvents, userReports = [] }) => {
  const mapRef = useRef();

  // Chennai coordinates - center of our monitoring area
  const chennaiCenter = [13.0827, 80.2707];
  const defaultZoom = 11;

  // Create unified marker data to eliminate ALL duplicates
  const unifiedMarkers = createUnifiedMarkerData(hazardEvents, userReports);
  
  console.log(`Rendering ${unifiedMarkers.length} deduplicated markers from ${hazardEvents.length} hazard events and ${userReports.length} user reports`);

  return (
    <div className="map-container">
      <div className="map-header">
        <h2>
          <i className="fas fa-map-marked-alt"></i>
          Live Hazard Map
        </h2>
        <div className="map-stats">
          <span className="event-count">
            {unifiedMarkers.length} Unique Locations
          </span>
          <div className="legend">
            <div className="legend-item">
              <span className="legend-icon" style={{background: '#e74c3c'}}>🚨</span>
              Emergency
            </div>
            <div className="legend-item">
              <span className="legend-icon" style={{background: '#3498db'}}>💧</span>
              Flood
            </div>
            <div className="legend-item">
              <span className="legend-icon" style={{background: '#e74c3c'}}>🌊</span>
              Tsunami
            </div>
            <div className="legend-item">
              <span className="legend-icon" style={{background: '#9b59b6'}}>🌀</span>
              Cyclone
            </div>
            <div className="legend-item">
              <span className="legend-icon" style={{background: '#f39c12'}}>🏔️</span>
              Erosion
            </div>
            <div className="legend-item">
              <span className="legend-icon" style={{background: '#27ae60'}}>🏭</span>
              Pollution
            </div>
          </div>
        </div>
      </div>

      <MapContainer
        ref={mapRef}
        center={chennaiCenter}
        zoom={defaultZoom}
        style={{ height: '100%', width: '100%' }}
        className="hazard-map"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        
        {/* Satellite overlay option */}
        <TileLayer
          attribution='Tiles &copy; Esri'
          url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          opacity={0.3}
        />

        {/* Fit bounds to show all markers */}
        <FitBounds hazardEvents={hazardEvents} userReports={userReports} />

        {/* Render unified markers - NO DUPLICATES */}
        {unifiedMarkers.map((marker, index) => {
          const isHazardEvent = marker.type === 'hazard_event';
          const data = marker.data;
          
          if (isHazardEvent) {
            // Render hazard event marker
            const confidenceLevel = getConfidenceLevel(data.confidence);
            const timeAgo = getTimeAgo(data.created_at);

            return (
              <React.Fragment key={`hazard-${data.id}-${index}`}>
                {/* Main marker */}
                <Marker
                  position={[marker.lat, marker.lon]}
                  icon={createHazardIcon(data.hazard_type, data.status, data.confidence || 0.5)}
                >
                  <Popup className={`hazard-popup ${data.status}`}>
                    <div className="popup-content">
                      <div className="popup-header">
                        <h3>
                          <span className={`hazard-type ${data.hazard_type}`}>
                            {data.hazard_type.charAt(0).toUpperCase() + data.hazard_type.slice(1)}
                          </span>
                          {data.status === 'emergency' && (
                            <span className="emergency-badge">
                              <i className="fas fa-exclamation-triangle"></i>
                              EMERGENCY
                            </span>
                          )}
                        </h3>
                        <span className="event-time">{timeAgo}</span>
                      </div>

                      <div className="popup-details">
                        <div className="detail-row">
                          <span className="detail-label">Confidence Score:</span>
                          <div className="confidence-display">
                            <div className={`confidence-bar ${confidenceLevel}`}>
                              <div className="confidence-fill" style={{width: `${data.confidence * 100}%`}}></div>
                            </div>
                            <span className="confidence-text">{(data.confidence * 100).toFixed(1)}%</span>
                          </div>
                        </div>

                        <div className="detail-row">
                          <span className="detail-label">Source Contributions:</span>
                          <div className="source-breakdown">
                            <div className="source-item">
                              <span className="source-icon">👥</span>
                              <span className="source-name">Citizen Reports</span>
                              <span className="source-contribution">
                                {data.evidence_json?.source_distribution?.citizen || 0} reports
                              </span>
                            </div>
                            <div className="source-item">
                              <span className="source-icon">🌊</span>
                              <span className="source-name">INCOIS Data</span>
                              <span className="source-contribution">
                                {data.evidence_json?.source_distribution?.incois || 0} bulletins
                              </span>
                            </div>
                            <div className="source-item">
                              <span className="source-icon">📱</span>
                              <span className="source-name">Social Media</span>
                              <span className="source-contribution">
                                {data.evidence_json?.source_distribution?.social || 0} posts
                              </span>
                            </div>
                            <div className="source-item">
                              <span className="source-icon">📡</span>
                              <span className="source-name">IoT Sensors</span>
                              <span className="source-contribution">
                                {data.evidence_json?.source_distribution?.iot || 0} sensors
                              </span>
                            </div>
                          </div>
                        </div>
                        <div className="detail-row">
                          <span className="detail-label">Event ID:</span>
                          <span>#{data.id}</span>
                        </div>
                        
                        <div className="detail-row">
                          <span className="detail-label">Severity:</span>
                          <span className="severity-level">
                            {'⭐'.repeat(data.severity)} ({data.severity}/5)
                          </span>
                        </div>
                        
                        <div className="detail-row">
                          <span className="detail-label">Status:</span>
                          <span className={`status-badge ${data.status}`}>
                            {data.status.toUpperCase()}
                          </span>
                        </div>
                        
                        <div className="detail-row">
                          <span className="detail-label">Location:</span>
                          <span className="coordinates">
                            {marker.lat.toFixed(4)}°N, {marker.lon.toFixed(4)}°E
                          </span>
                        </div>
                        
                        <div className="detail-row">
                          <span className="detail-label">Last Updated:</span>
                          <span>{formatUTCTimestamp(data.updated_at)}</span>
                        </div>
                      </div>

                      <div className="popup-actions">
                        <button 
                          className="btn-details"
                          onClick={() => {
                            console.log('View details for event', data.id);
                          }}
                        >
                          <i className="fas fa-info-circle"></i>
                          View Details
                        </button>
                      </div>
                    </div>
                  </Popup>
                </Marker>

                {/* Uncertainty circle for low confidence events */}
                {data.confidence < 0.6 && (
                  <Circle
                    center={[marker.lat, marker.lon]}
                    radius={1000 * (1 - data.confidence)} // Larger circle for lower confidence
                    pathOptions={{
                      color: '#ff7f7f',
                      fillColor: '#ff7f7f',
                      fillOpacity: 0.1,
                      weight: 2,
                      dashArray: '5, 5'
                    }}
                  />
                )}
              </React.Fragment>
            );
          } else {
            // Render user report marker
            return (
              <Marker
                key={`user-report-${data.id}-${index}`}
                position={[marker.lat, marker.lon]}
                icon={createUserReportIcon(data.isUserSubmission, data.processed)}
              >
                <Popup className={data.isUserSubmission ? "user-report-popup" : "citizen-report-popup"}>
                  <div className="popup-content">
                    <div className="popup-header user-report">
                      <i className={data.isUserSubmission ? "fas fa-user-check" : "fas fa-users"}></i>
                      <span>{data.isUserSubmission ? "Your Report" : "Citizen Report"}</span>
                      <div className="user-badge">{data.user_name || "Citizen"}</div>
                    </div>
                    <div className="popup-body">
                      <div className="detail-row">
                        <strong>Submitted:</strong>
                        <span>{new Date(data.timestamp).toLocaleString()}</span>
                      </div>
                      <div className="detail-row">
                        <strong>Status:</strong>
                        <span className={`status-badge ${data.status}`}>
                          {data.status?.toUpperCase() || 'PROCESSING'}
                        </span>
                      </div>
                      <div className="detail-row">
                        <strong>Location:</strong>
                        <span className="coordinates">
                          {marker.lat.toFixed(4)}°N, {marker.lon.toFixed(4)}°E
                        </span>
                      </div>
                      {data.confidence && (
                        <div className="detail-row">
                          <strong>ML Confidence:</strong>
                          <span className="confidence-value">
                            {(data.confidence * 100).toFixed(1)}%
                          </span>
                        </div>
                      )}
                      {data.hazard_type && (
                        <div className="detail-row">
                          <strong>Detected Type:</strong>
                          <span className={`hazard-type ${data.hazard_type}`}>
                            {data.hazard_type.toUpperCase()}
                          </span>
                        </div>
                      )}
                      <div className="report-note">
                        <i className="fas fa-info-circle"></i>
                        {data.isUserSubmission ? 
                          (data.confidence ? 
                            'Your report has been processed through our ML pipeline.' :
                            'Your report is being processed through our ML pipeline and will contribute to hazard detection.'
                          ) : 
                          (data.confidence ?
                            'This citizen report has been processed through our ML pipeline.' :
                            'This citizen report is being processed through our ML pipeline.'
                          )
                        }
                      </div>
                    </div>
                  </div>
                </Popup>
              </Marker>
            );
          }
        })}
      </MapContainer>

      {/* Map overlay info */}
      <div className="map-overlay">
        <div className="live-indicator">
          <div className="pulse-dot"></div>
          LIVE
        </div>
        <div className="last-update">
          Last updated: {new Date().toLocaleTimeString()}
        </div>
      </div>

      <style jsx>{`
        .map-container {
          position: relative;
          height: 600px;
          border-radius: 15px;
          overflow: hidden;
        }

        .map-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 1rem 1.5rem;
          background: #f8f9fa;
          border-bottom: 1px solid #e9ecef;
        }

        .map-header h2 {
          color: #2c3e50;
          margin: 0;
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }

        .map-stats {
          display: flex;
          align-items: center;
          gap: 1.5rem;
        }

        .event-count {
          background: #0077be;
          color: white;
          padding: 0.5rem 1rem;
          border-radius: 20px;
          font-weight: 600;
        }

        .legend {
          display: flex;
          gap: 1rem;
          flex-wrap: wrap;
        }

        .legend-item {
          display: flex;
          align-items: center;
          gap: 0.25rem;
          font-size: 0.85rem;
        }

        .legend-icon {
          width: 20px;
          height: 20px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
        }

        .map-overlay {
          position: absolute;
          top: 1rem;
          right: 1rem;
          background: rgba(255, 255, 255, 0.9);
          padding: 0.75rem;
          border-radius: 8px;
          box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
          z-index: 1000;
        }

        .live-indicator {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          font-weight: 600;
          color: #27ae60;
        }

        .pulse-dot {
          width: 8px;
          height: 8px;
          background: #27ae60;
          border-radius: 50%;
          animation: pulse 2s infinite;
        }

        .last-update {
          font-size: 0.75rem;
          color: #7f8c8d;
          margin-top: 0.25rem;
        }

        .popup-content {
          min-width: 250px;
        }

        .popup-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 1rem;
          padding-bottom: 0.5rem;
          border-bottom: 1px solid #eee;
        }

        .popup-header h3 {
          margin: 0;
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }

        .emergency-badge {
          background: #e74c3c;
          color: white;
          padding: 0.25rem 0.5rem;
          border-radius: 12px;
          font-size: 0.7rem;
          font-weight: 600;
        }

        .event-time {
          font-size: 0.8rem;
          color: #7f8c8d;
        }

        .popup-details {
          margin-bottom: 1rem;
        }

        .detail-row {
          display: flex;
          justify-content: space-between;
          margin-bottom: 0.5rem;
          font-size: 0.9rem;
        }

        .coordinates {
          font-family: monospace;
          font-size: 0.8rem;
        }

        .status-badge {
          padding: 0.25rem 0.5rem;
          border-radius: 12px;
          font-size: 0.7rem;
          font-weight: 600;
        }

        .status-badge.emergency {
          background: #e74c3c;
          color: white;
        }

        .status-badge.review {
          background: #f39c12;
          color: white;
        }

        .status-badge.resolved {
          background: #27ae60;
          color: white;
        }

        .severity-level {
          font-size: 0.9rem;
        }

        .confidence-display {
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }

        .confidence-bar {
          width: 60px;
          height: 8px;
          background: #e9ecef;
          border-radius: 4px;
          position: relative;
          overflow: hidden;
        }

        .confidence-fill {
          height: 100%;
          border-radius: 4px;
          transition: width 0.3s ease;
        }

        .confidence-bar.high .confidence-fill {
          background: linear-gradient(to right, #27ae60, #2ecc71);
        }

        .confidence-bar.medium .confidence-fill {
          background: linear-gradient(to right, #f39c12, #e67e22);
        }

        .confidence-bar.low .confidence-fill {
          background: linear-gradient(to right, #e74c3c, #c0392b);
        }

        .confidence-text {
          font-size: 0.8rem;
          font-weight: 600;
          min-width: 35px;
        }

        .source-breakdown {
          margin-top: 0.5rem;
        }

        .source-item {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          margin-bottom: 0.25rem;
          font-size: 0.8rem;
        }

        .source-icon {
          width: 20px;
          text-align: center;
        }

        .source-name {
          flex: 1;
          font-weight: 500;
        }

        .source-contribution {
          color: #7f8c8d;
          font-size: 0.75rem;
        }

        .popup-actions {
          text-align: center;
        }

        .btn-details {
          background: #0077be;
          color: white;
          border: none;
          padding: 0.5rem 1rem;
          border-radius: 20px;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 0.5rem;
          font-size: 0.85rem;
        }

        .btn-details:hover {
          background: #005a8b;
        }

        @keyframes pulse {
          0% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.1); }
          100% { opacity: 1; transform: scale(1); }
        }

        @media (max-width: 768px) {
          .map-header {
            flex-direction: column;
            gap: 1rem;
          }

          .legend {
            justify-content: center;
          }

          .map-overlay {
            top: 0.5rem;
            right: 0.5rem;
            font-size: 0.8rem;
          }
        }

        /* User Report Styles */
        .user-report-popup .leaflet-popup-content-wrapper {
          background: linear-gradient(135deg, #4CAF50, #66BB6A);
          color: white;
          border-radius: 12px;
        }

        .user-report-popup .leaflet-popup-tip {
          background: #4CAF50;
        }

        /* Citizen Report Styles */
        .citizen-report-popup .leaflet-popup-content-wrapper {
          background: linear-gradient(135deg, #2196F3, #42A5F5);
          color: white;
          border-radius: 12px;
        }

        .citizen-report-popup .leaflet-popup-tip {
          background: #2196F3;
        }

        .popup-header.user-report {
          background: rgba(255, 255, 255, 0.2);
          color: white;
        }

        .user-badge {
          background: rgba(255, 255, 255, 0.3);
          color: white;
          padding: 0.25rem 0.5rem;
          border-radius: 12px;
          font-size: 0.8rem;
          font-weight: 600;
        }

        .report-note {
          background: rgba(255, 255, 255, 0.1);
          padding: 0.75rem;
          border-radius: 8px;
          margin-top: 0.5rem;
          font-size: 0.85rem;
          display: flex;
          align-items: flex-start;
          gap: 0.5rem;
        }

        .report-note i {
          margin-top: 0.1rem;
          opacity: 0.8;
        }
      `}</style>
    </div>
  );
};

export default HazardMap;