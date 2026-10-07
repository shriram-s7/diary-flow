import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import { clock, deliveryText, litres, pendingStops, statusLabel } from '../format'
import {
  PIN_LAYOUT, TRUCK_COLORS, circlePolygon, createMap, updateMapBasemap, createTruckElement,
  featureCollection, hubIcon, line, point, removeStale, roadLngLat, routeSegmentAt, toLngLat, upsertLayer,
} from '../map/mapUtils'
import TruckAnimator from '../map/TruckAnimator'
import { routeGeometry } from '../map/routeCache'
import { Icon } from './Icons'
import { Button, Slider } from './ui'

const PARKED = new Set(['RESERVE', 'AT_PLANT', 'LOADING'])
const DRIVING = new Set(['EN_ROUTE', 'RETURNING'])
const PIN_LAYERS = ['hubs', 'plant']

export default function MapView({
  world,
  theme = 'dark',
  clickMode,
  disruptionParams = { rainRadiusKm: 3, areaRadiusM: 500 },
  onDisruptionParamChange,
  onSelectClickMode,
  onCancelClickMode,
  preview,
  onAddHubAt,
  onRainAt,
  onTrafficAt,
  onRoadblockAt,
  hubActions,
  followTruckId,
  motionRef,
  motionConfig,
}) {
  const container = useRef(null)
  const map = useRef(null)
  const ready = useRef(false)
  const markers = useRef({})
  const animators = useRef({})
  const popup = useRef(null)
  const latest = useRef({})
  const overlays = useRef({})   // per truck: drawn route id, gradient key, reroute animation state
  const camera = useRef({ holdUntil: 0 })

  latest.current = {
    world,
    clickMode,
    onAddHubAt,
    onRainAt,
    onTrafficAt,
    onRoadblockAt,
    hubActions,
    motionRef,
    motionConfig,
    followTruckId,
  }

  // --- Map Init & Style ---
  useEffect(() => {
    const m = createMap(container.current, {}, theme)
    map.current = m
    if (import.meta.env.DEV) {
      window.__dairyflow = { map: m, markers: markers.current, truckMarkers: markers.current, animators: animators.current, motion: newMotionStats() }
      window.__dairyflow.resetMotion = () => { window.__dairyflow.motion = newMotionStats() }
    }

    m.on('load', () => {
      upsertLayer(m, 'plant', featureCollection([]), { type: 'symbol', layout: { ...PIN_LAYOUT, 'icon-image': 'pin-plant' } })
      upsertLayer(m, 'hubs', featureCollection([]), { type: 'symbol', layout: { ...PIN_LAYOUT, 'icon-image': ['get', 'icon'] } })
      upsertLayer(m, 'snap-preview', featureCollection([]), { type: 'symbol', layout: { ...PIN_LAYOUT, 'icon-image': 'pin-preview' } })
      ready.current = true
      if (latest.current.world) drawStatic(m, latest.current.world)
    })

    m.on('click', 'hubs', (e) => openPopup(m, 'hub', e.features[0].properties.id))
    m.on('click', 'plant', () => openPopup(m, 'plant', 'plant'))

    for (const layer of PIN_LAYERS) {
      m.on('mouseenter', layer, () => { m.getCanvas().style.cursor = 'pointer' })
      m.on('mouseleave', layer, () => { m.getCanvas().style.cursor = latest.current.clickMode ? 'crosshair' : '' })
    }

    m.on('click', (e) => {
      if (m.queryRenderedFeatures(e.point, { layers: PIN_LAYERS }).length) return
      const { world: w, clickMode: mode } = latest.current
      if (mode === 'add-hub') latest.current.onAddHubAt?.(e.lngLat.lat, e.lngLat.lng)
      else if (mode === 'rain') latest.current.onRainAt?.(e.lngLat.lat, e.lngLat.lng)
      else if (mode === 'traffic') latest.current.onTrafficAt?.(e.lngLat.lat, e.lngLat.lng, routeSegmentAt(m, w?.trucks || [], e.point))
      else if (mode === 'roadblock') latest.current.onRoadblockAt?.(e.lngLat.lat, e.lngLat.lng, 150)
      else if (mode === 'area') latest.current.onRoadblockAt?.(e.lngLat.lat, e.lngLat.lng, disruptionParams.areaRadiusM || 500)
    })

    // --- Animation loop: every frame advances each truck along its route (see TruckAnimator) ---
    let raf = null
    let lastMs = null
    let lastSeq = null
    function frame(nowMs) {
      raf = requestAnimationFrame(frame)
      // A stalled frame advances at most 100 ms; the next server message eases in the rest.
      const frameMs = lastMs === null ? 0 : nowMs - lastMs
      const dtMs = Math.min(frameMs, 100)
      lastMs = nowMs
      const msg = latest.current.motionRef?.current
      if (!ready.current || !msg) return
      const cfg = latest.current.motionConfig || {}
      const live = msg.phase === 'EXECUTING' && msg.running && !msg.paused
      const fresh = msg.seq !== lastSeq
      lastSeq = msg.seq

      msg.trucks.forEach((t, i) => {
        let anim = animators.current[t.id]
        if (!anim) anim = animators.current[t.id] = new TruckAnimator(t.id, cfg)
        const route = routeGeometry(t.route_id)
        // Apply each message once, and again when a route geometry it names has just loaded.
        const geometryArrived = route && anim.route?.id !== route.id && DRIVING.has(t.status)
        if (fresh || geometryArrived) {
          const ageS = msg.latencyS + (nowMs - msg.receivedAt) / 1000
          const reroute = anim.onMessage(t, route, live ? ageS : 0, msg.sim_speed)
          if (reroute) startReroute(m, t.id, reroute.oldRemaining, anim, cfg)
        }
        anim.frame(dtMs, msg.sim_speed, live)
        drawTruck(m, t, i, anim, frameMs)
        drawRoute(m, t.id, i, anim, cfg, nowMs)
      })
      for (const [id, marker] of Object.entries(markers.current)) {
        if (!msg.trucks.some((t) => t.id === id)) { marker.remove(); delete markers.current[id] }
      }
      followCamera(m, nowMs)
    }
    raf = requestAnimationFrame(frame)

    function drawTruck(map, t, i, anim, frameMs) {
      let marker = markers.current[t.id]
      if (PARKED.has(t.status)) {
        if (marker) { marker.remove(); delete markers.current[t.id] }
        return
      }
      const created = !marker
      if (created) {
        marker = new maplibregl.Marker({ element: createTruckElement(TRUCK_COLORS[i % TRUCK_COLORS.length]), anchor: 'center' })
          .setLngLat(anim.pos).addTo(map)
        markers.current[t.id] = marker
      }
      marker.setLngLat(anim.pos)
      const badge = marker.getElement().querySelector('.df-truck-badge')
      if (badge && anim.heading !== null) badge.style.transform = `rotate(${anim.heading}deg)`
      marker.getElement().title = `${t.id} · ${statusLabel(t.status)}${t.approximate ? ' · approximate route' : ''}`
      if (import.meta.env.DEV && window.__dairyflow?.motion && !created) recordJump(window.__dairyflow.motion, t.id, anim.lastJumpM, frameMs, t.status)
    }

    // The leg's travelled part faded and the rest solid: one line coloured by line-progress. After a reroute
    // the new route reveals itself from the truck forwards over REROUTE_DRAW_MS.
    function drawRoute(map, truckId, i, anim, cfg, nowMs) {
      const o = overlays.current[truckId] || (overlays.current[truckId] = { routeId: null, key: null })
      const id = `route-${truckId}`
      const route = anim.driving ? anim.route : null
      if (!route) {
        if (o.routeId !== null) {
          map.getSource(id)?.setData(featureCollection([]))
          o.routeId = null
          o.key = null
        }
        drawRerouteFade(map, truckId, o, cfg, nowMs)
        return
      }
      const color = TRUCK_COLORS[i % TRUCK_COLORS.length]
      if (!map.getSource(id)) {
        map.addSource(id, { type: 'geojson', data: featureCollection([]), lineMetrics: true })
        map.addLayer({ id, type: 'line', source: id, layout: { 'line-cap': 'round', 'line-join': 'round' },
                       paint: { 'line-width': 4.5, 'line-gradient': routeGradient(color, 0, 1) } }, beforePins(map))
        // Approximate (straight-line) legs get a white dashed centre line.
        map.addLayer({ id: `${id}-approx`, type: 'line', source: id,
                       paint: { 'line-color': '#ffffff', 'line-width': 1.5, 'line-opacity': 0, 'line-dasharray': [2, 2] } }, beforePins(map))
      }
      if (o.routeId !== route.id) {
        map.getSource(id).setData(line(route.coordinates))
        map.setPaintProperty(`${id}-approx`, 'line-opacity', route.approximate ? 0.9 : 0)
        o.routeId = route.id
        o.key = null
      }
      const travelled = anim.travelled
      let reveal = 1
      if (o.drawStart != null) {
        const k = Math.min(1, (nowMs - o.drawStart) / (cfg.REROUTE_DRAW_MS ?? 1500))
        reveal = travelled + (1 - travelled) * (1 - (1 - k) ** 3)
        if (k >= 1) o.drawStart = null
      }
      const key = `${travelled.toFixed(4)}|${reveal.toFixed(4)}`
      if (key !== o.key) {
        map.setPaintProperty(id, 'line-gradient', routeGradient(color, travelled, reveal))
        o.key = key
      }
      drawRerouteFade(map, truckId, o, cfg, nowMs)
    }

    function startReroute(map, truckId, oldRemaining, anim, cfg) {
      const o = overlays.current[truckId] || (overlays.current[truckId] = { routeId: null, key: null })
      const now = performance.now()
      o.drawStart = now
      if (oldRemaining.length >= 2) {
        o.fadeStart = now
        const id = `reroute-old-${truckId}`
        if (map.getSource(id)) map.getSource(id).setData(line(oldRemaining))
        else {
          map.addSource(id, { type: 'geojson', data: line(oldRemaining) })
          map.addLayer({ id, type: 'line', source: id,
                         paint: { 'line-color': '#ef4444', 'line-width': 3.5, 'line-opacity': 1, 'line-dasharray': [2, 1.5] } }, beforePins(map))
        }
      }
      if (latest.current.followTruckId === truckId && anim.route) {
        const coords = anim.remaining()
        const bounds = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]))
        map.fitBounds(bounds, { padding: 80, duration: 800, maxZoom: 15 })
        camera.current.holdUntil = now + (cfg.REROUTE_DRAW_MS ?? 1500)
      }
    }

    function drawRerouteFade(map, truckId, o, cfg, nowMs) {
      if (o.fadeStart == null) return
      const id = `reroute-old-${truckId}`
      const k = (nowMs - o.fadeStart) / (cfg.REROUTE_FADE_MS ?? 1000)
      if (k >= 1) {
        if (map.getLayer(id)) map.removeLayer(id)
        if (map.getSource(id)) map.removeSource(id)
        o.fadeStart = null
      } else if (map.getLayer(id)) {
        map.setPaintProperty(id, 'line-opacity', 1 - k)
      }
    }

    // A followed truck stays in view: re-centre once it nears the edge (not while a reroute fit is showing).
    function followCamera(map, nowMs) {
      const id = latest.current.followTruckId
      const anim = id && animators.current[id]
      if (!anim?.pos || nowMs < camera.current.holdUntil || map.isMoving()) return
      const p = map.project(anim.pos)
      const { clientWidth: w, clientHeight: h } = map.getContainer()
      if (p.x < w * 0.2 || p.x > w * 0.8 || p.y < h * 0.2 || p.y > h * 0.8) {
        map.easeTo({ center: anim.pos, duration: 600 })
      }
    }

    return () => {
      ready.current = false
      cancelAnimationFrame(raf)
      if (import.meta.env.DEV) delete window.__dairyflow
      m.remove()
      map.current = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Handle Theme Switching
  useEffect(() => {
    if (map.current && ready.current) {
      updateMapBasemap(map.current, theme)
    }
  }, [theme])

  // Handle Esc Key to Cancel Placing Mode
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape' && clickMode) {
        onCancelClickMode?.()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [clickMode, onCancelClickMode])

  useEffect(() => {
    if (map.current) map.current.getCanvas().style.cursor = clickMode ? 'crosshair' : ''
  }, [clickMode])

  useEffect(() => {
    if (map.current && ready.current && world) {
      drawStatic(map.current, world)
      fillPopup()
    }
  }, [world])

  useEffect(() => {
    const source = ready.current && map.current?.getSource('snap-preview')
    if (source) source.setData(featureCollection(preview ? [point([preview.road_lon, preview.road_lat])] : []))
  }, [preview])

  useEffect(() => {
    const anim = followTruckId && animators.current[followTruckId]
    if (map.current && anim?.pos) map.current.easeTo({ center: anim.pos, zoom: Math.max(map.current.getZoom(), 13), duration: 600 })
  }, [followTruckId])

  function openPopup(m, kind, id) {
    popup.current?.popup.remove()
    const p = new maplibregl.Popup({ closeButton: true, maxWidth: '340px', offset: [0, -38] })
    popup.current = { popup: p, kind, id }
    p.on('close', () => { if (popup.current?.popup === p) popup.current = null })
    fillPopup()
    p.addTo(m)
  }

  function fillPopup() {
    const open = popup.current
    const w = latest.current.world
    if (!open || !w) return
    if (open.kind === 'plant') {
      open.popup.setLngLat(roadLngLat(w.plant)).setDOMContent(plantPopup(w))
      return
    }
    const hub = w.hubs.find((h) => h.id === open.id)
    if (!hub) { open.popup.remove(); return }
    open.popup.setLngLat(roadLngLat(hub)).setDOMContent(hubPopup(hub, latest.current.hubActions?.(hub) || []))
  }

  return (
    <div className="df-map-area">
      <div ref={container} className="df-map-canvas" />

      {/* Docked Disruption Toolbar at Top Edge of Map Area */}
      {world?.phase === 'EXECUTING' && (
        <div className="df-disruption-toolbar">
          <Button
            variant={clickMode === 'rain' ? 'primary' : 'ghost'}
            size="sm"
            icon="rain"
            onClick={() => onSelectClickMode?.(clickMode === 'rain' ? null : 'rain')}
          >
            Rain zone
          </Button>
          <Button
            variant={clickMode === 'traffic' ? 'primary' : 'ghost'}
            size="sm"
            icon="traffic"
            onClick={() => onSelectClickMode?.(clickMode === 'traffic' ? null : 'traffic')}
          >
            Traffic jam
          </Button>
          <Button
            variant={clickMode === 'roadblock' ? 'primary' : 'ghost'}
            size="sm"
            icon="roadblock"
            onClick={() => onSelectClickMode?.(clickMode === 'roadblock' ? null : 'roadblock')}
          >
            Roadblock
          </Button>
          <Button
            variant={clickMode === 'area' ? 'primary' : 'ghost'}
            size="sm"
            icon="area-closure"
            onClick={() => onSelectClickMode?.(clickMode === 'area' ? null : 'area')}
          >
            Area closure
          </Button>
        </div>
      )}

      {/* Clear Active Placing State Bar */}
      {clickMode && (
        <div className={`df-disruption-status-bar ${clickMode === 'roadblock' || clickMode === 'area' ? 'warning' : ''}`}>
          <Icon name={clickMode === 'add-hub' ? 'map-pin' : clickMode === 'area' ? 'area-closure' : clickMode} size={16} />
          <span>
            {clickMode === 'add-hub' && 'Click anywhere to place a new delivery hub'}
            {clickMode === 'rain' && `Placing Rain Zone (${disruptionParams.rainRadiusKm} km radius) — Click map`}
            {clickMode === 'traffic' && 'Placing Traffic Jam (~1 km stretch) — Click any road'}
            {clickMode === 'roadblock' && 'Placing Roadblock (150 m closure) — Click road to close'}
            {clickMode === 'area' && `Placing Area Closure (${disruptionParams.areaRadiusM} m radius) — Click map`}
          </span>

          {clickMode === 'rain' && (
            <div className="df-slider-compact">
              <Slider
                min={1}
                max={8}
                step={0.5}
                value={disruptionParams.rainRadiusKm}
                onChange={(v) => onDisruptionParamChange?.('rainRadiusKm', v)}
              />
            </div>
          )}

          {clickMode === 'area' && (
            <div className="df-slider-compact">
              <Slider
                min={200}
                max={2000}
                step={100}
                value={disruptionParams.areaRadiusM}
                onChange={(v) => onDisruptionParamChange?.('areaRadiusM', v)}
              />
            </div>
          )}

          <Button
            variant="secondary"
            size="sm"
            onClick={onCancelClickMode}
            className="df-ml-1-5"
          >
            Cancel (Esc)
          </Button>
        </div>
      )}

      {/* Map Navigation Controls */}
      <div className="df-map-controls">
        <Button
          variant="secondary"
          size="sm"
          iconOnly
          title="Zoom In"
          onClick={() => map.current?.zoomIn()}
        >
          +
        </Button>
        <Button
          variant="secondary"
          size="sm"
          iconOnly
          title="Zoom Out"
          onClick={() => map.current?.zoomOut()}
        >
          −
        </Button>
        <Button
          variant="secondary"
          size="sm"
          iconOnly
          icon="map-pin"
          title="Reset to Chennai"
          onClick={() => map.current?.easeTo({ center: [80.22, 13.05], zoom: 10.6, duration: 600 })}
        />
      </div>

      {/* Map Legend inside map area */}
      <div className="df-map-legend">
        <div className="df-map-legend-header">
          <Icon name="layers" size={14} />
          <span>Map Legend</span>
        </div>
        <div className="df-legend-item">
          <Icon name="plant" size={14} color="var(--color-accent)" />
          <span><strong>Plant</strong> (Madhavaram)</span>
        </div>
        <div className="df-legend-item">
          <Icon name="hub" size={14} color="var(--color-success)" />
          <span><strong>Hub</strong> (Milk Churn, % Ring)</span>
        </div>
        <div className="df-legend-item">
          <Icon name="truck" size={14} color="var(--truck-color-0)" />
          <span><strong>Truck</strong> (Route Heading)</span>
        </div>
        <div className="df-legend-item">
          <Icon name="rain" size={14} color="var(--disruption-rain)" />
          <span><strong>Rain</strong> (Speed &times; 0.6)</span>
        </div>
        <div className="df-legend-item">
          <Icon name="traffic" size={14} color="var(--disruption-traffic)" />
          <span><strong>Traffic</strong> (Speed &times; 0.4)</span>
        </div>
        <div className="df-legend-item">
          <Icon name="roadblock" size={14} color="var(--disruption-roadblock)" />
          <span><strong>Roadblock</strong> (Impassable)</span>
        </div>
        <div className="df-legend-item">
          <Icon name="area-closure" size={14} color="var(--disruption-area)" />
          <span><strong>Area Closure</strong> (No Access)</span>
        </div>
      </div>
    </div>
  )
}

