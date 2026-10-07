import React from 'react'
import { Modal, Button } from './ui'
import { Icon } from './Icons'

export default function HowItWorksModal({ onClose }) {
  return (
    <Modal
      title="How DairyFlow Works"
      icon="info"
      onClose={onClose}
      size="lg"
      footer={<Button variant="primary" onClick={onClose}>Got it</Button>}
    >
      <div className="df-flex-col df-gap-3 df-text-xs df-lh-normal df-text-secondary">
        <div>
          <h4 className="df-how-title">
            <Icon name="plant" size={16} color="var(--color-accent)" />
            Primary Layer Milk Distribution
          </h4>
          <p>
            DairyFlow plans and simulates the early-morning delivery layer across Chennai: the Madhavaram processing plant dispatches refrigerated milk trucks to regional distribution hubs before the <strong>06:00</strong> morning delivery cutoff.
          </p>
        </div>

        <div>
          <h4 className="df-how-title">
            <Icon name="layers" size={16} color="var(--color-success)" />
            Real Road Routing & Constraints
          </h4>
          <p>
            All distances and routes use real OSRM road geometry snapped to street points. Capacity is a hard constraint, and stops are sequenced for the shortest tour while respecting tight delivery windows.
          </p>
        </div>

        <div>
          <h4 className="df-how-title">
            <Icon name="roadblock" size={16} color="var(--color-danger)" />
            Live Disruption Engine
          </h4>
          <p>
            Click anywhere on the map using the docked disruption toolbar to place:
          </p>
          <ul className="df-how-list">
            <li><strong>Rain zones (1–8 km)</strong>: speeds drop by 40% (speed &times; 0.6).</li>
            <li><strong>Traffic jams (~1 km)</strong>: road speeds drop by 60% (speed &times; 0.4).</li>
            <li><strong>Roadblocks & Area Closures</strong>: impassable closures forcing trucks to seek alternatives or via-point detours.</li>
          </ul>
        </div>

        <div>
          <h4 className="df-how-title">
            <Icon name="alert" size={16} color="var(--color-warning)" />
            Roadblocks: reroute first, ask later
          </h4>
          <p>
            A truck learns about a closure 5 sim minutes after it is placed, or within 300 m of it. The detour is
            searched in the background while the truck keeps driving, and it is taken <strong>at once</strong>
            (<strong>AUTO_REROUTE</strong>). If it costs more than 15 min or 30% extra km, or makes a stop late,
            you also get an alert card: keep the detour (default), hand the stop to another truck or the reserve,
            or skip it and notify the hub. If no route exists the truck stops safely (<strong>BLOCKED</strong>) with
            a red alert; clearing the closure lets it drive on immediately. The simulation only pauses when you
            press Pause.
          </p>
        </div>
      </div>
    </Modal>
  )
}
