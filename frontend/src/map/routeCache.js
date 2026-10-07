// Route geometries by id (GET /routes/{id}), fetched once per id and kept for the session.
// A route: {id, coordinates: [[lon, lat]], cum_m: [m from the first point], length_m, approximate}.
import { api } from '../api'

const routes = new Map()
const pending = new Set()

/** The geometry for routeId if loaded; otherwise starts loading it and returns null. */
export function routeGeometry(routeId) {
  if (!routeId) return null
  const route = routes.get(routeId)
  if (route) return route
  if (!pending.has(routeId)) {
    pending.add(routeId)
    api.getRoute(routeId)
      .then((r) => routes.set(routeId, r))
      .catch(() => {})  // retried on the next message that names it
      .finally(() => pending.delete(routeId))
  }
  return null
}
