import { useEffect, useMemo, useState } from 'react'
import './App.css'

type ConfigResponse = {
  tenant_id: string
  client_id: string
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
  sync_type: string
  started_at: string
  completed_at: string | null
  users_count: number | null
  groups_count: number | null
  memberships_count: number | null
  roles_count?: number | null
  role_memberships_count?: number | null
  users_file: string | null
  groups_file: string | null
  memberships_file: string | null
  roles_file?: string | null
  role_memberships_file?: string | null
  error: string | null
}

type SyncStatusResponse = {
  active_run_id: string | null
  running: boolean
  schedule_enabled: boolean
  schedule_interval_minutes: number
  schedule_sync_type: string
  next_scheduled_run_at: string | null
  latest_run: SyncRunResponse | null
  live_progress: LiveProgressResponse | null
}

type LiveProgressResponse = {
  stage: string
  users_fetched: number
  groups_fetched: number
  memberships_fetched: number
  roles_fetched: number
  role_memberships_fetched: number
}

type LogResponse = {
  lines: string[]
}

type ScheduleResponse = {
  enabled: boolean
  interval_minutes: number
  sync_type: string
  next_run_at: string | null
  updated_at: string | null
}

type RetryConfigResponse = {
  max_retry_attempts: number
  max_retry_delay_seconds: number
  updated_at: string | null
}

type ConnectionTestResponse = {
  success: boolean
  detail: string
}

type SyncType = 'full' | 'incremental'

const apiBase = import.meta.env.VITE_API_BASE_URL ?? '/api'

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T | undefined> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `Request failed with ${response.status}`)
  }

  if (response.status === 204) {
    return undefined
  }

  return (await response.json()) as T
}

