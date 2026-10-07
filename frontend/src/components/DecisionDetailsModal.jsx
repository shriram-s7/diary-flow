import React from 'react'
import { clock } from '../format'
import { Modal, Button } from './ui'

const NESTED = new Set(['etas', 'keep_order', 'serve_next'])

function cell(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-IN') : value.toFixed(1)
  if (Array.isArray(value)) return value.length ? value.map(cell).join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function OptionsTable({ rows }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((k) => !NESTED.has(k))
  return (
    <div className="df-table-wrap df-table-bordered">
      <table className="df-custom-table">
        <thead>
          <tr className="df-table-th-elevated">
            {columns.map((c) => (
              <th key={c}>
                {c.replaceAll('_', ' ')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className={row.chosen || row.moved ? 'selected' : ''}
            >
              {columns.map((c) => (
                <td key={c}>
                  {cell(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function DecisionDetailsModal({ decision, onClose }) {
  const { options, ...rest } = decision.details || {}
  const scalars = Object.entries(rest).filter(([, v]) => v === null || typeof v !== 'object')
  const lists = Object.entries(rest).filter(([, v]) => Array.isArray(v) && v.every((x) => typeof x === 'string'))

  return (
    <Modal
      title={`${decision.type} · ${clock(decision.time)}`}
      icon="layers"
      onClose={onClose}
      size="lg"
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}
    >
      <div className="df-text-sm df-mb-3">
        <strong>{decision.truck_id}:</strong> {decision.summary}
      </div>

      {Array.isArray(options) && options.length > 0 && (
        <>
          <h4 className="df-section-header df-mt-2 df-mb-1">
            Options Evaluated
          </h4>
          <OptionsTable rows={options} />
        </>
      )}

      {lists.map(([key, items]) => (
        <div key={key} className="df-mt-2 df-text-xs">
          <strong>{key.replaceAll('_', ' ')}</strong>
          <ul className="df-how-list df-text-secondary">
            {items.map((x, i) => <li key={i}>{x}</li>)}
          </ul>
        </div>
      ))}

      {scalars.length > 0 && (
        <div className="df-table-wrap df-table-bordered df-mt-3">
          <table className="df-custom-table">
            <tbody>
              {scalars.map(([k, v]) => (
                <tr key={k}>
                  <td className="df-text-muted">{k.replaceAll('_', ' ')}</td>
                  <td>{cell(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  )
}
