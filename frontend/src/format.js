// Display formatting only. Every number shown comes from the backend; nothing here computes ETAs,
// slack, risk, distances or costs. clock() rounds halves up, like the backend's format_clock.

export function clock(minutes) {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '—'
  const total = ((Math.round(minutes) % 1440) + 1440) % 1440
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

// HH:MM:SS — for the running simulation clock display (sim_time is float minutes-from-midnight).
export function clockHMS(minutes) {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '—:—:—'
  const totalSec = Math.floor(((minutes % 1440) + 1440) % 1440 * 60)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// "HH:MM" from an <input type="time"> -> minutes from midnight.
export function parseClock(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value || '')
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

const n0 = (x) => Math.round(x).toLocaleString('en-IN')

export const litres = (x) => (x === null || x === undefined ? '—' : `${n0(x)} L`)
export const rupees = (x) => (x === null || x === undefined ? '—' : `₹${n0(x)}`)
export const km = (x) => (x === null || x === undefined ? '—' : `${x.toFixed(1)} km`)
export const pct = (x) => (x === null || x === undefined ? '—' : `${Math.round(x)}%`)
export const minutes = (x) => (x === null || x === undefined ? '—' : `${x >= 0 ? '' : '−'}${Math.abs(Math.round(x))} min`)

// Colours for the backend's risk labels using CSS variables.
export const RISK_COLORS = {
  Low: 'var(--color-success)',
  Medium: 'var(--color-warning)',
  High: 'var(--color-danger)',
  Late: 'var(--color-unreachable)',
}
export const riskColor = (risk) => RISK_COLORS[risk] || 'var(--text-muted)'

export function truckIndex(truckId) {
  if (!truckId) return 0
  const match = String(truckId).match(/\d+/)
  if (match) {
    const num = parseInt(match[0], 10)
    return Math.max(0, (num - 1) % 8)
  }
  return 0
}

export const STATUS_LABELS = {
  RESERVE: 'Reserve',
  LOADING: 'Loading',
  EN_ROUTE: 'En route',
  UNLOADING: 'Unloading',
  RETURNING: 'Returning',
  AT_PLANT: 'At plant',
  BLOCKED: 'Blocked (no route)',
}
export const statusLabel = (status) => STATUS_LABELS[status] || status


export const hubName = (world, hubId) => world?.hubs?.find((h) => h.id === hubId)?.name || hubId

// Pending stops of a truck, in order (the first one is the current stop).
export const pendingStops = (truck) => (truck?.plan || []).filter((s) => s.status === 'PENDING')

// --- Stop wording, shared by the manager table, the hub popup and the driver view --------
// Works on a truck Stop or a hub delivery (same fields). Never shows an ETA in the past: once a truck has
// arrived the stop shows unloading progress, then the delivery time.

const n0s = (x) => Math.round(x).toLocaleString('en-IN')

export function stopState(stop) {
  if (stop.completed_at != null) return 'delivered'
  if (stop.arrived_at != null) return 'unloading'
  return 'upcoming'
}

// "Unloading — 640/1,500 L, 6 min left" · "Delivered 05:12" · "ETA 04:16"
export function stopStatusText(stop) {
  const state = stopState(stop)
  if (state === 'delivered') {
    const short = Math.round(stop.delivered_l) < Math.round(stop.drop_l) ? ` (${n0s(stop.delivered_l)} of ${n0s(stop.drop_l)} L)` : ''
    return `Delivered ${clock(stop.completed_at)}${short}`
  }
  if (state === 'unloading') {
    return `Unloading — ${n0s(stop.delivered_l)}/${n0s(stop.drop_l)} L, ${Math.ceil(stop.unload_min_left ?? 0)} min left`
  }
  return `ETA ${clock(stop.eta)}`
}

// One truck's delivery at a hub: "TRK-03 1,500 L @ 04:16"
export function deliveryText(delivery) {
  const state = stopState(delivery)
  if (state === 'upcoming') return `${delivery.truck_id} ${litres(delivery.drop_l)} @ ${clock(delivery.eta)}`
  return `${delivery.truck_id} ${litres(delivery.drop_l)} · ${stopStatusText(delivery)}`
}

export const deliveriesText = (hub) => (hub.deliveries || []).map(deliveryText).join(' · ')
