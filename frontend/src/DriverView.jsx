/**
 * Driver navigation (phone): open http://<pc-lan-ip>:5600/driver.html?truckId=TRK-03
 *
 * Google-Maps-style: heading-up 3D map that follows the truck, a navigation arrow at the truck's live position
 * (placed by distance along its route, so it stays on the road), the remaining route in blue, the next stop and
 * the final stop marked, and a turn-by-turn banner ("240 m · Turn right onto Anna Salai", then the next one)
 * from GET /routes/{id}/directions — computed on the very route the truck drives, detours included.
 * Every time/ETA shown comes from the backend; the client only interpolates position between /ws messages.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { api } from './api'
import { clock, clockHMS, hubName, litres, minutes, pendingStops, truckIndex } from './format'
import useWorldState, { liveSimTime } from './hooks/useWorldState'
import { toLngLat, roadLngLat, line, point, circlePolygon, featureCollection, upsertLayer, removeStale, TRUCK_COLORS } from './map/mapUtils'
import { Icon } from './components/Icons'
import './driver.css'

const OSM_TILES = ['https://tile.openstreetmap.org/{z}/{x}/{y}.png']

// --- Geometry along a route ({coordinates: [[lon, lat]], cum_m: [m]}) -------------------------------------

function segmentAt(route, d) {
  const cum = route.cum_m
  let lo = 1
  let hi = cum.length - 1
  while (lo < hi) {   // first index with cum >= d
    const mid = (lo + hi) >> 1
    if (cum[mid] < d) lo = mid + 1
    else hi = mid
  }
  return lo
}

function pointAlong(route, d) {
  const c = route.coordinates
  if (c.length < 2) return c[0]
  const dd = Math.max(0, Math.min(d, route.cum_m[route.cum_m.length - 1]))
  const i = segmentAt(route, dd)
  const seg = route.cum_m[i] - route.cum_m[i - 1]
  const f = seg > 0 ? (dd - route.cum_m[i - 1]) / seg : 0
  return [c[i - 1][0] + (c[i][0] - c[i - 1][0]) * f, c[i - 1][1] + (c[i][1] - c[i - 1][1]) * f]
}

function bearing(p1, p2) {
  const toRad = Math.PI / 180
  const y = Math.sin((p2[0] - p1[0]) * toRad) * Math.cos(p2[1] * toRad)
  const x = Math.cos(p1[1] * toRad) * Math.sin(p2[1] * toRad) -
            Math.sin(p1[1] * toRad) * Math.cos(p2[1] * toRad) * Math.cos((p2[0] - p1[0]) * toRad)
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
}

/** Direction of travel at distance d: bearing over the next ~30 m of road. */
function headingAlong(route, d) {
  const total = route.cum_m[route.cum_m.length - 1]
  const a = pointAlong(route, Math.max(0, Math.min(d, total - 1)))
  const b = pointAlong(route, Math.min(total, d + 30))
  return a[0] === b[0] && a[1] === b[1] ? null : bearing(a, b)
}

/** Ease an angle toward a target by the shortest way round (no spin at 359° -> 0°). */
function easeAngle(from, to, k) {
  const diff = ((to - from + 540) % 360) - 180
  return (from + diff * k + 360) % 360
}

/** Live distance (m) of the truck along its current route, extrapolated from the last /ws message. */
function liveDistance(motionMsg, truckId, route) {
  const m = motionMsg?.trucks?.find((t) => t.id === truckId)
  if (!m || !route || m.route_id !== route.route_id) return null
  let d = m.distance_along_m ?? 0
  if (motionMsg.running && !motionMsg.paused && m.speed_mps > 0) {
    const ageS = Math.min((motionMsg.latencyS ?? 0) + (performance.now() - motionMsg.receivedAt) / 1000, 1.5)
    d += m.speed_mps * (motionMsg.sim_speed ?? 1) * ageS
  }
  return Math.min(d, route.length_m)
}

// --- Directions text -----------------------------------------------------------------------------------------

function distanceText(m) {
  if (m == null) return ''
  if (m < 50) return 'Now'
  if (m < 1000) return `${Math.round(m / 10) * 10} m`
  return `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`
}

