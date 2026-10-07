import { useEffect, useState } from 'react'
import { api } from '../api'

// Constants from GET /config (the backend's config.py). Fetched once; retried until it succeeds.
export default function useConfig() {
  const [config, setConfig] = useState(null)
  useEffect(() => {
    let alive = true
    let timer
    const load = async () => {
      try {
        const next = await api.getConfig()
        if (alive) setConfig(next)
      } catch {
        if (alive) timer = setTimeout(load, 2000)
      }
    }
    load()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [])
  return config
}
