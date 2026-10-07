// Map rendering helpers (MapLibre GL JS)
// Coherent Control Room Theme with CARTO basemaps and custom SVG/Canvas icons
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

export const MAP_CENTER = [80.22, 13.05] // Chennai [lon, lat]
export const MAP_ZOOM = 10.6

// Unified truck colors matching CSS tokens:
export const TRUCK_COLORS = [
  '#2563eb', // TRK-01 Blue
  '#ec4899', // TRK-02 Pink
  '#8b5cf6', // TRK-03 Purple
  '#f97316', // TRK-04 Orange
  '#10b981', // TRK-05 Emerald
  '#06b6d4', // TRK-06 Cyan
  '#eab308', // Reserve Gold
  '#64748b', // Spare Slate
]

// OpenStreetMap open-source raster tiles & attribution
export const OSM_TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

export function createMap(container, options = {}, theme = 'dark') {
  const isDark = theme === 'dark'

  const map = new maplibregl.Map({
    container,
    style: {
      version: 8,
      sources: {
        osm: {
          type: 'raster',
          tiles: [OS_TILES_URL],
          tileSize: 256,
          attribution: OSM_ATTRIBUTION,
        },
      },
      layers: [
        {
          id: 'osm-layer',
          type: 'raster',
          source: 'osm',
          paint: {
            'raster-saturation': 0.12,
            'raster-contrast': 0.08,
            'raster-brightness-max': 1.0,
            'raster-brightness-min': 0.0,
          },
        },
      ],
    },
    center: MAP_CENTER,
    zoom: MAP_ZOOM,
    ...options,
  })

  map.on('load', () => registerPinIcons(map))
  return map
}

const OS_TILES_URL = OSM_TILES

export function updateMapBasemap(map, theme = 'dark') {
  if (!map || !map.getLayer('osm-layer')) return
  // Maintain vibrant clarity in both themes
  map.setPaintProperty('osm-layer', 'raster-saturation', 0.12)
  map.setPaintProperty('osm-layer', 'raster-contrast', 0.08)
  map.setPaintProperty('osm-layer', 'raster-brightness-max', 1.0)
  map.setPaintProperty('osm-layer', 'raster-brightness-min', 0.0)
}



export const toLngLat = (p) => [p.lon, p.lat]
export const roadLngLat = (place) => [place.road_lon, place.road_lat]

export function upsertLayer(map, id, data, layer, beforeId) {
  const source = map.getSource(id)
  if (source) {
    source.setData(data)
  } else {
    map.addSource(id, { type: 'geojson', data })
    map.addLayer({ id, source: id, ...layer }, beforeId && map.getLayer(beforeId) ? beforeId : undefined)
  }
}

export function removeStale(map, prefix, keep) {
  for (const layer of map.getStyle()?.layers || []) {
    if (layer.id.startsWith(prefix) && !keep.has(layer.id)) {
      map.removeLayer(layer.id)
      if (map.getSource(layer.id)) map.removeSource(layer.id)
    }
  }
}

export const line = (coords) => ({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords } })
export const featureCollection = (features) => ({ type: 'FeatureCollection', features })
export const point = (lngLat, properties = {}) => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: lngLat }, properties,
})

export function circlePolygon(lat, lon, radiusKm, sides = 64) {
  const points = []
  for (let i = 0; i <= sides; i++) {
    const a = (i / sides) * 2 * Math.PI
    points.push([
      lon + (radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(a),
      lat + (radiusKm / 110.57) * Math.cos(a),
    ])
  }
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [points] } }
}

export function routeSegmentAt(map, trucks, pt, maxPixels = 16) {
  let best = null
  for (const truck of trucks) {
    const route = truck.route || []
    for (let i = 0; i < route.length - 1; i++) {
      const a = map.project(toLngLat(route[i]))
      const b = map.project(toLngLat(route[i + 1]))
      const d = pixelDistanceToSegment(pt, a, b)
      if (!best || d < best.distance) best = { truckId: truck.id, segmentIndex: i, distance: d }
    }
  }
  return best && best.distance <= maxPixels ? best : null
}

function pixelDistanceToSegment(p, a, b) {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const lenSq = dx * dx + dy * dy
  const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq))
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
}

// --- Dynamic Canvas Pin Generation (NO EMOJIS) ---
export const PIN_COLORS = {
  Low: '#10b981',
  Medium: '#f59e0b',
  High: '#f97316',
  Late: '#ef4444',
  unreachable: '#dc2626',
  done: '#059669',
  none: '#64748b',
  plant: '#0284c7',
  preview: '#8b5cf6',
}

