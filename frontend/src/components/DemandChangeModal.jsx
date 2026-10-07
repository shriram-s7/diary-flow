import React, { useState } from 'react'
import { api, errorMessage } from '../api'
import { clock, litres } from '../format'
import RecommendationCard from './RecommendationCard'
import { Button, ErrorText, Modal, RiskBadge } from './ui'

export default function DemandChangeModal({ hub, world, controlMode, onClose, onChanged }) {
  const [demand, setDemand] = useState(String(hub.demand_l))
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const rec = result?.recommendation
    ? world?.recommendations?.find((r) => r.id === result.recommendation.id) || result.recommendation
    : null

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const res = await api.updateHubDemand(hub.id, Number(demand))
      setResult(res)
      onChanged?.()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Change Demand — ${hub.name}`}
      icon="edit"
      onClose={onClose}
      size="md"
      footer={
        result ? (
          <Button variant="secondary" onClick={onClose}>Close</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={submit} disabled={busy}>
              {busy ? 'Evaluating...' : 'Apply Demand Update'}
            </Button>
          </>
        )
      }
    >
      <div className="df-text-xs df-text-secondary df-mb-2">
        Current Demand: <strong>{litres(hub.demand_l)}</strong> · Delivered: <strong>{litres(hub.delivered_l)}</strong> · Window: <strong className="df-font-mono">{clock(hub.window_end)}</strong>
        <div className="df-mt-1">
          <RiskBadge risk={hub.risk} />
        </div>
      </div>

      {!result && (
        <form onSubmit={submit}>
          <label className="df-form-label">
            New Target Demand (Litres)
          </label>
          <input
            className="df-input df-mb-1-5"
            type="number"
            min="0"
            step="50"
            value={demand}
            onChange={(e) => setDemand(e.target.value)}
            autoFocus
          />
          <div className="df-text-2xs df-text-muted">
            {controlMode === 'approve'
              ? 'Approve mode: an increase generates a recommendation awaiting manager approval.'
              : 'Auto mode: an increase is covered immediately with the optimal fleet response.'}
          </div>
          <ErrorText>{error}</ErrorText>
        </form>
      )}

      {result && (
        <div>
          {result.decision && (
            <div className="df-decision-box df-mb-3">
              <strong>Decision:</strong> {result.decision.summary}
            </div>
          )}
          {rec && <RecommendationCard rec={rec} world={world} onChanged={onChanged} />}
        </div>
      )}
    </Modal>
  )
}
