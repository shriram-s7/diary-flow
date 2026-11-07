import React, { useEffect, useState } from 'react'
import './platform.css'

const roleName = role => role.replaceAll('_', ' ')

export default function Platform() {
  const [status, setStatus] = useState(null)
  const [demos, setDemos] = useState([])
  const [session, setSession] = useState(null) // Memory only; no reusable token left on a shared phone.
  const [grant, setGrant] = useState(null)
  const [me, setMe] = useState(null)
  const [nodes, setNodes] = useState([])
  const [products, setProducts] = useState([])
  const [trips, setTrips] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function clear() {
    setSession(null); setGrant(null); setMe(null); setNodes([]); setProducts([]); setTrips([])
  }
  async function request(path, options = {}, activeSession = session, activeGrant = grant) {
    const response = await fetch(`/api/v2${path}`, {
      ...options, headers: { 'Content-Type': 'application/json',
        ...(activeSession && activeGrant ? { Authorization: `Bearer ${activeSession.token}`,
          'X-Run-Id': activeGrant.run_id, 'X-Grant-Id': activeGrant.id } : {}), ...options.headers },
      cache: 'no-store',
    })
    const body = await response.json()
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) clear()
      throw new Error(typeof body.detail === 'string' ? body.detail : 'Check your phone number and PIN.')
    }
    return body
  }
  useEffect(() => {
    let alive = true
    request('/status').then(async data => {
      if (!alive) return
      setStatus(data)
      if (data.demo) {
        const users = await request('/auth/demo-users')
        if (alive) setDemos(users)
      }
    }).catch(e => alive && setError(e.message))
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!session || !grant) return
    let alive = true
    const refresh = async () => {
      try {
        const [identity, network, assignments] = await Promise.all([
          request('/me'), request('/network'), request('/trips'),
        ])
        const catalogue = ['admin', 'plant_manager', 'hub_manager'].includes(identity.scope.role)
          ? await request('/products') : []
        if (alive) { setMe(identity); setNodes(network); setTrips(assignments); setProducts(catalogue) }
      } catch (e) { if (alive) setError(e.message) }
    }
    refresh()
    const timer = setInterval(refresh, 15000)
    return () => { alive = false; clearInterval(timer) }
  }, [session, grant])

  async function signIn(path, body) {
    setBusy(true); setError(''); clear()
    try {
      const result = await request(path, { method: 'POST', body: JSON.stringify(body) }, null, null)
      setSession(result); setGrant(result.grants[0])
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  async function signOut() {
    setBusy(true)
    try { await request('/auth/logout', { method: 'POST' }); setError('') }
    catch (e) { setError(`Signed out on this device. Server logout could not be confirmed: ${e.message}`) }
    finally { clear(); setBusy(false) }
  }
  async function viewAs(userId) {
    setBusy(true); setError('')
    try {
      const result = await request('/auth/view-as', { method: 'POST', body: JSON.stringify({ user_id: userId }) })
      clear(); setSession(result); setGrant(result.grants[0])
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  return <div className="v2-platform">
    <header className="v2-header"><a href="/platform" className="v2-brand">Allocra <span>v2</span></a>
      <div>{status?.demo && <span className="v2-badge">Prototype</span>}<a href="/">Legacy simulator</a></div></header>
    <main>
      <p className="v2-eyebrow">DISTRIBUTION WORKSPACE</p>
      <h1>{me ? `Your ${roleName(me.scope.role)} workspace` : 'One connected distribution day.'}</h1>
      <p className="v2-intro">Plant → hubs → booths. Sign in to see the records assigned to your role.</p>
      {error && <div role="alert" className="v2-error">{error}</div>}
      {!status && !error && <p role="status">Connecting to the platform…</p>}
      {status && !status.configured && <section className="v2-card"><h2>Setup is required</h2>
        <p>The database and signing key must be configured before login. Follow the local setup guide in docs/v2/AUTH.md.</p></section>}
      {!session && status?.configured && <div className="v2-columns">
        <section className="v2-card"><h2>Sign in</h2>
          <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget)
            signIn('/auth/login', { phone: data.get('phone'), pin: data.get('pin') }) }}>
            <label>Phone number<input name="phone" type="tel" autoComplete="username" placeholder="+919876543210" required /></label>
            <label>PIN<input name="pin" type="password" inputMode="numeric" autoComplete="current-password" minLength={6} maxLength={12} required /></label>
            <button disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
          </form><p className="v2-muted">Your session is cleared when this page closes.</p></section>
        {status.demo && <section className="v2-card"><h2>Explore a prototype role</h2>
          <p>These demo identities access an isolated prototype dataset. No real delivery data.</p>
          <div className="v2-demo-grid">{demos.map(user => <button key={user.user_id} disabled={busy}
            onClick={() => signIn('/auth/demo-login', { user_id: user.user_id })}>{user.label}</button>)}</div>
        </section>}
      </div>}
      {session && <>
        <div className="v2-toolbar"><label>Active role<select value={grant ? `${grant.run_id}:${grant.id}` : ''} onChange={e => {
          setMe(null); setNodes([]); setProducts([]); setTrips([])
          setGrant(session.grants.find(g => `${g.run_id}:${g.id}` === e.target.value))
        }}>{session.grants.map(g => <option key={`${g.run_id}:${g.id}`} value={`${g.run_id}:${g.id}`}>{roleName(g.role)} · {g.run_id}</option>)}</select></label>
          <button onClick={signOut} disabled={busy}>Sign out</button></div>
        {!me ? <p role="status">Loading your workspace…</p> : <>
          {me.real_actor_id !== me.user_id && <p className="v2-badge">Prototype View as — actions are attributed to your administrator identity.</p>}
          {status?.demo && me.scope.role === 'admin' && <section className="v2-card"><h2>View as a demo user</h2>
            <p>Switch into a scoped phone or manager workspace. This action is audited.</p>
            <div className="v2-demo-grid">{demos.filter(u => u.user_id !== me.user_id).map(u =>
              <button key={u.user_id} disabled={busy} onClick={() => viewAs(u.user_id)}>{u.label}</button>)}</div></section>}
          <div className="v2-metrics"><section><strong>{nodes.length}</strong><span>Visible network locations</span></section>
            <section><strong>{products.length}</strong><span>Visible catalogue products</span></section>
            <section><strong>{trips.length}</strong><span>Assigned / visible trips</span></section></div>
          <section className="v2-card"><h2>Your network</h2>{nodes.length ? <ul className="v2-records">{nodes.map(n =>
            <li key={n.id}><strong>{n.name}</strong><span>{n.kind}</span><small>{n.parent_id ? `Supplied by ${n.parent_id}` : 'Network source'}</small></li>)}</ul>
            : <p>No network locations are available for this role. Drivers receive their assigned trip details.</p>}</section>
          {products.length > 0 && <section className="v2-card"><h2>Saved products</h2><ul className="v2-records">{products.map(p =>
            <li key={p.id}><strong>{p.name}</strong><span>{p.units_per_container} units / container</span><small>{p.lane}</small></li>)}</ul></section>}
          <section className="v2-card"><h2>Trips</h2>{trips.length ? <ul>{trips.map(t => <li key={t.id}>{t.id} · {t.state}</li>)}</ul>
            : <p>No trips have been planned yet.</p>}</section>
        </>}
      </>}
      <footer className="v2-checkpoint"><strong>Implementation checkpoint</strong><p>Connected login and scoped saved-data views. Product and network editing, ordering and dispatch are still being built.</p></footer>
    </main>
  </div>
}
