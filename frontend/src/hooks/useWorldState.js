/**
 * World state from the backend.
 *
 * /ws pushes a motion message at WS_PUSH_HZ (5 Hz) and the full world in every WS_WORLD_EVERY-th one (~1 Hz).
 *   world      – the latest full world (React state; re-renders at ~1 Hz).
 *   motionRef  – the latest motion message {seq, receivedAt, latencyS, sim_time, sim_speed, running, paused,
 *                phase, trucks: [{id, lat, lon, heading, speed_mps, route_id, distance_along_m, status,
 *                approximate}]}, kept in a ref so 5 Hz updates never re-render; the map's animation loop
 *                and the live clock read it.
 *   refresh()  – fetch GET /state now (after an action that changes the world).
 *   connection – 'connecting' | 'live' | 'reconnecting': 'reconnecting' once no message (each carries a
 *                heartbeat) arrived for WS_STALE_MS (3 s). The last world stays on screen meanwhile.
 * The socket reconnects with exponential backoff (0.5 s → 8 s); a socket silent for 2 × WS_STALE_MS is
 * closed and reopened. While it is down, GET /state is polled every `pollingMs`.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, API_BASE, errorMessage } from '../api'

function wsUrl() {
  if (API_BASE.startsWith('http')) return API_BASE.replace(/^http/, 'ws') + '/ws'
  return window.location.origin.replace(/^http/, 'ws') + '/ws'  // dev: Vite proxies /ws to the backend
}

// A motion message built from a full world (GET /state), for when the socket is not connected.
function motionFromWorld(world, seq) {
  return {
    seq,
    receivedAt: performance.now(),
    latencyS: 0,
    sim_time: world.sim_time,
    sim_speed: world.sim_speed,
    running: world.running,
    paused: world.paused,
    phase: world.phase,
    trucks: (world.trucks || []).map((t) => ({
      id: t.id, lat: t.position.lat, lon: t.position.lon, heading: null, speed_mps: 0,
      route_id: t.route_id ?? null, distance_along_m: t.distance_along_route_m ?? 0, status: t.status,
      approximate: t.route_source === 'fallback',
    })),
  }
}

const WS_STALE_MS = 3000          // config WS_STALE_MS (needed before /config has loaded)
const BACKOFF_MIN_MS = 500
const BACKOFF_MAX_MS = 8000

export default function useWorldState(pollingMs = 5000) {
  const [world, setWorld] = useState(null)
  const [error, setError] = useState(null)
  const [connection, setConnection] = useState('connecting')
  const lastMessageAt = useRef(0)
  const motionRef = useRef(null)
  const localSeq = useRef(-1)   // seq numbers for messages made from GET /state (negative: never clash)
  const wsRef = useRef(null)
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const next = await api.getState()
      if (!alive.current) return
      setWorld(next)
      setError(null)
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        motionRef.current = motionFromWorld(next, localSeq.current--)
      }
    } catch (err) {
      if (alive.current) setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    alive.current = true
    refresh()
    let reconnectTimer = null
    let pollTimer = null
    let backoff = BACKOFF_MIN_MS

    function scheduleReconnect() {
      if (!alive.current || reconnectTimer) return
      reconnectTimer = setTimeout(() => { reconnectTimer = null; connect() }, backoff)
      backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
    }

    function connect() {
      if (!alive.current) return
      let ws
      try {
        ws = new WebSocket(wsUrl())
      } catch {
        scheduleReconnect()
        return
      }
      wsRef.current = ws
      ws.onopen = () => { backoff = BACKOFF_MIN_MS }
      ws.onmessage = (e) => {
        if (!alive.current) return
        let msg
        try { msg = JSON.parse(e.data) } catch { return }
        lastMessageAt.current = Date.now()
        setConnection((c) => (c === 'live' ? c : 'live'))
        const latencyS = Math.min(Math.max(Date.now() / 1000 - (msg.server_time ?? Date.now() / 1000), 0), 0.5)
        const { world: nextWorld, ...motion } = msg
        motionRef.current = { ...motion, receivedAt: performance.now(), latencyS }
        if (nextWorld) {
          setWorld(nextWorld)
          setError(null)
        }
      }
      ws.onerror = () => {}   // onclose follows
      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null
        scheduleReconnect()
      }
    }
    connect()

    // Watchdog: no message for WS_STALE_MS -> show "Reconnecting…"; for twice that -> drop the socket
    // (a half-open connection never fires onclose by itself) and reconnect.
    const watchdog = setInterval(() => {
      if (!alive.current) return
      const silentMs = Date.now() - lastMessageAt.current
      if (lastMessageAt.current && silentMs > WS_STALE_MS) setConnection('reconnecting')
      if (lastMessageAt.current && silentMs > 2 * WS_STALE_MS && wsRef.current) {
        const ws = wsRef.current
        wsRef.current = null
        try { ws.close() } catch { /* already closing */ }
        scheduleReconnect()
      }
    }, 500)

    function poll() {
      if (!alive.current) return
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) refresh()
      pollTimer = setTimeout(poll, pollingMs)
    }
    pollTimer = setTimeout(poll, pollingMs)

    return () => {
      alive.current = false
      clearTimeout(pollTimer)
      clearTimeout(reconnectTimer)
      clearInterval(watchdog)
      wsRef.current?.close()
    }
  }, [refresh, pollingMs])

  return { world, error, refresh, motionRef, connection }
}

/** Simulated time now (minutes from midnight), extrapolated from the last motion message.
 *  simSecondsPerRealSecond is config SIM_SECONDS_PER_REAL_SECOND (the rate at 1x). */
export function liveSimTime(motion, simSecondsPerRealSecond = 1) {
  if (!motion || motion.sim_time == null) return null
  if (motion.phase !== 'EXECUTING' || !motion.running || motion.paused) return motion.sim_time
  const ageS = motion.latencyS + (performance.now() - motion.receivedAt) / 1000
  return motion.sim_time + Math.min(ageS, 1) * motion.sim_speed * simSecondsPerRealSecond / 60
}
