import React, { useState } from 'react'
import { Modal, Button } from './ui'
import { Icon } from './Icons'

export default function CallDriverModal({ truck, onClose }) {
  const [callState, setCallState] = useState('idle') // 'idle' | 'calling' | 'connected'
  const driverName = truck?.driver_name || 'Driver'
  const driverPhone = truck?.driver_phone || '+91 98401 23456'

  const startCall = () => {
    setCallState('calling')
    setTimeout(() => {
      setCallState('connected')
    }, 1200)
  }

  return (
    <Modal
      title={`Call Driver — ${driverName}`}
      icon="phone"
      onClose={onClose}
      size="sm"
    >
      <div className="df-text-center df-p-2">
        <div className="df-call-avatar">
          <Icon name="phone" size={26} color="var(--color-accent)" />
        </div>

        <h3 className="df-call-driver-name">
          {driverName}
        </h3>
        <div className="df-call-driver-phone">
          {driverPhone}
        </div>
        <div className="df-text-xs df-text-secondary df-mb-4">
          Vehicle ID: <strong>{truck?.id}</strong> ({truck?.capacity_l} L capacity)
        </div>

        <div className={`df-call-status-box ${callState}`}>
          {callState === 'idle' && 'Cellular voice channel ready'}
          {callState === 'calling' && 'Connecting to Chennai fleet driver...'}
          {callState === 'connected' && 'Call connected (Voice simulation active)'}
        </div>

        {callState === 'idle' ? (
          <Button
            variant="success"
            size="lg"
            icon="phone"
            onClick={startCall}
            className="df-w-full"
          >
            Dial Driver
          </Button>
        ) : (
          <Button
            variant="danger"
            size="lg"
            icon="phone"
            onClick={() => {
              setCallState('idle')
              onClose()
            }}
            className="df-w-full"
          >
            End Call
          </Button>
        )}
      </div>
    </Modal>
  )
}
