// Every backend call in one place. Components never build URLs themselves.
import axios from 'axios'

// Same-origin '/api' (and '/ws'): the Vite dev server proxies both to the backend at 127.0.0.1:DAIRYFLOW_API_PORT
// (vite.config.js), on every device, so there is exactly one backend address to get right.
export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// A request never hangs the UI: after 10 s it fails with a message (the live view keeps running on /ws).
const http = axios.create({ baseURL: API_BASE, timeout: 10000 })
const data = (request) => request.then((res) => res.data)
const id = (value) => encodeURIComponent(value)

function upload(path, file) {
  const form = new FormData()
  form.append('file', file)
  return data(http.post(path, form))
}

// The backend's own message for a failed request (400/404/409 carry a readable `detail`).
export function errorMessage(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  return err?.message || 'Request failed'
}

export const api = {
  // World
  getConfig: () => data(http.get('/config')),
  getState: () => data(http.get('/state')),
  getRoute: (routeId) => data(http.get(`/routes/${id(routeId)}`)),
  getNetwork: () => data(http.get('/network')),
  getDirections: (routeId) => data(http.get(`/routes/${id(routeId)}/directions`)),

  // Scenario
  listScenarios: () => data(http.get('/scenarios')),
  loadScenario: (scenarioId) => data(http.post(`/scenarios/${id(scenarioId)}/load`)),
  emptyScenario: () => data(http.post('/scenario/empty')),
  snap: (lat, lon) => data(http.get('/snap', { params: { lat, lon } })),
  uploadHubs: (file) => upload('/upload/hubs', file),
  uploadTrucks: (file) => upload('/upload/trucks', file),

  // Plan
  createPlan: () => data(http.post('/plan')),
  getPlan: () => data(http.get('/plan')),

  // Simulation
  startSimulation: () => data(http.post('/simulation/start')),
  stopSimulation: () => data(http.post('/simulation/stop')),
  pauseSimulation: () => data(http.post('/simulation/pause')),
  resumeSimulation: () => data(http.post('/simulation/resume')),
  resetSimulation: () => data(http.post('/simulation/reset')),
  setSpeed: (speed) => data(http.post('/simulation/speed', { speed })),
  getSummary: () => data(http.get('/summary')),

  // Hubs
  createHub: (hub) => data(http.post('/hubs/manual', hub)),
  updateHubDemand: (hubId, demand_l) => data(http.put(`/hubs/${id(hubId)}/demand`, { demand_l })),
  updateHubWindow: (hubId, window_end) => data(http.put(`/hubs/${id(hubId)}/window`, { window_end })),
  updateHubPriority: (hubId, priority) => data(http.put(`/hubs/${id(hubId)}/priority`, { priority })),
  deleteHub: (hubId) => data(http.delete(`/hubs/${id(hubId)}`)),

  // Control tower
  getSettings: () => data(http.get('/settings')),
  updateSettings: (settings) => data(http.put('/settings', settings)),
  getRecommendations: () => data(http.get('/recommendations')),
  approveRecommendation: (recId) => data(http.post(`/recommendations/${id(recId)}/approve`)),
  rejectRecommendation: (recId) => data(http.post(`/recommendations/${id(recId)}/reject`)),

  // Trucks
  createTruck: (truckId, capacity_l) => data(http.post('/trucks', { id: truckId, capacity_l })),
  updateTruck: (truckId, capacity_l) => data(http.put(`/trucks/${id(truckId)}`, { capacity_l })),
  deleteTruck: (truckId) => data(http.delete(`/trucks/${id(truckId)}`)),
  sendInstruction: (truckId, instruction) =>
    data(http.post(`/trucks/${id(truckId)}/instruction`, { instruction })),
  acknowledgeInstruction: (truckId) => data(http.post(`/trucks/${id(truckId)}/acknowledge-instruction`)),

  // Decisions
  // Manager's answer to a roadblock alert card (keep_detour, skip_stop, reassign_stop, return_to_plant,
  // wait_for_clearance, call_driver); alert_id picks the card (default: the truck's latest open one).
  resolveDecision: (truck_id, action, alert_id = null) =>
    data(http.post('/decisions/resolve', { truck_id, action, alert_id })),

  // Impacts
  addRain: (lat, lon, radius_km) => data(http.post('/impacts/rain', { lat, lon, radius_km })),
  addTraffic: (lat, lon, truck_id = null, segment_index = null) =>
    data(http.post('/impacts/traffic', { lat, lon, truck_id, segment_index })),
  addRoadblock: (lat, lon, radius_m = 150) =>
    data(http.post('/impacts/roadblock', { lat, lon, radius_m })),
  removeImpact: (impactId) => data(http.delete(`/impacts/${id(impactId)}`)),
}