function stepText(step, destinationName) {
  if (!step) return ''
  if (step.type === 'arrive') return `Arrive at ${destinationName || 'destination'}`
  const onto = step.street ? ` onto ${step.street}` : ''
  switch (step.modifier) {
    case 'uturn': return `Make a U-turn${onto}`
    case 'slight left': return `Keep left${onto}`
    case 'slight right': return `Keep right${onto}`
    case 'sharp left': return `Turn sharp left${onto}`
    case 'sharp right': return `Turn sharp right${onto}`
    case 'left': return `Turn left${onto}`
    case 'right': return `Turn right${onto}`
    default: return step.street ? `Continue on ${step.street}` : 'Continue straight'
  }
}

/** Manoeuvre arrow (white, for the green banner). */
function ManeuverArrow({ step, size = 34 }) {
  const mod = step?.type === 'arrive' ? 'arrive' : step?.modifier || 'straight'
  const rot = { straight: 0, 'slight right': 35, right: 90, 'sharp right': 135, 'slight left': -35, left: -90, 'sharp left': -135 }
  if (mod === 'arrive') {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 21s-6-5.6-6-10.5A6 6 0 0 1 18 10.5C18 15.4 12 21 12 21z" />
        <circle cx="12" cy="10.5" r="2.2" fill="white" />
      </svg>
    )
  }
  if (mod === 'uturn') {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M16 20V9a4 4 0 0 0-8 0v4" /><path d="M5 10l3 3 3-3" />
      </svg>
    )
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"
         style={{ transform: `rotate(${rot[mod] ?? 0}deg)` }}>
      <path d="M12 21V4" /><path d="M6 10l6-6 6 6" />
    </svg>
  )
}

