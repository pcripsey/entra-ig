import { useEffect, useMemo, useState } from 'react'
import './App.css'

type ConfigResponse = {
  tenant_id_present: boolean
  client_id_present: boolean
  client_secret_present: boolean
  masked_client_secret: string
  graph_scope: string
  export_base_dir: string
  database_path: string
  log_file_path: string
  frontend_dist: string
}

type HealthResponse = {
  status: 'ok' | 'degraded'
  graph_configured: boolean
  graph_reachable: boolean
  detail: string
  latest_run_status: string | null
}

type SyncRunResponse = {
  id: string
  status: string
  started_at: string
  completed_at: string | null
  users_count: number | null
  groups_count: number | null
  memberships_count: number | null
  users_file: string | null
  groups_file: string | null
  memberships_file: string | null
  error: string | null
}

type SyncStatusResponse = {
  active_run_id: string | null
  running: boolean
  schedule_enabled: boolean
  schedule_interval_minutes: number
  next_scheduled_run_at: string | null
  latest_run: SyncRunResponse | null
}

type LogResponse = {
  lines: string[]
}

type ScheduleResponse = {
  enabled: boolean
  interval_minutes: number
  next_run_at: string | null
  updated_at: string | null
}

const apiBase = import.meta.env.VITE_API_BASE_URL ?? '/api'

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `Request failed with ${response.status}`)
  }

  return (await response.json()) as T
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

