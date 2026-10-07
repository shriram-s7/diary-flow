/**
 * TruckAnimator: smooth motion for one truck, driven by /ws motion messages (5 Hz).
 *
 * The truck's position is a distance along its leg's route polyline (GET /routes/{id}), never a free
 * lat/lon, so it always stays on the road:
 *   - every frame it advances by speed_mps x sim speed x real dt (dead reckoning);
 *   - when a message arrives, the server's distance (extrapolated from server_time to now) is compared
 *     with ours and the difference is eased in over EASE_MS; only an error above TELEPORT_M jumps;
 *   - a new route (next leg, or a reroute) blends from the drawn position over EASE_MS;
 *   - the icon's heading follows the route bearing, eased over HEADING_EASE_MS.
 * While the truck is not driving (unloading, holding) it stands at the server position.
 */
import { bearingDeg, distanceM } from './geo'

const DRIVING = new Set(['EN_ROUTE', 'RETURNING'])
const MAX_MESSAGE_AGE_S = 0.5     // clamp for clock skew between server and browser
const REROUTE_MIN_REMAINING_M = 30  // a route change with less than this left is just the next leg
const HEADING_LOOKAHEAD_M = 20

// Sine ease-in-out: peak rate ~1.6x the average, so a correction never lurches in its first frame.
const ease = (t) => (1 - Math.cos(Math.PI * t)) / 2
const angleDiff = (a, b) => ((b - a) % 360 + 540) % 360 - 180

export default class TruckAnimator {
  constructor(id, cfg = {}) {
    this.id = id
    this.EASE_MS = cfg.EASE_MS ?? 300
    this.TELEPORT_M = cfg.TELEPORT_M ?? 500
    this.HEADING_EASE_MS = cfg.HEADING_EASE_MS ?? 250

    this.route = null        // {id, coordinates: [[lon, lat]], cum_m: [m], length_m, approximate}
    this.d = 0               // our distance along this.route (m)
    this.speedMps = 0        // simulated m/s on the current segment
    this.status = null
    this.serverPos = null    // [lon, lat] of the last message (used while not driving / no geometry yet)
    this.corr = null         // {total, applied, t}: distance correction being eased in
    this.blend = null        // {from: [lon, lat], t}: position blend after a discontinuity
    this.pos = null          // drawn [lon, lat]
    this.heading = null      // drawn heading (deg)
    this.lastSeq = -1
    this.lastJumpM = 0       // distance moved in the last frame (for the smoothness check)
  }

  get driving() {
    return DRIVING.has(this.status) && this.route !== null
  }

  get travelled() {
    return this.route && this.route.length_m > 0 ? Math.min(1, this.d / this.route.length_m) : 0
  }

  /**
   * Apply one motion message. `route` is the geometry for msg.route_id if already loaded (else null),
   * `ageS` how long ago (real seconds) the server state was true, `simSpeed` the sim speed multiplier.
   * Returns {oldRemaining} when the truck was rerouted mid-leg, else null.
   */
  onMessage(msg, route, ageS, simSpeed) {
    const prevRoute = this.route
    const wasDriving = this.driving
    this.status = msg.status
    this.speedMps = msg.speed_mps || 0
    this.serverPos = [msg.lon, msg.lat]
    let reroute = null

    if (this.pos === null) this.pos = [msg.lon, msg.lat]

    if (DRIVING.has(msg.status) && route) {
      const age = Math.min(Math.max(ageS, 0), MAX_MESSAGE_AGE_S)
      const target = Math.min(route.length_m, msg.distance_along_m + this.speedMps * simSpeed * age)
      if (!prevRoute || prevRoute.id !== route.id) {
        if (wasDriving && prevRoute && prevRoute.length_m - this.d > REROUTE_MIN_REMAINING_M) {
          reroute = { oldRemaining: this.remaining() }
        }
        this.route = route
        this.d = target
        this.corr = null
        this.startBlend()
      } else {
        const err = target - this.d
        if (Math.abs(err) > this.TELEPORT_M) {
          this.d = target
          this.corr = null
          this.blend = null
        } else {
          this.corr = { total: err, applied: 0, t: 0 }
        }
      }
    } else if (DRIVING.has(msg.status) && this.route !== null && msg.route_id !== this.route.id) {
      // The new route's geometry is still loading: carry on along the old one until it arrives
      // (the caller re-applies this message then), so a reroute is still recognised.
    } else {
      // Not driving (or no geometry at all yet): stand at the server position.
      if (this.route !== null) this.startBlend()
      this.route = null
      this.corr = null
      if (distanceM(this.pos, this.serverPos) > this.TELEPORT_M) {
        this.pos = this.serverPos
        this.blend = null
      } else if (!this.blend && distanceM(this.pos, this.serverPos) > 0.5) {
        this.startBlend()
      }
    }
    if (this.heading === null && msg.heading != null) this.heading = msg.heading
    return reroute
  }