async function fetchJsonRequired<T>(path: string, options?: RequestInit): Promise<T> {
  const result = await fetchJson<T>(path, options)
  if (result === undefined) {
    throw new Error(`Expected response body but received none for ${path}`)
  }
  return result
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
  const [scheduleSyncType, setScheduleSyncType] = useState<SyncType>('full')
  const [retryConfig, setRetryConfig] = useState<RetryConfigResponse | null>(null)
  const [retryAttempts, setRetryAttempts] = useState('5')
  const [retryDelaySeconds, setRetryDelaySeconds] = useState('32')
  const [savingRetryConfig, setSavingRetryConfig] = useState(false)
  const [syncType, setSyncType] = useState<SyncType>('full')
  const [tenantId, setTenantId] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [graphScope, setGraphScope] = useState('https://graph.microsoft.com/.default')
  const [connectionTestResult, setConnectionTestResult] = useState<ConnectionTestResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [savingSchedule, setSavingSchedule] = useState(false)
  const [testingConnection, setTestingConnection] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadDashboard = async () => {
    try {
      setError(null)
      const [configData, healthData, statusData, runData, logData, scheduleData, retryConfigData] = await Promise.all([
        fetchJsonRequired<ConfigResponse>('/config'),
        fetchJsonRequired<HealthResponse>('/health'),
        fetchJsonRequired<SyncStatusResponse>('/status'),
        fetchJsonRequired<SyncRunResponse[]>('/runs'),
        fetchJsonRequired<LogResponse>('/logs?lines=200'),
        fetchJsonRequired<ScheduleResponse>('/schedule'),
        fetchJsonRequired<RetryConfigResponse>('/retry-config'),
      ])
      setConfig(configData)
      setTenantId(configData.tenant_id)
      setClientId(configData.client_id)
      setGraphScope(configData.graph_scope)
      setHealth(healthData)
      setStatus(statusData)
      setRuns(runData)
      setLogs(logData.lines)
      setSchedule(scheduleData)
      setScheduleEnabled(scheduleData.enabled)
      setScheduleIntervalMinutes(String(scheduleData.interval_minutes))
      setScheduleSyncType((scheduleData.sync_type as SyncType) ?? 'full')
      setRetryConfig(retryConfigData)
      setRetryAttempts(String(retryConfigData.max_retry_attempts))
      setRetryDelaySeconds(String(retryConfigData.max_retry_delay_seconds))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load dashboard data.')
    } finally {
      setLoading(false)
    }
  }

  // initial load
  useEffect(() => {
    const initialLoad = window.setTimeout(() => {
      void loadDashboard()
    }, 0)
    return () => window.clearTimeout(initialLoad)
  }, [])

  // dynamic polling: 5 s while running, 15 s when idle
  useEffect(() => {
    const intervalMs = status?.running ? 5000 : 15000
    const timer = window.setInterval(() => {
      void loadDashboard()
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [status?.running])

  const triggerSync = async () => {
    try {
      setBusy(true)
      setError(null)
      await fetchJson('/sync', { method: 'POST', body: JSON.stringify({ sync_type: syncType }) })
      await loadDashboard()
    } catch (syncError) {
      setError(syncError instanceof Error ? syncError.message : 'Unable to start sync.')
    } finally {
      setBusy(false)
    }
  }

  const saveSchedule = async () => {
    try {
      setSavingSchedule(true)
      setError(null)
      const payload = {
        enabled: scheduleEnabled,
        interval_minutes: Number(scheduleIntervalMinutes),
        sync_type: scheduleSyncType,
      }
      const scheduleData = await fetchJsonRequired<ScheduleResponse>('/schedule', {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      setSchedule(scheduleData)
      setScheduleEnabled(scheduleData.enabled)
      setScheduleIntervalMinutes(String(scheduleData.interval_minutes))
      setScheduleSyncType((scheduleData.sync_type as SyncType) ?? 'full')
      await loadDashboard()
    } catch (scheduleError) {
      setError(scheduleError instanceof Error ? scheduleError.message : 'Unable to save schedule.')
    } finally {
      setSavingSchedule(false)
    }
  }

  const testConnection = async () => {
    try {
      setTestingConnection(true)
      setError(null)
      const result = await fetchJsonRequired<ConnectionTestResponse>('/connection/test', {
        method: 'POST',
        body: JSON.stringify({
          tenant_id: tenantId,
          client_id: clientId,
          client_secret: clientSecret || undefined,
          graph_scope: graphScope,
        }),
      })
      setConnectionTestResult(result)
    } catch (connectionError) {
      setError(connectionError instanceof Error ? connectionError.message : 'Unable to test the connection.')
    } finally {
      setTestingConnection(false)
    }
  }

  const saveRetryConfig = async () => {
    try {
      setSavingRetryConfig(true)
      setError(null)
      const data = await fetchJsonRequired<RetryConfigResponse>('/retry-config', {
        method: 'PUT',
        body: JSON.stringify({
          max_retry_attempts: Number(retryAttempts),
          max_retry_delay_seconds: Number(retryDelaySeconds),
        }),
      })
      setRetryConfig(data)
      setRetryAttempts(String(data.max_retry_attempts))
      setRetryDelaySeconds(String(data.max_retry_delay_seconds))
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : 'Unable to save retry configuration.')
    } finally {
      setSavingRetryConfig(false)
    }
  }

  const deleteRun = async (runId: string) => {
    try {
      setError(null)
      await fetchJson(`/runs/${runId}`, { method: 'DELETE' })
      await loadDashboard()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'Unable to delete run.')
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
        value: schedule?.enabled
          ? `Every ${schedule.interval_minutes} min (${schedule.sync_type})`
          : 'Disabled',
        tone: schedule?.enabled ? 'info' : 'neutral',
      },
    ],
    [health?.graph_reachable, schedule?.enabled, schedule?.interval_minutes, schedule?.sync_type, status?.latest_run?.status, status?.running],
  )

  return (
    <main className="shell">
      <header className="hero-panel">
        <div>
          <p className="eyebrow">Admin Console developed by Paul Cripsey</p>
          <h1>Entra ID governance export monitor</h1>
          <p className="hero-copy">
            Manage Microsoft Entra connectivity, monitor export execution, and review CSV collector output
            paths from one operational dashboard.
          </p>
        </div>
        <div className="hero-actions">
          <label className="sync-type-label">
            <span>Sync type</span>
            <select
              value={syncType}
              onChange={(event) => setSyncType(event.target.value as SyncType)}
              disabled={busy || status?.running}
            >
              <option value="full">Full</option>
              <option value="incremental">Incremental</option>
            </select>
          </label>
          <button className="primary-action" onClick={() => void triggerSync()} disabled={busy || status?.running}>
            {busy || status?.running ? 'Export running…' : 'Run export now'}
          </button>
        </div>
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
          {status?.running && status.live_progress ? (
            <div className="live-progress">
              <p className="live-stage">{status.live_progress.stage}…</p>
              <dl className="detail-list compact">
                <div><dt>Users</dt><dd>{status.live_progress.users_fetched.toLocaleString()}</dd></div>
                <div><dt>Groups</dt><dd>{status.live_progress.groups_fetched.toLocaleString()}</dd></div>
                <div><dt>Group memberships</dt><dd>{status.live_progress.memberships_fetched.toLocaleString()}</dd></div>
                <div><dt>Roles</dt><dd>{status.live_progress.roles_fetched.toLocaleString()}</dd></div>
                <div><dt>Role memberships</dt><dd>{status.live_progress.role_memberships_fetched.toLocaleString()}</dd></div>
              </dl>
            </div>
          ) : null}
        </article>

        <article className="panel">
          <div className="panel-header">
            <h2>Connection configuration</h2>
            <span className="pill neutral">Env-backed</span>
          </div>
          <div className="schedule-form">
            <label>
              <span>Tenant ID</span>
              <input type="text" value={tenantId} onChange={(event) => setTenantId(event.target.value)} />
            </label>
            <label>
              <span>Client ID</span>
              <input type="text" value={clientId} onChange={(event) => setClientId(event.target.value)} />
            </label>
            <label>
              <span>Client secret</span>
              <input
                type="password"
                placeholder={config?.masked_client_secret || 'Enter secret for connection test'}
                value={clientSecret}
                onChange={(event) => setClientSecret(event.target.value)}
              />
            </label>
            <label>
              <span>Graph scope</span>
              <input type="text" value={graphScope} onChange={(event) => setGraphScope(event.target.value)} />
            </label>
            <div className="actions-row">
              <button className="secondary-action" onClick={() => void testConnection()} disabled={testingConnection}>
                {testingConnection ? 'Testing…' : 'Test connection'}
              </button>
              <span className={`pill ${connectionTestResult?.success ? 'success' : 'neutral'}`}>
                {connectionTestResult ? connectionTestResult.detail : 'Use values above to test Entra access'}
              </span>
            </div>
            <dl className="detail-list compact">
              <div>
                <dt>Runtime secret</dt>
                <dd>{config?.masked_client_secret || 'Not configured'}</dd>
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
          </div>
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
              <span>Sync type</span>
              <select
                value={scheduleSyncType}
                onChange={(event) => setScheduleSyncType(event.target.value as SyncType)}
              >
                <option value="full">Full — re-fetch everything</option>
                <option value="incremental">Incremental — delta changes only</option>
              </select>
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

        <article className="panel">
          <div className="panel-header">
            <h2>Retry configuration</h2>
            <span className="pill neutral">Throttle control</span>
          </div>
          <div className="schedule-form">
            <label>
              <span>Max retry attempts</span>
              <input
                type="number"
                min={1}
                max={20}
                value={retryAttempts}
                onChange={(event) => setRetryAttempts(event.target.value)}
              />
            </label>
            <label>
              <span>Max retry delay (seconds)</span>
              <input
                type="number"
                min={1}
                max={300}
                value={retryDelaySeconds}
                onChange={(event) => setRetryDelaySeconds(event.target.value)}
              />
            </label>
            <label>
              <span>Last updated</span>
              <input type="text" disabled value={formatDate(retryConfig?.updated_at ?? null)} />
            </label>
            <button className="secondary-action" onClick={() => void saveRetryConfig()} disabled={savingRetryConfig}>
              {savingRetryConfig ? 'Saving…' : 'Save retry config'}
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
                  <th>Type</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Completed</th>
                  <th>Users</th>
                  <th>Groups</th>
                  <th>Memberships</th>
                  <th>Roles</th>
                  <th>Role memberships</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {runs.length === 0 ? (
                  <tr>
                    <td colSpan={11}>No sync runs recorded.</td>
                  </tr>
                ) : (
                  runs.map((run) => (
                    <tr key={run.id}>
                      <td className="mono">{run.id}</td>
                      <td>{run.sync_type}</td>
                      <td>{run.status}</td>
                      <td>{formatDate(run.started_at)}</td>
                      <td>{formatDate(run.completed_at)}</td>
                      <td>{run.users_count ?? '—'}</td>
                      <td>{run.groups_count ?? '—'}</td>
                      <td>{run.memberships_count ?? '—'}</td>
                      <td>{run.roles_count ?? '—'}</td>
                      <td>{run.role_memberships_count ?? '—'}</td>
                      <td>
                        <button
                          className="secondary-action"
                          onClick={() => void deleteRun(run.id)}
                          disabled={run.id === status?.active_run_id}
                          title={run.id === status?.active_run_id ? 'Cannot delete an active run' : 'Delete run'}
                        >
                          Delete
                        </button>
                      </td>
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
              <dt>Roles CSV</dt>
              <dd>{status?.latest_run?.roles_file ?? 'Awaiting first export'}</dd>
            </div>
            <div>
              <dt>Role memberships CSV</dt>
              <dd>{status?.latest_run?.role_memberships_file ?? 'Awaiting first export'}</dd>
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