// --- Static Draws (Hubs, Plant, Plan Lines, Impacts) ---
function drawStatic(m, w) {
  if (import.meta.env.DEV && window.__dairyflow) window.__dairyflow.world = w
  m.getSource('plant')?.setData(featureCollection([point(roadLngLat(w.plant), { id: 'plant' })]))
  m.getSource('hubs')?.setData(featureCollection(w.hubs.map((h) => point(roadLngLat(h), { id: h.id, icon: hubIcon(h) }))))
  syncPlanLines(m, w)
  syncImpacts(m, w)
}

function syncPlanLines(m, w) {
  const keep = new Set()
  const hubsById = Object.fromEntries(w.hubs.map((h) => [h.id, h]))
  w.trucks.forEach((truck, i) => {
    const color = TRUCK_COLORS[i % TRUCK_COLORS.length]
    const remaining = pendingStops(truck).filter((s) => s.arrived_at == null).map((s) => hubsById[s.hub_id]).filter(Boolean)
    if (remaining.length) {
      const start = truck.route?.length > 1 ? roadLngLat(remaining[0]) : toLngLat(truck.position)
      const rest = (truck.route?.length > 1 ? remaining.slice(1) : remaining).map(roadLngLat)
      const id = `plan-${truck.id}`
      keep.add(id)
      upsertLayer(m, id, line([start, ...rest, roadLngLat(w.plant)]), {
        type: 'line', paint: { 'line-color': color, 'line-width': 2.5, 'line-opacity': 0.75, 'line-dasharray': [3, 2] },
      }, 'plant')
    }
  })
  removeStale(m, 'plan-', keep)
}