  /** Advance by dtMs of real time. `live` is false while paused / stopped. Returns the drawn [lon, lat]. */
  frame(dtMs, simSpeed, live) {
    const before = this.pos
    let target
    if (this.driving) {
      if (live) this.d += this.speedMps * simSpeed * (dtMs / 1000)
      if (this.corr) {
        this.corr.t = Math.min(this.corr.t + dtMs, this.EASE_MS)
        const want = this.corr.total * ease(this.corr.t / this.EASE_MS)
        this.d += want - this.corr.applied
        this.corr.applied = want
        if (this.corr.t >= this.EASE_MS) this.corr = null
      }
      this.d = Math.max(0, Math.min(this.d, this.route.length_m))
      target = this.positionAt(this.d)
      this.easeHeading(this.bearingAt(this.d), dtMs)
    } else {
      target = this.serverPos ?? this.pos
    }

    if (this.blend) {
      this.blend.t = Math.min(this.blend.t + dtMs, this.EASE_MS)
      const k = ease(this.blend.t / this.EASE_MS)
      const from = this.blend.from
      target = [from[0] + (target[0] - from[0]) * k, from[1] + (target[1] - from[1]) * k]
      if (this.blend.t >= this.EASE_MS) this.blend = null
    }
    this.pos = target
    this.lastJumpM = before ? distanceM(before, target) : 0
    return this.pos
  }

  /** The not-yet-driven part of the current route, from the truck: [[lon, lat]]. */
  remaining() {
    if (!this.route) return []
    const i = this.segmentIndex(this.d)
    return [this.positionAt(this.d), ...this.route.coordinates.slice(i + 1)]
  }

  // --- internals -------------------------------------------------------------

  startBlend() {
    if (this.pos) this.blend = { from: this.pos, t: 0 }
  }

  easeHeading(want, dtMs) {
    if (want === null) return
    if (this.heading === null) {
      this.heading = want
      return
    }
    const k = 1 - Math.exp(-dtMs / this.HEADING_EASE_MS)
    this.heading = (this.heading + angleDiff(this.heading, want) * k + 360) % 360
  }

  /** Index i of the segment [i, i + 1] that contains distance d (binary search on cum_m). */
  segmentIndex(d) {
    const cum = this.route.cum_m
    let lo = 0
    let hi = cum.length - 1
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (cum[mid] <= d) lo = mid
      else hi = mid
    }
    return lo
  }

  positionAt(d) {
    const { coordinates: c, cum_m: cum } = this.route
    if (c.length === 1) return c[0]
    const i = this.segmentIndex(d)
    const seg = cum[i + 1] - cum[i]
    const f = seg > 0 ? Math.min(1, Math.max(0, (d - cum[i]) / seg)) : 0
    return [c[i][0] + (c[i + 1][0] - c[i][0]) * f, c[i][1] + (c[i + 1][1] - c[i][1]) * f]
  }

  bearingAt(d) {
    const len = this.route.length_m
    const a = this.positionAt(Math.min(d, Math.max(0, len - HEADING_LOOKAHEAD_M)))
    const b = this.positionAt(Math.min(len, d + HEADING_LOOKAHEAD_M))
    return distanceM(a, b) > 0.5 ? bearingDeg(a, b) : null
  }
}
