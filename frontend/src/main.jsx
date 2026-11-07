import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './index.css'
import Platform from './v2/Platform'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      {window.location.pathname.startsWith('/platform') ? <Platform /> : <><App /><a className="v2-legacy-link" href="/platform">Open Allocra v2 workspace →</a></>}
    </ErrorBoundary>
  </React.StrictMode>
)
