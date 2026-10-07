import React, { useEffect, useRef, useState } from 'react'
import { clockHMS } from '../format'
import { liveSimTime } from '../hooks/useWorldState'
import { Icon } from './Icons'
import { Button } from './ui'

const FALLBACK_PRESETS = [1, 5, 15, 30, 60, 120]  // until GET /config (SIM_SPEED_PRESETS) arrives

// HH:MM:SS, ticking between /ws messages (extrapolated from the last one at the current sim speed).
function LiveClock({ world, motionRef, simSecondsPerRealSecond }) {
  const [, setFrame] = useState(0)
  const shown = useRef(null)
  useEffect(() => {
    const timer = setInterval(() => setFrame((n) => n + 1), 100)
    return () => clearInterval(timer)
  }, [])
  let minutes = liveSimTime(motionRef?.current, simSecondsPerRealSecond ?? 1) ?? world?.sim_time
  // Never tick backwards on a small correction (a reset moves the clock back by much more than a minute).
  if (minutes != null && shown.current != null && minutes < shown.current && shown.current - minutes < 1) {
    minutes = shown.current
  }
  shown.current = minutes
  return <span data-testid="sim-clock">{minutes != null ? clockHMS(minutes) : '—:—:—'}</span>
}

export default function Topbar({
  world,
  theme = 'dark',
  onToggleTheme,
  onOpenHowItWorks,
  onToggleLeftDrawer,
  onToggleRightDrawer,
  onPlayPause,
  onRestart,
  onSetSpeed,
  currentSpeed = 1,
  speedPresets,
  motionRef,
  simSecondsPerRealSecond,
}) {
  const isExecuting = world?.phase === 'EXECUTING'
  const isPaused = world?.paused || false

  return (
    <header className="df-topbar">
      {/* Left: Mobile Drawer Toggle + Logo + Scenario */}
      <div className="df-topbar-left">
        <Button
          variant="ghost"
          size="sm"
          icon="sidebar-left"
          iconOnly
          title="Toggle Hubs/Setup Drawer"
          onClick={onToggleLeftDrawer}
          className="df-drawer-btn"
        />

        <div className="df-logo-group">
          <div className="df-logo-icon">
            <Icon name="milk-drop" size={18} color="white" />
          </div>
          <span className="df-logo-text">DairyFlow</span>
        </div>

        <span className="df-scenario-chip">
          Chennai Primary Layer
        </span>
      </div>

      {/* Center: Live Clock + Speed Presets + Play/Pause/Restart */}
      <div className="df-topbar-center">
        {/* Clock with live pulse dot */}
        <div className="df-clock-badge">
          <span className={`df-pulse-dot ${!isExecuting || isPaused ? 'paused' : ''}`} />
          <LiveClock world={world} motionRef={motionRef} simSecondsPerRealSecond={simSecondsPerRealSecond} />
        </div>

        {/* Speed presets segmented buttons */}
        <div className="df-speed-presets" role="group" aria-label="Simulation speed">
          {(speedPresets || FALLBACK_PRESETS).map((spd) => (
            <button
              key={spd}
              type="button"
              className={`df-speed-btn ${currentSpeed === spd ? 'active' : ''}`}
              aria-pressed={currentSpeed === spd}
              onClick={() => onSetSpeed(spd)}
              title={`Set speed to ${spd}x`}
            >
              {spd}&times;
            </button>
          ))}
        </div>

        {/* Playback Controls */}
        <div className="df-playback-group">
          <Button
            variant={isPaused ? 'success' : 'secondary'}
            size="sm"
            icon={isPaused ? 'play' : 'pause'}
            iconOnly
            title={isPaused ? 'Resume Simulation' : 'Pause Simulation'}
            onClick={onPlayPause}
          />
          <Button
            variant="ghost"
            size="sm"
            icon="restart"
            iconOnly
            title="Reset Simulation"
            onClick={onRestart}
          />
        </div>
      </div>

      {/* Right: How it works + Theme toggle + Right Drawer toggle (the sim pauses only via the Pause button) */}
      <div className="df-topbar-right">
        <Button
          variant="ghost"
          size="sm"
          icon="help"
          onClick={onOpenHowItWorks}
        >
          How it works
        </Button>

        <Button
          variant="ghost"
          size="sm"
          icon={theme === 'dark' ? 'sun' : 'moon'}
          iconOnly
          title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} Theme`}
          onClick={onToggleTheme}
        />

        <Button
          variant="ghost"
          size="sm"
          icon="sidebar-right"
          iconOnly
          title="Toggle Fleet/Alerts Drawer"
          onClick={onToggleRightDrawer}
          className="df-drawer-btn"
        />
      </div>
    </header>
  )
}
