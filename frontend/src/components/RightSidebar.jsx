import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { clock, clockHMS, litres, km, rupees, statusLabel, pendingStops, hubName, truckIndex } from '../format'
import { Icon } from './Icons'
import { Button, Card, Chip, Progress, RiskBadge, Tabs } from './ui'

export default function RightSidebar({
  world,
  activeTab,
  onTabChange,
  collapsed,
  onToggleCollapse,
  isDrawerOpen,
  onCloseDrawer,
  onShowCallDialog,
  onResolveDecision,
  onApproveRecommendation,
  onRejectRecommendation,
  onOpenDriverView,
  followTruckId,
  onFollowTruck,
}) {
  const [expandedDecisions, setExpandedDecisions] = useState(new Set())

  // Driver app links for phones on the same Wi-Fi (this PC's LAN address, from the backend)
  const [driverBase, setDriverBase] = useState(null)
  const [copied, setCopied] = useState(null)
  useEffect(() => {
    api.getNetwork().then((n) => setDriverBase(n.driver_urls?.[0] || null)).catch(() => {})
  }, [])
  const driverUrl = (truckId) => `${driverBase || `${window.location.origin}/driver.html`}?truckId=${encodeURIComponent(truckId)}`
  const copyDriverUrl = async (truckId) => {
    try {
      await navigator.clipboard.writeText(driverUrl(truckId))
      setCopied(truckId)
      setTimeout(() => setCopied(null), 1500)
    } catch { /* clipboard blocked: the link is shown on screen anyway */ }
  }

  const toggleDecisionExpand = (idx) => {
    setExpandedDecisions((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  // Open roadblock alert cards (backend world.alerts) + pending control-tower recommendations
  const openAlerts = (world?.alerts || []).filter((a) => a.status === 'OPEN').slice().reverse()
  const pendingRecs = (world?.recommendations || []).filter((r) => r.status === 'PENDING')
  const totalAlertsCount = openAlerts.length + pendingRecs.length

  const tabs = [
    { id: 'fleet', label: 'Fleet', icon: 'truck', badge: world?.trucks?.length || 0 },
    { id: 'decisions', label: 'Decisions', icon: 'layers' },
    { id: 'alerts', label: 'Alerts', icon: 'alert', badge: totalAlertsCount },
  ]

  const truckStatusVariant = (status) => {
    if (status === 'BLOCKED') return 'danger'
    if (status === 'EN_ROUTE') return 'info'
    if (status === 'UNLOADING') return 'success'
    return 'neutral'
  }

  return (
    <>
      {isDrawerOpen && <div className="df-drawer-backdrop" onClick={onCloseDrawer} />}
      <aside className={`df-sidebar-right ${isDrawerOpen ? 'drawer-open' : ''}`}>
        <div className="df-sidebar-header">
          <Tabs tabs={tabs} activeTab={activeTab} onChange={onTabChange} />
          <Button
            variant="ghost"
            size="sm"
            icon={collapsed ? 'chevron-left' : 'chevron-right'}
            iconOnly
            title={collapsed ? 'Expand Sidebar' : 'Collapse Sidebar'}
            onClick={onToggleCollapse}
          />
        </div>

        <div className="df-sidebar-content">
          {/* --- TAB: FLEET --- */}
          {activeTab === 'fleet' && (
            <>
              <div className="df-section-header">
                ACTIVE VEHICLES ({(world?.trucks || []).length})
              </div>

              <div className="df-phone-link-box">
                <div className="df-font-semibold df-text-xs">📱 Driver app on a phone</div>
                <div className="df-text-2xs df-text-muted">
                  Phone on the same Wi-Fi as this PC → open the truck's link below in the phone's browser.
                  Navigation starts once you press <strong>Dispatch</strong>; before that it shows the planned trip.
                </div>
                {driverBase && <code className="df-phone-link">{driverUrl('TRK-01')}</code>}
              </div>

              {(world?.trucks || []).map((truck) => {
                const tIdx = truckIndex(truck.id)
                const next = pendingStops(truck)[0]
                const isRerouting = truck.reroute_pending
                const isBlocked = truck.status === 'BLOCKED'
                const isReturning = truck.status === 'RETURNING'
                const isParked = truck.status === 'AT_PLANT' || truck.status === 'RESERVE'

                return (
                  <Card key={truck.id} className={isBlocked ? 'df-card-danger' : isRerouting ? 'df-card-warning' : ''}>
                    <div className="df-card-header">
                      <div className="df-card-title">
                        <span className={`df-truck-swatch df-truck-bg-${tIdx}`} />
                        <strong>{truck.id}</strong>
                      </div>
                      <Chip variant={truckStatusVariant(truck.status)}>
                        {isBlocked ? 'BLOCKED' : isRerouting ? 'FINDING DETOUR' : statusLabel(truck.status)}
                      </Chip>
                    </div>

                    <div className="df-card-body">
                      {/* Driver Details */}
                      {truck.driver_name && (
                        <div className="df-card-row">
                          <span className="df-flex df-items-center df-gap-1">
                            <Icon name="user" size={12} />
                            <span>{truck.driver_name}</span>
                          </span>
                          <span className="df-font-mono">{truck.driver_phone}</span>
                        </div>
                      )}

                      {/* Next Stop Info */}
                      {!isParked && !isReturning && next && (
                        <div className={`df-fleet-next-stop df-truck-border-${tIdx}`}>
                          <div className="df-flex-between df-text-xs">
                            <span>Next: <strong>{hubName(world, next.hub_id)}</strong></span>
                            <span>Drop: <strong>{litres(next.drop_l)}</strong></span>
                          </div>
                          <div className="df-flex-between df-text-xs df-mt-0-5 df-text-secondary">
                            <span>ETA: <strong className="df-font-mono">{clock(next.eta)}</strong></span>
                            <span>Slack: <strong className="df-font-mono">{Math.round(next.slack_min || 0)} min</strong></span>
                          </div>
                        </div>
                      )}

                      {/* Load Progress */}
                      <Progress
                        value={truck.load_l}
                        max={truck.capacity_l}
                        label="Milk on Board"
                        valueText={`${litres(truck.load_l)} / ${litres(truck.capacity_l)}`}
                      />

                      {/* Telemetry Stats */}
                      <div className="df-card-row df-mt-0-5">
                        <span className="df-flex df-items-center df-gap-1">
                          <Icon name="fuel" size={12} />
                          <span>Fuel: {truck.km_driven ? (truck.km_driven / 5).toFixed(1) : '0'} L</span>
                        </span>
                        <span>Distance: {truck.km_driven ? truck.km_driven.toFixed(1) : '0'} km</span>
                      </div>

                      {truck.route_source === 'fallback' && !isParked && (
                        <div className="df-text-xs df-text-secondary df-mt-0-5" title="The road router could not be reached; this leg is drawn and driven as a straight line">
                          ≈ Approximate route (road data unavailable)
                        </div>
                      )}

                      {/* Action to Follow / Driver View */}
                      <div className="df-flex df-gap-1 df-mt-1">
                        <Button
                          variant={followTruckId === truck.id ? 'primary' : 'outline'}
                          size="sm"
                          icon="map-pin"
                          onClick={() => onFollowTruck?.(followTruckId === truck.id ? null : truck.id)}
                        >
                          {followTruckId === truck.id ? 'Following' : 'Follow'}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          icon="truck"
                          onClick={() => window.open(driverUrl(truck.id), '_blank')}
                        >
                          Open Driver Screen
                        </Button>
                      </div>
                      <div className="df-phone-link-row">
                        <code className="df-phone-link" title="Open this on the driver's phone">{driverUrl(truck.id)}</code>
                        <Button variant="ghost" size="sm" onClick={() => copyDriverUrl(truck.id)}>
                          {copied === truck.id ? 'Copied' : 'Copy'}
                        </Button>
                      </div>
                    </div>
                  </Card>
                )
              })}
            </>
          )}

          {/* --- TAB: DECISIONS TIMELINE --- */}
          {activeTab === 'decisions' && (
            <>
              <div className="df-section-header">
                AUDIT LOG & DECISIONS ({(world?.decisions || []).length})
              </div>

              {(!world?.decisions || world.decisions.length === 0) && (
                <div className="df-empty-notice">
                  No decisions logged yet. System events will be recorded here.
                </div>
              )}

              {(world?.decisions || []).slice().reverse().map((dec, i) => {
                const isExpanded = expandedDecisions.has(i)
                const hasOptions = dec.details?.options || dec.details?.window_breaks

                return (
                  <Card key={i} className="df-decision-card">
                    <div className="df-card-header">
                      <div className="df-flex df-items-center df-gap-1-5">
                        <span className="df-font-mono df-text-xs df-text-muted">
                          {clock(dec.time)}
                        </span>
                        <strong className="df-text-xs">{dec.truck_id}</strong>
                      </div>
                      <Chip variant={dec.type.includes('NO_ROUTE') ? 'danger' : dec.type.includes('DETOUR_ALERT') || dec.type.includes('SIM_ERROR') ? 'warning' : 'info'}>
                        {dec.type}
                      </Chip>
                    </div>

                    <div className="df-card-body">
                      <div className="df-text-xs df-text-primary df-lh-normal">
                        {dec.summary}
                      </div>

                      {hasOptions && (
                        <div>
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={isExpanded ? 'chevron-up' : 'chevron-down'}
                            onClick={() => toggleDecisionExpand(i)}
                            className="df-p-1 df-text-2xs"
                          >
                            {isExpanded ? 'Hide Options Table' : 'Show Evaluated Options'}
                          </Button>

                          {isExpanded && (
                            <div className="df-mt-1-5 df-p-2 df-code-wrap">
                              <pre className="df-code-block">
                                {JSON.stringify(dec.details, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </Card>
                )
              })}
            </>
          )}

          {/* --- TAB: ALERTS --- */}
          {activeTab === 'alerts' && (
            <>
              <div className="df-section-header">
                ACTIVE SITUATION ALERTS ({totalAlertsCount})
              </div>

              {totalAlertsCount === 0 && (
                <div className="df-success-notice">
                  <Icon name="check" size={24} color="var(--color-success)" />
                  <div className="df-mt-2 df-font-semibold">All Operations Nominal</div>
                  <div className="df-text-xs df-text-muted">
                    No trucks blocked or waiting for approvals.
                  </div>
                </div>
              )}

              {/* Roadblock alert cards. NO_ROUTE (red): the truck stopped safely and waits. DETOUR_COSTLY (amber):
                  the truck already took the detour; ignoring the card keeps it (the default). */}
              {openAlerts.map((a) => {
                const isNoRoute = a.kind === 'NO_ROUTE'
                const truck = (world?.trucks || []).find((t) => t.id === a.truck_id)
                const variantFor = (id) => (id === 'call_driver' || id === 'keep_detour' ? 'success'
                  : id === 'reassign_stop' ? 'primary' : id === 'return_to_plant' || id === 'skip_stop' ? 'warning'
                  : 'secondary')
                return (
                  <Card key={a.id} className={isNoRoute ? 'df-card-danger' : 'df-card-warning'}>
                    <div className="df-card-header">
                      <div className={`df-card-title ${isNoRoute ? 'df-text-danger' : 'df-text-warning'}`}>
                        <Icon name={isNoRoute ? 'roadblock' : 'alert'} size={16}
                              color={isNoRoute ? 'var(--color-danger)' : 'var(--color-warning)'} />
                        <span>{isNoRoute ? `${a.truck_id} stopped — no route` : `${a.truck_id} costly detour`}</span>
                      </div>
                      <Chip variant={isNoRoute ? 'danger' : 'warning'}>{isNoRoute ? 'NO ROUTE' : 'DETOUR TAKEN'}</Chip>
                    </div>

                    <div className="df-card-body">
                      <div className="df-text-xs df-text-primary df-lh-normal">{a.summary}</div>
                      <div className="df-text-xs df-text-muted">Raised {clock(a.created_at)}</div>
                      {!isNoRoute && (
                        <div className="df-text-xs df-flex-col df-gap-0-5 df-text-secondary">
                          <div>Extra time: <strong className="df-text-danger">+{Math.round(a.extra_min ?? 0)} min</strong></div>
                          <div>Extra distance: <strong>+{(a.extra_km ?? 0).toFixed(1)} km ({Math.round(a.extra_km_pct ?? 0)}%)</strong></div>
                          {a.windows_broken?.length > 0 && (
                            <div className="df-text-danger">Late: {a.windows_broken.join(', ')}</div>
                          )}
                          <div className="df-text-muted">The truck keeps this detour unless you choose otherwise.</div>
                        </div>
                      )}
                      {isNoRoute && (
                        <div className="df-text-xs df-text-secondary">
                          Driver: <strong>{a.driver_name || 'Driver'}</strong> ({a.driver_phone || 'no phone on file'})
                        </div>
                      )}

                      <div className={isNoRoute ? 'df-grid-2 df-gap-1-5 df-mt-1-5' : 'df-flex-col df-gap-1-5 df-mt-1-5'}>
                        {a.options.map((o) => (
                          <Button
                            key={o.id}
                            variant={variantFor(o.id)}
                            size="sm"
                            icon={o.id === 'call_driver' ? 'phone' : undefined}
                            title={o.description}
                            onClick={() => (o.id === 'call_driver'
                              ? onShowCallDialog?.(truck || { id: a.truck_id, driver_name: a.driver_name, driver_phone: a.driver_phone })
                              : onResolveDecision?.(a.truck_id, o.id, a.id))}
                          >
                            {o.label}{o.id === a.default_option ? ' (default)' : ''}
                          </Button>
                        ))}
                      </div>
                    </div>
                  </Card>
                )
              })}

              {/* Control Tower Recommendations */}
              {pendingRecs.map((rec) => (
                <Card key={rec.id} title="Demand Change Recommendation" icon="settings">
                  <div className="df-text-xs df-text-secondary">
                    {rec.summary}
                  </div>
                  <div className="df-flex df-gap-1-5 df-mt-1-5">
                    <Button
                      variant="success"
                      size="sm"
                      className="df-flex-1"
                      onClick={() => onApproveRecommendation?.(rec.id)}
                    >
                      Approve
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      className="df-flex-1"
                      onClick={() => onRejectRecommendation?.(rec.id)}
                    >
                      Reject
                    </Button>
                  </div>
                </Card>
              ))}
            </>
          )}
        </div>
      </aside>
    </>
  )
}