function syncImpacts(m, w) {
  const keep = new Set()
  const iconFeatures = []
  for (const impact of w.impacts || []) {
    const id = `impact-${impact.id}`
    keep.add(id)

    if (impact.type === 'rain') {
      upsertLayer(m, id, circlePolygon(impact.lat, impact.lon, impact.radius_km), {
        type: 'fill', paint: { 'fill-color': 'rgba(56, 189, 248, 0.22)', 'fill-outline-color': 'rgba(56, 189, 248, 1)' },
      }, 'plant')
      iconFeatures.push(point([impact.lon, impact.lat], { icon: 'icon-rain' }))
    } else if (impact.type === 'traffic') {
      if (impact.lat != null && impact.lon != null) {
        upsertLayer(m, id, circlePolygon(impact.lat, impact.lon, impact.radius_km || 0.5), {
          type: 'fill', paint: { 'fill-color': 'rgba(249, 115, 22, 0.28)', 'fill-outline-color': 'rgba(249, 115, 22, 1)' },
        }, 'plant')
        iconFeatures.push(point([impact.lon, impact.lat], { icon: 'icon-traffic' }))
      } else if (impact.start && impact.end) {
        upsertLayer(m, id, line([toLngLat(impact.start), toLngLat(impact.end)]), {
          type: 'line', paint: { 'line-color': 'rgba(249, 115, 22, 1)', 'line-width': 10, 'line-opacity': 0.9 },
        }, 'plant')
      }
    } else if (impact.type === 'roadblock') {
      const radiusKm = (impact.radius_m || 150) / 1000.0
      const isArea = (impact.radius_m || 150) > 300
      upsertLayer(m, id, circlePolygon(impact.lat, impact.lon, radiusKm), {
        type: 'fill', paint: { 'fill-color': 'rgba(239, 68, 68, 0.35)', 'fill-outline-color': 'rgba(220, 38, 38, 1)' },
      }, 'plant')

      const ringId = `impact-ring-${impact.id}`
      keep.add(ringId)
      upsertLayer(m, ringId, circlePolygon(impact.lat, impact.lon, radiusKm), {
        type: 'line', paint: { 'line-color': 'rgba(239, 68, 68, 1)', 'line-width': 3, 'line-dasharray': [2, 1] },
      }, 'plant')

      iconFeatures.push(point([impact.lon, impact.lat], { icon: isArea ? 'icon-area' : 'icon-roadblock' }))
    }
  }

  removeStale(m, 'impact-', keep)
  removeStale(m, 'impact-ring-', keep)

  upsertLayer(m, 'impact-icons', featureCollection(iconFeatures), {
    type: 'symbol',
    layout: {
      'icon-image': ['get', 'icon'],
      'icon-size': 0.9,
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
  })
}

// Route layers go under the plant and hub pins.
function beforePins(map) {
  return map.getLayer('plant') ? 'plant' : undefined
}

function rgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

// line-gradient over line-progress: faded up to `travelled`, solid up to `reveal`, hidden after it.
function routeGradient(color, travelled, reveal) {
  const t = Math.min(Math.max(travelled, 1e-6), 1 - 2e-6)
  const expr = ['step', ['line-progress'], rgba(color, 0.3), t, rgba(color, 0.95)]
  if (reveal < 1 - 1e-6) expr.push(Math.max(reveal, t + 1e-6), rgba(color, 0))
  return expr
}

function newMotionStats() {
  return { frames: 0, maxJumpM: 0, maxJumpTruck: null, perTruck: {}, lastLog: performance.now() }
}

// DEV: the largest distance a truck marker moved in one frame (e2e/motion_smoothness.py reads it).
function recordJump(stats, truckId, jumpM, frameMs, status) {
  stats.frames += 1
  stats.perTruck[truckId] = Math.max(stats.perTruck[truckId] || 0, jumpM)
  if (jumpM > stats.maxJumpM) {
    stats.maxJumpM = jumpM
    stats.maxJumpTruck = truckId
    stats.maxJumpAt = { frameMs: Math.round(frameMs), status }
  }
  const now = performance.now()
  if (now - stats.lastLog > 5000) {
    stats.lastLog = now
    console.info(`[motion] max per-frame jump ${stats.maxJumpM.toFixed(1)} m (${stats.maxJumpTruck}, ${JSON.stringify(stats.maxJumpAt)}) over ${stats.frames} truck-frames`)
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text != null) node.textContent = text
  return node
}

function hubPopup(hub, actions) {
  const root = el('div', 'df-card')
  root.style.minWidth = '220px'
  root.style.background = 'var(--bg-surface)'
  root.style.color = 'var(--text-primary)'
  root.style.padding = 'var(--space-3)'

  const title = el('div', 'df-card-title', hub.name)
  title.style.marginBottom = 'var(--space-1)'
  root.appendChild(title)

  const meta = el('div', null, `${litres(hub.delivered_l)} of ${litres(hub.demand_l)} · Window ${clock(hub.window_end)} · ${hub.priority}`)
  meta.style.fontSize = 'var(--text-xs)'
  meta.style.color = 'var(--text-secondary)'
  root.appendChild(meta)

  if (hub.risk) {
    const risk = el('div', null, `Risk: ${hub.risk}`)
    risk.style.fontSize = 'var(--text-xs)'
    risk.style.color = 'var(--color-warning)'
    risk.style.marginTop = '4px'
    root.appendChild(risk)
  }

  for (const d of hub.deliveries || []) {
    const row = el('div', null, deliveryText(d))
    row.style.fontSize = 'var(--text-xs)'
    row.style.marginTop = '4px'
    root.appendChild(row)
  }

  if (actions.length) {
    const row = el('div', null)
    row.style.display = 'flex'
    row.style.gap = '6px'
    row.style.marginTop = '8px'
    for (const action of actions) {
      const button = el('button', 'df-btn df-btn-primary df-btn-sm', action.label)
      button.type = 'button'
      button.addEventListener('click', () => action.onClick(hub))
      row.appendChild(button)
    }
    root.appendChild(row)
  }
  return root
}

function plantPopup(world) {
  const root = el('div', 'df-card')
  root.style.minWidth = '220px'
  root.style.background = 'var(--bg-surface)'
  root.style.color = 'var(--text-primary)'
  root.style.padding = 'var(--space-3)'

  const title = el('div', 'df-card-title', world.plant.name)
  title.style.marginBottom = 'var(--space-1)'
  root.appendChild(title)

  const stock = el('div', null, `${litres(world.plant.milk_available_l)} in stock`)
  stock.style.fontSize = 'var(--text-xs)'
  stock.style.color = 'var(--text-secondary)'
  root.appendChild(stock)

  const parked = world.trucks.filter((t) => PARKED.has(t.status))
  const trucks = el('div', null, parked.length ? `At plant: ${parked.map((t) => t.id).join(', ')}` : 'No trucks at plant')
  trucks.style.fontSize = 'var(--text-xs)'
  trucks.style.marginTop = '4px'
  root.appendChild(trucks)

  return root
}