export function hubIcon(hub) {
  if (hub.status === 'UNREACHABLE') return 'pin-unreachable-critical'
  if (hub.status === 'COMPLETED') return 'pin-done'
  const risk = hub.risk || 'none'
  return `pin-${risk}${hub.priority === 'Critical' ? '-critical' : ''}`
}

const PIN_W = 30
const PIN_H = 42
const RATIO = 2

// Teardrop pin drawing with milk churn glyph, progress ring, complete check, or unreachable slash
function drawPin(color, { glyph = 'churn', ringColor = null, slash = false, progressPct = 0 } = {}) {
  const w = PIN_W * RATIO
  const h = PIN_H * RATIO
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  ctx.scale(RATIO, RATIO)

  const cx = PIN_W / 2
  const r = PIN_W / 2 - 3

  // Main teardrop path
  ctx.beginPath()
  ctx.moveTo(cx, PIN_H)
  ctx.bezierCurveTo(cx - 5, PIN_H - 10, 2, r + 12, 2, r + 3)
  ctx.arc(cx, r + 3, r, Math.PI, 0)
  ctx.bezierCurveTo(PIN_W - 2, r + 12, cx + 5, PIN_H - 10, cx, PIN_H)
  ctx.closePath()

  // Base fill
  ctx.fillStyle = color
  ctx.fill()

  // Outer border or ring
  ctx.lineWidth = ringColor ? 2.5 : 1.5
  ctx.strokeStyle = ringColor || '#ffffff'
  ctx.stroke()

  // Delivery progress ring if provided
  if (progressPct > 0 && progressPct < 100) {
    ctx.beginPath()
    const startAngle = -Math.PI / 2
    const endAngle = startAngle + (2 * Math.PI * (progressPct / 100))
    ctx.arc(cx, r + 3, r + 1.5, startAngle, endAngle)
    ctx.lineWidth = 3
    ctx.strokeStyle = '#38bdf8'
    ctx.stroke()
  }

  // Draw internal glyph
  ctx.fillStyle = '#ffffff'
  ctx.strokeStyle = '#ffffff'

  if (glyph === 'plant') {
    // Factory building + milk drop
    ctx.fillRect(cx - 7, r - 3, 14, 10)
    ctx.fillRect(cx - 7, r - 8, 4, 5)
    ctx.fillRect(cx - 1, r - 7, 3, 4)
    // Milk drop
    ctx.beginPath()
    ctx.arc(cx + 2, r + 2, 2.5, 0, Math.PI * 2)
    ctx.fill()
  } else if (glyph === 'check') {
    // Checkmark
    ctx.lineWidth = 2.5
    ctx.beginPath()
    ctx.moveTo(cx - 5, r + 3)
    ctx.lineTo(cx - 1, r + 7)
    ctx.lineTo(cx + 6, r - 2)
    ctx.stroke()
  } else {
    // Milk Churn / Can Glyph
    ctx.lineWidth = 1.5
    // Lid
    ctx.beginPath()
    ctx.roundRect(cx - 3.5, r - 6, 7, 2, 1)
    ctx.fill()
    // Neck
    ctx.fillRect(cx - 2.5, r - 4, 5, 2.5)
    // Body
    ctx.beginPath()
    ctx.roundRect(cx - 5.5, r - 1.5, 11, 9, 2)
    ctx.fill()
    // Handles
    ctx.beginPath()
    ctx.arc(cx - 5.5, r + 2, 2, Math.PI / 2, (3 * Math.PI) / 2)
    ctx.stroke()
    ctx.beginPath()
    ctx.arc(cx + 5.5, r + 2, 2, -Math.PI / 2, Math.PI / 2)
    ctx.stroke()
  }

  // Red slash if UNREACHABLE
  if (slash) {
    ctx.lineWidth = 3
    ctx.strokeStyle = '#dc2626'
    ctx.beginPath()
    ctx.moveTo(cx - 7, r - 4)
    ctx.lineTo(cx + 7, r + 10)
    ctx.stroke()
  }

  return ctx.getImageData(0, 0, w, h)
}