export default function DriverView({ truckId }) {
  const resolvedTruckId = truckId || new URLSearchParams(window.location.search).get('truckId') || 'TRK-03'
  const { world, refresh, motionRef, connection } = useWorldState(1000)

  const truck = world?.trucks?.find((t) => t.id === resolvedTruckId)
  const stops = pendingStops(truck)
  const nextStop = stops[0]
  const nextHub = nextStop && world?.hubs?.find((h) => h.id === nextStop.hub_id)
  const finalStop = stops[stops.length - 1]
  const finalHub = finalStop && world?.hubs?.find((h) => h.id === finalStop.hub_id)
  const returning = truck?.status === 'RETURNING'
  const destinationName = returning ? world?.plant?.name || 'Plant' : nextHub?.name

  const tIdx = truckIndex(resolvedTruckId)
  const truckAccentColor = TRUCK_COLORS[tIdx % TRUCK_COLORS.length]

  const [isFollowing, setIsFollowing] = useState(true)
  const [sheetExpanded, setSheetExpanded] = useState(false)
  const [showCallModal, setShowCallModal] = useState(false)

  // --- Directions for the leg being driven (refetched when the route changes, e.g. after a detour) ---
  const [directions, setDirections] = useState(null)
  const directionsRef = useRef(null)
  const routeId = truck?.route_id
  useEffect(() => {
    if (!routeId) return undefined
    let alive = true
    let retry
    const load = () => api.getDirections(routeId)
      .then((d) => { if (alive) { directionsRef.current = d; setDirections(d) } })
      .catch(() => { if (alive) retry = setTimeout(load, 2000) })
    load()
    return () => { alive = false; clearTimeout(retry) }
  }, [routeId])
  const legRoute = directions && directions.route_id === routeId ? directions : null

  // --- Navigation numbers, refreshed 4x a second from the live position ---
  const [nav, setNav] = useState({ along: null, step: null, then: null, toStepM: null, leftM: null, speedKmh: 0, simTime: null })
  useEffect(() => {
    const tick = () => {
      const route = directionsRef.current
      const msg = motionRef.current
      const live = msg?.trucks?.find((t) => t.id === resolvedTruckId)
      const speedKmh = live ? Math.round((live.speed_mps || 0) * 3.6) : 0
      const simTime = liveSimTime(msg)
      if (!route || !live || live.route_id !== route.route_id) {
        setNav((n) => ({ ...n, along: null, step: null, then: null, toStepM: null, leftM: null, speedKmh, simTime }))
        return
      }
      const along = liveDistance(msg, resolvedTruckId, route) ?? 0
      const idx = route.steps.findIndex((s) => s.type !== 'depart' && s.at_m > along + 15)
      const step = idx >= 0 ? route.steps[idx] : route.steps[route.steps.length - 1]
      const then = idx >= 0 ? route.steps[idx + 1] : null
      setNav({ along, step, then, toStepM: Math.max(0, step.at_m - along), leftM: Math.max(0, route.length_m - along), speedKmh, simTime })
    }
    tick()
    const t = setInterval(tick, 250)
    return () => clearInterval(t)
  }, [motionRef, resolvedTruckId])

  // --- Notifications (roadblock news worded by the backend, blocked, demand spikes) ---
  const [notifications, setNotifications] = useState([])
  const lastNewsRef = useRef(null)
  const prevDemandRef = useRef({})
  const addNotification = useCallback((notif) => {
    const id = Date.now() + Math.random()
    setNotifications((prev) => [...prev, { id, ...notif }])
    if (!notif.sticky) {
      setTimeout(() => setNotifications((prev) => prev.filter((n) => n.id !== id)), notif.duration || 4500)
    }
  }, [])

  useEffect(() => {
    if (!truck) return
    const roadNews = /^(Road closed|Road cleared|Road reopened)/.test(truck.instructions || '')
    if (roadNews && truck.instructions !== lastNewsRef.current) {
      addNotification({ type: 'rerouting', message: truck.instructions, duration: 5000 })
    }
    if (roadNews) lastNewsRef.current = truck.instructions

    const alreadyBlocked = notifications.some((n) => n.type === 'blocked')
    if (truck.status === 'BLOCKED' && !alreadyBlocked) {
      addNotification({ type: 'blocked', message: 'Stop safely — no route available. Manager notified.', sticky: true })
    } else if (truck.status !== 'BLOCKED' && alreadyBlocked) {
      setNotifications((prev) => prev.filter((n) => n.type !== 'blocked'))
    }

    if (nextHub) {
      const prevD = prevDemandRef.current[nextHub.id]
      if (prevD != null && nextHub.demand_l > prevD) {
        addNotification({ type: 'demand', message: `${nextHub.name}: +${litres(nextHub.demand_l - prevD)} added to your trip`, duration: 5000 })
      }
      prevDemandRef.current[nextHub.id] = nextHub.demand_l
    }
  }, [truck, nextHub, addNotification, notifications])

  const hasActiveInstruction = truck?.instruction_status === 'ACTIVE' && truck?.instructions
  const handleAcknowledgeInstruction = async () => {
    try {
      await api.acknowledgeInstruction(truck.id)
      refresh()
    } catch (_) { /* shown again on the next refresh */ }
  }

  const driving = truck && (truck.status === 'EN_ROUTE' || truck.status === 'RETURNING')
  const slowed = driving && nav.speedKmh > 0 && nav.speedKmh < (truck.speed_kmph ?? 30) - 1
  const now = nav.simTime ?? world?.sim_time
  const etaMin = nextStop?.eta != null && now != null ? Math.max(0, nextStop.eta - now) : null

  // Nothing to drive yet (or any more): a clear full-screen card instead of an empty map.
  const phase = world?.phase
  const trip = stops.reduce((sum, s) => sum + (s.drop_l || 0), 0)
  let waiting = null
  if (!world) {
    waiting = { title: 'Connecting to DairyFlow…', text: 'Keep this phone on the same Wi-Fi as the PC running DairyFlow. This screen updates by itself.' }
  } else if (!truck) {
    waiting = { title: `No truck "${resolvedTruckId}"`, text: 'Pick your truck below.' }
  } else if (phase === 'PLANNING' && !(world.hubs || []).length) {
    waiting = { title: 'No delivery plan yet', text: "The manager has not loaded today's hubs. Your trip appears here as soon as it is planned." }
  } else if (phase === 'PLANNING' && !stops.length) {
    waiting = world.plan
      ? { title: 'Reserve truck today', text: 'No stops planned for you. Stay at the plant; you are called if a hub needs extra milk.' }
      : { title: 'Waiting for the plan', text: 'Hubs are loaded but the manager has not planned or dispatched yet.' }
  } else if (phase === 'PLANNING') {
    waiting = { title: 'Trip planned — waiting for dispatch', text: `${stops.length} stop${stops.length > 1 ? 's' : ''} · ${litres(trip)} · load at ${clock(175)}, leave the plant at ${clock(180)}. Navigation starts when the manager presses Dispatch.`, showStops: true }
  } else if (phase === 'EXECUTING' && !stops.length && (truck.status === 'RESERVE' || truck.status === 'AT_PLANT')) {
    waiting = truck.status === 'RESERVE'
      ? { title: 'Reserve — stay at the plant', text: 'You are called here if a hub needs extra milk.' }
      : { title: 'Back at the plant', text: `Trip done: ${litres(truck.plan.reduce((x, s) => x + (s.delivered_l || 0), 0))} delivered. Wait for new instructions.` }
  } else if (phase === 'COMPLETED' || phase === 'STOPPED') {
    waiting = { title: phase === 'COMPLETED' ? "Today's run is complete" : 'Run stopped by the manager', text: 'Thank you! Navigation resumes when a new run is dispatched.' }
  }

  let banner = null
  if (!truck) banner = { text: world ? `Truck ${resolvedTruckId} not found` : 'Connecting…', dist: '' }
  else if (truck.status === 'BLOCKED') banner = { text: 'Stop safely — waiting for the manager', dist: 'Stopped', blocked: true }
  else if (truck.reroute_pending) banner = { text: 'Road closed ahead — finding a detour…', dist: '', blocked: true }
  else if (truck.status === 'LOADING') banner = { text: `Loading at the plant · leaves ${clock(180)}`, dist: '' }
  else if (truck.status === 'AT_PLANT' || truck.status === 'RESERVE') banner = { text: 'At the plant — no trip assigned', dist: '' }
  else if (driving && nav.step) banner = { step: nav.step, text: stepText(nav.step, destinationName), dist: distanceText(nav.toStepM) }
  else if (driving) banner = { text: `Head to ${destinationName || 'destination'}`, dist: '' }

  return (
    <div className="df-driver-viewport">
      <div className="df-phone-frame">
        {/* Desktop preview only (hidden on a real phone): sim clock */}
        <div className="df-phone-notch">
          <span>{now != null ? clockHMS(now).slice(0, 5) : '--:--'}</span>
          <div className="df-dynamic-island" />
          <span>{resolvedTruckId}</span>
        </div>

        <div className="df-driver-map-container">
          <DriverNavMap
            world={world}
            truck={truck}
            stops={stops}
            returning={returning}
            route={legRoute}
            isFollowing={isFollowing}
            onUserPan={() => setIsFollowing(false)}
            truckColor={truckAccentColor}
            motionRef={motionRef}
          />
        </div>

        {waiting && (
          <div className="df-driver-waiting">
            <div className="df-driver-waiting-card">
              <div className="df-driver-waiting-truck" style={{ color: truckAccentColor }}>
                {resolvedTruckId}{truck?.driver_name ? ` · ${truck.driver_name}` : ''}
              </div>
              <h2>{waiting.title}</h2>
              <p>{waiting.text}</p>
              {waiting.showStops && (
                <ol className="df-driver-waiting-stops">
                  {stops.map((s, i) => (
                    <li key={`${s.hub_id}-${i}`}>
                      <strong>{hubName(world, s.hub_id)}</strong>{i === stops.length - 1 ? ' (final)' : ''}
                      <span>{litres(s.drop_l)} · ETA {s.eta != null ? clock(s.eta) : '—'} · by {clock(s.window_end)}</span>
                    </li>
                  ))}
                </ol>
              )}
              {(world?.trucks || []).length > 0 && (
                <div className="df-driver-picker">
                  <span>Truck</span>
                  {world.trucks.map((t) => (
                    <a key={t.id} href={`?truckId=${encodeURIComponent(t.id)}`}
                       className={t.id === resolvedTruckId ? 'active' : ''}>{t.id}</a>
                  ))}
                </div>
              )}
              <div className="df-driver-waiting-hint">
                <span className="df-driver-live-dot" /> Live — this screen updates by itself
              </div>
            </div>
          </div>
        )}

        {/* Turn-by-turn banner */}
        {!waiting && truck?.status !== 'UNLOADING' && banner && (
          <div className={`df-maneuver-banner ${banner.blocked ? 'df-maneuver-blocked' : ''}`}>
            <div className="df-maneuver-main">
              <div className="df-maneuver-icon-box">
                {banner.step ? <ManeuverArrow step={banner.step} /> : <Icon name={banner.blocked ? 'roadblock' : 'truck'} size={26} color="white" />}
              </div>
              <div className="df-maneuver-info">
                {banner.dist && <div className="df-maneuver-dist">{banner.dist}</div>}
                <div className="df-maneuver-street">{banner.text}</div>
              </div>
            </div>
            {banner.step && nav.then && (
              <div className="df-maneuver-secondary">
                <span>Then</span>
                <ManeuverArrow step={nav.then} size={14} />
                <span>{stepText(nav.then, destinationName)}</span>
              </div>
            )}
          </div>
        )}

        {connection === 'reconnecting' && (
          <div className="df-driver-reconnecting">Reconnecting… (last known position)</div>
        )}

        <div className="df-driver-alerts-stack">
          {hasActiveInstruction && (
            <div className="df-driver-notification info">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Icon name="phone" size={18} color="var(--color-accent)" />
                <span style={{ fontSize: '12px' }}>{truck.instructions}</span>
              </div>
              <button type="button" className="df-driver-ack-btn" onClick={handleAcknowledgeInstruction}>Acknowledge</button>
            </div>
          )}
          {notifications.map((n) => (
            <div key={n.id} className={`df-driver-notification ${n.type}`}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Icon name={n.type === 'blocked' ? 'roadblock' : n.type === 'rerouting' ? 'refresh' : 'alert'} size={18} color="white" />
                <span style={{ fontSize: '12px', fontWeight: 600 }}>{n.message}</span>
              </div>
              {n.type === 'blocked' && (
                <button type="button" className="df-driver-call-btn" onClick={() => setShowCallModal(true)}>Call Manager</button>
              )}
            </div>
          ))}
        </div>

        {!isFollowing && (
          <button type="button" className="df-recenter-btn" onClick={() => setIsFollowing(true)}>
            <Icon name="play" size={14} color="#0f172a" />
            <span>Re-centre</span>
          </button>
        )}

        {/* Live speed (rain / traffic included) */}
        <div className={`df-speed-bubble ${slowed ? 'impact-slow' : ''}`} style={waiting ? { display: 'none' } : undefined}>
          <span className="df-speed-val">{driving ? nav.speedKmh : 0}</span>
          <span className="df-speed-unit">km/h</span>
        </div>

        {truck?.status === 'UNLOADING' && (
          <div className="df-unloading-overlay">
            <div className="df-unloading-title">Unloading at {nextHub?.name}</div>
            <div className="df-unloading-desc">
              {litres(nextStop?.delivered_l)} of {litres(nextStop?.drop_l)}
              {nextStop?.unload_min_left != null ? ` · ${Math.ceil(nextStop.unload_min_left)} min left` : ''}
            </div>
          </div>
        )}

        {/* Bottom sheet: time / distance to the next stop, then the whole trip */}
        <div className="df-bottom-sheet" style={{ transform: sheetExpanded ? 'translateY(0)' : 'translateY(calc(100% - 78px))', display: waiting ? 'none' : undefined }}>
          <div className="df-sheet-handle-bar" onClick={() => setSheetExpanded((p) => !p)} />
          <div className="df-sheet-collapsed-row" onClick={() => setSheetExpanded((p) => !p)}>
            <div className="df-eta-main">
              <span className="df-eta-minutes">
                {returning ? 'To plant' : etaMin != null ? `${Math.round(etaMin)} min` : '—'}
              </span>
              <span className="df-eta-subtitle">
                {nav.leftM != null ? ` · ${(nav.leftM / 1000).toFixed(1)} km` : ''}
                {!returning && nextStop?.eta != null ? ` · ${clock(nextStop.eta)}` : ''}
                {destinationName ? ` · ${destinationName}` : ''}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '12px', fontWeight: 700, color: truckAccentColor }}>{resolvedTruckId}</span>
              <Icon name={sheetExpanded ? 'chevron-down' : 'chevron-up'} size={18} color="#94a3b8" />
            </div>
          </div>

          <div className="df-sheet-expanded-content">
            <div className="df-tank-progress">
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '6px' }}>
                <span style={{ color: '#94a3b8' }}>Milk on board</span>
                <strong style={{ color: truckAccentColor }}>{litres(truck?.load_l)} / {litres(truck?.capacity_l)}</strong>
              </div>
              <div style={{ height: '6px', backgroundColor: '#334155', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${truck?.capacity_l ? (truck.load_l / truck.capacity_l) * 100 : 0}%`, backgroundColor: truckAccentColor, transition: 'width 0.3s ease' }} />
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <span style={{ fontSize: '11px', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase' }}>
                Stops ({stops.length}){finalHub ? ` · final: ${finalHub.name}` : ''}
              </span>
              {stops.map((s, idx) => (
                <div key={`${s.hub_id}-${idx}`} className="df-stop-card">
                  <div className="df-stop-card-row">
                    <strong style={{ fontSize: '14px', color: 'white' }}>
                      {idx + 1}. {hubName(world, s.hub_id)}{idx === stops.length - 1 ? ' (final)' : ''}
                    </strong>
                    <span style={{ fontSize: '12px', color: '#22c55e', fontWeight: 700 }}>Drop {litres(s.drop_l)}</span>
                  </div>
                  <div className="df-stop-card-row" style={{ fontSize: '12px', color: '#94a3b8' }}>
                    <span>ETA <strong>{s.eta != null ? clock(s.eta) : '—'}</strong> · by <strong>{clock(s.window_end)}</strong></span>
                    <span>Slack <strong style={{ color: s.slack_min < 10 ? '#ef4444' : '#22c55e' }}>{minutes(s.slack_min)}</strong></span>
                  </div>
                </div>
              ))}
              <div className="df-stop-card" style={{ opacity: 0.7 }}>
                <strong style={{ fontSize: '13px', color: 'white' }}>Then back to {world?.plant?.name || 'the plant'}</strong>
              </div>
            </div>
          </div>
        </div>

        {showCallModal && (
          <div className="df-driver-call-modal">
            <div className="df-driver-call-card">
              <div className="df-driver-call-icon"><Icon name="phone" size={28} color="white" /></div>
              <h3 style={{ margin: '0 0 6px' }}>Dispatch Control</h3>
              <p style={{ fontSize: '13px', color: '#94a3b8', margin: '0 0 20px' }}>Connecting to the fleet manager…</p>
              <div style={{ backgroundColor: '#0f172a', padding: '12px', borderRadius: '12px', fontSize: '12px', marginBottom: '20px' }}>
                <strong>Status:</strong> no route around the closure. Stay stopped safely; the route resumes as soon as the road reopens or the manager decides.
              </div>
              <button type="button" className="df-driver-end-call" onClick={() => setShowCallModal(false)}>End Call</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// --- Map: heading-up follow camera, navigation arrow, route, next & final stops ---------------------------

function DriverNavMap({ world, truck, stops, returning, route, isFollowing, onUserPan, truckColor, motionRef }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const readyRef = useRef(false)
  const puckRef = useRef(null)
  const anim = useRef({ lon: null, lat: null, heading: 0 })
  const latest = useRef({})
  latest.current = { truck, route, isFollowing }
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: { osm: { type: 'raster', tiles: OSM_TILES, tileSize: 256, maxzoom: 19, attribution: '&copy; OpenStreetMap contributors' } },
        // Land-coloured background: tiles still loading at the horizon never show as black.
        layers: [
          { id: 'land', type: 'background', paint: { 'background-color': '#ece8e0' } },
          { id: 'osm-layer', type: 'raster', source: 'osm', paint: { 'raster-fade-duration': 0 } },
        ],
      },
      center: [80.22, 13.05],
      zoom: 16.5,
      pitch: 45,
      maxPitch: 60,
      bearing: 0,
      attributionControl: { compact: true },
    })
    map.on('load', () => {
      readyRef.current = true
      setReady(true)
    })
    // Driver moves the map -> stop following until "Re-centre"
    map.on('dragstart', () => onUserPan?.())
    map.on('rotatestart', (e) => { if (e.originalEvent) onUserPan?.() })

    // Navigation arrow. rotationAlignment 'map': the arrow keeps its true heading whatever the map bearing,
    // so in heading-up mode it points straight up. (Rotation via setRotation; never style the marker root.)
    const el = document.createElement('div')
    el.className = 'df-nav-puck'
    el.innerHTML = `
      <div class="df-nav-puck-inner">
        <div class="df-nav-halo"></div>
        <svg class="df-nav-arrow" width="44" height="44" viewBox="0 0 44 44">
          <circle cx="22" cy="22" r="19" fill="white" />
          <path d="M22 8 L33 33 L22 27 L11 33 Z" fill="${truckColor}" stroke="white" stroke-width="1.5" stroke-linejoin="round" />
        </svg>
      </div>`
    puckRef.current = new maplibregl.Marker({ element: el, anchor: 'center', rotationAlignment: 'map', pitchAlignment: 'map' })
      .setLngLat([80.22, 13.05])
      .addTo(map)
    mapRef.current = map
    return () => {
      readyRef.current = false
      map.remove()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep the truck in the lower part of the screen (more road ahead visible), like a car navigator.
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready) return undefined
    const pad = () => m.setPadding({ top: Math.round(m.getContainer().clientHeight * 0.35), bottom: 0, left: 0, right: 0 })
    pad()
    window.addEventListener('resize', pad)
    return () => window.removeEventListener('resize', pad)
  }, [ready])

  // Animation loop: arrow at the live distance along the route, eased heading, follow camera.
  useEffect(() => {
    let raf
    let lastCam = 0
    const loop = (ts) => {
      const m = mapRef.current
      const { truck: t, route: r, isFollowing: follow } = latest.current
      if (m && readyRef.current && t) {
        const msg = motionRef?.current
        const live = msg?.trucks?.find((x) => x.id === t.id)
        let target = null
        let heading = null
        const along = r ? liveDistance(msg, t.id, r) : null
        if (along != null && r.coordinates.length >= 2) {
          target = pointAlong(r, along)
          heading = headingAlong(r, along)
        } else if (live) {
          target = [live.lon, live.lat]
          heading = live.heading
        } else if (t.position) {
          target = toLngLat(t.position)
        }
        if (target) {
          const a = anim.current
          if (a.lon == null) { a.lon = target[0]; a.lat = target[1] }
          a.lon += (target[0] - a.lon) * 0.35
          a.lat += (target[1] - a.lat) * 0.35
          if (heading != null) a.heading = easeAngle(a.heading, heading, 0.15)
          puckRef.current?.setLngLat([a.lon, a.lat]).setRotation(a.heading)
          if (follow && ts - lastCam > 33) {   // ~30 fps camera
            lastCam = ts
            m.jumpTo({ center: [a.lon, a.lat], bearing: a.heading })
          }
        }
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [motionRef])

  // Back to following: ease the camera in once (zoom/pitch of a navigator).
  useEffect(() => {
    const m = mapRef.current
    if (m && ready && isFollowing) m.easeTo({ zoom: 16.5, pitch: 45, duration: 600 })
  }, [isFollowing, ready])

  // Route (driven part grey, remaining part blue), next stop, later stops, final stop, closures.
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready || !truck) return undefined
    const draw = () => {
      const keep = new Set()
      const r = latest.current.route
      if (r && r.coordinates.length >= 2) {
        const along = liveDistance(motionRef?.current, truck.id, r) ?? truck.distance_along_route_m ?? 0
        const i = segmentAt(r, Math.max(0.01, along))
        const here = pointAlong(r, along)
        const done = r.coordinates.slice(0, i).concat([here])
        const ahead = [here].concat(r.coordinates.slice(i))
        keep.add('driver-route-done').add('driver-route-casing').add('driver-route-ahead')
        upsertLayer(m, 'driver-route-done', line(done), { type: 'line', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#94a3b8', 'line-width': 7, 'line-opacity': 0.8 } })
        upsertLayer(m, 'driver-route-casing', line(ahead), { type: 'line', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#1e40af', 'line-width': 11 } })
        upsertLayer(m, 'driver-route-ahead', line(ahead), { type: 'line', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#3b82f6', 'line-width': 7 } })
      }

      const hubsById = Object.fromEntries((world?.hubs || []).map((h) => [h.id, h]))
      const later = stops.slice(1, -1).map((s) => hubsById[s.hub_id]).filter(Boolean)
      const next = returning ? world?.plant : hubsById[stops[0]?.hub_id]
      const final = !returning && stops.length > 1 ? hubsById[stops[stops.length - 1].hub_id] : null
      if (later.length) {
        keep.add('driver-stops-later')
        upsertLayer(m, 'driver-stops-later', featureCollection(later.map((h) => point(roadLngLat(h)))), {
          type: 'circle', paint: { 'circle-radius': 7, 'circle-color': '#ffffff', 'circle-stroke-width': 3, 'circle-stroke-color': truckColor },
        })
      }
      if (final) {   // final destination: red pin-style dot with a white core
        keep.add('driver-final').add('driver-final-core')
        const fc = featureCollection([point(roadLngLat(final))])
        upsertLayer(m, 'driver-final', fc, { type: 'circle', paint: { 'circle-radius': 13, 'circle-color': '#dc2626', 'circle-stroke-width': 3, 'circle-stroke-color': '#ffffff' } })
        upsertLayer(m, 'driver-final-core', fc, { type: 'circle', paint: { 'circle-radius': 4, 'circle-color': '#ffffff' } })
      }
      if (next) {    // next stop (or the plant when returning): big dot in the truck colour
        keep.add('driver-next').add('driver-next-core')
        const fc = featureCollection([point(roadLngLat(next))])
        upsertLayer(m, 'driver-next', fc, { type: 'circle', paint: { 'circle-radius': 14, 'circle-color': stops.length === 1 && !returning ? '#dc2626' : truckColor, 'circle-stroke-width': 3, 'circle-stroke-color': '#ffffff' } })
        upsertLayer(m, 'driver-next-core', fc, { type: 'circle', paint: { 'circle-radius': 4, 'circle-color': '#ffffff' } })
      }

      for (const impact of world?.impacts || []) {
        const id = `driver-impact-${impact.id}`
        if (impact.type === 'roadblock') {
          keep.add(id)
          upsertLayer(m, id, circlePolygon(impact.lat, impact.lon, (impact.radius_m || 150) / 1000), { type: 'fill', paint: { 'fill-color': 'rgba(220, 38, 38, 0.45)', 'fill-outline-color': '#b91c1c' } })
        } else if (impact.type === 'traffic' && impact.lat != null) {
          keep.add(id)
          upsertLayer(m, id, circlePolygon(impact.lat, impact.lon, impact.radius_km || 0.5), { type: 'fill', paint: { 'fill-color': 'rgba(249, 115, 22, 0.3)', 'fill-outline-color': '#ea580c' } })
        } else if (impact.type === 'rain') {
          keep.add(id)
          upsertLayer(m, id, circlePolygon(impact.lat, impact.lon, impact.radius_km), { type: 'fill', paint: { 'fill-color': 'rgba(59, 130, 246, 0.12)', 'fill-outline-color': '#60a5fa' } })
        }
      }
      removeStale(m, 'driver-', keep)
    }
    draw()
    const t = setInterval(draw, 250)   // the driven/remaining split follows the arrow
    return () => clearInterval(t)
  }, [world, truck, stops, returning, route, truckColor, ready, motionRef])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