function App() {
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [status, setStatus] = useState<SyncStatusResponse | null>(null)
  const [runs, setRuns] = useState<SyncRunResponse[]>([])
  const [logs, setLogs] = useState<string[]>([])
  const [schedule, setSchedule] = useState<ScheduleResponse | null>(null)
  const [scheduleEnabled, setScheduleEnabled] = useState(false)
  const [scheduleIntervalMinutes, setScheduleIntervalMinutes] = useState('60')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [savingSchedule, setSavingSchedule] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadDashboard = async () => {
    try {
      setError(null)
      const [configData, healthData, statusData, runData, logData, scheduleData] = await Promise.all([
        fetchJson<ConfigResponse>('/config'),
        fetchJson<HealthResponse>('/health'),
        fetchJson<SyncStatusResponse>('/status'),
        fetchJson<SyncRunResponse[]>('/runs'),
        fetchJson<LogResponse>('/logs?lines=200'),
        fetchJson<ScheduleResponse>('/schedule'),
      ])
      setConfig(configData)
      setHealth(healthData)
      setStatus(statusData)
      setRuns(runData)
      setLogs(logData.lines)
      setSchedule(scheduleData)
      setScheduleEnabled(scheduleData.enabled)
      setScheduleIntervalMinutes(String(scheduleData.interval_minutes))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load dashboard data.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadDashboard()
    const timer = window.setInterval(() => {
      void loadDashboard()
    }, 15000)
    return () => window.clearInterval(timer)
  }, [])

  const triggerSync = async () => {
    try {
      setBusy(true)
      setError(null)
      await fetchJson('/sync', { method: 'POST' })
      await loadDashboard()
    } catch (syncError) {
      setError(syncError instanceof Error ? syncError.message : 'Unable to start sync.')
    } finally {
      setBusy(false)
    }

    const saveSchedule = async () => {
      try {
        setSavingSchedule(true)
        setError(null)
        const payload = {
          enabled: scheduleEnabled,
          interval_minutes: Number(scheduleIntervalMinutes),
        }
        const scheduleData = await fetchJson<ScheduleResponse>('/schedule', {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        setSchedule(scheduleData)
        setScheduleEnabled(scheduleData.enabled)
        setScheduleIntervalMinutes(String(scheduleData.interval_minutes))
        await loadDashboard()
      } catch (scheduleError) {
        setError(scheduleError instanceof Error ? scheduleError.message : 'Unable to save schedule.')
      } finally {
        setSavingSchedule(false)
      }
    }
  }

  const summaryCards = useMemo(
    () => [
      {
        label: 'Connection health',
        value: health?.graph_reachable ? 'Connected' : 'Needs attention',
        tone: health?.graph_reachable ? 'success' : 'warning',
      },
      {
        label: 'Current sync',
        value: status?.running ? 'Running' : 'Idle',
        tone: status?.running ? 'info' : 'neutral',
      },
      {
        label: 'Latest export',
        value: status?.latest_run?.status ?? 'No runs yet',
        tone: status?.latest_run?.status === 'completed' ? 'success' : 'neutral',
      },
      {
        label: 'Schedule',
        value: schedule?.enabled ? `Every ${schedule.interval_minutes} min` : 'Disabled',
        tone: schedule?.enabled ? 'info' : 'neutral',
      },
    ],
    [health?.graph_reachable, schedule?.enabled, schedule?.interval_minutes, status?.latest_run?.status, status?.running],
  )

  return (
    <main className="shell">
      <header className="hero-panel">
        <div>
          <p className="eyebrow">OpenText JATO-inspired admin console</p>
          <h1>Entra ID governance export monitor</h1>
          <p className="hero-copy">
            Manage Microsoft Entra connectivity, monitor export execution, and review CSV collector output
            paths from one operational dashboard.
          </p>
        </div>
        <button className="primary-action" onClick={() => void triggerSync()} disabled={busy || status?.running}>
          {busy || status?.running ? 'Export running…' : 'Run export now'}
        </button>
      </header>

      {error ? <section className="banner error">{error}</section> : null}
      {loading ? <section className="banner">Loading dashboard…</section> : null}

      <section className="summary-grid">
        {summaryCards.map((card) => (
          <article key={card.label} className={`summary-card ${card.tone}`}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
          </article>
        ))}
      </section>

      <section className="content-grid">
        <article className="panel">
          <div className="panel-header">
            <h2>Connection status</h2>
            <span className={`pill ${health?.status ?? 'neutral'}`}>{health?.status ?? 'unknown'}</span>
          </div>
          <dl className="detail-list">
            <div>
              <dt>Graph configured</dt>
              <dd>{health?.graph_configured ? 'Yes' : 'No'}</dd>
            </div>
            <div>
              <dt>Graph reachable</dt>
              <dd>{health?.graph_reachable ? 'Yes' : 'No'}</dd>
            </div>
            <div>
              <dt>Detail</dt>
              <dd>{health?.detail ?? '—'}</dd>
            </div>
            <div>
              <dt>Latest run</dt>
              <dd>{health?.latest_run_status ?? '—'}</dd>
            </div>
          </dl>
        </article>

        <article className="panel">
          <div className="panel-header">
            <h2>Configuration</h2>
            <span className="pill neutral">Runtime</span>
          </div>
          <dl className="detail-list">
            <div>
              <dt>Tenant ID loaded</dt>
              <dd>{config?.tenant_id_present ? 'Yes' : 'No'}</dd>
            </div>
            <div>
              <dt>Client ID loaded</dt>
              <dd>{config?.client_id_present ? 'Yes' : 'No'}</dd>
            </div>
            <div>
              <dt>Client secret</dt>
              <dd>{config?.masked_client_secret || 'Not configured'}</dd>
            </div>
            <div>
              <dt>Scope</dt>
              <dd>{config?.graph_scope ?? '—'}</dd>
            </div>
            <div>
              <dt>Export path</dt>
              <dd>{config?.export_base_dir ?? '—'}</dd>
            </div>
            <div>
              <dt>Database path</dt>
              <dd>{config?.database_path ?? '—'}</dd>
            </div>
          </dl>
        </article>

        <article className="panel">
          <div className="panel-header">
            <h2>Refresh schedule</h2>
            <span className={`pill ${schedule?.enabled ? 'info' : 'neutral'}`}>
              {schedule?.enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>
          <div className="schedule-form">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={scheduleEnabled}
                onChange={(event) => setScheduleEnabled(event.target.checked)}
              />
              Enable automatic refreshes
            </label>
            <label>
              <span>Interval (minutes)</span>
              <input
                type="number"
                min={5}
                max={1440}
                value={scheduleIntervalMinutes}
                onChange={(event) => setScheduleIntervalMinutes(event.target.value)}
              />
            </label>
            <label>
              <span>Next run</span>
              <input type="text" disabled value={formatDate(schedule?.next_run_at ?? null)} />
            </label>
            <label>
              <span>Last schedule update</span>
              <input type="text" disabled value={formatDate(schedule?.updated_at ?? null)} />
            </label>
            <button className="secondary-action" onClick={() => void saveSchedule()} disabled={savingSchedule}>
              {savingSchedule ? 'Saving…' : 'Save schedule'}
            </button>
          </div>
        </article>

        <article className="panel wide">
          <div className="panel-header">
            <h2>Recent export runs</h2>
            <span className="pill info">History</span>
          </div>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Completed</th>
                  <th>Users</th>
                  <th>Groups</th>
                  <th>Memberships</th>
                </tr>
              </thead>
              <tbody>
                {runs.length === 0 ? (
                  <tr>
                    <td colSpan={7}>No sync runs recorded.</td>
                  </tr>
                ) : (
                  runs.map((run) => (
                    <tr key={run.id}>
                      <td className="mono">{run.id}</td>
                      <td>{run.status}</td>
                      <td>{formatDate(run.started_at)}</td>
                      <td>{formatDate(run.completed_at)}</td>
                      <td>{run.users_count ?? '—'}</td>
                      <td>{run.groups_count ?? '—'}</td>
                      <td>{run.memberships_count ?? '—'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel wide">
          <div className="panel-header">
            <h2>Latest export artifacts</h2>
            <span className="pill success">Collector-ready</span>
          </div>
          <dl className="detail-list compact">
            <div>
              <dt>Users CSV</dt>
              <dd>{status?.latest_run?.users_file ?? 'Awaiting first export'}</dd>
            </div>
            <div>
              <dt>Groups CSV</dt>
              <dd>{status?.latest_run?.groups_file ?? 'Awaiting first export'}</dd>
            </div>
            <div>
              <dt>Memberships CSV</dt>
              <dd>{status?.latest_run?.memberships_file ?? 'Awaiting first export'}</dd>
            </div>
            <div>
              <dt>Last error</dt>
              <dd>{status?.latest_run?.error ?? 'None'}</dd>
            </div>
          </dl>
        </article>

        <article className="panel wide">
          <div className="panel-header">
            <h2>Application log tail</h2>
            <span className="pill neutral">Observability</span>
          </div>
          <pre className="log-viewer">{logs.length > 0 ? logs.join('\n') : 'No log entries yet.'}</pre>
        </article>
      </section>
    </main>
  )
}

export default App
