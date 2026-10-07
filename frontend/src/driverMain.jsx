import React from 'react'
import { createRoot } from 'react-dom/client'
import DriverView from './DriverView'
import ErrorBoundary from './components/ErrorBoundary'
import './index.css'

const truckId = new URLSearchParams(window.location.search).get('truckId')

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <DriverView truckId={truckId} />
    </ErrorBoundary>
  </React.StrictMode>
)
