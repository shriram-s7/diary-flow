import React, { useState } from 'react'
import { api, errorMessage } from '../api'
import { clock, litres, parseClock } from '../format'
import { Button, ErrorText, Modal, Select } from './ui'

const PRIORITIES = [
  { value: 'Normal', label: 'Normal Priority' },
  { value: 'High', label: 'High Priority' },
  { value: 'Critical', label: 'Critical Priority' },
]

export default function HubModal({ mode, hub, position, snap, config, canDelete, onClose, onSaved }) {
  const isEdit = mode === 'edit'
  const [name, setName] = useState(hub?.name || '')
  const [demand, setDemand] = useState(String(hub?.demand_l ?? ''))
  const [windowEnd, setWindowEnd] = useState(clock(hub?.window_end ?? config?.DEFAULT_WINDOW_END))
  const [priority, setPriority] = useState(hub?.priority || 'Normal')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const save = async (e) => {
    e.preventDefault()
    setError(null)
    const demandL = Number(demand)
    const windowMin = parseClock(windowEnd)
    if (windowMin === null) return setError('Pick a window end time (HH:MM).')
    setBusy(true)
    try {
      if (!isEdit) {
        const result = await api.createHub({
          name: name.trim(),
          lat: position.lat,
          lon: position.lon,
          demand_l: demandL,
          window_end: windowMin,
          priority,
        })
        return onSaved(result)
      }

      let last = null
      if (windowMin !== Math.round(hub.window_end)) last = await api.updateHubWindow(hub.id, windowMin)
      if (priority !== hub.priority) last = await api.updateHubPriority(hub.id, priority)
      if (demand !== '' && demandL !== hub.demand_l) last = await api.updateHubDemand(hub.id, demandL)
      onSaved(last)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!window.confirm(`Delete hub ${hub.name}?`)) return
    setBusy(true)
    try {
      onSaved(await api.deleteHub(hub.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={isEdit ? `Edit Hub — ${hub.name}` : 'Add Delivery Hub'}
      icon="hub"
      onClose={onClose}
      size="md"
      footer={
        <>
          {isEdit && canDelete && (
            <Button variant="danger" size="sm" onClick={remove} disabled={busy} className="df-mr-auto">
              Delete Hub
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={save} disabled={busy}>
            {busy ? 'Saving...' : isEdit ? 'Save Changes' : 'Create Hub'}
          </Button>
        </>
      }
    >
      <form onSubmit={save} className="df-flex-col df-gap-3">
        {!isEdit && snap && (
          <div className="df-text-xs df-text-secondary">
            Trucks will stop on <strong>{snap.road_name || 'the nearest road'}</strong> ({Math.round(snap.snap_distance_m)} m snap distance).
          </div>
        )}

        {isEdit && (
          <div className="df-text-xs df-text-secondary">
            Delivered so far: <strong>{litres(hub.delivered_l)}</strong> · Remaining: <strong>{litres(hub.remaining_l)}</strong>
          </div>
        )}

        {!isEdit && (
          <div>
            <label className="df-form-label">
              Hub Name
            </label>
            <input
              className="df-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Perambur"
              autoFocus
            />
          </div>
        )}

        <div>
          <label className="df-form-label">
            Demand (Litres)
          </label>
          <input
            className="df-input"
            type="number"
            min="0"
            step="50"
            value={demand}
            onChange={(e) => setDemand(e.target.value)}
          />
        </div>

        <div>
          <label className="df-form-label">
            Delivery Window Ends At
          </label>
          <input
            className="df-input"
            type="time"
            value={windowEnd}
            onChange={(e) => setWindowEnd(e.target.value)}
          />
        </div>

        <div>
          <label className="df-form-label">
            Priority Level
          </label>
          <Select
            value={priority}
            onChange={setPriority}
            options={PRIORITIES}
          />
        </div>

        <ErrorText>{error}</ErrorText>
      </form>
    </Modal>
  )
}
