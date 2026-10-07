import React, { useState } from 'react'
import { api, errorMessage } from '../api'
import { hubName, km, litres } from '../format'
import { Button, Chip, ErrorText } from './ui'

const KIND = { A: 'Redirect truck on the road', B: 'Dispatch from plant', C: 'Redirect + dispatch' }

export default function RecommendationCard({ rec, world, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const spike = rec.spike

  const act = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      onChanged?.(await fn(rec.id))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const isPending = rec.status === 'PENDING'
  const isApplied = rec.status === 'APPLIED'

  return (
    <div className={`df-rec-card ${isPending ? 'pending' : isApplied ? 'applied' : ''}`}>
      <div className="df-flex-between df-mb-1-5">
        <strong>{rec.id} · {spike.hub_name}</strong>
        <Chip variant={isPending ? 'warning' : isApplied ? 'success' : 'neutral'}>
          {rec.status}
        </Chip>
      </div>

      <div className="df-text-secondary df-mb-2">
        Demand: {litres(spike.old_demand_l)} &rarr; {litres(spike.new_demand_l)} · En route: {litres(spike.committed_l)} · Shortage: <strong className="df-text-danger">{litres(spike.shortage_l)}</strong>
      </div>

      {rec.options.length > 0 && (
        <div className="df-table-wrap">
          <table className="df-custom-table">
            <thead>
              <tr>
                <th></th>
                <th>Option</th>
                <th>Covers</th>
                <th>Extra</th>
                <th>Windows</th>
              </tr>
            </thead>
            <tbody>
              {rec.options.map((o) => (
                <tr
                  key={o.id}
                  className={o.id === rec.chosen ? 'selected' : ''}
                >
                  <td>{o.id === rec.chosen ? '●' : ''}</td>
                  <td>
                    <strong>{o.id}</strong> <span className="df-text-muted">({KIND[o.kind]})</span>
                    <div className="df-text-secondary">{o.summary}</div>
                  </td>
                  <td>{litres(o.litres_covered)}</td>
                  <td>+{km(o.added_km)}</td>
                  <td className={o.windows_broken.length ? 'df-text-danger' : 'df-text-success'}>
                    {o.windows_broken.length ? o.windows_broken.join(', ') : 'all kept'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="df-rec-reason">
        {rec.reason}
      </div>

      {isPending && (
        <div className="df-flex df-gap-2 df-mt-2">
          <Button
            variant="success"
            size="sm"
            disabled={busy}
            className="df-flex-1"
            onClick={() => act(api.approveRecommendation)}
          >
            Approve
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            className="df-flex-1"
            onClick={() => act(api.rejectRecommendation)}
          >
            Reject
          </Button>
        </div>
      )}

      <ErrorText>{error}</ErrorText>
      {world && isApplied && (
        <div className="df-rec-applied">
          Applied: routes updated for {hubName(world, spike.hub_id)}.
        </div>
      )}
    </div>
  )
}
