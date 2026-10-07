import React from 'react'
import { clock, litres, km, rupees, truckIndex } from '../format'
import { Icon } from './Icons'
import { Button, Tabs, Chip, Card, Progress, RiskBadge } from './ui'

export default function LeftSidebar({
  world,
  activeTab,
  onTabChange,
  collapsed,
  onToggleCollapse,
  isDrawerOpen,
  onCloseDrawer,
  addHubMode,
  onToggleAddHub,
  onLoadScenario,
  onStartEmpty,
  onCreatePlan,
  onDispatch,
  onChangeDemand,
  onSetPriority,
  onEditHub,
}) {
  const hubs = world?.hubs || []
  const plant = world?.plant
  const phase = world?.phase || 'PLANNING'
  const isExecuting = phase === 'EXECUTING'

  const priorityVariant = (p) => {
    if (p === 'Critical') return 'danger'
    if (p === 'High') return 'warning'
    return 'neutral'
  }

  return (
    <>
      {/* Mobile Drawer Backdrop */}
      {isDrawerOpen && (
        <div className="df-drawer-backdrop" onClick={onCloseDrawer} />
      )}

      <aside
        className={`df-sidebar df-sidebar-left ${collapsed ? 'collapsed' : ''} ${
          isDrawerOpen ? 'drawer-open' : ''
        }`}
      >
        {/* Sidebar Header with Tabs & Collapse Button */}
        <div className="df-sidebar-header">
          <Tabs
            tabs={[
              { id: 'setup', label: 'Setup', icon: 'settings' },
              { id: 'hubs', label: `Hubs (${hubs.length})`, icon: 'map-pin' },
              { id: 'plan', label: 'Plan', icon: 'layers' },
            ]}
            activeTab={activeTab}
            onChange={onTabChange}
          />
          <Button
            variant="ghost"
            size="sm"
            icon={collapsed ? 'chevron-right' : 'chevron-left'}
            iconOnly
            title={collapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
            onClick={onToggleCollapse}
          />
        </div>

        {/* Sidebar Scrollable Body */}
        <div className="df-sidebar-content">
          {/* --- TAB: SETUP --- */}
          {activeTab === 'setup' && (
            <>
              {plant && (
                <Card title={plant.name} subtitle="Primary Source Plant" icon="plant">
                  <div className="df-card-row">
                    <span>Milk Available:</span>
                    <strong className="df-text-accent df-font-mono">
                      {litres(plant.milk_available_l)}
                    </strong>
                  </div>
                  <div className="df-card-row">
                    <span>Coordinates:</span>
                    <span className="df-font-mono">
                      {plant.road_lat?.toFixed(4)}, {plant.road_lon?.toFixed(4)}
                    </span>
                  </div>
                  <div className="df-card-row">
                    <span>Fleet Size:</span>
                    <strong>{world?.trucks?.length || 6} vehicles</strong>
                  </div>
                </Card>
              )}

              <Card title="Scenario Management" icon="settings">
                <div className="df-flex-col df-gap-2">
                  <Button
                    variant="primary"
                    icon="refresh"
                    onClick={onLoadScenario}
                    disabled={isExecuting}
                  >
                    Load Default Chennai Scenario
                  </Button>
                  <Button
                    variant="secondary"
                    icon="layers"
                    onClick={onStartEmpty}
                    disabled={isExecuting}
                  >
                    Start Empty Session
                  </Button>
                  <Button
                    variant={addHubMode ? 'warning' : 'outline'}
                    icon="map-pin"
                    onClick={onToggleAddHub}
                  >
                    {addHubMode ? 'Placing Hub on Map...' : '+ Add Custom Hub via Map'}
                  </Button>
                </div>
              </Card>

              <Card title="System Rules & Timing" icon="info">
                <div className="df-text-xs df-text-secondary df-lh-normal">
                  <p>• Dispatch window begins at <strong>03:00</strong>.</p>
                  <p>• Default delivery window ends at <strong>06:00</strong>.</p>
                  <p>• Unloading rate: 100 L/min + 10 min dock time.</p>
                  <p>• Vehicle speed: 30 km/h (reduced by rain and traffic).</p>
                </div>
              </Card>
            </>
          )}

          {/* --- TAB: HUBS (Cards layout, NOT cramped table) --- */}
          {activeTab === 'hubs' && (
            <>
              <div className="df-flex-between">
                <span className="df-section-header">
                  REGIONAL HUBS ({hubs.length})
                </span>
                <Button
                  variant={addHubMode ? 'warning' : 'outline'}
                  size="sm"
                  icon="map-pin"
                  onClick={onToggleAddHub}
                >
                  {addHubMode ? 'Placing...' : '+ Add Hub'}
                </Button>
              </div>

              {hubs.length === 0 && (
                <div className="df-empty-notice">
                  No hubs created yet. Load scenario or click map to add a hub.
                </div>
              )}

              {hubs.map((hub) => {
                const isUnreachable = hub.status === 'UNREACHABLE'
                const isCompleted = hub.status === 'COMPLETED'
                const pct = hub.demand_l > 0 ? (hub.delivered_l / hub.demand_l) * 100 : 0

                return (
                  <Card key={hub.id} className={isUnreachable ? 'df-card-danger' : ''}>
                    <div className="df-card-header">
                      <div className="df-card-title">
                        <Icon name="hub" size={16} color={isCompleted ? 'var(--color-success)' : 'var(--color-accent)'} />
                        <span>{hub.name}</span>
                      </div>
                      <div className="df-flex df-gap-1">
                        <Chip variant={priorityVariant(hub.priority)}>{hub.priority}</Chip>
                        {isUnreachable ? (
                          <Chip variant="unreachable">UNREACHABLE</Chip>
                        ) : (
                          <RiskBadge risk={hub.risk} />
                        )}
                      </div>
                    </div>

                    <div className="df-card-body">
                      {isUnreachable && (
                        <div className="df-error-box df-mb-2">
                          Blocked: {litres(hub.remaining_l)} at risk
                        </div>
                      )}

                      {/* Delivered / Demand Progress Bar */}
                      <Progress
                        value={hub.delivered_l}
                        max={hub.demand_l}
                        label={`Delivered (${Math.round(pct)}%)`}
                        valueText={`${litres(hub.delivered_l)} / ${litres(hub.demand_l)}`}
                        variant={isCompleted ? 'success' : isUnreachable ? 'danger' : 'primary'}
                      />

                      <div className="df-card-row">
                        <span>Window Deadline:</span>
                        <strong className="df-font-mono">{clock(hub.window_end)}</strong>
                      </div>

                      {/* Per-truck deliveries breakdown */}
                      <div className="df-mt-1">
                        <div className="df-text-2xs df-text-muted df-mb-1">
                          ASSIGNED DELIVERIES
                        </div>
                        {(!hub.deliveries || hub.deliveries.length === 0) ? (
                          <div className="df-text-xs df-text-muted">
                            Unscheduled
                          </div>
                        ) : (
                          <div className="df-flex-col df-gap-1">
                            {hub.deliveries.map((del, dIdx) => {
                              const tIdx = truckIndex(del.truck_id)
                              return (
                                <div
                                  key={dIdx}
                                  className={`df-hub-delivery-item df-truck-border-${tIdx}`}
                                >
                                  <strong className={`df-truck-color-${tIdx}`}>{del.truck_id}</strong>
                                  <span>{litres(del.drop_l)}</span>
                                  <span className="df-font-mono df-text-secondary">
                                    {del.arrived_at != null
                                      ? `Unloading ${clock(del.eta)}`
                                      : del.completed_at != null
                                      ? `Delivered ${clock(del.completed_at)}`
                                      : `ETA ${clock(del.eta)}`}
                                  </span>
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>

                      {/* Hub Actions Buttons */}
                      <div className="df-flex df-gap-1-5 df-mt-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          icon="edit"
                          className="df-flex-1"
                          onClick={() => onChangeDemand(hub)}
                        >
                          Demand
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          icon="priority"
                          className="df-flex-1"
                          onClick={() => {
                            const nextPriority = hub.priority === 'Normal' ? 'High' : hub.priority === 'High' ? 'Critical' : 'Normal'
                            onSetPriority?.(hub, nextPriority)
                          }}
                        >
                          Priority
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon="settings"
                          onClick={() => onEditHub(hub)}
                          title="Edit Hub Details"
                        />
                      </div>
                    </div>
                  </Card>
                )
              })}
            </>
          )}

          {/* --- TAB: PLAN --- */}
          {activeTab === 'plan' && (
            <>
              <div className="df-section-header">
                DISPATCH PLAN
              </div>

              {world?.plan ? (
                <>
                  <Card title="Plan Metrics Overview" icon="layers">
                    <div className="df-card-row">
                      <span>Total Demand:</span>
                      <strong className="df-font-mono">
                        {litres(hubs.reduce((acc, h) => acc + (h.demand_l || 0), 0))}
                      </strong>
                    </div>
                    <div className="df-card-row">
                      <span>Active Trucks:</span>
                      <strong>
                        {world.trucks.filter((t) => t.plan && t.plan.length > 0).length} of {world.trucks.length}
                      </strong>
                    </div>
                    <div className="df-card-row">
                      <span>Total Distance:</span>
                      <strong className="df-font-mono">
                        {km(world.trucks.reduce((acc, t) => acc + (t.km_driven || 0), 0))}
                      </strong>
                    </div>
                    <div className="df-card-row">
                      <span>Est. Fuel:</span>
                      <strong className="df-font-mono">
                        {litres(world.trucks.reduce((acc, t) => acc + (t.km_driven ? t.km_driven / 5 : 0), 0))}
                      </strong>
                    </div>
                    <div className="df-card-row">
                      <span>Est. Cost:</span>
                      <strong className="df-font-mono df-text-success">
                        {rupees(world.trucks.reduce((acc, t) => acc + (t.km_driven ? t.km_driven * 18 : 0), 0))}
                      </strong>
                    </div>
                  </Card>

                  {phase === 'PLANNING' && (
                    <Button
                      variant="success"
                      size="lg"
                      icon="play"
                      className="df-w-full df-mt-2"
                      onClick={onDispatch}
                    >
                      Dispatch Fleet (Start Sim)
                    </Button>
                  )}
                </>
              ) : (
                <Card title="No Plan Generated" icon="info">
                  <p className="df-text-xs df-text-secondary">
                    Add regional distribution hubs or load default scenario, then click below to generate an optimal road-based multi-truck plan.
                  </p>
                  <Button
                    variant="primary"
                    icon="layers"
                    onClick={onCreatePlan}
                    className="df-mt-3"
                  >
                    Generate Optimal Plan
                  </Button>
                </Card>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}
