// Small spherical-earth helpers on [lon, lat] pairs (the same formulas as the backend's routing.py).
const R = 6_371_000
const RAD = Math.PI / 180

export function distanceM(a, b) {
  const dlat = (b[1] - a[1]) * RAD
  const dlon = (b[0] - a[0]) * RAD
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.sin(dlon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

// Initial compass bearing (degrees, 0 = north) from a to b.
export function bearingDeg(a, b) {
  const p1 = a[1] * RAD
  const p2 = b[1] * RAD
  const dl = (b[0] - a[0]) * RAD
  const x = Math.sin(dl) * Math.cos(p2)
  const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
  return (Math.atan2(x, y) / RAD + 360) % 360
}
