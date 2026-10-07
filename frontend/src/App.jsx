import React, { useEffect, useRef, useState, useCallback } from 'react'
import { api, errorMessage } from './api'
import useConfig from './hooks/useConfig'
import useWorldState from './hooks/useWorldState'
import Topbar from './components/Topbar'
import LeftSidebar from './components/LeftSidebar'
import RightSidebar from './components/RightSidebar'
import MapView from './components/MapView'
import DemandChangeModal from './components/DemandChangeModal'
import HubModal from './components/HubModal'
import DecisionDetailsModal from './components/DecisionDetailsModal'
import CallDriverModal from './components/CallDriverModal'
import HowItWorksModal from './components/HowItWorksModal'
import { ToastStack } from './components/ui'
import './app.css'

export default function App() {
  const { world, error, refresh, motionRef, connection } = useWorldState(2500)
  const config = useConfig()

  // Theme state: dark default + light
  const [theme, setTheme] = useState(() => localStorage.getItem('df_theme') || 'dark')
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('df_theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  // Control Room Settings
  const [settings, setSettings] = useState(null)
  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => {})
  }, [])

  // Sidebars and Drawers state
  const [leftTab, setLeftTab] = useState('hubs')
  const [rightTab, setRightTab] = useState('fleet')
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [isLeftDrawerOpen, setIsLeftDrawerOpen] = useState(false)
  const [isRightDrawerOpen, setIsRightDrawerOpen] = useState(false)

  // Simulation controls
  const [currentSpeed, setCurrentSpeed] = useState(1)
  const [followTruckId, setFollowTruckId] = useState(null)

  // Disruption Tools & Map Click Mode: null | 'add-hub' | 'rain' | 'traffic' | 'roadblock' | 'area'
  const [clickMode, setClickMode] = useState(null)
  const [disruptionParams, setDisruptionParams] = useState({
    rainRadiusKm: 3,
    areaRadiusM: 500,
  })

  // Toasts stack: max 3, auto-dismissing
  const [toasts, setToasts] = useState([])
  const addToast = useCallback((message, type = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((prev) => [...prev.slice(-2), { id, message, type }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4500)
  }, [])

  const dismissToast = (id) => setToasts((prev) => prev.filter((t) => t.id !== id))

  // Modals state: only one active at a time
  const [activeModal, setActiveModal] = useState(null) // { type, data }
  const [preview, setPreview] = useState(null)

  // Roadblock news: a toast per new reroute / alert decision, and the Alerts tab when a new card opens.
  // The simulation never pauses by itself: only the Pause button pauses it.
  const seenDecisions = useRef(null)
  const seenAlerts = useRef(new Set())
  useEffect(() => {
    if (!world) return
    const decisions = world.decisions || []
    const last = decisions.length ? `${decisions[decisions.length - 1].time}|${decisions[decisions.length - 1].summary}` : ''
    if (seenDecisions.current === null) {
      seenDecisions.current = last   // first world: don't replay history as toasts
    } else if (last !== seenDecisions.current) {
      const idx = decisions.findIndex((d) => `${d.time}|${d.summary}` === seenDecisions.current)
      const fresh = idx >= 0 ? decisions.slice(idx + 1) : decisions.slice(-3)
      seenDecisions.current = last
      for (const d of fresh.slice(-3)) {
        if (d.type === 'AUTO_REROUTE') addToast(d.summary, 'info')
        else if (d.type === 'DETOUR_ALERT') addToast(d.summary, 'warning')
        else if (d.type === 'NO_ROUTE' || d.type === 'SIM_ERROR') addToast(d.summary, 'danger')
      }
    }
    const open = (world.alerts || []).filter((a) => a.status === 'OPEN')
    if (open.some((a) => !seenAlerts.current.has(a.id))) setRightTab('alerts')
    seenAlerts.current = new Set(open.map((a) => a.id))
  }, [world, addToast])

  // --- Handlers: Simulation Playback ---
  const handlePlayPause = async () => {
    try {
      if (world?.paused) {
        await api.resumeSimulation()
        addToast('Simulation resumed', 'success')
      } else {
        await api.pauseSimulation()
        addToast('Simulation paused', 'info')
      }
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleRestart = async () => {
    try {
      await api.resetSimulation()
      addToast('Simulation reset to start', 'info')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleSetSpeed = async (speed) => {
    try {
      setCurrentSpeed(speed)
      await api.setSpeed(speed)
      addToast(`Simulation speed set to ${speed}×`, 'info')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  // --- Handlers: Scenario & Fleet Operations ---
  const handleLoadScenario = async () => {
    try {
      await api.loadScenario('chennai-default')
      setLeftTab('hubs')
      addToast('Chennai Default Scenario loaded (8 Hubs, 22,400 L)', 'success')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleStartEmpty = async () => {
    try {
      await api.emptyScenario()
      setLeftTab('setup')
      addToast('Empty scenario initialized', 'info')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleCreatePlan = async () => {
    try {
      await api.createPlan()
      setLeftTab('plan')
      addToast('Optimal road dispatch plan generated', 'success')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleDispatch = async () => {
    try {
      await api.startSimulation()
      addToast('Fleet dispatched! Simulation executing.', 'success')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  // --- Handlers: Map Clicks & Disruptions ---
  const handleAddHubAt = async (lat, lon) => {
    try {
      const snap = await api.snap(lat, lon)
      if (!snap.ok) {
        setPreview(null)
        addToast(snap.message || 'No road access here — pick a road point', 'danger')
        return
      }
      setPreview(snap)
      setActiveModal({
        type: 'hub-modal',
        data: { mode: 'create', position: { lat, lon }, snap },
      })
      setClickMode(null)
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleRainAt = async (lat, lon) => {
    try {
      const rad = disruptionParams.rainRadiusKm || 3
      await api.addRain(lat, lon, rad)
      addToast(`Rain zone placed (${rad} km radius, speed × 0.6)`, 'success')
      setClickMode(null)
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleTrafficAt = async (lat, lon, hit) => {
    try {
      if (hit) {
        await api.addTraffic(lat, lon, hit.truckId, hit.segmentIndex)
        addToast(`Traffic jam placed on ${hit.truckId} route (speed × 0.4)`, 'success')
      } else {
        await api.addTraffic(lat, lon)
        addToast('Traffic jam placed on road stretch (speed × 0.4)', 'success')
      }
      setClickMode(null)
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleRoadblockAt = async (lat, lon, radiusM = 150) => {
    try {
      await api.addRoadblock(lat, lon, radiusM)
      const radDesc = radiusM >= 1000 ? `${(radiusM / 1000).toFixed(1)} km` : `${Math.round(radiusM)} m`
      addToast(`Roadblock placed (${radDesc} closure, road impassable)`, 'warning')
      setClickMode(null)
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  // --- Handlers: Alerts & Decision Resolution ---
  const handleResolveDecision = async (truckId, action, alertId = null) => {
    try {
      await api.resolveDecision(truckId, action, alertId)
      addToast(`${truckId}: ${action.replaceAll('_', ' ')}`, 'success')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleApproveRecommendation = async (recId) => {
    try {
      await api.approveRecommendation(recId)
      addToast('Recommendation approved', 'success')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleRejectRecommendation = async (recId) => {
    try {
      await api.rejectRecommendation(recId)
      addToast('Recommendation rejected', 'info')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  const handleSetPriority = async (hub, priority) => {
    try {
      await api.updateHubPriority(hub.id, priority)
      addToast(`Updated ${hub.name} priority to ${priority}`, 'info')
      refresh()
    } catch (err) {
      addToast(errorMessage(err), 'danger')
    }
  }

  // Hub popup actions
  const hubActions = (hub) => [
    { label: 'Demand', onClick: (h) => setActiveModal({ type: 'demand-change', data: { hub: h } }) },
    { label: 'Edit', onClick: (h) => setActiveModal({ type: 'hub-modal', data: { mode: 'edit', hub: h } }) },
  ]

  return (
    <div className={`df-shell ${leftCollapsed ? 'left-collapsed' : ''} ${rightCollapsed ? 'right-collapsed' : ''}`}>
      {/* Top Bar */}
      <Topbar
        world={world}
        theme={theme}
        onToggleTheme={toggleTheme}
        onOpenHowItWorks={() => setActiveModal({ type: 'how-it-works' })}
        onToggleLeftDrawer={() => setIsLeftDrawerOpen((o) => !o)}
        onToggleRightDrawer={() => setIsRightDrawerOpen((o) => !o)}
        onPlayPause={handlePlayPause}
        onRestart={handleRestart}
        onSetSpeed={handleSetSpeed}
        currentSpeed={world?.sim_speed ?? currentSpeed}
        speedPresets={config?.SIM_SPEED_PRESETS}
        motionRef={motionRef}
        simSecondsPerRealSecond={config?.SIM_SECONDS_PER_REAL_SECOND}
      />

      {/* Left Sidebar (340 px, collapsible) */}
      <LeftSidebar
        world={world}
        activeTab={leftTab}
        onTabChange={setLeftTab}
        collapsed={leftCollapsed}
        onToggleCollapse={() => setLeftCollapsed((c) => !c)}
        isDrawerOpen={isLeftDrawerOpen}
        onCloseDrawer={() => setIsLeftDrawerOpen(false)}
        addHubMode={clickMode === 'add-hub'}
        onToggleAddHub={() => setClickMode((m) => (m === 'add-hub' ? null : 'add-hub'))}
        onLoadScenario={handleLoadScenario}
        onStartEmpty={handleStartEmpty}
        onCreatePlan={handleCreatePlan}
        onDispatch={handleDispatch}
        onChangeDemand={(hub) => setActiveModal({ type: 'demand-change', data: { hub } })}
        onEditHub={(hub) => setActiveModal({ type: 'hub-modal', data: { mode: 'edit', hub } })}
        onSetPriority={handleSetPriority}
      />

      {/* Center Map Area with Docked Toolbar, Legend, & Toasts */}
      <div className="df-map-area">
        <MapView
          world={world}
          theme={theme}
          clickMode={clickMode}
          disruptionParams={disruptionParams}
          onDisruptionParamChange={(k, v) => setDisruptionParams((prev) => ({ ...prev, [k]: v }))}
          onSelectClickMode={(mode) => setClickMode(mode)}
          onCancelClickMode={() => setClickMode(null)}
          preview={preview}
          onAddHubAt={handleAddHubAt}
          onRainAt={handleRainAt}
          onTrafficAt={handleTrafficAt}
          onRoadblockAt={handleRoadblockAt}
          hubActions={hubActions}
          motionRef={motionRef}
          motionConfig={config?.MOTION}
          followTruckId={followTruckId}
        />

        {/* Live link to the backend lost: keep the last picture, say so, reconnect in the background */}
        {connection === 'reconnecting' && (
          <div className="df-reconnecting" role="status" aria-live="polite">
            <span className="df-reconnecting-dot" /> Reconnecting… (showing the last known state)
          </div>
        )}

        {/* Toasts Stack (top-right of map area, max 3) */}
        <ToastStack toasts={toasts} onDismiss={dismissToast} />
      </div>

      {/* Right Sidebar (360 px, collapsible) */}
      <RightSidebar
        world={world}
        activeTab={rightTab}
        onTabChange={setRightTab}
        collapsed={rightCollapsed}
        onToggleCollapse={() => setRightCollapsed((c) => !c)}
        isDrawerOpen={isRightDrawerOpen}
        onCloseDrawer={() => setIsRightDrawerOpen(false)}
        onShowCallDialog={(truck) => setActiveModal({ type: 'call-driver', data: { truck } })}
        onResolveDecision={handleResolveDecision}
        onApproveRecommendation={handleApproveRecommendation}
        onRejectRecommendation={handleRejectRecommendation}
        onOpenDriverView={(truckId) => window.open(`/driver.html?truckId=${truckId}`, '_blank')}
        followTruckId={followTruckId}
        onFollowTruck={setFollowTruckId}
      />

      {/* Modals — Only one active at a time */}
      {activeModal?.type === 'demand-change' && (
        <DemandChangeModal
          hub={activeModal.data.hub}
          world={world}
          controlMode={settings?.control_mode || 'auto'}
          onClose={() => setActiveModal(null)}
          onChanged={refresh}
        />
      )}

      {activeModal?.type === 'hub-modal' && (
        <HubModal
          mode={activeModal.data.mode}
          hub={activeModal.data.hub}
          position={activeModal.data.position}
          snap={activeModal.data.snap}
          config={config}
          canDelete={world?.phase === 'PLANNING'}
          onClose={() => {
            setActiveModal(null)
            setPreview(null)
          }}
          onSaved={() => {
            setActiveModal(null)
            setPreview(null)
            refresh()
          }}
        />
      )}

      {activeModal?.type === 'call-driver' && (
        <CallDriverModal
          truck={activeModal.data.truck}
          onClose={() => setActiveModal(null)}
        />
      )}

      {activeModal?.type === 'how-it-works' && (
        <HowItWorksModal
          onClose={() => setActiveModal(null)}
        />
      )}

      {activeModal?.type === 'decision-details' && (
        <DecisionDetailsModal
          decision={activeModal.data.decision}
          onClose={() => setActiveModal(null)}
        />
      )}
    </div>
  )
}
