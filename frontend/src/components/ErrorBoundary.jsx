import React, { Component } from 'react'
import { Button } from './ui'

// Catches render errors so one broken panel shows a message instead of a blank page.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('[DairyFlow] UI error:', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div role="alert" className="df-error-boundary-screen">
        <div className="df-error-boundary-card">
          <h2>Something went wrong</h2>
          <p>
            The screen hit an unexpected error. The simulation on the server is not affected.
          </p>
          <pre className="df-error-boundary-pre">
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <Button
            variant="primary"
            onClick={() => this.setState({ error: null })}
            className="df-mt-3"
          >
            Try again
          </Button>
        </div>
      </div>
    )
  }
}