function drawDisruptionBadge(type) {
  const size = 28 * RATIO
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  ctx.scale(RATIO, RATIO)
  const cx = 14
  const cy = 14
  const r = 12

  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  if (type === 'rain') ctx.fillStyle = '#0284c7'
  else if (type === 'traffic') ctx.fillStyle = '#ea580c'
  else ctx.fillStyle = '#dc2626'
  ctx.fill()
  ctx.lineWidth = 2
  ctx.strokeStyle = '#ffffff'
  ctx.stroke()

  ctx.fillStyle = '#ffffff'
  ctx.strokeStyle = '#ffffff'
  ctx.lineWidth = 2

  if (type === 'rain') {
    // Cloud
    ctx.beginPath()
    ctx.arc(cx - 3, cy - 2, 4, Math.PI * 0.5, Math.PI * 1.5)
    ctx.arc(cx + 1, cy - 4, 5, Math.PI, Math.PI * 2)
    ctx.arc(cx + 4, cy - 2, 3, Math.PI * 1.5, Math.PI * 0.5)
    ctx.closePath()
    ctx.fill()
    // Rain lines
    ctx.beginPath()
    ctx.moveTo(cx - 3, cy + 4); ctx.lineTo(cx - 4, cy + 7)
    ctx.moveTo(cx + 1, cy + 4); ctx.lineTo(cx, cy + 7)
    ctx.moveTo(cx + 5, cy + 4); ctx.lineTo(cx + 4, cy + 7)
    ctx.stroke()
  } else if (type === 'traffic') {
    // Traffic 3 vertical dots
    ctx.beginPath(); ctx.arc(cx, cy - 5, 2, 0, Math.PI * 2); ctx.fill()
    ctx.beginPath(); ctx.arc(cx, cy, 2, 0, Math.PI * 2); ctx.fill()
    ctx.beginPath(); ctx.arc(cx, cy + 5, 2, 0, Math.PI * 2); ctx.fill()
  } else {
    // Roadblock / barrier white rectangle
    ctx.beginPath()
    ctx.roundRect(cx - 7, cy - 2.5, 14, 5, 1)
    ctx.fill()
  }
  return ctx.getImageData(0, 0, size, size)
}

export function registerPinIcons(map) {
  const add = (name, img) => {
    if (!map.hasImage(name)) map.addImage(name, img, { pixelRatio: RATIO })
  }

  // Register hub pins
  for (const [key, color] of Object.entries(PIN_COLORS)) {
    if (key === 'plant') continue
    const isDone = key === 'done'
    const isUnreachable = key === 'unreachable'

    add(`pin-${key}`, drawPin(color, {
      glyph: isDone ? 'check' : 'churn',
      slash: isUnreachable,
    }))
    add(`pin-${key}-critical`, drawPin(color, {
      glyph: isDone ? 'check' : 'churn',
      ringColor: '#dc2626',
      slash: isUnreachable,
    }))
  }

  // Plant icon
  add('pin-plant', drawPin(PIN_COLORS.plant, { glyph: 'plant' }))
  add('pin-preview', drawPin(PIN_COLORS.preview, { glyph: 'churn', ringColor: '#a855f7' }))

  // Disruption badges
  add('icon-rain', drawDisruptionBadge('rain'))
  add('icon-traffic', drawDisruptionBadge('traffic'))
  add('icon-roadblock', drawDisruptionBadge('roadblock'))
  add('icon-area', drawDisruptionBadge('area'))
}


export const PIN_LAYOUT = {
  'icon-anchor': 'bottom',
  'icon-allow-overlap': true,
  'icon-ignore-placement': true,
}

// --- DOM Truck Markers (NO EMOJIS) ---
// Round badge in truck's color with milk-tanker glyph and rotating heading arrow
export function createTruckElement(color = '#2563eb') {
  const root = document.createElement('div')
  root.className = 'df-truck-marker'
  root.style.width = '36px'
  root.style.height = '36px'
  root.style.pointerEvents = 'none'

  root.innerHTML = `
    <div class="df-truck-badge" style="
      width: 32px;
      height: 32px;
      margin: 2px;
      background-color: #ffffff;
      border-radius: 50%;
      border: 3px solid ${color};
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 8px rgba(0,0,0,0.45);
      position: relative;
    ">
      <!-- Heading Arrow Indicator -->
      <div class="df-truck-arrow" style="
        position: absolute;
        top: -6px;
        left: 50%;
        transform: translateX(-50%);
        width: 0;
        height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-bottom: 6px solid ${color};
      "></div>

      <!-- Milk Tanker SVG Glyph -->
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="1" y="8" width="12" height="7" rx="2"></rect>
        <path d="M13 10h4l3 3v2h-7v-5z"></path>
        <circle cx="5" cy="17" r="2" fill="${color}"></circle>
        <circle cx="16" cy="17" r="2" fill="${color}"></circle>
      </svg>
    </div>
  `
  return root
}

export const createArrowElement = createTruckElement
