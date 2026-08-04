import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';


function getPulseRuntimeConfig() {
  const cfg = window.__PULSE_RUNTIME_CONFIG;
  return cfg && typeof cfg === 'object' ? cfg : null;
}

function apiUrl(apiBase, path) {
  const runtime = getPulseRuntimeConfig();
  const normalizedPath = String(path || '');

  if (runtime && typeof runtime.getApiUrl === 'function') {
    const runtimeUrl = runtime.getApiUrl(normalizedPath.replace(/^\/api\//, ''));
    if (runtimeUrl) return runtimeUrl;
  }

  if (runtime && typeof runtime.apiBaseUrl === 'string' && runtime.apiBaseUrl) {
    return `${runtime.apiBaseUrl}${normalizedPath}`;
  }

  // If `apiBase` is empty, fall back to a relative URL.
  // This keeps requests under the current Code Studio base path (important for 8995).
  // In preview mode (3000), `apiBase` should be inferred or configured.
  return apiBase ? `${apiBase}${normalizedPath}` : `.${normalizedPath}`;
}

function PulseSection({ title, children }) {
  return (
    <div className="PulseCard">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString();
  } catch (_) {
    return iso;
  }
}

function parseIsoDateLabel(label) {
  const value = String(label || '').trim();
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;

  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
}

function formatChartDateLabel(value) {
  const parsed = parseIsoDateLabel(value);
  if (!parsed) return String(value || '');
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(parsed);
}

function fillDailySeriesGaps(points) {
  const normalized = (points || [])
    .map((point) => {
      const rawLabel = String(point?.label || '').trim();
      const isoDate = parseIsoDateLabel(rawLabel);
      return {
        rawLabel,
        isoDate,
        value: Number(point?.value || 0),
      };
    })
    .filter((point) => point.rawLabel);

  if (!normalized.length) return [];

  const allIsoDates = normalized.every((point) => point.isoDate instanceof Date);
  if (!allIsoDates) {
    return normalized.map((point) => ({
      label: point.rawLabel,
      displayLabel: point.rawLabel,
      value: point.value,
    }));
  }

  const dailyTotals = new Map();
  normalized.forEach((point) => {
    dailyTotals.set(point.rawLabel, (dailyTotals.get(point.rawLabel) || 0) + point.value);
  });

  const sortedLabels = Array.from(dailyTotals.keys()).sort();
  const start = parseIsoDateLabel(sortedLabels[0]);
  const end = parseIsoDateLabel(sortedLabels[sortedLabels.length - 1]);
  if (!start || !end) {
    return sortedLabels.map((label) => ({
      label,
      displayLabel: label,
      value: dailyTotals.get(label) || 0,
    }));
  }

  const filled = [];
  const current = new Date(start.getTime());
  while (current.getTime() <= end.getTime()) {
    const label = current.toISOString().slice(0, 10);
    filled.push({
      label,
      displayLabel: formatChartDateLabel(label),
      value: dailyTotals.get(label) || 0,
    });
    current.setUTCDate(current.getUTCDate() + 1);
  }

  return filled;
}

function formatDocumentationCoverageStatus(status) {
  switch (status) {
    case 'complete':
      return 'Well documented';
    case 'partial':
      return 'Some context';
    case 'sparse':
    default:
      return 'Minimal context';
  }
}

function Badge({ children }) {
  return <span className="PulseBadge">{children}</span>;
}

function InfoTip({ text }) {
  return (
    <span className="PulseInfoTip" tabIndex={0} aria-label={text} title={text}>
      ⓘ
    </span>
  );
}

function ActionBadge({ children, onClick, title }) {
  return (
    <button type="button" className="PulseBadge PulseBadgeAction" onClick={onClick} title={title}>
      {children}
    </button>
  );
}

function Modal({ title, onClose, children }) {
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div className="PulseModalOverlay" role="dialog" aria-modal="true" onMouseDown={onClose}>
      <div className="PulseModal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="PulseModalHeader">
          <div className="PulseModalTitle">{title}</div>
          <button className="PulseButton" onClick={onClose} type="button">
            Close
          </button>
        </div>
        <div className="PulseModalBody">{children}</div>
      </div>
    </div>
  );
}

function RightFilterPanel({ expanded, onToggle, filterPanel, children, width = 320 }) {
  return (
    <>
      <div className={`PulseRightPanelTabWrap ${expanded ? 'PulseRightPanelTabWrapHidden' : ''}`}>
        <button className="PulseRightPanelTab" type="button" onClick={() => onToggle(true)}>
          Filters ◀
        </button>
      </div>

      <div className={`PulseRightPanelLayout ${expanded ? 'PulseRightPanelLayoutOpen' : 'PulseRightPanelLayoutClosed'}`} style={{ '--pulse-right-panel-width': `${width}px` }}>
        <div className={`PulseRightPanelContent ${expanded ? 'PulseRightPanelContentNarrow' : 'PulseRightPanelContentFull'}`}>
          {children}
        </div>

        <aside className={`PulseRightPanelDrawer ${expanded ? 'PulseRightPanelDrawerOpen' : 'PulseRightPanelDrawerClosed'}`}>
          <div className="PulseRightPanelInner">
            <div className="PulseResultsHeader">
              <h2 style={{ marginBottom: 0 }}>Filters</h2>
              <button className="PulseButton" type="button" onClick={() => onToggle(false)}>
                ▶
              </button>
            </div>
            {filterPanel}
          </div>
        </aside>
      </div>
    </>
  );
}

function FilterPageLayout({ filtersExpanded, onOpenFilters, onCloseFilters, filterContent, children }) {
  return (
    <RightFilterPanel
      expanded={filtersExpanded}
      onToggle={(nextOpen) => {
        if (nextOpen) {
          onOpenFilters();
        } else {
          onCloseFilters();
        }
      }}
      filterPanel={filterContent}
    >
      {children}
    </RightFilterPanel>
  );
}

function UserInformationSection({
  detail,
  detailInstances,
  selectedInstance,
  expanded,
  onToggle,
  loadingMessage = 'Loading user details…',
}) {
  const hasDirectoryCoverage = Boolean(detail) || (detailInstances || []).length > 0;

  return (
    <PulseSection title="User Information">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 10 }}>
        <div className="PulseMuted">
          Click to {expanded ? 'hide' : 'show'} profile details and per-instance directory records.
        </div>
        <button className="PulseButton" type="button" onClick={onToggle}>
          {expanded ? 'Hide details' : 'Show details'}
        </button>
      </div>

      {expanded ? (
        detail ? (
          <>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              User account details for {selectedInstance ? `the selected instance (${selectedInstance})` : 'the best available instance-level record'}.
            </div>
            <div className="PulseDetailGrid">
              <div>
                <div className="PulseMuted">Display name</div>
                <div style={{ fontWeight: 800, fontSize: 16 }}>{detail.display_name || '-'}</div>
              </div>
              <div>
                <div className="PulseMuted">Profile</div>
                <div style={{ fontWeight: 800, fontSize: 16 }}>{detail.user_profile || '-'}</div>
              </div>
              <div>
                <div className="PulseMuted">Primary instance</div>
                <div style={{ fontWeight: 800, fontSize: 16 }}>{detail.instance_name || '-'}</div>
              </div>
              <div>
                <div className="PulseMuted">Email</div>
                <div style={{ fontWeight: 800, fontSize: 16 }}>{detail.email || '-'}</div>
              </div>
              <div>
                <div className="PulseMuted">Enabled</div>
                <div style={{ fontWeight: 800, fontSize: 16 }}>{detail.enabled == null ? '-' : (detail.enabled ? 'Yes' : 'No')}</div>
              </div>
            </div>
          </>
        ) : (
          <div className="PulseMuted">{hasDirectoryCoverage ? 'No preferred user directory record was identified.' : loadingMessage}</div>
        )
      ) : null}

      {expanded ? (
        <>
          <div className="PulseMuted" style={{ margin: '16px 0 8px' }}>Directory Records by Instance</div>
          {detailInstances.length ? (
            <div className="PulseTableWrap">
              <table className="PulseTable">
                <thead>
                  <tr>
                    <th>Instance</th>
                    <th>Display name</th>
                    <th>Email</th>
                    <th>Profile</th>
                    <th>Enabled</th>
                  </tr>
                </thead>
                <tbody>
                  {detailInstances.map((r, idx) => (
                    <tr key={`${r.instance_name || r.instanceName || 'inst'}__${r.login || idx}`}>
                      <td><Badge>{r.instance_name || r.instanceName || '-'}</Badge></td>
                      <td>{r.display_name || r.displayName || '-'}</td>
                      <td>{r.email || '-'}</td>
                      <td>{r.user_profile || r.userProfile || '-'}</td>
                      <td>{r.enabled == null ? '-' : (r.enabled ? 'Yes' : 'No')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="PulseMuted">
              {hasDirectoryCoverage ? 'No instance-level directory records were found for this user.' : 'No directory coverage is available for this actor in the loaded history.'}
            </div>
          )}
        </>
      ) : null}
    </PulseSection>
  );
}

function UserDashboard({
  apiBase = '',
  login,
  mode = 'organization',
  selectedInstance = '',
  windowValue = 'last_3_months',
  title,
  subtitle,
  showContextBadges = true,
  showCloseButton = false,
  onClose,
}) {
  const [userDetail, setUserDetail] = useState(null);
  const [topProjects, setTopProjects] = useState([]);
  const [userTrendMode, setUserTrendMode] = useState('developing');
  const [showUserInformation, setShowUserInformation] = useState(false);
  const [loadingSections, setLoadingSections] = useState({});
  const [error, setError] = useState('');

  const beginLoad = useCallback((key) => {
    setLoadingSections((prev) => ({ ...prev, [key]: true }));
  }, []);

  const endLoad = useCallback((key) => {
    setLoadingSections((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const setRequestError = useCallback((message) => {
    setError(message || 'Unexpected user dashboard error');
  }, []);

  const clearRequestError = useCallback(() => {
    setError('');
  }, []);

  const isAbortError = (e) => e?.name === 'AbortError';

  const fetchJson = useCallback(async (path, { params, signal } = {}) => {
    const suffix = params ? `?${params.toString()}` : '';
    const response = await fetch(apiUrl(apiBase, `${path}${suffix}`), { signal });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || `Failed loading ${path}`);
    return data;
  }, [apiBase]);

  useEffect(() => {
    setUserDetail(null);
    setTopProjects([]);
    setUserTrendMode('developing');
    setShowUserInformation(false);
    clearRequestError();

    if (!login) {
      return undefined;
    }

    const detailController = new AbortController();
    const projectsController = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowValue);
    if (selectedInstance) params.set('instance_name', selectedInstance);

    beginLoad('userDetail');
    fetchJson(`/api/build/users/${encodeURIComponent(login)}`, { params, signal: detailController.signal })
      .then((data) => {
        setUserDetail(data);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('userDetail'));

    beginLoad('topProjects');
    fetchJson(`/api/build/users/${encodeURIComponent(login)}/top-projects`, { params, signal: projectsController.signal })
      .then((data) => {
        setTopProjects(data.rows || []);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('topProjects'));

    return () => {
      detailController.abort();
      projectsController.abort();
    };
  }, [beginLoad, clearRequestError, endLoad, fetchJson, login, selectedInstance, setRequestError, windowValue]);

  const loading = Object.keys(loadingSections).length > 0;
  const detail = userDetail?.user || null;
  const detailInstances = userDetail?.instances || [];
  const summary = userDetail?.summary || null;
  const activityDailyCreating = (userDetail?.activityDaily || []).map((p) => ({
    label: p.label,
    value: Number(p.developing ?? 0),
  }));
  const activityDailyConsuming = (userDetail?.activityDaily || []).map((p) => ({
    label: p.label,
    value: Number(p.viewing ?? 0),
  }));
  const activityMonthlyCreating = (userDetail?.activityMonthly || []).map((p) => ({
    label: String(p.month || '').slice(0, 7),
    value: Number(p.developing ?? 0),
  }));
  const activityMonthlyConsuming = (userDetail?.activityMonthly || []).map((p) => ({
    label: String(p.month || '').slice(0, 7),
    value: Number(p.viewing ?? 0),
  }));
  const hasActivityInWindow = Boolean((summary?.total_actions ?? ((summary?.viewing ?? 0) + (summary?.developing ?? 0))) > 0);
  const activityWindowLabel = summary?.months ? `${summary.months} month${summary.months === 1 ? '' : 's'}` : `${summary?.days || 30} day${(summary?.days || 30) === 1 ? '' : 's'}`;
  const normalizedWindowBadge = windowValue.replaceAll('_', ' ');
  const resolvedTitle = title || `${detail?.display_name || detail?.login || login} Dashboard`;
  const resolvedSubtitle = subtitle || (mode === 'self'
    ? 'Your personal Dataiku Pulse activity overview.'
    : 'Detailed user activity overview.');

  if (!login) {
    return <div className="PulseMuted">User login is not available.</div>;
  }

  return (
    <div>
      {title || subtitle ? (
        <div className="PulseHero">
          <h1>{resolvedTitle}</h1>
          {resolvedSubtitle ? <p>{resolvedSubtitle}</p> : null}
        </div>
      ) : null}

      {error ? <div className="PulseError">{error}</div> : null}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {showContextBadges ? <Badge>{login}</Badge> : null}
        {showContextBadges ? <Badge>{normalizedWindowBadge}</Badge> : null}
        {showContextBadges && detail?.display_name && detail?.login && detail.display_name !== detail.login ? <Badge>{detail.login}</Badge> : null}
        {showContextBadges ? (selectedInstance ? <Badge>{selectedInstance}</Badge> : <Badge>All instances</Badge>) : null}
        {showCloseButton ? <button className="PulseButton" type="button" onClick={onClose}>Close</button> : null}
      </div>

      {loading && !userDetail ? <div className="PulseMuted" style={{ marginBottom: 12 }}>Loading user dashboard…</div> : null}

      <UserInformationSection
        detail={detail}
        detailInstances={detailInstances}
        selectedInstance={selectedInstance}
        expanded={showUserInformation}
        onToggle={() => setShowUserInformation((v) => !v)}
        loadingMessage="Loading user details…"
      />

      <PulseSection title="User activity summary">
        {summary ? (
          <>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Activity totals for the selected {activityWindowLabel} window{selectedInstance ? ` on ${selectedInstance}` : ' across all instances'}.
              {!hasActivityInWindow ? ' No consuming or creating activity was found in this window.' : ''}
            </div>
            <div className="PulseDetailGrid">
              <div>
                <div className="PulseMuted">Viewing actions</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(summary.viewing || 0).toLocaleString()}</div>
              </div>
              <div>
                <div className="PulseMuted">Creation actions</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(summary.developing || 0).toLocaleString()}</div>
              </div>
              <div>
                <div className="PulseMuted">Total actions</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(summary.total_actions || 0).toLocaleString()}</div>
              </div>
              <div>
                <div className="PulseMuted">Primary activity type</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{summary.activity_mode ? String(summary.activity_mode).replace('_', ' ') : '-'}</div>
              </div>
              <div>
                <div className="PulseMuted">Active instances</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(summary.instances || 0).toLocaleString()}</div>
              </div>
              <div>
                <div className="PulseMuted">Active projects</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(summary.projects || 0).toLocaleString()}</div>
              </div>
            </div>
          </>
        ) : (
          <div className="PulseMuted">{loading ? 'Loading activity summary…' : 'No activity summary is available for this user.'}</div>
        )}
      </PulseSection>

      <PulseSection title="Activity trend">
        {hasActivityInWindow ? (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <button
                className={`PulseButton ${userTrendMode === 'developing' ? 'PulseButtonToggleActive' : ''}`}
                type="button"
                onClick={() => setUserTrendMode('developing')}
              >
                Creation
              </button>
              <button
                className={`PulseButton ${userTrendMode === 'viewing' ? 'PulseButtonToggleActive' : ''}`}
                type="button"
                onClick={() => setUserTrendMode('viewing')}
              >
                Viewing
              </button>
            </div>
            <div className="PulseVizGrid">
              <LineChart
                title={userTrendMode === 'viewing' ? 'Viewing activity by day' : 'Creation activity by day'}
                points={userTrendMode === 'viewing' ? activityDailyConsuming : activityDailyCreating}
              />
              <LineChart
                title={userTrendMode === 'viewing' ? 'Viewing activity by month' : 'Creation activity by month'}
                points={userTrendMode === 'viewing' ? activityMonthlyConsuming : activityMonthlyCreating}
              />
            </div>
          </>
        ) : (
          <div className="PulseMuted">
            {loading ? 'Loading activity trend…' : `No consuming or creating activity was identified for this user in the selected window${selectedInstance ? ` on ${selectedInstance}` : ''}.`}
          </div>
        )}
      </PulseSection>

      <PulseSection title="Most active projects">
        <div className="PulseMuted" style={{ marginBottom: 8 }}>
          Projects are shown by instance and project key.
        </div>
        {topProjects.length ? (
          <div className="PulseTableWrap">
            <table className="PulseTable">
              <thead>
                <tr>
                  <th>Instance</th>
                  <th>Project Key</th>
                  <th>Creating</th>
                  <th>Consuming</th>
                </tr>
              </thead>
              <tbody>
                {topProjects.map((r) => (
                  <tr key={`${r.instanceName}__${r.projectKey}`}>
                    <td><Badge>{r.instanceName}</Badge></td>
                    <td><Badge>{r.projectKey}</Badge></td>
                    <td>{r.developing}</td>
                    <td>{r.viewing}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="PulseMuted">{loading ? 'Loading project activity…' : 'No project activity is available for this user in the selected window.'}</div>
        )}
      </PulseSection>
    </div>
  );
}

function BuildAssetsInventoryPage({
  apiBase = '',
  embedded = false,
  title = 'Assets Inventory',
  description =
    'Explore all assets (projects, datasets, recipes, etc.) across instances with filtering and details capabilities',
  endpointBase = '/api/build/assets',
  facetsEndpoint = '/api/build/assets/facets',
  typeFacetLabel = 'Object type',
  typeColumnLabel = 'Type',
  detailsTitle = 'Asset details',
  typeDetailLabel = 'Type',
} = {}) {
  const [allAssets, setAllAssets] = useState([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState({ instances: [], projects: [], types: [], owners: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [q, setQ] = useState('');
  const [selectedInstances, setSelectedInstances] = useState([]);
  const [selectedProjects, setSelectedProjects] = useState([]);
  const [selectedTypes, setSelectedTypes] = useState([]);
  const [owner, setOwner] = useState('');
  const [completenessStatus, setCompletenessStatus] = useState('');
  const [sort, setSort] = useState('updated_desc');

  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);

  const [selectedAssetId, setSelectedAssetId] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const [detailsInfo, setDetailsInfo] = useState(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState('');
  const [metadataSummary, setMetadataSummary] = useState({ summary: {}, byType: [] });
  const [filtersExpanded, setFiltersExpanded] = useState(false);


  const filtered = useMemo(() => allAssets, [allAssets]);

  const pageItems = filtered;

  const selectedAsset = useMemo(() => {
    if (!selectedAssetId) return null;
    return allAssets.find((a) => a.assetId === selectedAssetId) || null;
  }, [allAssets, selectedAssetId]);

  const openDetails = (assetId) => {
    setSelectedAssetId(assetId);
    setDetailsOpen(true);
  };

  const closeDetails = () => {
    setDetailsOpen(false);
  };

  const detailsEndpoint = `${endpointBase}/details`;
  const metadataSummaryEndpoint = `${endpointBase}/metadata-summary`;

  useEffect(() => {
    let cancelled = false;

    fetch(apiUrl(apiBase, metadataSummaryEndpoint))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading metadata summary');
        if (!cancelled) {
          setMetadataSummary({
            summary: data.summary || {},
            byType: data.byType || [],
          });
        }
      })
      .catch(() => {
        if (!cancelled) setMetadataSummary({ summary: {}, byType: [] });
      });

    return () => {
      cancelled = true;
    };
  }, [apiBase, metadataSummaryEndpoint]);

  useEffect(() => {
    if (!detailsOpen || !selectedAssetId) {
      setDetailsInfo(null);
      setDetailsError('');
      setDetailsLoading(false);
      return;
    }

    let cancelled = false;

    const load = async () => {
      setDetailsLoading(true);
      setDetailsError('');
      setDetailsInfo(null);

      try {
        const res = await fetch(
          apiUrl(apiBase, `${detailsEndpoint}?assetId=${encodeURIComponent(selectedAssetId)}`)
        );
        const raw = await res.text();
        let data;
        try {
          data = JSON.parse(raw);
        } catch (_) {
          throw new Error(`Non-JSON response (${res.status}): ${raw.slice(0, 200)}`);
        }

        if (!res.ok || !data.ok) throw new Error(data?.error || 'Failed loading details');
        if (!cancelled) setDetailsInfo(data);
      } catch (e) {
        if (!cancelled) setDetailsError(e.message);
      } finally {
        if (!cancelled) setDetailsLoading(false);
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [apiBase, detailsEndpoint, detailsOpen, selectedAssetId]);

  const applyQuickFilter = ({ instanceName, projectKey, objectType, ownerLogin }) => {
    // “Reset to tag”: clear all filters then apply the requested one(s)
    setQ('');
    setSelectedInstances([]);
    setSelectedProjects([]);
    setSelectedTypes([]);
    setOwner('');

    if (instanceName) setSelectedInstances([instanceName]);
    if (projectKey) setSelectedProjects([projectKey]);
    if (objectType) setSelectedTypes([objectType]);
    if (ownerLogin) setOwner(ownerLogin);

    setOffset(0);
    setDetailsOpen(false);
  };

  useEffect(() => {
    // Reset pagination when filters change
    setOffset(0);
  }, [q, selectedInstances, selectedProjects, selectedTypes, owner, sort, limit]);

  // Load facets once (for filter rail)
  useEffect(() => {
    setError('');
    fetch(apiUrl(apiBase, facetsEndpoint))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading facets');
        setFacets({
          instances: data.instances || [],
          projects: data.projects || [],
          types: data.types || [],
          owners: data.owners || [],
        });
      })
      .catch((e) => setError(e.message));
  }, [apiBase, facetsEndpoint]);

  // Load paginated rows from DuckDB
  useEffect(() => {
    const params = new URLSearchParams();
    if (q.trim()) params.set('q', q.trim());
    if (owner.trim()) params.set('owner', owner.trim());
    if (selectedInstances.length) params.set('instances', selectedInstances.join(','));
    if (selectedProjects.length) params.set('projects', selectedProjects.join(','));
    if (selectedTypes.length) params.set('types', selectedTypes.join(','));
    if (completenessStatus) params.set('completenessStatus', completenessStatus);
    params.set('sort', sort);
    params.set('limit', String(limit));
    params.set('offset', String(offset));

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `${endpointBase}?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading rows');
        setAllAssets(data.rows || []);
        setTotal(data.total || 0);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [
    apiBase,
    endpointBase,
    q,
    owner,
    selectedInstances,
    selectedProjects,
    selectedTypes,
    sort,
    completenessStatus,
    limit,
    offset,
  ]);

  const toggleMulti = (value, current, setFn) => {
    if (current.includes(value)) {
      setFn(current.filter((v) => v !== value));
    } else {
      setFn([...current, value]);
    }
  };

  const resetFilters = () => {
    setQ('');
    setSelectedInstances([]);
    setSelectedProjects([]);
    setSelectedTypes([]);
    setOwner('');
    setSort('updated_desc');
    setLimit(25);
    setOffset(0);
    setSelectedAssetId(null);
    setDetailsOpen(false);
  };

  return (
    <div className={embedded ? undefined : 'PulseWide'}>
      {!embedded ? (
        <div className="PulseHero">
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
      ) : null}

      <FilterPageLayout
        filtersExpanded={filtersExpanded}
        onOpenFilters={() => setFiltersExpanded(true)}
        onCloseFilters={() => setFiltersExpanded(false)}
        filterContent={(
          <>
            {loading ? <div className="PulseMuted" style={{ marginTop: 8 }}>Loading…</div> : null}
            {error ? <div className="PulseError">{error}</div> : null}
            <div className={embedded ? 'PulseFilterRail PulseFilterRailEmbedded' : 'PulseFilterRail'}>
              <div className={embedded ? 'PulseCard PulseCardTight' : 'PulseCard'}>
                <label className="PulseLabel">
                  Search
                  <input
                    className="PulseInput"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder="Search name, key, owner, project..."
                  />
                </label>

                <label className="PulseLabel">
                  Owner (dss_login)
                  <input
                    className="PulseInput"
                    value={owner}
                    onChange={(e) => setOwner(e.target.value)}
                    placeholder="e.g. alice"
                  />
                </label>

                <div className="PulseLabel">Instance</div>
                <div className="PulseCheckboxList">
                  {facets.instances.map((inst) => (
                    <label key={inst} className="PulseCheckboxRow">
                      <input
                        type="checkbox"
                        checked={selectedInstances.includes(inst)}
                        onChange={() => toggleMulti(inst, selectedInstances, setSelectedInstances)}
                      />
                      <span>{inst}</span>
                    </label>
                  ))}
                </div>

                <div className="PulseLabel">Project</div>
                <div className="PulseCheckboxList">
                  {facets.projects.map((pk) => (
                    <label key={pk} className="PulseCheckboxRow">
                      <input
                        type="checkbox"
                        checked={selectedProjects.includes(pk)}
                        onChange={() => toggleMulti(pk, selectedProjects, setSelectedProjects)}
                      />
                      <span>{pk}</span>
                    </label>
                  ))}
                </div>

                <div className="PulseLabel">{typeFacetLabel}</div>
                <div className="PulseCheckboxList">
                  {facets.types.map((t) => (
                    <label key={t} className="PulseCheckboxRow">
                      <input
                        type="checkbox"
                        checked={selectedTypes.includes(t)}
                        onChange={() => toggleMulti(t, selectedTypes, setSelectedTypes)}
                      />
                      <span>{t}</span>
                    </label>
                  ))}
                </div>

                <label className="PulseLabel">
                  Documentation coverage
                  <select className="PulseSelect" value={completenessStatus} onChange={(e) => setCompletenessStatus(e.target.value)}>
                    <option value="">All statuses</option>
                    <option value="complete">Well documented</option>
                    <option value="partial">Some context</option>
                    <option value="sparse">Minimal context</option>
                  </select>
                </label>

                <label className="PulseLabel">
                  Sort
                  <select className="PulseSelect" value={sort} onChange={(e) => setSort(e.target.value)}>
                    <option value="updated_desc">Most recently updated</option>
                    <option value="updated_asc">Least recently updated</option>
                    <option value="activity_desc">Most active (30d)</option>
                    <option value="completeness_desc">Best documented</option>
                    <option value="completeness_asc">Least documented</option>
                    <option value="name_asc">Name (A→Z)</option>
                  </select>
                </label>

                <label className="PulseLabel">
                  Page size
                  <select className="PulseSelect" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                </label>

                <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                  <button className="PulseButton" type="button" onClick={resetFilters}>Reset</button>
                </div>
              </div>
            </div>
          </>
        )}
      >
        <div className="PulseResults">
          <div className={embedded ? 'PulseCard PulseCardTight' : 'PulseCard'}>
            <div className="PulseResultsHeader">
              <div>
                <h2 style={{ marginBottom: 4 }}>Documentation coverage</h2>
                <div className="PulseMuted">This indicates how much useful context Pulse has captured for each asset. A higher score means the asset is easier for someone else to recognize, trust, and reuse because basic details like what it is, who owns it, and how recently it changed are available.</div>
                <div className="PulseCallout">
                  <div className="PulseCalloutTitle">Ways to improve the score</div>
                  <ul className="PulseCalloutList">
                    <li>Add a clear name and description</li>
                    <li>Make sure ownership is assigned</li>
                    <li>Keep the asset updated so recent activity is visible</li>
                  </ul>
                </div>
              </div>
            </div>

            <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
              <div className="PulseSummaryTile PulseSummaryTileStatic">
                <div className="PulseSummaryCount">{Number(metadataSummary.summary?.avgScore || 0).toFixed(1)}%</div>
                <div className="PulseSummaryLabel">Average coverage score</div>
              </div>
              <div className="PulseSummaryTile PulseSummaryTileStatic">
                <div className="PulseSummaryCount">{Number(metadataSummary.summary?.completeCount || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Well documented</div>
              </div>
              <div className="PulseSummaryTile PulseSummaryTileStatic">
                <div className="PulseSummaryCount">{Number(metadataSummary.summary?.partialCount || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Some context</div>
              </div>
              <div className="PulseSummaryTile PulseSummaryTileStatic">
                <div className="PulseSummaryCount">{Number(metadataSummary.summary?.sparseCount || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Minimal context</div>
              </div>
            </div>

            <div className="PulseVizGrid">
              <BarList
                title="Documentation coverage by type"
                rows={(metadataSummary.byType || []).map((row) => ({
                  label: row.label,
                  value: Number(row.avgScore || 0),
                }))}
                maxRows={50}
                formatValue={(value) => `${value.toFixed(1)}%`}
              />
            </div>
          </div>

          <div className={embedded ? 'PulseCard PulseCardTight' : 'PulseCard'}>
            <div className="PulseResultsHeader">
              <div>
                <h2 style={{ marginBottom: 4 }}>Results</h2>
                <div className="PulseMuted">Showing {Math.min(offset + 1, total)}–{Math.min(offset + limit, total)} of {total}</div>
                {loading ? <div className="PulseMuted">Loading…</div> : null}
                {error ? <div className="PulseError">{error}</div> : null}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="PulseButton"
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  disabled={offset === 0}
                >
                  Prev
                </button>
                <button
                  className="PulseButton"
                  onClick={() => setOffset(Math.min(Math.max(0, total - limit), offset + limit))}
                  disabled={offset + limit >= total}
                >
                  Next
                </button>
              </div>
            </div>

            <div className="PulseTableWrap">
              <table className="PulseTable">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>{typeColumnLabel}</th>
                    <th>Instance</th>
                    <th>Project Key</th>
                    <th>Owner</th>
                    <th>Metadata</th>
                    <th>Updated</th>
                    <th>Activity (30d)</th>
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((a) => (
                    <tr
                      key={a.assetId}
                      className={a.assetId === selectedAssetId ? 'PulseRowSelected' : ''}
                    >
                      <td>
                        <button
                          type="button"
                          className="PulseLinkButton"
                          onClick={() => openDetails(a.assetId)}
                        >
                          {a.objectName}
                        </button>
                        <div className="PulseMuted">{a.objectKey}</div>
                      </td>
                      <td><Badge>{a.objectType}</Badge></td>
                      <td><Badge>{a.instanceName}</Badge></td>
                      <td><Badge>{a.projectKey}</Badge></td>
                      <td><Badge>{a.ownerLogin}</Badge></td>
                      <td>
                        <Badge>{formatDocumentationCoverageStatus(a.metadataCompletenessStatus || 'sparse')}</Badge>
                        <div className="PulseMuted">{Number(a.metadataCompletenessScore ?? 0)}%</div>
                      </td>
                      <td>{formatDate(a.updatedAt)}</td>
                      <td>{a.activity30d}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {detailsOpen && selectedAsset ? (
            <Modal title={detailsTitle} onClose={closeDetails}>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                <ActionBadge
                  title="Filter to this object type"
                  onClick={() => applyQuickFilter({ objectType: selectedAsset.objectType })}
                >
                  {selectedAsset.objectType}
                </ActionBadge>

                <ActionBadge
                  title="Filter to this instance"
                  onClick={() => applyQuickFilter({ instanceName: selectedAsset.instanceName })}
                >
                  {selectedAsset.instanceName}
                </ActionBadge>

                <ActionBadge
                  title="Filter to this project within this instance"
                  onClick={() =>
                    applyQuickFilter({
                      instanceName: selectedAsset.instanceName,
                      projectKey: selectedAsset.projectKey,
                    })
                  }
                >
                  {selectedAsset.projectKey}
                </ActionBadge>

                <ActionBadge
                  title="Filter to this owner (dss_login)"
                  onClick={() => applyQuickFilter({ ownerLogin: selectedAsset.ownerLogin })}
                >
                  {selectedAsset.ownerLogin}
                </ActionBadge>
              </div>

              <div style={{ fontWeight: 700, fontSize: 18 }}>{selectedAsset.objectName}</div>
              <div className="PulseMuted" style={{ marginBottom: 12 }}>{selectedAsset.objectKey}</div>

              <div className="PulseDetailGrid">
                <div>
                  <div className="PulseMuted">Updated</div>
                  <div>{formatDate(selectedAsset.updatedAt)}</div>
                </div>
                <div>
                  <div className="PulseMuted">Activity (30d)</div>
                  <div>{selectedAsset.activity30d}</div>
                </div>
                <div>
                  <div className="PulseMuted">Instance</div>
                  <div>{selectedAsset.instanceName}</div>
                </div>
                <div>
                  <div className="PulseMuted">Project</div>
                  <div>{selectedAsset.projectKey}</div>
                </div>
                <div>
                  <div className="PulseMuted">Owner (dss_login)</div>
                  <div>{selectedAsset.ownerLogin}</div>
                </div>
                 <div>
                   <div className="PulseMuted">{typeDetailLabel}</div>
                   <div>{selectedAsset.objectType}</div>
                 </div>

              </div>

              <div style={{ marginTop: 16 }}>
                <div className="PulseMuted" style={{ marginBottom: 6 }}>
                  Captured info
                </div>

                {detailsLoading ? <div className="PulseMuted">Loading details…</div> : null}
                {detailsError ? <div style={{ color: 'salmon' }}>{detailsError}</div> : null}

                {!detailsLoading && !detailsError ? (
                  <ul>
                    <li>
                      Description:{' '}
                      {detailsInfo?.capturedInfo?.description ? detailsInfo.capturedInfo.description : '—'}
                    </li>
                    <li>
                      Documentation coverage:{' '}
                      {formatDocumentationCoverageStatus(selectedAsset?.metadataCompletenessStatus || 'sparse')} ({Number(selectedAsset?.metadataCompletenessScore ?? 0)}%)
                    </li>
                      <li>
                      	Consumption summary (all time):{' '}
                        {Number.isFinite(detailsInfo?.usageSummary?.eventsAllTime)
                          ? detailsInfo.usageSummary.eventsAllTime
                          : 0}
                      </li>
                    <li>
                      Related assets:{' '}
                      {detailsInfo?.relatedAssets?.length ? (
                        <ul>
                          {detailsInfo.relatedAssets.map((r) => (
                            <li key={`${r.instanceName}:${r.projectKey}`}>
                              {r.instanceName}:{r.projectKey}
                              {Number.isFinite(r.eventCount) ? ` (${r.eventCount})` : ''}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        '—'
                      )}
                    </li>
                  </ul>
                ) : null}
              </div>
            </Modal>
          ) : null}
        </div>
      </FilterPageLayout>
    </div>
  );
}

function ReloadDuckDBTab({ apiBase }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [initStatus, setInitStatus] = useState(null);
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());

  const fetchInitStatus = useCallback(async () => {
    try {
      const res = await fetch(apiUrl(apiBase, '/api/startup/init-status'), { cache: 'no-cache' });
      if (!res.ok) return null;
      return await res.json();
    } catch (_) {
      return null;
    }
  }, [apiBase]);

  useEffect(() => {
    if (!loading) return undefined;

    let cancelled = false;
    let timerId = null;

    const poll = async () => {
      const nextStatus = await fetchInitStatus();
      if (cancelled || !nextStatus?.init) return;
      setInitStatus(nextStatus.init);

      if (nextStatus.init.state === 'running') {
        timerId = window.setTimeout(poll, 1000);
      }
    };

    poll();

    return () => {
      cancelled = true;
      if (timerId) window.clearTimeout(timerId);
    };
  }, [fetchInitStatus, loading]);

  useEffect(() => {
    if (!(loading || initStatus?.state === 'running')) return undefined;
    const timerId = window.setInterval(() => {
      setElapsedNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timerId);
  }, [initStatus?.state, loading]);

  const goToSplash = () => {
    const basePath = String(window.location.pathname || '');
    const query = String(window.location.search || '');
    window.location.assign(`${basePath}${query}`);
  };

  const reload = async () => {
    setLoading(true);
    setStatus(null);
    setInitStatus({
      state: 'running',
      message: 'Starting DuckDB reload…',
      startedAt: Date.now() / 1000,
    });
    try {
      const res = await fetch(apiUrl(apiBase, '/api/debug/duckdb/reload'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });

      const raw = await res.text();
      let data;
      try {
        data = JSON.parse(raw);
      } catch (_) {
        throw new Error(`Non-JSON response (${res.status}): ${raw.slice(0, 200)}`);
      }

      if (!res.ok) throw new Error(data?.error || 'Reload failed');
      const latestStatus = await fetchInitStatus();
      if (latestStatus?.init) setInitStatus(latestStatus.init);
      setStatus({ ok: true, data, redirecting: true });
      window.setTimeout(() => {
        goToSplash();
      }, 250);
    } catch (e) {
      setStatus({ ok: false, error: e.message });
    } finally {
      setLoading(false);
    }
  };

  const elapsedSeconds = useMemo(() => {
    if (!initStatus?.startedAt) return null;
    return Math.max(0, Math.round(elapsedNow / 1000 - Number(initStatus.startedAt)));
  }, [elapsedNow, initStatus]);

  const phase = String(initStatus?.phase || 'idle');
  const progressMessage = phase === 'waiting_lock'
    ? 'Another DuckDB initialization or reload appears to still hold the lock. Pulse will continue as soon as that lock is released.'
    : initStatus?.message || 'Starting DuckDB reload…';
  const progressDetails = [];
  if (Number.isFinite(elapsedSeconds)) progressDetails.push(`Elapsed: ${elapsedSeconds}s`);
  if (initStatus?.dbPath) progressDetails.push(initStatus.dbPath);

  const showProgressCard = loading || initStatus?.state === 'running';

  const steps = [
    { key: 'backend', label: 'Connect to the Pulse backend', state: 'done' },
    { key: 'rebuild', label: 'Rebuild the Pulse analytics database', state: 'pending' },
    { key: 'finish', label: 'Return to the Pulse dashboard', state: 'pending' },
  ];

  if (showProgressCard) {
    steps[1].state = phase === 'frontend_ready' ? 'done' : 'active';
    steps[2].state = phase === 'frontend_ready' ? 'active' : 'pending';
  }

  const rebuildSubsteps = [
    ['waiting_lock', 'Waiting for the reload lock'],
    ['preparing_db', 'Preparing the local DuckDB file'],
    ['preparing_reload', 'Opening DuckDB for reload'],
    ['listing_gold', 'Finding GOLD datasets to load'],
    ['loading_gold', 'Loading GOLD datasets into DuckDB'],
    ['inventory_views', 'Creating compatibility views'],
    ['seeding_demo', 'Seeding demo data when needed'],
    ['building_views', 'Building Pulse dashboard views'],
  ];

  const activeSubstepIndex = rebuildSubsteps.findIndex(([key]) => key === phase);
  const renderedSubsteps = rebuildSubsteps.map(([key, label], index) => {
    let state = 'pending';
    if (phase === 'frontend_ready') state = 'done';
    else if (phase === 'failed' || phase === 'unavailable') {
      if (activeSubstepIndex >= 0 && index < activeSubstepIndex) state = 'done';
      else if (activeSubstepIndex === index) state = 'error';
    } else if (activeSubstepIndex >= 0) {
      if (index < activeSubstepIndex) state = 'done';
      else if (index === activeSubstepIndex) state = 'active';
    }
    return { key, label, state };
  });

  const cardStyleForState = (stepState) => {
    if (stepState === 'done') {
      return { border: '1px solid rgba(34,197,94,0.22)', background: '#dcfce7', color: '#166534' };
    }
    if (stepState === 'active') {
      return { border: '1px solid rgba(59,130,246,0.22)', background: '#dbeafe', color: '#1d4ed8' };
    }
    if (stepState === 'error') {
      return { border: '1px solid rgba(239,68,68,0.22)', background: '#fee2e2', color: '#b91c1c' };
    }
    return { border: '1px solid rgba(148,163,184,0.18)', background: '#ffffff', color: '#0f172a' };
  };

  return (
    <div className="PulseWide" style={{ maxWidth: 1600, margin: '0 auto' }}>
      <h2>Reload DuckDB</h2>
      <p>Create/init the DB under <code>/tmp/pulse/dataiku_pulse.db</code>.</p>
      <button onClick={reload} disabled={loading}>
        {loading ? 'Reloading...' : 'Reload DuckDB'}
      </button>
      {showProgressCard ? (
        <div
          style={{
            textAlign: 'left',
            marginTop: 16,
            padding: '18px 20px',
            borderRadius: 16,
            background: 'linear-gradient(180deg, #eff6ff 0%, #e0f2fe 100%)',
            border: '1px solid rgba(59, 130, 246, 0.18)',
            color: '#0f172a',
            boxShadow: '0 16px 32px rgba(37, 99, 235, 0.10)',
          }}
        >
          <div style={{ fontSize: 20, fontWeight: 700, color: '#153e75' }}>Reloading Pulse data</div>
          <div style={{ marginTop: 10, fontSize: 14, lineHeight: 1.6, color: '#334155' }}>{progressMessage}</div>
          <div style={{ marginTop: 14, display: 'grid', gap: 10 }}>
            {steps.map((step, index) => (
              <div key={step.key} style={{ padding: '12px 14px', borderRadius: 12, ...cardStyleForState(step.state) }}>
                <strong>{`Step ${index + 1}:`}</strong> {step.label}
                {step.key === 'rebuild' ? (
                  <div style={{ marginTop: 8, paddingLeft: 14, display: 'grid', gap: 6, fontSize: 12 }}>
                    {renderedSubsteps.map((substep) => (
                      <div key={substep.key} style={{ color: cardStyleForState(substep.state).color }}>
                        <span style={{ display: 'inline-block', width: 14, fontWeight: 700 }}>
                          {substep.state === 'done' ? '✓' : substep.state === 'active' ? '•' : substep.state === 'error' ? '!' : '○'}
                        </span>
                        {substep.label}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          <div style={{ marginTop: 14, fontSize: 12, color: '#64748b' }}>
            {progressDetails.length ? progressDetails.join(' · ') : 'This can take a moment for larger datasets.'}
          </div>
        </div>
      ) : null}
      {status && (
        <div style={{ textAlign: 'left', marginTop: 12 }}>
          {status.ok && status.redirecting ? (
            <div
              style={{
                marginBottom: 12,
                padding: '14px 16px',
                borderRadius: 12,
                background: 'linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%)',
                border: '1px solid rgba(37, 99, 235, 0.18)',
                color: '#1e3a8a',
                boxShadow: '0 10px 24px rgba(30, 64, 175, 0.10)',
              }}
            >
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Reloading Pulse…</div>
              <div style={{ fontSize: 14 }}>
                The database refresh has started. Taking you to the welcome screen now.
              </div>
            </div>
          ) : null}
          <div style={{ marginBottom: 8 }}>
            {status.ok && status.data?.load ? (
              <>
                <div>Loaded tables: <strong>{(status.data.load.loaded || []).length}</strong></div>
                <div>Failed: <strong>{(status.data.load.failed || []).length}</strong></div>
              </>
            ) : null}
          </div>
          <pre style={{ padding: 12, background: '#111', color: '#fff', overflowX: 'auto' }}>
            {JSON.stringify(status, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function ViewDuckDBTab({ apiBase }) {
  const [tables, setTables] = useState([]);
  const [views, setViews] = useState([]);
  const [selected, setSelected] = useState('');
  const [tableInfo, setTableInfo] = useState(null);
  const [loadingTables, setLoadingTables] = useState(false);
  const [loadingInfo, setLoadingInfo] = useState(false);
  const [queryText, setQueryText] = useState('');
  const [runningQuery, setRunningQuery] = useState(false);
  const [queryResult, setQueryResult] = useState(null);
  const [queryError, setQueryError] = useState('');
  const [error, setError] = useState('');

  const objects = useMemo(
    () => [
      ...tables.map((name) => ({ name, type: 'table' })),
      ...views.map((name) => ({ name, type: 'view' })),
    ],
    [tables, views]
  );

  const loadTables = async () => {
    setLoadingTables(true);
    setError('');
    try {
      const res = await fetch(apiUrl(apiBase, '/api/debug/duckdb/tables'));
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Failed to load tables');
      setTables(data.tables || []);
      setViews(data.views || []);
      const objectNames = [
        ...((data.tables || []).map((name) => ({ name, type: 'table' }))),
        ...((data.views || []).map((name) => ({ name, type: 'view' }))),
      ];
      if (objectNames.length && !selected) {
        setSelected(objectNames[0].name);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingTables(false);
    }
  };

  const loadInfo = async (tableName) => {
    if (!tableName) return;
    setLoadingInfo(true);
    setError('');
    setTableInfo(null);
    try {
      const res = await fetch(
        apiUrl(apiBase, `/api/debug/duckdb/table/${encodeURIComponent(tableName)}`)
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Failed to load table info');
      setTableInfo(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingInfo(false);
    }
  };

  useEffect(() => {
    loadTables();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selected) loadInfo(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const selectedObject = useMemo(
    () => objects.find((object) => object.name === selected) || null,
    [objects, selected]
  );

  const selectedSummaryRows = useMemo(() => {
    if (!tableInfo?.summary) return [];
    const summary = tableInfo.summary;
    return [
      ['Object name', summary.objectName ?? '—'],
      ['Object type', summary.objectType ?? '—'],
      ['Column count', summary.columnCount ?? '—'],
      ['Row count', summary.rowCount ?? '—'],
      ['Estimated size', summary.estimatedSizeBytes == null ? '—' : `${(Number(summary.estimatedSizeBytes) / (1024 * 1024)).toFixed(2)} MB`],
      ['Schema', summary.schemaName ?? '—'],
      ['Database', summary.databaseName ?? '—'],
    ];
  }, [tableInfo]);

  const normalizeSchemaRow = useCallback((row) => {
    const columnName = row?.column ?? row?.name ?? row?.column_name ?? null;
    const typeName = row?.type ?? row?.data_type ?? null;
    const notNullValue = row?.notnull ?? row?.not_null ?? row?.nullable;
    const defaultValue = row?.default ?? row?.dflt_value ?? row?.column_default;
    const primaryKeyValue = row?.pk ?? row?.primaryKey;

    const nullableLabel = (() => {
      if (notNullValue === true || notNullValue === 1 || notNullValue === '1') return 'Not Null';
      if (notNullValue === false || notNullValue === 0 || notNullValue === '0') return 'Nullable';
      if (typeof notNullValue === 'string') {
        const normalized = notNullValue.trim().toUpperCase();
        if (normalized === 'NO' || normalized === 'TRUE') return 'Not Null';
        if (normalized === 'YES' || normalized === 'FALSE') return 'Nullable';
      }
      return '—';
    })();

    const primaryKeyLabel = (() => {
      if (primaryKeyValue == null) return '—';
      if (primaryKeyValue === true) return 'Yes';
      if (primaryKeyValue === false) return 'No';
      const numeric = Number(primaryKeyValue);
      if (Number.isFinite(numeric)) return numeric > 0 ? 'Yes' : 'No';
      return '—';
    })();

    return {
      column: columnName ?? '—',
      type: typeName ?? '—',
      nullable: nullableLabel,
      default: defaultValue == null || defaultValue === '' ? '—' : String(defaultValue),
      primaryKey: primaryKeyLabel,
    };
  }, []);

  const normalizedSchemaRows = useMemo(
    () => (tableInfo?.columns || []).map((row) => normalizeSchemaRow(row)),
    [normalizeSchemaRow, tableInfo]
  );

  const runQuery = async () => {
    setRunningQuery(true);
    setQueryError('');
    setQueryResult(null);
    try {
      const queryUrl = new URL(
        apiUrl(apiBase, '/api/debug/duckdb/query'),
        window.location.href
      );
      queryUrl.search = new URLSearchParams({ sql: queryText }).toString();
      const res = await fetch(queryUrl.toString(), {
        method: 'GET',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      const contentType = (res.headers.get('content-type') || '').toLowerCase();
      const responseText = await res.text();
      if (!contentType.includes('application/json')) {
        const safePreview = responseText.replace(/\s+/g, ' ').trim().slice(0, 200) || '(empty body)';
        throw new Error(`Unexpected response: HTTP ${res.status}, URL ${queryUrl.toString()}, Content-Type ${contentType || 'unknown'}, Body ${safePreview}`);
      }
      let data;
      try {
        data = responseText ? JSON.parse(responseText) : null;
      } catch (_error) {
        const safePreview = responseText.replace(/\s+/g, ' ').trim().slice(0, 200) || '(empty body)';
        throw new Error(`Invalid JSON response: HTTP ${res.status}, URL ${queryUrl.toString()}, Content-Type ${contentType || 'unknown'}, Body ${safePreview}`);
      }
      if (!res.ok || data?.ok !== true) {
        const message = typeof data?.error === 'string'
          ? data.error
          : data?.error?.message || 'Failed to run query';
        throw new Error(message);
      }
      setQueryResult(data);
    } catch (e) {
      setQueryError(e.message);
    } finally {
      setRunningQuery(false);
    }
  };

  const insertSampleQuery = useCallback(() => {
    if (!selected) return;
    const quoted = `"${String(selected).replace(/"/g, '""')}"`;
    setQueryText(`SELECT *\nFROM ${quoted}\nLIMIT 100;`);
  }, [selected]);

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <h2>Preview DuckDB</h2>
      {error && <p style={{ color: 'salmon' }}>{error}</p>}

      <h3 style={{ marginTop: 16 }}>Selected Table Inspection</h3>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button onClick={loadTables} disabled={loadingTables}>
          {loadingTables ? 'Refreshing...' : 'Refresh tables'}
        </button>
        <label>
          DuckDB object:{' '}
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            {!objects.length ? <option value="">No objects available</option> : null}
            {objects.map((object) => (
              <option key={`${object.type}:${object.name}`} value={object.name}>
                {object.name}
              </option>
            ))}
          </select>
        </label>
        <button onClick={insertSampleQuery} disabled={!selected}>
          Insert SELECT sample
        </button>
      </div>

      {!selected ? <p style={{ marginTop: 12 }}>Select a DuckDB table or view to inspect its structure.</p> : null}
      {loadingInfo && selected ? <p>Loading...</p> : null}
      {selectedObject && selectedSummaryRows.length ? (
        <div
          style={{
            marginTop: 12,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: 12,
          }}
        >
          {selectedSummaryRows.map(([label, value]) => (
            <div
              key={label}
              style={{
                border: '1px solid #dbe2ea',
                borderRadius: 10,
                padding: '12px 14px',
                background: '#fff',
              }}
            >
              <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 15, fontWeight: 600, color: '#0f172a', wordBreak: 'break-word' }} title={String(value)}>
                {String(value)}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {tableInfo?.summary?.rowCountError ? (
        <p style={{ marginTop: 10, color: '#64748b' }}>
          Row count unavailable: {tableInfo.summary.rowCountError}
        </p>
      ) : null}

      <h4 style={{ marginTop: 16 }}>PRAGMA table_info</h4>
      {normalizedSchemaRows.length ? (
        <div style={{ overflowX: 'auto', marginTop: 12, maxWidth: '100%' }}>
          <table style={{ width: '100%', minWidth: 720, borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#f8fafc' }}>
                {['Column', 'Type', 'Nullable / Not Null', 'Default', 'Primary Key'].map((column) => (
                  <th
                    key={column}
                    style={{
                      textAlign: 'left',
                      padding: '8px 10px',
                      borderBottom: '1px solid #dbe2ea',
                      position: 'sticky',
                      top: 0,
                      background: '#f8fafc',
                      zIndex: 1,
                    }}
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {normalizedSchemaRows.map((row, index) => (
                <tr key={index}>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid #eef2f7', verticalAlign: 'top' }}>{row.column}</td>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid #eef2f7', verticalAlign: 'top' }}>{row.type}</td>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid #eef2f7', verticalAlign: 'top' }}>{row.nullable}</td>
                  <td
                    style={{
                      padding: '8px 10px',
                      borderBottom: '1px solid #eef2f7',
                      verticalAlign: 'top',
                      maxWidth: 320,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                    title={row.default === '—' ? '' : row.default}
                  >
                    {row.default}
                  </td>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid #eef2f7', verticalAlign: 'top' }}>{row.primaryKey}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : selected ? (
        <p style={{ marginTop: 12 }}>No columns returned for the selected object.</p>
      ) : null}

      <h3 style={{ marginTop: 24 }}>Read-Only SQL Query</h3>
      <textarea
        value={queryText}
        onChange={(e) => setQueryText(e.target.value)}
        placeholder={'SELECT * FROM "your_table" LIMIT 100;'}
        spellCheck={false}
        style={{
          width: '100%',
          minHeight: 220,
          resize: 'vertical',
          fontFamily: 'ui-monospace, SFMono-Regular, SFMono-Regular, Consolas, monospace',
          fontSize: 13,
          padding: 12,
          borderRadius: 10,
          border: '1px solid #cbd5e1',
          boxSizing: 'border-box',
        }}
      />
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10 }}>
        <button onClick={runQuery} disabled={runningQuery || !queryText.trim()}>
          {runningQuery ? 'Running...' : 'Run Query'}
        </button>
        {queryResult?.limit ? (
          <span style={{ fontSize: 12, color: '#64748b' }}>Row limit: {queryResult.limit}</span>
        ) : null}
      </div>

      {queryError ? <p style={{ color: 'salmon', marginTop: 12 }}>{queryError}</p> : null}
      {queryResult ? (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13, color: '#334155' }}>
            <span>Status: Success</span>
            <span>Returned rows: {Number(queryResult.returnedRowCount || 0).toLocaleString()}</span>
            <span>Total rows: {Number(queryResult.rowCount || 0).toLocaleString()}</span>
            <span>Truncated: {queryResult.truncated ? 'Yes' : 'No'}</span>
          </div>
          <div style={{ overflowX: 'auto', marginTop: 12, maxWidth: '100%' }}>
            <table style={{ width: '100%', minWidth: 720, borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#f8fafc' }}>
                  {(queryResult.columns || []).map((column) => (
                    <th
                      key={column}
                      style={{
                        textAlign: 'left',
                        padding: '8px 10px',
                        borderBottom: '1px solid #dbe2ea',
                        position: 'sticky',
                        top: 0,
                        background: '#f8fafc',
                        zIndex: 1,
                      }}
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(queryResult.rows || []).length ? (
                  queryResult.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {(queryResult.columns || []).map((column) => {
                        const value = row?.[column];
                        const displayValue = value === null ? 'null' : value === '' ? '(empty string)' : String(value);
                        return (
                          <td
                            key={`${rowIndex}-${column}`}
                            style={{
                              padding: '8px 10px',
                              borderBottom: '1px solid #eef2f7',
                              verticalAlign: 'top',
                              maxWidth: 320,
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              color: value === null ? '#7c3aed' : value === '' ? '#94a3b8' : '#0f172a',
                              fontStyle: value === null || value === '' ? 'italic' : 'normal',
                            }}
                            title={displayValue}
                          >
                            {displayValue}
                          </td>
                        );
                      })}
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td
                      colSpan={Math.max((queryResult.columns || []).length, 1)}
                      style={{ padding: '12px 10px', color: '#64748b' }}
                    >
                      Query returned no rows.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

    </div>
  );
}

function DebugReloadPage({ apiBase }) {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Debug: Reload DuckDB</h1>
        <p>Reload curated GOLD parquet tables into the local DuckDB.</p>
      </div>
      <PulseSection title="Reload">
        <ReloadDuckDBTab apiBase={apiBase} />
      </PulseSection>
    </div>
  );
}

function DebugPreviewPage({ apiBase }) {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Preview DuckDB</h1>
        <p>Inspect one DuckDB object at a time and run read-only SQL queries.</p>
      </div>
      <PulseSection title="Preview DuckDB">
        <ViewDuckDBTab apiBase={apiBase} />
      </PulseSection>
    </div>
  );
}

function LlmMeshPlaceholderPage({ title, description }) {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <PulseSection title="Status">
        <div className="PulseEmptyState">
          <h2>Coming Soon</h2>
          <p>{description}</p>
        </div>
      </PulseSection>
    </div>
  );
}

function UsersActivityPage({ apiBase }) {
  const [windowKind, setWindowKind] = useState('last_3_months');
  const [selectedInstance, setSelectedInstance] = useState('');
  const [activityFilter, setActivityFilter] = useState('license_creator');
  const [showInstanceBreakdown, setShowInstanceBreakdown] = useState(true);
  const [draftWindowKind, setDraftWindowKind] = useState('last_3_months');
  const [draftSelectedInstance, setDraftSelectedInstance] = useState('');
  const [draftActivityFilter, setDraftActivityFilter] = useState('license_creator');
  const [draftShowInstanceBreakdown, setDraftShowInstanceBreakdown] = useState(true);
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [facets, setFacets] = useState({ instances: [] });

  const windowParam = useMemo(() => {
    if (windowKind === 'this_month') return { window: 'this_month' };
    if (windowKind === 'last_12_months') return { window: 'last_12_months' };
    return { window: 'last_3_months' };
  }, [windowKind]);

  const [viewingRows, setViewingRows] = useState([]);
  const [developingRows, setDevelopingRows] = useState([]);

  const [userKpisAll, setUserKpisAll] = useState(null);
  const [userKpisInstance, setUserKpisInstance] = useState(null);
  const [userInstancesAll, setUserInstancesAll] = useState([]);
  const [monthlyActiveAggregate, setMonthlyActiveAggregate] = useState([]);
  const [monthlyActiveByInstance, setMonthlyActiveByInstance] = useState([]);
  const [formalMauAggregate, setFormalMauAggregate] = useState([]);
  const [formalMauByInstance, setFormalMauByInstance] = useState([]);
  const [formalMauByProfile, setFormalMauByProfile] = useState([]);
  const [formalMauByInstanceProfile, setFormalMauByInstanceProfile] = useState([]);
  const [formalMauLatestMonth, setFormalMauLatestMonth] = useState(null);
  const [formalMauAvailable, setFormalMauAvailable] = useState(true);
  const [userSegments, setUserSegments] = useState([]);
  const [userSegmentTotals, setUserSegmentTotals] = useState(null);
  const [stickinessSeries, setStickinessSeries] = useState([]);
  const [stickinessLatest, setStickinessLatest] = useState(null);
  const [selectedLogin, setSelectedLogin] = useState(null);
  const [loadingSections, setLoadingSections] = useState({});
  const [error, setError] = useState('');

  const beginLoad = useCallback((key) => {
    setLoadingSections((prev) => ({ ...prev, [key]: true }));
  }, []);

  const endLoad = useCallback((key) => {
    setLoadingSections((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const setRequestError = useCallback((message) => {
    setError(message || 'Unexpected User Activity error');
  }, []);

  const clearRequestError = useCallback(() => {
    setError('');
  }, []);

  const isAbortError = (e) => e?.name === 'AbortError';

  const fetchJson = useCallback(async (path, { params, signal } = {}) => {
    const suffix = params ? `?${params.toString()}` : '';
    const response = await fetch(apiUrl(apiBase, `${path}${suffix}`), { signal });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || `Failed loading ${path}`);
    return data;
  }, [apiBase]);

  const loading = Object.keys(loadingSections).length > 0;
  const filtersDirty = (
    draftWindowKind !== windowKind
    || draftSelectedInstance !== selectedInstance
    || draftActivityFilter !== activityFilter
    || draftShowInstanceBreakdown !== showInstanceBreakdown
  );

  const leaderboardWindowLabel = useMemo(() => {
    if (windowKind === 'this_month') return 'this month';
    if (windowKind === 'last_12_months') return 'the last 12 months';
    return 'the last 3 months';
  }, [windowKind]);

  const applyFilters = useCallback(() => {
    setWindowKind(draftWindowKind);
    setSelectedInstance(draftSelectedInstance);
    setActivityFilter(draftActivityFilter);
    setShowInstanceBreakdown(draftShowInstanceBreakdown);
    setSelectedLogin(null);
  }, [draftActivityFilter, draftSelectedInstance, draftShowInstanceBreakdown, draftWindowKind]);

  const resetFilters = useCallback(() => {
    setDraftWindowKind('last_3_months');
    setDraftSelectedInstance('');
    setDraftActivityFilter('license_creator');
    setDraftShowInstanceBreakdown(true);
  }, []);


  useEffect(() => {
    const controller = new AbortController();

    clearRequestError();
    beginLoad('facets');

    fetchJson('/api/build/users/facets', { signal: controller.signal })
      .then((data) => {
        setFacets({ instances: data.instances || [] });
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('facets'));

    return () => controller.abort();
  }, [apiBase, beginLoad, clearRequestError, endLoad, fetchJson, setRequestError]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window);
    params.set('activityFilter', activityFilter);

    clearRequestError();
    beginLoad('kpisAll');

    fetchJson('/api/build/users/kpis', { params, signal: controller.signal })
      .then((data) => {
        setUserKpisAll(data.kpis || null);
        setUserInstancesAll(data.byInstance || []);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('kpisAll'));

    return () => controller.abort();
  }, [activityFilter, beginLoad, clearRequestError, endLoad, fetchJson, setRequestError, windowParam]);

  useEffect(() => {
    if (!selectedInstance) {
      setUserKpisInstance(null);
      return;
    }

    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window);
    params.set('activityFilter', activityFilter);
    params.set('instance_name', selectedInstance);

    clearRequestError();
    beginLoad('kpisInstance');

    fetchJson('/api/build/users/kpis', { params, signal: controller.signal })
      .then((data) => {
        setUserKpisInstance(data.kpis || null);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('kpisInstance'));

    return () => controller.abort();
  }, [activityFilter, beginLoad, clearRequestError, endLoad, fetchJson, selectedInstance, setRequestError, windowParam]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window);
    if (selectedInstance) params.set('instance_name', selectedInstance);

    clearRequestError();
    beginLoad('formalMauMonthly');

    fetchJson('/api/build/users/formal-mau-monthly', { params, signal: controller.signal })
      .then((data) => {
        setFormalMauAggregate(data.aggregate || []);
        setFormalMauByInstance(data.byInstance || []);
        setFormalMauByProfile(data.byProfile || []);
        setFormalMauByInstanceProfile(data.byInstanceProfile || []);
        setFormalMauLatestMonth(data.latestMonth || null);
        setFormalMauAvailable(data.available !== false);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('formalMauMonthly'));

    return () => controller.abort();
  }, [beginLoad, clearRequestError, endLoad, fetchJson, selectedInstance, setRequestError, windowParam]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window);
    params.set('activityFilter', activityFilter);
    if (selectedInstance) params.set('instance_name', selectedInstance);

    clearRequestError();
    beginLoad('activeMonthly');

    fetchJson('/api/build/users/active-monthly', { params, signal: controller.signal })
      .then((data) => {
        setMonthlyActiveAggregate(data.aggregate || []);
        setMonthlyActiveByInstance(data.byInstance || []);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('activeMonthly'));

    return () => controller.abort();
  }, [activityFilter, beginLoad, clearRequestError, endLoad, fetchJson, selectedInstance, setRequestError, windowParam]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window);
    params.set('activityFilter', activityFilter);
    if (selectedInstance) params.set('instance_name', selectedInstance);

    beginLoad('segments');

    fetchJson('/api/build/users/segments', { params, signal: controller.signal })
      .then((data) => {
        setUserSegments(data.segments || []);
        setUserSegmentTotals(data.totals || null);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('segments'));

    return () => controller.abort();
  }, [activityFilter, beginLoad, endLoad, fetchJson, selectedInstance, setRequestError, windowParam]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window === 'this_month' ? 'last_3_months' : windowParam.window);
    params.set('activityFilter', activityFilter);
    if (selectedInstance) params.set('instance_name', selectedInstance);

    beginLoad('stickiness');

    fetchJson('/api/build/users/stickiness', { params, signal: controller.signal })
      .then((data) => {
        setStickinessSeries(data.series || []);
        setStickinessLatest(data.latest || null);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('stickiness'));

    return () => controller.abort();
  }, [activityFilter, beginLoad, endLoad, fetchJson, selectedInstance, setRequestError, windowParam]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    params.set('window', windowParam.window);
    params.set('activityFilter', activityFilter);
    if (selectedInstance) params.set('instance_name', selectedInstance);

    clearRequestError();
    beginLoad('leaderboard');

    fetchJson('/api/build/users/leaderboard', { params, signal: controller.signal })
      .then((data) => {
        setViewingRows(data.viewing || []);
        setDevelopingRows(data.developing || []);
      })
      .catch((e) => {
        if (!isAbortError(e)) setRequestError(e.message);
      })
      .finally(() => endLoad('leaderboard'));

    return () => controller.abort();
  }, [activityFilter, beginLoad, clearRequestError, endLoad, fetchJson, selectedInstance, setRequestError, windowParam]);

  const toBarRows = (rows, key = 'value') => {
    return (rows || []).map((r) => ({
      label: r.displayName || r.login || r.label,
      value: Number(r[key] ?? r.value ?? 0),
      login: r.login || r.label,
      raw: r,
    }));
  };

  const onUserClick = (row) => {
    const login = row?.login || row;
    if (!login) return;
    setSelectedLogin(login);
  };

  const toMonthlyPoints = (rows) => {
    return (rows || []).map((r) => ({
      label: String(r.month || '').slice(0, 7),
      value: Number(r.active_users ?? r.activeUsers ?? 0),
    }));
  };

  const lastMonthLabel = useMemo(() => {
    const latestWithActivity = (monthlyActiveByInstance || [])
      .map((r) => ({
        label: String(r.month || '').slice(0, 7),
        value: Number(r.active_users ?? 0),
      }))
      .filter((r) => r.label && r.value > 0)
      .map((r) => r.label)
      .sort()
      .pop();

    if (latestWithActivity) return latestWithActivity;

    const pts = toMonthlyPoints(monthlyActiveAggregate);
    return pts.length ? pts[pts.length - 1].label : '';
  }, [monthlyActiveAggregate, monthlyActiveByInstance]);

  const latestMonthByInstanceRows = useMemo(() => {
    return (monthlyActiveByInstance || [])
      .filter((r) => String(r.month || '').slice(0, 7) === lastMonthLabel)
      .map((r) => ({
        label: r.instance_name || r.instanceName,
        value: Number(r.active_users ?? 0),
      }));
  }, [monthlyActiveByInstance, lastMonthLabel]);

  const formalMauLastMonthLabel = useMemo(() => {
    const latestWithActivity = (formalMauByInstance || [])
      .map((r) => ({
        label: String(r.month || '').slice(0, 7),
        value: Number(r.active_users ?? 0),
      }))
      .filter((r) => r.label && r.value > 0)
      .map((r) => r.label)
      .sort()
      .pop();

    if (latestWithActivity) return latestWithActivity;

    const pts = toMonthlyPoints(formalMauAggregate);
    return pts.length ? pts[pts.length - 1].label : '';
  }, [formalMauAggregate, formalMauByInstance]);

  const formalMauLatestMonthByInstanceRows = useMemo(() => {
    return (formalMauByInstance || [])
      .filter((r) => String(r.month || '').slice(0, 7) === formalMauLastMonthLabel)
      .map((r) => ({
        label: r.instance_name || r.instanceName,
        value: Number(r.active_users ?? 0),
      }));
  }, [formalMauByInstance, formalMauLastMonthLabel]);

  const formalMauByProfileRows = useMemo(() => {
    return (formalMauByProfile || []).map((r) => ({
      label: `${r.license_group || 'Other Licenses'} • ${r.userProfile || 'UNKNOWN'}`,
      value: Number(r.active_users ?? 0),
    }));
  }, [formalMauByProfile]);

  const formalMauByInstanceProfileRows = useMemo(() => {
    return (formalMauByInstanceProfile || []).map((r) => ({
      label: `${r.instance_name || '-'} • ${r.license_group || 'Other Licenses'} • ${r.userProfile || 'UNKNOWN'}`,
      value: Number(r.active_users ?? 0),
    }));
  }, [formalMauByInstanceProfile]);

  const instanceCreatingRows = useMemo(() => {
    return (userInstancesAll || []).map((r) => ({
      label: r.instanceName,
      value: Number(r.developing_users ?? 0),
    }));
  }, [userInstancesAll]);

  const normalizeSegmentLabel = (label) => {
    return String(label || '')
      .replace(/\bdevelopers?\b/gi, 'Creators')
      .replace(/\bdeveloping\b/gi, 'Creating')
      .replace(/\bcreators?\b/g, (match) => (match[0] === 'c' ? 'creators' : 'Creators'))
      .replace(/\bviewers?\b/gi, 'Consumers')
      .replace(/\bviewing\b/gi, 'Consuming')
      .replace(/\bconsuming\b/g, (match) => (match[0] === 'c' ? 'Consuming' : 'Consuming'))
      .replace(/\bconsumers?\b/g, (match) => (match[0] === 'c' ? 'Consumers' : 'Consumers'))
      .replace(/\bmixed users\b/gi, 'mixed observed actors');
  };

  const displayUserSegments = useMemo(() => {
    const order = { Mixed: 0, 'Viewer only': 1, Inactive: 2 };
    return (userSegments || [])
      .map((row) => {
        const rawLabel = String(row.label || '');
        let label = normalizeSegmentLabel(rawLabel);
        if (rawLabel.toLowerCase() === 'mixed') {
          label = 'Creators';
        } else if (rawLabel.toLowerCase() === 'viewer only') {
          label = 'Consumers';
        }
        return {
          ...row,
          label,
          _sort: order[rawLabel] ?? 99,
        };
      })
      .sort((left, right) => left._sort - right._sort)
      .map(({ _sort, ...row }) => row);
  }, [userSegments]);

  const segmentSummaryTiles = [
    {
      label: 'Enabled users considered',
      detail: 'This is the total unique enabled-user population used as the denominator for the presence-based segment split.',
      value: Number(userSegmentTotals?.enabledUsers ?? 0).toLocaleString(),
    },
    {
      label: 'Observed users represented',
      detail: 'This equals consumer plus creating users in the selected window.',
      value: Number(((userSegmentTotals?.viewerOnlyUsers ?? 0) + (userSegmentTotals?.mixedUsers ?? 0))).toLocaleString(),
    },
    {
      label: 'Creating users explained',
      detail: 'These are users with both consumption and creation activity in the selected window.',
      value: Number(userSegmentTotals?.mixedUsers ?? 0).toLocaleString(),
    },
  ];

  const activityKpiSource = selectedInstance ? userKpisInstance : userKpisAll;

  const parseHistoryDate = useCallback((value) => {
    const raw = String(value || '').trim();
    if (!raw) return null;
    const normalized = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00Z` : raw;
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }, []);

  const activityHistoryStart = useMemo(() => parseHistoryDate(activityKpiSource?.activity_history_start), [activityKpiSource?.activity_history_start, parseHistoryDate]);
  const activityHistoryEnd = useMemo(() => parseHistoryDate(activityKpiSource?.activity_history_end), [activityKpiSource?.activity_history_end, parseHistoryDate]);

  const observedWindowDisplay = useCallback((value, requiredDays, formatter) => {
    if (!(activityHistoryStart instanceof Date) || Number.isNaN(activityHistoryStart.getTime())) {
      return { value: '-', available: false };
    }
    if (!(activityHistoryEnd instanceof Date) || Number.isNaN(activityHistoryEnd.getTime())) {
      return { value: '-', available: false };
    }
    const availableDays = Math.floor((activityHistoryEnd.getTime() - activityHistoryStart.getTime()) / 86400000) + 1;
    if (availableDays < requiredDays) {
      return { value: '-', available: false, availableDays };
    }
    return { value: formatter(value), available: true, availableDays };
  }, [activityHistoryEnd, activityHistoryStart]);

  const activityVolumeTiles = [
    { label: 'Viewing actions', value: Number(activityKpiSource?.total_viewing_actions ?? 0).toLocaleString() },
    { label: 'Creation actions', value: Number(activityKpiSource?.total_developing_actions ?? 0).toLocaleString() },
    { label: 'Share of creation activity', value: `${(((activityKpiSource?.developing_action_share ?? 0) * 100)).toFixed(1)}%` },
  ];

  const activityWindowRows = [
    {
      window: '30 days',
      observedActors: observedWindowDisplay(activityKpiSource?.active_users_30d ?? 0, 30, (value) => Number(value ?? 0).toLocaleString()),
      observedActorRate: observedWindowDisplay(activityKpiSource?.active_rate_30d ?? 0, 30, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
      creatorRate: observedWindowDisplay(activityKpiSource?.contributor_rate_30d ?? 0, 30, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
    },
    {
      window: '90 days',
      observedActors: observedWindowDisplay(activityKpiSource?.active_users_90d ?? 0, 90, (value) => Number(value ?? 0).toLocaleString()),
      observedActorRate: observedWindowDisplay(activityKpiSource?.active_rate_90d ?? 0, 90, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
      creatorRate: observedWindowDisplay(activityKpiSource?.contributor_rate_90d ?? 0, 90, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
    },
    {
      window: '6 months',
      observedActors: observedWindowDisplay(activityKpiSource?.active_users_6m ?? 0, 183, (value) => Number(value ?? 0).toLocaleString()),
      observedActorRate: observedWindowDisplay(activityKpiSource?.active_rate_6m ?? 0, 183, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
      creatorRate: observedWindowDisplay(activityKpiSource?.contributor_rate_6m ?? 0, 183, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
    },
    {
      window: '12 months',
      observedActors: observedWindowDisplay(activityKpiSource?.active_users_12m ?? 0, 365, (value) => Number(value ?? 0).toLocaleString()),
      observedActorRate: observedWindowDisplay(activityKpiSource?.active_rate_12m ?? 0, 365, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
      creatorRate: observedWindowDisplay(activityKpiSource?.contributor_rate_12m ?? 0, 365, (value) => `${((Number(value ?? 0) * 100)).toFixed(1)}%`),
    },
  ];

  const secondaryActivityTiles = [
    { label: 'Inactive users (6 months)', detail: 'Enabled users with no recorded activity in the last 6 months.', value: Number(activityKpiSource?.inactive_users_6m ?? 0).toLocaleString() },
    { label: 'View-only users (6 months)', detail: 'Users with viewing activity but no creation activity in the last 6 months.', value: Number(activityKpiSource?.viewer_only_users_6m ?? 0).toLocaleString() },
  ];

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>User Adoption and Activity</h1>
        <p>
          See who is using your Dataiku instances, how active they are, and what types of users make up that activity. On this page, creator and consumer labels describe observed behavior, not license assignment.
        </p>
      </div>
      <FilterPageLayout
        filtersExpanded={filtersExpanded}
        onOpenFilters={() => setFiltersExpanded(true)}
        onCloseFilters={() => setFiltersExpanded(false)}
        filterContent={(
          <>
            {loading ? <div className="PulseMuted" style={{ marginTop: 8 }}>Loading…</div> : null}
            {error ? <div className="PulseError">{error}</div> : null}
            <div className="PulseMuted" style={{ marginTop: 8 }}>
              Adjust filters, then click Apply filters to refresh the page content.
            </div>
            <>
                <label className="PulseLabel">
                  Reporting window
                  <select className="PulseSelect" value={draftWindowKind} onChange={(e) => setDraftWindowKind(e.target.value)}>
                    <option value="this_month">This month</option>
                    <option value="last_3_months">Last 3 months</option>
                    <option value="last_12_months">Last 12 months</option>
                  </select>
                </label>

                <label className="PulseLabel">
                  User activity type
                  <select className="PulseSelect" value={draftActivityFilter} onChange={(e) => setDraftActivityFilter(e.target.value)}>
                    <option value="license_creator">Users creating</option>
                    <option value="license_consumer">Users viewing</option>
                  </select>
                </label>

                <label className="PulseLabel">
                  Show instance comparison
                  <select
                    className="PulseSelect"
                    value={draftShowInstanceBreakdown ? 'show' : 'hide'}
                    onChange={(e) => setDraftShowInstanceBreakdown(e.target.value === 'show')}
                  >
                    <option value="show">Show per instance</option>
                    <option value="hide">Aggregate only</option>
                  </select>
                </label>

                <label className="PulseLabel">
                  Instance
                  <select className="PulseSelect" value={draftSelectedInstance} onChange={(e) => setDraftSelectedInstance(e.target.value)}>
                    <option value="">All instances</option>
                    {facets.instances.map((inst) => (
                      <option key={inst} value={inst}>{inst}</option>
                    ))}
                  </select>
                </label>

                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
                  <button className="PulseButton" type="button" onClick={applyFilters} disabled={!filtersDirty}>
                    Apply
                  </button>
                  <button className="PulseButton" type="button" onClick={resetFilters}>
                    Reset
                  </button>
                  {selectedLogin ? (
                    <button className="PulseButton" type="button" onClick={() => { setSelectedLogin(null); }}>
                      Clear selection
                    </button>
                  ) : null}
                </div>
                {filtersDirty ? (
                  <div className="PulseMuted" style={{ marginTop: 8 }}>
                    You have unapplied filter changes.
                  </div>
                ) : null}
            </>
          </>
        )}
      >
      <div className="PulseCard">
        <h2>Current User Activity</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          These measures show user activity over fixed periods: 30 days, 90 days, 6 months, and 12 months. If there is not enough history for a period, the value is shown as - rather than suggesting a full comparison.
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          {activityVolumeTiles.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              {tile.detail ? <div className="PulseSummaryDetail">{tile.detail}</div> : null}
            </div>
          ))}
        </div>
        <div className="PulseActivityMatrix">
          <div className="PulseActivityColumn">
            <div className="PulseActivityColumnTitle">Active users</div>
            {activityWindowRows.map((row) => (
              <div key={`observed-${row.window}`} className="PulseActivityMetricRow">
                <div className="PulseActivityWindow">{row.window}</div>
                <div className="PulseActivityValue">{row.observedActors.value}</div>
              </div>
            ))}
          </div>
          <div className="PulseActivityColumn">
            <div className="PulseActivityColumnTitle">Active user rate</div>
            {activityWindowRows.map((row) => (
              <div key={`rate-${row.window}`} className="PulseActivityMetricRow">
                <div className="PulseActivityWindow">{row.window}</div>
                <div className="PulseActivityValue">{row.observedActorRate.value}</div>
              </div>
            ))}
          </div>
          <div className="PulseActivityColumn">
            <div className="PulseActivityColumnTitle">User creation rate</div>
            {activityWindowRows.map((row) => (
              <div key={`creator-${row.window}`} className="PulseActivityMetricRow">
                <div className="PulseActivityWindow">{row.window}</div>
                <div className="PulseActivityValue">{row.creatorRate.value}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="PulseMuted" style={{ marginTop: 10 }}>
          {activityKpiSource?.activity_history_start ? `Available activity history starts ${String(activityKpiSource.activity_history_start).slice(0, 10)}.` : 'Activity history coverage is not available.'}
        </div>
        <div className="PulseSummaryGrid" style={{ marginTop: 14 }}>
          {secondaryActivityTiles.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              {tile.detail ? <div className="PulseSummaryDetail">{tile.detail}</div> : null}
            </div>
          ))}
        </div>
      </div>

      <div className="PulseCard">
        <h2>Monthly Adoption Trends</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Compare two monthly user measures over time: active users based on recorded activity, and formal monthly active users (MAU) used for reporting.
        </div>
        <div className="PulseVizGrid">
          <MonthlyObservedActorsChart title="All instances (users counted once)" points={toMonthlyPoints(monthlyActiveAggregate)} />
          {showInstanceBreakdown ? (
            <BarList
              title={`By instance (${lastMonthLabel || 'latest month'})`}
              rows={latestMonthByInstanceRows}
              maxRows={12}
            />
          ) : null}
        </div>
        <div className="PulseMuted" style={{ marginTop: 14, marginBottom: 10 }}>
          Formal MAU is a stricter measure than active users. It counts users with qualifying sign-in activity and applies reporting eligibility rules.
        </div>
        {formalMauAvailable ? (
          <>
            <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
              <div className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
                <div className="PulseSummaryCount">{Number(formalMauLatestMonth?.active_users ?? 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Formal monthly active users</div>
                <div className="PulseSummaryDetail">
                  {formalMauLatestMonth?.month ? `Latest month (${String(formalMauLatestMonth.month).slice(0, 7)}) based on qualifying sign-in activity.` : 'Latest month based on qualifying sign-in activity.'}
                </div>
              </div>
            </div>
            <div className="PulseVizGrid">
              <MonthlyObservedActorsChart title="Formal monthly active users" points={toMonthlyPoints(formalMauAggregate)} />
              {showInstanceBreakdown ? (
                <BarList
                  title={`Formal monthly active users by instance (${formalMauLastMonthLabel || 'latest month'})`}
                  rows={formalMauLatestMonthByInstanceRows}
                  maxRows={12}
                />
              ) : null}
            </div>
            <div className="PulseMuted" style={{ marginTop: 14, marginBottom: 10 }}>
              Formal MAU is also grouped by license type so you can compare creator, viewer, admin, and other user groups. In the all-instance view, each user is counted once. In the instance view, a user can appear once per active instance.
            </div>
            <div className="PulseVizGrid">
              <BarList
                title={`Formal monthly active users by license type (${formalMauLastMonthLabel || 'latest month'})`}
                rows={formalMauByProfileRows}
                maxRows={12}
              />
              {showInstanceBreakdown ? (
                <BarList
                  title={`Formal monthly active users by instance and license type (${formalMauLastMonthLabel || 'latest month'})`}
                  rows={formalMauByInstanceProfileRows}
                  maxRows={12}
                />
              ) : null}
            </div>
          </>
        ) : (
          <div className="PulseSummaryTile PulseSummaryTileStatic" style={{ marginTop: 4 }}>
            <div className="PulseSummaryLabel">Formal Admin MAU</div>
            <div className="PulseSummaryDetail" style={{ marginTop: 8 }}>
              Formal monthly active user data is not available in this environment.
            </div>
          </div>
        )}
      </div>

      <div className="PulseCard">
        <h2>User Retention</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          See whether active users return, stay active, or become active again over time.
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          <div className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
            <div className="PulseSummaryCount">{`${(((stickinessLatest?.activeRate ?? 0)) * 100).toFixed(1)}%`}</div>
                <div className="PulseSummaryLabel">Current active user rate</div>
          </div>
          <div className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
            <div className="PulseSummaryCount">{Number(stickinessLatest?.reactivatedUsers || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Users active again</div>
          </div>
          <div className="PulseSummaryTile PulseSummaryTileStatic">
            <div className="PulseSummaryCount">{Number(stickinessLatest?.retainedUsers || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Users active in consecutive months</div>
          </div>
          <div className="PulseSummaryTile PulseSummaryTileStatic">
            <div className="PulseSummaryCount">{Number(stickinessLatest?.newActiveUsers || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">New active users</div>
          </div>
        </div>
        <div className="PulseVizGrid">
          <MonthlyRateChart
            title="Monthly active user rate"
            points={(stickinessSeries || []).map((r) => ({ label: String(r.month || '').slice(0, 7), value: Number(r.activeRate || 0) * 100 }))}
          />
          <BarList
            title="Users active again by month"
            rows={(stickinessSeries || []).map((r) => ({ label: String(r.month || '').slice(0, 7), value: Number(r.reactivatedUsers || 0) })).reverse()}
            maxRows={12}
          />
        </div>
      </div>

      <div className="PulseCard">
        <h2>User Mix</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          These groups show how enabled users are participating during the selected period. Each user appears in one group only.
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          {segmentSummaryTiles.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              <div className="PulseSummaryDetail">{tile.detail}</div>
            </div>
          ))}
        </div>
        <div className="PulseVizGrid">
          <BarList title="User mix" rows={displayUserSegments} maxRows={10} />
        </div>
      </div>

      {!selectedInstance ? (
        <div className="PulseCard">
          <h2>Activity by Instance</h2>
          <div className="PulseMuted" style={{ marginBottom: 10 }}>
            Compare user activity across instances, including active users and users creating work.
          </div>
          <div className="PulseVizGrid">
            <BarList title={`Active users by instance (${lastMonthLabel || 'latest month'})`} rows={latestMonthByInstanceRows} maxRows={12} />
            <BarList title="Users creating by instance" rows={instanceCreatingRows} maxRows={12} />
          </div>
        </div>
      ) : null}

      <div className="PulseCard">
        <h2>Most Active Users</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Click a user to see their full activity details, including creation and viewing activity.
        </div>
        <div className="PulseVizGrid">
          <BarList
            title={`Users with the most viewing activity (${leaderboardWindowLabel})`}
            rows={toBarRows(viewingRows)}
            maxRows={12}
            onRowClick={(label) => {
              const row = viewingRows.find((r) => (r.displayName || r.login || r.label) === label) || { login: label };
              onUserClick(row);
            }}
          />
          <BarList
            title={`Users with the most creation activity (${leaderboardWindowLabel})`}
            rows={toBarRows(developingRows)}
            maxRows={12}
            onRowClick={(label) => {
              const row = developingRows.find((r) => (r.displayName || r.login || r.label) === label) || { login: label };
              onUserClick(row);
            }}
          />
        </div>
      </div>

      {selectedLogin ? (
        <Modal title={`${selectedLogin} user card`} onClose={() => setSelectedLogin(null)}>
          <UserDashboard
            apiBase={apiBase}
            login={selectedLogin}
            mode="organization"
            selectedInstance={selectedInstance}
            windowValue={windowParam.window}
            showContextBadges={true}
          />
        </Modal>
      ) : null}

      </FilterPageLayout>
    </div>
  );
}



function DevelopmentActivityPage({ apiBase }) {
  const [windowDays, setWindowDays] = useState(30);
  const [selectedCapability, setSelectedCapability] = useState(null);
  const [selectedUser, setSelectedUser] = useState(null);

  const [activityDaily, setActivityDaily] = useState([]);
  const [byCapability, setByCapability] = useState([]);
  const [byCategory, setByCategory] = useState([]);
  const [topUsers, setTopUsers] = useState([]);
  const [capabilityUsage, setCapabilityUsage] = useState({ summary: {}, activityDaily: [], byCapability: [], topByUsers: [], topByProjects: [], topByInstances: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [capabilitySummary, setCapabilitySummary] = useState(null);
  const [capabilityActivityDaily, setCapabilityActivityDaily] = useState([]);
  const [capabilityCategories, setCapabilityCategories] = useState([]);
  const [capabilityTags, setCapabilityTags] = useState([]);
  const [capabilityTopUsers, setCapabilityTopUsers] = useState([]);

  const [userSummary, setUserSummary] = useState(null);
  const [userActivityDaily, setUserActivityDaily] = useState([]);
  const [userCapabilities, setUserCapabilities] = useState([]);
  const [userCategories, setUserCategories] = useState([]);
  const [userTags, setUserTags] = useState([]);
  const [userDetail, setUserDetail] = useState(null);
  const [topProjects, setTopProjects] = useState([]);
  const [userTrendMode, setUserTrendMode] = useState('developing');
  const [showUserInformation, setShowUserInformation] = useState(false);
  const [filtersExpanded, setFiltersExpanded] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('days', String(windowDays));

    fetch(apiUrl(apiBase, `/api/consumption/process-usage?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading capability usage');
        setCapabilityUsage({
          summary: data.summary || {},
          activityDaily: data.activityDaily || [],
          byCapability: data.byCapability || [],
          topByUsers: data.topByUsers || [],
          topByProjects: data.topByProjects || [],
          topByInstances: data.topByInstances || [],
        });
      })
      .catch((e) => setError(e.message));
  }, [apiBase, windowDays]);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('days', String(windowDays));

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/build/development-activity?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading development activity');
        setActivityDaily(data.activityDaily || []);
        setByCapability(data.byCapability || []);
        setByCategory(data.byCategory || []);
        setTopUsers(data.topUsers || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiBase, windowDays]);

  useEffect(() => {
    if (!selectedCapability) return;

    const params = new URLSearchParams();
    params.set('days', String(windowDays));

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/build/development-activity/capability/${encodeURIComponent(selectedCapability)}?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading capability drilldown');
        setCapabilitySummary(data.summary || null);
        setCapabilityActivityDaily(data.activityDaily || []);
        setCapabilityCategories(data.categories || []);
        setCapabilityTags(data.tags || []);
        setCapabilityTopUsers(data.topUsers || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiBase, selectedCapability, windowDays]);

  useEffect(() => {
    if (!selectedUser) return;

    const params = new URLSearchParams();
    params.set('days', String(windowDays));

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/build/development-activity/user/${encodeURIComponent(selectedUser)}?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading user drilldown');
        setUserSummary(data.summary || null);
        setUserActivityDaily(data.activityDaily || []);
        setUserCapabilities(data.capabilities || []);
        setUserCategories(data.categories || []);
        setUserTags(data.tags || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    fetch(apiUrl(apiBase, `/api/build/users/${encodeURIComponent(selectedUser)}?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading shared user detail');
        setUserDetail(data);
      })
      .catch((e) => setError(e.message));

    fetch(apiUrl(apiBase, `/api/build/users/${encodeURIComponent(selectedUser)}/top-projects?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading top projects');
        setTopProjects(data.rows || []);
      })
      .catch((e) => setError(e.message));
  }, [apiBase, selectedUser, windowDays]);

  const sharedDetail = userDetail?.user || null;
  const sharedInstances = userDetail?.instances || [];
  const activityDailyCreating = (userActivityDaily || []).map((p) => ({
    label: p.label,
    value: Number(p.value ?? 0),
  }));
  const activityDailyConsuming = activityDailyCreating;
  const activityTrendPoints = userTrendMode === 'viewing' ? activityDailyConsuming : activityDailyCreating;
  const activityTrendTotal = activityTrendPoints.reduce((sum, point) => sum + Number(point.value || 0), 0);
  const activityTrendPeak = activityTrendPoints.reduce((max, point) => Math.max(max, Number(point.value || 0)), 0);
  const activityTrendPeakPoint = activityTrendPoints.reduce((best, point) => {
    const value = Number(point.value || 0);
    if (!best || value > Number(best.value || 0)) return point;
    return best;
  }, null);
  const activityTrendAverage = activityTrendPoints.length ? (activityTrendTotal / activityTrendPoints.length) : 0;
  const hasActivityInWindow = Boolean(Number(userSummary?.events || 0));
  const activityPerformanceSummaryTiles = [
    {
      label: 'Total development events',
      value: Number(activityDaily.reduce((sum, point) => sum + Number(point.value || 0), 0)).toLocaleString(),
      detail: `Observed across the last ${windowDays} days of activity buckets.`,
    },
    {
      label: 'Active capabilities',
      value: Number((byCapability || []).length).toLocaleString(),
      detail: 'Capabilities with observed tagged development activity.',
    },
    {
      label: 'Top capability',
      value: byCapability?.[0]?.label || '—',
      detail: byCapability?.[0] ? `${Number(byCapability[0].value || 0).toLocaleString()} events in the current window.` : 'No active capability identified.',
    },
    {
      label: 'Top contributor',
      value: topUsers?.[0]?.label || '—',
      detail: topUsers?.[0] ? `${Number(topUsers[0].value || 0).toLocaleString()} events in the current window.` : 'No active contributor identified.',
    },
  ];

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Development Activity</h1>
        <p>
          Tracks how users are building in DSS using audit events tagged into categories, then grouped into capabilities.
        </p>
      </div>

      <FilterPageLayout
        filtersExpanded={filtersExpanded}
        onOpenFilters={() => setFiltersExpanded(true)}
        onCloseFilters={() => setFiltersExpanded(false)}
        filterContent={(
          <>
            {loading ? <div className="PulseMuted" style={{ marginTop: 8 }}>Loading…</div> : null}
            {error ? <div className="PulseError">{error}</div> : null}
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Querying DuckDB views loaded from curated GOLD base tables.
            </div>
            <label className="PulseLabel" style={{ maxWidth: 240 }}>
              Time window
              <select className="PulseSelect" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
            </label>
          </>
        )}
      >
          <div className="PulseCard">
        <h2>Activity Over Time</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Track how overall development-event volume is moving across the selected review window.
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          {activityPerformanceSummaryTiles.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              <div className="PulseSummaryDetail">{tile.detail}</div>
            </div>
          ))}
        </div>
        <div className="PulseVizGrid">
          <LineChart title="Dev Activity Events" points={activityDaily} />
          <BarList title="Daily Event Volumes" rows={activityDaily} maxRows={12} />
        </div>
      </div>

      <div className="PulseCard">
        <h2>Capability Adoption</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          See which platform capabilities are used by the largest share of active users. Click a capability to open details.
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          <div className="PulseSummaryTile">
            <div className="PulseSummaryCount">{Number(capabilityUsage.summary?.active_users || 0).toLocaleString()}</div>
            <div className="PulseSummaryLabel">Active users in selected period</div>
          </div>
          <div className="PulseSummaryTile">
            <div className="PulseSummaryCount">{Number(capabilityUsage.summary?.active_capabilities || 0).toLocaleString()}</div>
            <div className="PulseSummaryLabel">Active capabilities</div>
          </div>
          <div className="PulseSummaryTile">
            <div className="PulseSummaryCount">{capabilityUsage.topByUsers?.[0]?.label || '—'}</div>
            <div className="PulseSummaryLabel">Most adopted capability</div>
            <div className="PulseSummaryDetail">
              {capabilityUsage.topByUsers?.[0]
                ? `${Number(capabilityUsage.topByUsers[0].value || 0).toLocaleString()} users • ${Number(capabilityUsage.topByUsers[0].userShare || 0).toFixed(1)}% of active users`
                : 'No adopted capability identified.'}
            </div>
          </div>
          <div className="PulseSummaryTile">
            <div className="PulseSummaryCount">{capabilityUsage.byCapability?.[0]?.label || '—'}</div>
            <div className="PulseSummaryLabel">Highest activity capability</div>
            <div className="PulseSummaryDetail">
              {capabilityUsage.byCapability?.[0]
                ? `${Number(capabilityUsage.byCapability[0].value || 0).toLocaleString()} events in the current window`
                : 'No active capability identified.'}
            </div>
          </div>
        </div>
        <div className="PulseVizGrid">
          <BarList title="Capability Adoption" rows={(capabilityUsage.topByUsers || []).map((row) => ({ ...row, value: `${Number(row.value || 0).toLocaleString()} users • ${row.userShare == null ? '-' : `${Number(row.userShare).toFixed(1)}%`}` }))} maxRows={10} onRowClick={(label) => setSelectedCapability(label)} />
          <LineChart title="Capability activity over time" points={capabilityUsage.activityDaily || []} />
          <BarList title="Capability activity by events" rows={capabilityUsage.byCapability || []} maxRows={10} onRowClick={(label) => setSelectedCapability(label)} />
          <BarList title="Capabilities by projects" rows={capabilityUsage.topByProjects || []} maxRows={10} onRowClick={(label) => setSelectedCapability(label)} />
          <BarList title="Capabilities by instances" rows={capabilityUsage.topByInstances || []} maxRows={10} onRowClick={(label) => setSelectedCapability(label)} />
        </div>
      </div>

      <div className="PulseCard">
        <h2>Capability activity by events Details</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Use supporting activity, category, and contributor views to understand what is behind adoption levels.
        </div>
        <div className="PulseVizGrid">
          <BarList
            title="Activity by capability"
            rows={byCapability}
            maxRows={12}
            onRowClick={(label) => setSelectedCapability(label)}
          />
          <BarList title="Top capability categories" rows={byCategory} maxRows={12} />
          <BarList title="Top contributors" rows={topUsers} maxRows={12} />
        </div>
      </div>

      {selectedCapability && capabilitySummary ? (
        <Modal
          title={`${selectedCapability} details`}
          onClose={() => setSelectedCapability(null)}
        >
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <Badge>{selectedCapability}</Badge>
            <Badge>{windowDays}d window</Badge>
          </div>

          <PulseSection title="Summary">
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Summary for the selected capability and time period.
            </div>
            <div className="PulseDetailGrid">
              <div>
                <div className="PulseMuted">Events</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{capabilitySummary.events}</div>
              </div>
              <div>
                <div className="PulseMuted">Active users</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{capabilitySummary.users}</div>
              </div>
              <div>
                <div className="PulseMuted">Projects touched</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{capabilitySummary.projects}</div>
              </div>
              <div>
                <div className="PulseMuted">Instances</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{capabilitySummary.instances}</div>
              </div>
            </div>
          </PulseSection>

          <PulseSection title="Activity Over Time">
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Trend and daily distribution for this capability across the selected review window.
            </div>
            <div className="PulseVizGrid">
              <LineChart title="Events per day" points={capabilityActivityDaily} />
              <BarList title="Daily Event Volumes" rows={capabilityActivityDaily} maxRows={12} />
            </div>
          </PulseSection>

          <PulseSection title="Top Categories">
            <div className="PulseVizGrid">
              <BarList title="Events by category" rows={capabilityCategories} maxRows={12} />
            </div>
          </PulseSection>

          <PulseSection title="Top Base Tags">
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Base tags come from <code>msgtypebase</code>.
            </div>
            <div className="PulseVizGrid">
              <BarList title="Events by base tag" rows={capabilityTags} maxRows={12} />
            </div>
          </PulseSection>

          <PulseSection title="Top Users">
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Click a user to drill down.
            </div>
            <div className="PulseVizGrid">
              <BarList
                title="Users by Event Count"
                rows={capabilityTopUsers}
                maxRows={12}
                onRowClick={(label) => {
                  setSelectedUser(label);
                  setSelectedCapability(null);
                }}
              />
            </div>
          </PulseSection>
        </Modal>
      ) : null}

      {selectedUser && userSummary ? (
        <Modal title={`${selectedUser} details`} onClose={() => setSelectedUser(null)}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <Badge>{selectedUser}</Badge>
            <Badge>{windowDays}d window</Badge>
            <button className="PulseButton" type="button" onClick={() => { setSelectedUser(null); setUserDetail(null); setTopProjects([]); setUserTrendMode('developing'); setShowUserInformation(false); }}>
              Clear selection
            </button>
          </div>

          <UserInformationSection
            detail={sharedDetail}
            detailInstances={sharedInstances}
            selectedInstance={null}
            expanded={showUserInformation}
            onToggle={() => setShowUserInformation((v) => !v)}
            loadingMessage="Loading shared user information…"
          />

          <PulseSection title="Activity Summary">
            <div className="PulseDetailGrid">
              <div>
                <div className="PulseMuted">Events</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{userSummary.events}</div>
              </div>
              <div>
                <div className="PulseMuted">Projects touched</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{userSummary.projects}</div>
              </div>
              <div>
                <div className="PulseMuted">Instances</div>
                <div style={{ fontWeight: 800, fontSize: 20 }}>{userSummary.instances}</div>
              </div>
            </div>
          </PulseSection>

          <PulseSection title="Activity Over Time">
            {hasActivityInWindow ? (
              <>
                <div className="PulseMuted" style={{ marginBottom: 10 }}>
                  Review how this actor's observed development events move across the selected window. Use the mode toggle to switch the story between creating and consuming behavior.
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                  <button
                    className={`PulseButton ${userTrendMode === 'developing' ? 'PulseButtonToggleActive' : ''}`}
                    type="button"
                    onClick={() => setUserTrendMode('developing')}
                  >
                    Creating
                  </button>
                  <button
                    className={`PulseButton ${userTrendMode === 'viewing' ? 'PulseButtonToggleActive' : ''}`}
                    type="button"
                    onClick={() => setUserTrendMode('viewing')}
                  >
                    Consuming
                  </button>
                </div>
                <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
                  <div className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
                    <div className="PulseSummaryCount">{activityTrendTotal.toLocaleString()}</div>
                    <div className="PulseSummaryLabel">{userTrendMode === 'viewing' ? 'Consumption events' : 'Creation events'}</div>
                    <div className="PulseSummaryDetail">Observed across the current activity window.</div>
                  </div>
                  <div className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
                    <div className="PulseSummaryCount">{activityTrendAverage.toFixed(1)}</div>
                    <div className="PulseSummaryLabel">Average per bucket</div>
                    <div className="PulseSummaryDetail">Average observed daily event volume for the selected mode.</div>
                  </div>
                  <div className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
                    <div className="PulseSummaryCount">{activityTrendPeak.toLocaleString()}</div>
                    <div className="PulseSummaryLabel">Peak daily volume</div>
                    <div className="PulseSummaryDetail">{activityTrendPeakPoint?.label ? `Highest observed day: ${activityTrendPeakPoint.label}` : 'No peak day identified.'}</div>
                  </div>
                </div>
                <div className="PulseVizGrid">
                  <LineChart
                    title={userTrendMode === 'viewing' ? 'Consumption events by day' : 'Creation events by day'}
                    points={activityTrendPoints}
                  />
                  <BarList
                    title={userTrendMode === 'viewing' ? 'Daily consumption volumes' : 'Daily creation volumes'}
                    rows={activityTrendPoints}
                    maxRows={12}
                  />
                </div>
              </>
            ) : (
              <div className="PulseMuted">No activity was identified for this actor in the selected window.</div>
            )}
          </PulseSection>

          <PulseSection title="Capabilities Touched">
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Click a capability to drill down.
            </div>
            <div className="PulseVizGrid">
              <BarList
                title="Activity by capability"
                rows={userCapabilities}
                maxRows={12}
                onRowClick={(label) => {
                  setSelectedCapability(label);
                  setSelectedUser(null);
                  setShowUserInformation(false);
                }}
              />
            </div>
          </PulseSection>

          <PulseSection title="Top Categories">
            <div className="PulseVizGrid">
              <BarList title="Events by category" rows={userCategories} maxRows={12} />
            </div>
          </PulseSection>

          <PulseSection title="Top Base Tags">
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Base tags come from <code>msgtypebase</code>.
            </div>
            <div className="PulseVizGrid">
              <BarList title="Events by base tag" rows={userTags} maxRows={12} />
            </div>
          </PulseSection>

          <PulseSection title="Top Projects for Activity">
            <div className="PulseMuted" style={{ marginBottom: 8 }}>
              Projects are reported as <code>(instance_name, project_key)</code> pairs.
            </div>
            {topProjects.length ? (
              <div className="PulseTableWrap">
                <table className="PulseTable">
                  <thead>
                    <tr>
                      <th>Instance</th>
                      <th>Project Key</th>
                      <th>Creating</th>
                      <th>Consuming</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topProjects.map((r) => (
                      <tr key={`${r.instanceName}__${r.projectKey}`}>
                        <td><Badge>{r.instanceName}</Badge></td>
                        <td><Badge>{r.projectKey}</Badge></td>
                        <td>{r.developing}</td>
                        <td>{r.viewing}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="PulseMuted">No project activity is available for this user in the selected window.</div>
            )}
          </PulseSection>
        </Modal>
      ) : null}

      </FilterPageLayout>
    </div>
  );
}

const PRODUCT_TYPE_LABELS = {
  api_endpoint: 'API endpoints',
  agent: 'Agents',
  dashboard: 'Dashboards',
  web_application: 'Web apps',
  dataiku_application: 'Applications',
};

function labelForProductType(type) {
  if (!type) return '';
  if (PRODUCT_TYPE_LABELS[type]) return PRODUCT_TYPE_LABELS[type];
  return String(type)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}



function ProductOutputsPage({ apiBase }) {
  const [activeTab, setActiveTab] = useState('overview');
  const [typeSubTab, setTypeSubTab] = useState('overview');

  // Use DuckDB-backed views instead of placeholder generators.
  const [allOutputs, setAllOutputs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const isPerformance = activeTab === 'overview';
  const isInventory = activeTab === 'catalog';
  const selectedType = activeTab.startsWith('type:') ? activeTab.slice('type:'.length) : null;

  // Load outputs from product catalog (acts as "outputs" inventory).
  useEffect(() => {
    setLoading(true);
    setError('');

    // Pull enough rows for overview cards; deeper metrics are loaded per-type.
    const params = new URLSearchParams();
    params.set('limit', '5000');
    params.set('offset', '0');

    fetch(apiUrl(apiBase, `/api/build/products?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading products');
        const rows = data.rows || [];
        setAllOutputs(
          rows.map((r) => ({
            outputId: r.assetId,
            outputType: r.objectType,
            outputTypeLabel: labelForProductType(r.objectType),
            outputName: r.objectName,
            instanceName: r.instanceName,
            projectKey: r.projectKey,
            ownerLogin: r.ownerLogin,
            createdAt: r.updatedAt,
          }))
        );
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiBase]);

  // Reset sub-tab when switching product types.
  useEffect(() => {
    if (!selectedType) return;
    setTypeSubTab('overview');
  }, [selectedType]);

  const [typeMetricsLoading, setTypeMetricsLoading] = useState(false);
  const [typeMetricsError, setTypeMetricsError] = useState('');
  const [typeMetrics, setTypeMetrics] = useState(null);

  useEffect(() => {
    if (!selectedType) return;

    let cancelled = false;

    const params = new URLSearchParams();
    params.set('type', selectedType);
    params.set('days', '30');

    setTypeMetricsLoading(true);
    setTypeMetricsError('');

    fetch(apiUrl(apiBase, `/api/build/products/type-metrics?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading product type metrics');
        if (!cancelled) setTypeMetrics(data);
      })
      .catch((e) => {
        if (!cancelled) setTypeMetricsError(e.message);
      })
      .finally(() => {
        if (!cancelled) setTypeMetricsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiBase, selectedType]);

  const summary = useMemo(() => {
    const byType = new Map();
    for (const o of allOutputs) {
      const key = o.outputType;
      byType.set(key, (byType.get(key) || 0) + 1);
    }

    const typeMeta = new Map();
    for (const o of allOutputs) {
      if (!typeMeta.has(o.outputType)) {
        typeMeta.set(o.outputType, o.outputTypeLabel);
      }
    }

    return Array.from(byType.entries())
      .map(([type, count]) => ({ type, label: typeMeta.get(type) || labelForProductType(type), count }))
      .sort((a, b) => b.count - a.count);
  }, [allOutputs]);

  const typeTabs = useMemo(() => {
    // Keep the same order as the Performance summary cards (count desc).
    return summary.filter((s) => s.count > 0);
  }, [summary]);

  useEffect(() => {
    if (!selectedType) return;
    if (typeTabs.some((t) => t.type === selectedType)) return;
    setActiveTab('overview');
  }, [selectedType, typeTabs]);

  const selectedTypeLabel = useMemo(() => {
    if (!selectedType) return '';
    return typeTabs.find((t) => t.type === selectedType)?.label || labelForProductType(selectedType);
  }, [selectedType, typeTabs]);

  const productsByProject = useMemo(() => {
    const counts = new Map();
    for (const o of allOutputs) {
      counts.set(o.projectKey, (counts.get(o.projectKey) || 0) + 1);
    }
    return Array.from(counts.entries()).map(([label, value]) => ({ label, value }));
  }, [allOutputs]);

  const productsByInstance = useMemo(() => {
    const counts = new Map();
    for (const o of allOutputs) {
      counts.set(o.instanceName, (counts.get(o.instanceName) || 0) + 1);
    }
    return Array.from(counts.entries()).map(([label, value]) => ({ label, value }));
  }, [allOutputs]);

  const productsByType = useMemo(() => {
    return summary.map((s) => ({ label: s.label, value: s.count }));
  }, [summary]);

  const productsOverTime = useMemo(() => {
    // Month buckets (YYYY-MM)
    const bucket = (iso) => {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return null;
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      return `${y}-${m}`;
    };

    const counts = new Map();
    for (const o of allOutputs) {
      const b = bucket(o.createdAt);
      if (!b) continue;
      counts.set(b, (counts.get(b) || 0) + 1);
    }

    const keys = Array.from(counts.keys()).sort();
    const last = keys.slice(-12);
    return last.map((k) => ({ label: k.slice(5), value: counts.get(k) || 0 }));
  }, [allOutputs]);

  const selectedOutputs = useMemo(() => {
    if (!selectedType) return [];
    return allOutputs.filter((o) => o.outputType === selectedType);
  }, [allOutputs, selectedType]);

  const selectedByInstance = useMemo(() => {
    if (!selectedType) return [];
    const counts = new Map();
    for (const o of selectedOutputs) {
      counts.set(o.instanceName, (counts.get(o.instanceName) || 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
  }, [selectedOutputs, selectedType]);

  const selectedByProject = useMemo(() => {
    if (!selectedType) return [];
    const counts = new Map();
    for (const o of selectedOutputs) {
      counts.set(o.projectKey, (counts.get(o.projectKey) || 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
  }, [selectedOutputs, selectedType]);

  const selectedTopOwners = useMemo(() => {
    if (!selectedType) return [];
    const counts = new Map();
    for (const o of selectedOutputs) {
      counts.set(o.ownerLogin, (counts.get(o.ownerLogin) || 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 12);
  }, [selectedOutputs, selectedType]);

  const selectedOverTime = useMemo(() => {
    if (!selectedType) return [];

    const bucket = (iso) => {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return null;
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      return `${y}-${m}`;
    };

    const counts = new Map();
    for (const o of selectedOutputs) {
      const b = bucket(o.createdAt);
      if (!b) continue;
      counts.set(b, (counts.get(b) || 0) + 1);
    }

    const keys = Array.from(counts.keys()).sort();
    const last = keys.slice(-12);
    return last.map((k) => ({ label: k.slice(5), value: counts.get(k) || 0 }));
  }, [selectedOutputs, selectedType]);

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Products</h1>
        <p>
          Understand what is being built and browse the catalog of all products.
        </p>
      </div>

      <div className="PulseTabs" role="tablist" aria-label="Products">
        <button
          type="button"
          role="tab"
          aria-selected={isPerformance}
          className={isPerformance ? 'PulseTab PulseTabActive' : 'PulseTab'}
          onClick={() => setActiveTab('overview')}
        >
          Performance
        </button>

        {typeTabs.map((t) => (
          <button
            key={t.type}
            type="button"
            role="tab"
            aria-selected={selectedType === t.type}
            className={selectedType === t.type ? 'PulseTab PulseTabActive' : 'PulseTab'}
            onClick={() => setActiveTab(`type:${t.type}`)}
          >
            {t.label}
          </button>
        ))}

        <button
          type="button"
          role="tab"
          aria-selected={isInventory}
          className={isInventory ? 'PulseTab PulseTabActive' : 'PulseTab'}
          onClick={() => setActiveTab('catalog')}
        >
          Inventory
        </button>
      </div>

      {isInventory ? (
        <ProductInventoryTab apiBase={apiBase} />
      ) : (
        <>
          {loading ? <div className="PulseCard"><div className="PulseMuted">Loading…</div></div> : null}
          {error ? <div className="PulseCard"><div className="PulseError">{error}</div></div> : null}

          {isPerformance ? (
            <>
              <div className="PulseCard">
                <h2>Portfolio Summary</h2>
                <div className="PulseSummaryGrid">
                  {summary.map((row) => (
                    <button
                      key={row.type}
                      type="button"
                      className="PulseSummaryTile"
                      onClick={() => setActiveTab(`type:${row.type}`)}
                      title="Open category review"
                    >
                      <div className="PulseSummaryCount">{row.count}</div>
                      <div className="PulseSummaryLabel">{row.label}</div>
                    </button>
                  ))}
                </div>
                <div className="PulseMuted" style={{ marginTop: 10 }}>
                  Select a product category to review performance and key highlights.
                </div>
              </div>

              <div className="PulseCard">
                <h2>Portfolio Composition</h2>
                <div className="PulseMuted" style={{ marginBottom: 10 }}>
                  These charts provide a portfolio view across product categories, projects, instances, and recent creation trends.
                </div>

                <div className="PulseVizGrid">
                  <BarList title="Products by Type" rows={productsByType} maxRows={10} />
                  <BarList title="Products by Project" rows={productsByProject} maxRows={12} />
                  <BarList title="Products by Instance" rows={productsByInstance} maxRows={12} />
                  <LineChart title="Products Over Time" points={productsOverTime} />
                </div>
              </div>
            </>
           ) : selectedType ? (
             <>
               <div className="PulseCard">
                 <h2>{selectedTypeLabel}</h2>
                 <div className="PulseMuted" style={{ marginBottom: 10 }}>
                   A business summary of recent activity and performance for {selectedTypeLabel}.
                 </div>

                 <div className="PulseTabs" role="tablist" aria-label={`${selectedTypeLabel} details`}>
                   <button
                     type="button"
                     role="tab"
                     aria-selected={typeSubTab === 'overview'}
                     className={typeSubTab === 'overview' ? 'PulseTab PulseTabActive' : 'PulseTab'}
                     onClick={() => setTypeSubTab('overview')}
                   >
                     Performance
                   </button>
                   <button
                     type="button"
                     role="tab"
                     aria-selected={typeSubTab === 'insights'}
                     className={typeSubTab === 'insights' ? 'PulseTab PulseTabActive' : 'PulseTab'}
                     onClick={() => setTypeSubTab('insights')}
                   >
                     Highlights
                   </button>
                 </div>

                 {typeMetricsLoading ? <div className="PulseMuted">Loading…</div> : null}
                 {typeMetricsError ? <div className="PulseError">{typeMetricsError}</div> : null}

                 {(() => {
                   const totalProducts = Number(typeMetrics?.kpis?.totalProducts ?? 0);
                   const activeProducts30d = Number(typeMetrics?.kpis?.activeProducts30d ?? 0);
                   const events30d = Number(typeMetrics?.kpis?.events30d ?? 0);
                   const activeUsers30d = Number(typeMetrics?.kpis?.activeUsers30d ?? 0);
                   const activityDaily = typeMetrics?.charts?.activityDaily || [];
                   const topProjectsByEvents = typeMetrics?.charts?.topProjectsByEvents || [];
                   const topProductsByEvents = typeMetrics?.charts?.topProductsByEvents || [];
                   const topOwnersByProducts = typeMetrics?.charts?.topOwnersByProducts || [];
                   const eventsByInstance = typeMetrics?.charts?.eventsByInstance || [];
                   const adoptionRate = totalProducts > 0 ? Math.round((activeProducts30d / totalProducts) * 100) : 0;
                   const avgEventsPerActiveProduct = activeProducts30d > 0 ? Math.round(events30d / activeProducts30d) : 0;
                   const avgUsersPerActiveProduct = activeProducts30d > 0 ? activeUsers30d / activeProducts30d : 0;
                   const topProject = topProjectsByEvents[0] || null;
                   const topProduct = topProductsByEvents[0] || null;
                   const topOwner = topOwnersByProducts[0] || null;
                   const topInstance = eventsByInstance[0] || null;
                   const activeDays = activityDaily.filter((point) => Number(point?.value || 0) > 0).length;
                   const peakDay = activityDaily.reduce((best, point) => {
                     if (!best) return point;
                     return Number(point?.value || 0) > Number(best?.value || 0) ? point : best;
                   }, null);
                   const typeNarrativeConfig = {
                     dashboard: {
                       pluralNoun: 'dashboards',
                       activityNoun: 'views and interactions',
                       purpose: 'dashboard adoption and repeat engagement',
                       productLead: 'The most active dashboard was',
                       ownerLead: 'Dashboard ownership is led by',
                     },
                     insight: {
                       pluralNoun: 'insights',
                       activityNoun: 'views and interactions',
                       purpose: 'insight adoption and repeat engagement',
                       productLead: 'The most active insight was',
                       ownerLead: 'Insight ownership is led by',
                     },
                     web_application: {
                       pluralNoun: 'web apps',
                       activityNoun: 'sessions and interactions',
                       purpose: 'web app engagement and repeat usage',
                       productLead: 'The busiest web app was',
                       ownerLead: 'Web app ownership is led by',
                     },
                     dataiku_application: {
                       pluralNoun: 'applications',
                       activityNoun: 'usage events',
                       purpose: 'application adoption across projects and instances',
                       productLead: 'The leading application was',
                       ownerLead: 'Application ownership is led by',
                     },
                     api_endpoint: {
                       pluralNoun: 'API endpoints',
                       activityNoun: 'calls and interactions',
                       purpose: 'API endpoint utilization and reuse',
                       productLead: 'The most active API endpoint was',
                       ownerLead: 'API ownership is led by',
                     },
                     agent: {
                       pluralNoun: 'agents',
                       activityNoun: 'agent interactions',
                       purpose: 'agent engagement and repeat usage',
                       productLead: 'The most active agent was',
                       ownerLead: 'Agent ownership is led by',
                     },
                     agent_tool: {
                       pluralNoun: 'agent tools',
                       activityNoun: 'tool interactions',
                       purpose: 'agent tool engagement and reuse',
                       productLead: 'The most active agent tool was',
                       ownerLead: 'Agent tool ownership is led by',
                     },
                     api_service: {
                       pluralNoun: 'API services',
                       activityNoun: 'service interactions',
                       purpose: 'API service utilization and reuse',
                       productLead: 'The most active API service was',
                       ownerLead: 'API service ownership is led by',
                     },
                     saved_model: {
                       pluralNoun: 'saved models',
                       activityNoun: 'usage events',
                       purpose: 'saved model consumption and reuse',
                       productLead: 'The most active saved model was',
                       ownerLead: 'Saved model ownership is led by',
                     },
                     retrieval_augmented_llm: {
                       pluralNoun: 'RAG applications',
                       activityNoun: 'usage events',
                       purpose: 'RAG application adoption and reuse',
                       productLead: 'The most active RAG application was',
                       ownerLead: 'RAG ownership is led by',
                     },
                   };
                   const typeNarrative = typeNarrativeConfig[selectedType] || {
                     pluralNoun: selectedTypeLabel.toLowerCase(),
                     activityNoun: 'events',
                     purpose: `recent activity for ${selectedTypeLabel.toLowerCase()}`,
                     productLead: 'The most active product was',
                     ownerLead: 'Ownership is led by',
                   };

                   return !typeMetricsLoading && !typeMetricsError && typeMetrics ? (
                     typeSubTab === 'overview' ? (
                       <>
                         <div className="PulseDetailGrid" style={{ marginTop: 12 }}>
                           <div>
                             <div className="PulseMuted">Total products</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>
                               {totalProducts.toLocaleString()}
                             </div>
                           </div>
                           <div>
                             <div className="PulseMuted">Active products</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>
                               {activeProducts30d.toLocaleString()}
                             </div>
                           </div>
                           <div>
                             <div className="PulseMuted">Activity volume</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>
                               {events30d.toLocaleString()}
                             </div>
                           </div>
                           <div>
                             <div className="PulseMuted">Active users</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>
                               {activeUsers30d.toLocaleString()}
                             </div>
                           </div>
                         </div>

                         <div className="PulseVizGrid" style={{ marginTop: 12 }}>
                           <LineChart
                             title="Activity Trend (30d)"
                             points={activityDaily}
                           />
                           <BarList
                             title="Top Products by Activity"
                             rows={topProductsByEvents.map((r) => ({
                               label: r.label,
                               value: r.value,
                             }))}
                             maxRows={12}
                           />
                           <BarList
                             title="Top Projects by Activity"
                             rows={topProjectsByEvents}
                             maxRows={12}
                           />
                           <BarList
                             title="Top Owners (Product Count)"
                             rows={topOwnersByProducts}
                             maxRows={12}
                           />
                           <BarList
                             title="Activity by Instance"
                             rows={eventsByInstance}
                             maxRows={12}
                           />
                         </div>
                       </>
                     ) : (
                       <>
                         <div className="PulseMuted" style={{ marginTop: 12 }}>
                           An executive summary of {typeNarrative.purpose}.
                         </div>

                         <div className="PulseDetailGrid" style={{ marginTop: 12 }}>
                           <div>
                             <div className="PulseMuted">Adoption</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>{adoptionRate}%</div>
                             <div className="PulseMuted">{activeProducts30d.toLocaleString()} of {totalProducts.toLocaleString()} active in 30d</div>
                           </div>
                           <div>
                             <div className="PulseMuted">Average activity per active product</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>{avgEventsPerActiveProduct.toLocaleString()}</div>
                             <div className="PulseMuted">Based on active products in the last 30 days</div>
                           </div>
                           <div>
                             <div className="PulseMuted">Average users per active product</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>{avgUsersPerActiveProduct.toFixed(avgUsersPerActiveProduct >= 10 ? 0 : 1)}</div>
                             <div className="PulseMuted">Distinct users across active products</div>
                           </div>
                           <div>
                             <div className="PulseMuted">Days with activity</div>
                             <div style={{ fontWeight: 800, fontSize: 20 }}>{activeDays.toLocaleString()}</div>
                             <div className="PulseMuted">Days with observed activity in the last 30 days</div>
                           </div>
                         </div>

                         <div className="PulseCard" style={{ marginTop: 12 }}>
                           <h2>Key Takeaways</h2>
                           <ul>
                             <li>
                               {activeProducts30d.toLocaleString()} of {totalProducts.toLocaleString()} {typeNarrative.pluralNoun} were active in the last 30 days ({adoptionRate}% adoption).
                             </li>
                             <li>
                               {activeUsers30d.toLocaleString()} distinct users generated {events30d.toLocaleString()} {typeNarrative.activityNoun}, averaging {avgEventsPerActiveProduct.toLocaleString()} events per active product.
                             </li>
                             <li>
                               {peakDay ? <>Peak activity occurred on <strong>{peakDay.label}</strong> with <strong>{Number(peakDay.value || 0).toLocaleString()}</strong> events.</> : <>A daily activity trend is not yet available for this product category.</>}
                             </li>
                             <li>
                               {topProduct ? <>{typeNarrative.productLead} <strong>{topProduct.label}</strong> with <strong>{Number(topProduct.value || 0).toLocaleString()}</strong> events.</> : <>A product-level ranking is not yet available for this category.</>}
                             </li>
                             <li>
                               {topProject ? <>The leading project was <strong>{topProject.label}</strong> with <strong>{Number(topProject.value || 0).toLocaleString()}</strong> events.</> : <>A project-level ranking is not yet available for this category.</>}
                             </li>
                             <li>
                               {topOwner ? <>{typeNarrative.ownerLead} <strong>{topOwner.label}</strong>, who owns <strong>{Number(topOwner.value || 0).toLocaleString()}</strong> {typeNarrative.pluralNoun}.</> : <>An ownership concentration view is not yet available for this category.</>}
                             </li>
                             <li>
                               {topInstance ? <>The busiest instance was <strong>{topInstance.label}</strong> with <strong>{Number(topInstance.value || 0).toLocaleString()}</strong> events.</> : <>An instance-level activity view is not yet available for this category.</>}
                             </li>
                           </ul>
                         </div>
                       </>
                     )
                   ) : null;
                 })()}
               </div>

               {(!typeMetricsLoading && (typeMetricsError || !typeMetrics)) ? (
                 <div className="PulseCard">
                   <h2>{selectedTypeLabel} Breakdown (Fallback)</h2>
                   <div className="PulseMuted" style={{ marginBottom: 10 }}>
                     Showing portfolio fallback charts because recent category metrics are unavailable.
                   </div>
                   <div className="PulseVizGrid">
                     <BarList title="By Instance" rows={selectedByInstance} maxRows={12} />
                     <BarList title="By Project" rows={selectedByProject} maxRows={12} />
                     <BarList title="Top Owners" rows={selectedTopOwners} maxRows={12} />
                     <LineChart title="Over Time" points={selectedOverTime} />
                   </div>
                 </div>
               ) : null}
             </>
           ) : null}


        </>
      )}
    </div>
  );
}



function ConsumptionActivityPage({ apiBase }) {
  const [windowDays, setWindowDays] = useState(30);
  const [facets, setFacets] = useState({ instances: [], projects: [], types: [], owners: [] });

  const [q, setQ] = useState('');
  const [owner, setOwner] = useState('');
  const [selectedInstances, setSelectedInstances] = useState([]);
  const [selectedProjects, setSelectedProjects] = useState([]);
  const [selectedTypes, setSelectedTypes] = useState([]);

  const [totals, setTotals] = useState({ events: 0, activeUsers: 0, activeProducts: 0 });
  const [lifecycleSummary, setLifecycleSummary] = useState({});
  const [lifecycleError, setLifecycleError] = useState('');
  const [byType, setByType] = useState([]);
  const [activityDaily, setActivityDaily] = useState([]);
  const [topProducts, setTopProducts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [selectedProductId, setSelectedProductId] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState('');
  const [details, setDetails] = useState(null);
  const [selectedCapability, setSelectedCapability] = useState(null);
  const [capabilityDetails, setCapabilityDetails] = useState(null);
  const [capabilityLoading, setCapabilityLoading] = useState(false);
  const [capabilityError, setCapabilityError] = useState('');
  const [byTypeSort, setByTypeSort] = useState({ column: 'events', direction: 'desc' });
  const [topProductsPage, setTopProductsPage] = useState(1);

  const topProductsPageSize = 10;

  const toggleMulti = (value, current, setFn) => {
    if (current.includes(value)) {
      setFn(current.filter((v) => v !== value));
    } else {
      setFn([...current, value]);
    }
  };

  const resetFilters = () => {
    setQ('');
    setOwner('');
    setSelectedInstances([]);
    setSelectedProjects([]);
    setSelectedTypes([]);
  };

  const showLifecycleLatency = useMemo(() => {
    return Number(lifecycleSummary.productsWithCreatedAt || 0) > 0
      && Number(lifecycleSummary.productsWithFirstConsumption || 0) > 0;
  }, [lifecycleSummary]);

  useEffect(() => {
    fetch(apiUrl(apiBase, '/api/consumption/products/facets'))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading facets');
        setFacets({
          instances: data.instances || [],
          projects: data.projects || [],
          types: data.types || [],
          owners: data.owners || [],
        });
      })
      .catch(() => {
        // Non-fatal; keep empty facets.
        setFacets({ instances: [], projects: [], types: [], owners: [] });
      });
  }, [apiBase]);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('days', String(Math.max(windowDays, 365)));
    if (selectedInstances.length) params.set('instances', selectedInstances.join(','));
    if (selectedProjects.length) params.set('projects', selectedProjects.join(','));
    if (selectedTypes.length) params.set('types', selectedTypes.join(','));

    setLifecycleError('');

    fetch(apiUrl(apiBase, `/api/consumption/products/lifecycle-summary?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading lifecycle summary');
        setLifecycleSummary(data.summary || {});
      })
      .catch((e) => {
        setLifecycleSummary({});
        setLifecycleError(e.message);
      });
  }, [apiBase, windowDays, selectedInstances, selectedProjects, selectedTypes]);

  useEffect(() => {
    if (!selectedCapability) return;

    const params = new URLSearchParams();
    params.set('days', String(windowDays));

    setCapabilityLoading(true);
    setCapabilityError('');

    fetch(apiUrl(apiBase, `/api/consumption/process-usage/capability/${encodeURIComponent(selectedCapability)}?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading capability detail');
        setCapabilityDetails(data);
      })
      .catch((e) => setCapabilityError(e.message))
      .finally(() => setCapabilityLoading(false));
  }, [apiBase, selectedCapability, windowDays]);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('days', String(windowDays));
    if (q.trim()) params.set('q', q.trim());
    if (owner.trim()) params.set('owner', owner.trim());
    if (selectedInstances.length) params.set('instances', selectedInstances.join(','));
    if (selectedProjects.length) params.set('projects', selectedProjects.join(','));
    if (selectedTypes.length) params.set('types', selectedTypes.join(','));

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/consumption/products/summary?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading consumption activity');
        setTotals(data.totals || { events: 0, activeUsers: 0, activeProducts: 0 });
        setByType(data.byType || []);
        setActivityDaily(data.activityDaily || []);
        setTopProducts(data.topProducts || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiBase, windowDays, q, owner, selectedInstances, selectedProjects, selectedTypes]);

  useEffect(() => {
    if (!detailsOpen || !selectedProductId) {
      setDetails(null);
      setDetailsError('');
      setDetailsLoading(false);
      return;
    }

    let cancelled = false;

    const params = new URLSearchParams();
    params.set('productId', selectedProductId);
    params.set('days', String(windowDays));

    setDetailsLoading(true);
    setDetailsError('');
    setDetails(null);

    fetch(apiUrl(apiBase, `/api/consumption/products/details?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading product details');
        if (!cancelled) setDetails(data);
      })
      .catch((e) => {
        if (!cancelled) setDetailsError(e.message);
      })
      .finally(() => {
        if (!cancelled) setDetailsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiBase, detailsOpen, selectedProductId, windowDays]);

  const sortedByType = useMemo(() => {
    const rows = [...(byType || [])];
    const { column, direction } = byTypeSort;
    const multiplier = direction === 'desc' ? -1 : 1;
    rows.sort((left, right) => {
      const leftValue = Number(left?.[column] ?? 0);
      const rightValue = Number(right?.[column] ?? 0);
      if (leftValue === rightValue) {
        return compareNullableStrings(left?.label, right?.label, 'asc');
      }
      return (leftValue < rightValue ? -1 : 1) * multiplier;
    });
    return rows;
  }, [byType, byTypeSort]);

  const toggleByTypeSort = (column) => {
    setByTypeSort((current) => {
      if (current.column === column) {
        return { column, direction: current.direction === 'desc' ? 'asc' : 'desc' };
      }
      return { column, direction: 'desc' };
    });
  };

  const openDetails = (productId) => {
    setSelectedProductId(productId);
    setDetailsOpen(true);
  };

  const closeDetails = () => {
    setDetailsOpen(false);
    setSelectedProductId(null);
    setDetails(null);
    setDetailsError('');
    setDetailsLoading(false);
  };

  const topProductByEvents = useMemo(() => {
    return [...(topProducts || [])].sort((left, right) => Number(right?.events || 0) - Number(left?.events || 0))[0] || null;
  }, [topProducts]);

  const topProductByUsers = useMemo(() => {
    return [...(topProducts || [])].sort((left, right) => Number(right?.activeUsers || 0) - Number(left?.activeUsers || 0))[0] || null;
  }, [topProducts]);

  const mostCollaborativeProduct = useMemo(() => {
    return [...(topProducts || [])]
      .sort((left, right) => {
        const userDelta = Number(right?.activeUsers || 0) - Number(left?.activeUsers || 0);
        if (userDelta !== 0) return userDelta;
        return Number(right?.events || 0) - Number(left?.events || 0);
      })[0] || null;
  }, [topProducts]);

  const topProductsTotalPages = Math.max(1, Math.ceil((topProducts || []).length / topProductsPageSize));
  const paginatedTopProducts = useMemo(() => {
    const start = (topProductsPage - 1) * topProductsPageSize;
    return (topProducts || []).slice(start, start + topProductsPageSize);
  }, [topProducts, topProductsPage]);

  useEffect(() => {
    setTopProductsPage(1);
  }, [topProducts]);

  useEffect(() => {
    setTopProductsPage((current) => Math.min(current, topProductsTotalPages));
  }, [topProductsTotalPages]);

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Consumption Activity</h1>
        <p>
          Understand who is consuming which products, and how consumption trends evolve over time.
        </p>
      </div>

      <FilterPageLayout
        filtersExpanded={filtersExpanded}
        onOpenFilters={() => setFiltersExpanded(true)}
        onCloseFilters={() => setFiltersExpanded(false)}
        filterContent={(
          <>
            {loading ? <div className="PulseMuted" style={{ marginTop: 8 }}>Loading…</div> : null}
            {error ? <div className="PulseError">{error}</div> : null}
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Querying DuckDB views loaded from curated GOLD base tables. Lifecycle latency metrics use an observed 365-day minimum lookback, even when the activity window is shorter.
            </div>
            <label className="PulseLabel" style={{ maxWidth: 240 }}>
              Time window
              <select className="PulseSelect" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
            </label>

            <div className="PulseFilterRail">
              <div className="PulseCard">
                <label className="PulseLabel">
                  Search
                  <input
                    className="PulseInput"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder="Search product name/key..."
                  />
                </label>

                <label className="PulseLabel">
                  Owner (dss_login)
                  <input
                    className="PulseInput"
                    value={owner}
                    onChange={(e) => setOwner(e.target.value)}
                    placeholder="e.g. alice"
                  />
                </label>

                <div className="PulseLabel">Instance</div>
                <div className="PulseCheckboxList">
                  {facets.instances.map((inst) => (
                    <label key={inst} className="PulseCheckboxRow">
                      <input
                        type="checkbox"
                        checked={selectedInstances.includes(inst)}
                        onChange={() => toggleMulti(inst, selectedInstances, setSelectedInstances)}
                      />
                      <span>{inst}</span>
                    </label>
                  ))}
                </div>

                <div className="PulseLabel">Project</div>
                <div className="PulseCheckboxList">
                  {facets.projects.map((pk) => (
                    <label key={pk} className="PulseCheckboxRow">
                      <input
                        type="checkbox"
                        checked={selectedProjects.includes(pk)}
                        onChange={() => toggleMulti(pk, selectedProjects, setSelectedProjects)}
                      />
                      <span>{pk}</span>
                    </label>
                  ))}
                </div>

                <div className="PulseLabel">Product type</div>
                <div className="PulseCheckboxList">
                  {facets.types.map((t) => (
                    <label key={t} className="PulseCheckboxRow">
                      <input
                        type="checkbox"
                        checked={selectedTypes.includes(t)}
                        onChange={() => toggleMulti(t, selectedTypes, setSelectedTypes)}
                      />
                      <span>{t}</span>
                    </label>
                  ))}
                </div>

                <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                  <button className="PulseButton" type="button" onClick={resetFilters}>Reset</button>
                </div>
              </div>
            </div>
          </>
        )}
      >
          <div className="PulseResults">
          <div className="PulseCard">
            <h2>Portfolio Overview</h2>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Consumption activity across webapps, dashboards, agents, applications, and APIs.
            </div>
            <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.events || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Total consumption events</div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.activeUsers || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Active users</div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.activeProducts || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Active products</div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.avgUsersPerProduct || 0).toFixed(1)}</div>
                <div className="PulseSummaryLabel">Average users per product</div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.avgProductsPerUser || 0).toFixed(1)}</div>
                <div className="PulseSummaryLabel">Average products per user</div>
              </div>
            </div>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              All-products activity trend across the selected scope.
            </div>
            <div className="PulseVizGrid">
              <LineChart title="Consumption Events" points={activityDaily} />
            </div>
          </div>

          <div className="PulseCard">
            <h2>Products by Adoption Tier</h2>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Counts of products across the selected scope, grouped by observed user breadth and event volume in the current window.
            </div>
            <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.singleUserProducts || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Tier 1 · 1 user</div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.multiUserLightProducts || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Tier 2 · 2+ users, &lt;5 events</div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{Number(totals.adoptedProducts || 0).toLocaleString()}</div>
                <div className="PulseSummaryLabel">Tier 3 · 2+ users, 5+ events</div>
              </div>
            </div>
            <div className="PulseMuted">
              Tier 1 = exactly 1 user; Tier 2 = at least 2 users and fewer than 5 events; Tier 3 = at least 2 users and at least 5 events.
            </div>
          </div>

          {showLifecycleLatency ? (
            <div className="PulseCard">
              <h2>Lifecycle Latency</h2>
              <div className="PulseMuted" style={{ marginBottom: 10 }}>
                Observed timing from product creation to first consumption and early adoption milestones, measured over at least the last 365 days.
                <InfoTip text="This v1 cycle-time model uses product created_at plus observed consumption events. It does not yet include automation-node deployment timing." />
              </div>
              {lifecycleError ? <div className="PulseError">{lifecycleError}</div> : null}
              <div className="PulseDetailGrid">
                <div>
                  <div className="PulseMuted">Median days to first consumption<InfoTip text="Median days from product created_at to the first observed consumption event." /></div>
                  <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(lifecycleSummary.medianDaysToFirstConsumption || 0).toFixed(1)}</div>
                </div>
                <div>
                  <div className="PulseMuted">Median days to multi-user adoption<InfoTip text="Median days from product created_at until at least two users have consumed the product." /></div>
                  <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(lifecycleSummary.medianDaysToMultiUser || 0).toFixed(1)}</div>
                </div>
                <div>
                  <div className="PulseMuted">Median days to repeat adoption<InfoTip text="Median days from product created_at until the product reaches at least five observed consumption events." /></div>
                  <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(lifecycleSummary.medianDaysToRepeatUse || 0).toFixed(1)}</div>
                </div>
                <div>
                  <div className="PulseMuted">Products with first consumption</div>
                  <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(lifecycleSummary.productsWithFirstConsumption || 0).toLocaleString()}</div>
                </div>
                <div>
                  <div className="PulseMuted">Products with multi-user adoption</div>
                  <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(lifecycleSummary.productsWithMultiUser || 0).toLocaleString()}</div>
                </div>
                <div>
                  <div className="PulseMuted">Products with repeat adoption</div>
                  <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(lifecycleSummary.productsWithRepeatUse || 0).toLocaleString()}</div>
                </div>
              </div>
            </div>
          ) : (
            <div className="PulseCard">
              <h2>Lifecycle Latency</h2>
              <div className="PulseMuted">
                Not enough products currently have both creation timestamps and observed consumption milestones to compute lifecycle latency reliably.
              </div>
              {lifecycleError ? <div className="PulseError">{lifecycleError}</div> : null}
            </div>
          )}

          <div className="PulseCard">
            <h2>By Product Type</h2>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Portfolio summary by product type. Sorted by total events by default.
            </div>
            <div className="PulseTableWrap">
              <table className="PulseTable">
                <thead>
                  <tr>
                    <th><SortableHeader label="Product Type" column="label" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Total Events" column="events" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Active Users" column="active_users" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Active Products" column="active_products" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Avg Users / Product" column="avg_users_per_product" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Max Users on Product" column="max_users_on_product" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Avg Maturity" column="avg_maturity_score" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Max Maturity" column="max_maturity_score" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                    <th><SortableHeader label="Adoption Count" column="adoption_count" sortState={byTypeSort} onToggle={toggleByTypeSort} /></th>
                  </tr>
                </thead>
                <tbody>
                  {sortedByType.map((row) => (
                    <tr key={row.label}>
                      <td><Badge>{row.label || '-'}</Badge></td>
                      <td>{Number(row.events || 0).toLocaleString()}</td>
                      <td>{Number(row.active_users || 0).toLocaleString()}</td>
                      <td>{Number(row.active_products || 0).toLocaleString()}</td>
                      <td>{Number(row.avg_users_per_product || 0).toFixed(1)}</td>
                      <td>{Number(row.max_users_on_product || 0).toLocaleString()}</td>
                      <td>{Number(row.avg_maturity_score || 0).toFixed(1)}</td>
                      <td>{Number(row.max_maturity_score || 0).toFixed(1)}</td>
                      <td>{Number(row.adoption_count || 0).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="PulseCard">
            <h2>Top Products</h2>
            <div className="PulseMuted" style={{ marginBottom: 10 }}>
              Portfolio leaderboard for the products driving the most observed consumption. Click a row for drilldown.
            </div>
            <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{topProductByEvents?.productName || topProductByEvents?.productKey || '—'}</div>
                <div className="PulseSummaryLabel">Top Product by Events</div>
                <div className="PulseSummaryDetail">
                  {topProductByEvents
                    ? `${Number(topProductByEvents.events || 0).toLocaleString()} events • ${topProductByEvents.instanceName || '-'} / ${topProductByEvents.projectKey || '-'}`
                    : 'No ranked product available.'}
                </div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{topProductByUsers?.productName || topProductByUsers?.productKey || '—'}</div>
                <div className="PulseSummaryLabel">Top Product by Users</div>
                <div className="PulseSummaryDetail">
                  {topProductByUsers
                    ? `${Number(topProductByUsers.activeUsers || 0).toLocaleString()} active users • ${topProductByUsers.instanceName || '-'} / ${topProductByUsers.projectKey || '-'}`
                    : 'No ranked product available.'}
                </div>
              </div>
              <div className="PulseSummaryTile">
                <div className="PulseSummaryCount">{mostCollaborativeProduct?.productName || mostCollaborativeProduct?.productKey || '—'}</div>
                <div className="PulseSummaryLabel">Most Collaborative Product</div>
                <div className="PulseSummaryDetail">
                  {mostCollaborativeProduct
                    ? `${Number(mostCollaborativeProduct.activeUsers || 0).toLocaleString()} active users • ${Number(mostCollaborativeProduct.events || 0).toLocaleString()} events`
                    : 'No ranked product available.'}
                </div>
              </div>
            </div>
            <div className="PulseTableWrap">
              <table className="PulseTable">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Type</th>
                    <th>Owner</th>
                    <th>Instance</th>
                    <th>Project Key</th>
                    <th>Events</th>
                    <th>Active Users</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedTopProducts.map((r) => (
                    <tr key={r.productId}>
                      <td>
                        <button
                          type="button"
                          className="PulseLinkButton"
                          onClick={() => openDetails(r.productId)}
                        >
                          {r.productName || r.productKey || '-'}
                        </button>
                      </td>
                      <td><Badge>{r.productType}</Badge></td>
                      <td><Badge>{r.ownerLogin || '-'}</Badge></td>
                      <td><Badge>{r.instanceName}</Badge></td>
                      <td><Badge>{r.projectKey}</Badge></td>
                      <td>{Number(r.events || 0).toLocaleString()}</td>
                      <td>{Number(r.activeUsers || 0).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="PulseMuted PulseRiskFooter">
              {(topProducts || []).length
                ? `Showing ${((topProductsPage - 1) * topProductsPageSize) + 1}–${Math.min(topProductsPage * topProductsPageSize, topProducts.length)} of ${topProducts.length}`
                : 'Showing 0 of 0'}
            </div>
            <div className="PulseRiskPager">
              <button className="PulseButton" type="button" disabled={topProductsPage <= 1} onClick={() => setTopProductsPage((page) => Math.max(1, page - 1))}>Previous</button>
              <button className="PulseButton" type="button" disabled={topProductsPage >= topProductsTotalPages} onClick={() => setTopProductsPage((page) => Math.min(topProductsTotalPages, page + 1))}>Next</button>
            </div>
          </div>

      {selectedCapability ? (
        <Modal title={`${selectedCapability} capability`} onClose={() => { setSelectedCapability(null); setCapabilityDetails(null); }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <Badge>{selectedCapability}</Badge>
            <Badge>{windowDays}d window</Badge>
          </div>

          {capabilityLoading ? <div className="PulseMuted">Loading…</div> : null}
          {capabilityError ? <div className="PulseError">{capabilityError}</div> : null}

          {capabilityDetails ? (
            <>
              <PulseSection title="Summary">
                <div className="PulseDetailGrid">
                  <div><div className="PulseMuted">Events</div><div style={{ fontWeight: 800, fontSize: 20 }}>{capabilityDetails.summary?.events || 0}</div></div>
                  <div><div className="PulseMuted">Users</div><div style={{ fontWeight: 800, fontSize: 20 }}>{capabilityDetails.summary?.users || 0}</div></div>
                  <div><div className="PulseMuted">Projects</div><div style={{ fontWeight: 800, fontSize: 20 }}>{capabilityDetails.summary?.projects || 0}</div></div>
                  <div><div className="PulseMuted">Instances</div><div style={{ fontWeight: 800, fontSize: 20 }}>{capabilityDetails.summary?.instances || 0}</div></div>
                </div>
              </PulseSection>

              <PulseSection title="Activity Over Time">
                <div className="PulseVizGrid">
                  <LineChart title="Events per day" points={capabilityDetails.activityDaily || []} />
                </div>
              </PulseSection>

              <PulseSection title="Top Users and Projects">
                <div className="PulseVizGrid">
                  <BarList title="Top users" rows={capabilityDetails.topUsers || []} maxRows={12} />
                  <BarList title="Top projects" rows={capabilityDetails.topProjects || []} maxRows={12} />
                </div>
              </PulseSection>
            </>
          ) : null}
        </Modal>
      ) : null}

      {detailsOpen ? (
        <Modal title="Product Details" onClose={closeDetails}>
          {detailsLoading ? <div className="PulseMuted">Loading…</div> : null}
          {detailsError ? <div className="PulseError">{detailsError}</div> : null}

          {!detailsLoading && !detailsError && details ? (
            <>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                {details.product?.productName ? <Badge>{details.product.productName}</Badge> : null}
                {details.product?.productType ? <Badge>{details.product.productType}</Badge> : null}
                {details.product?.instanceName ? <Badge>{details.product.instanceName}</Badge> : null}
                {details.product?.projectKey ? <Badge>{details.product.projectKey}</Badge> : null}
                {details.product?.ownerLogin ? <Badge>{details.product.ownerLogin}</Badge> : null}
              </div>

              <PulseSection title="Summary">
                <div className="PulseDetailGrid">
                  <div>
                    <div className="PulseMuted">Events</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(details.totals?.events || 0).toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Active users</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(details.totals?.activeUsers || 0).toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Last activity</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{details.totals?.lastActivityAt || '-'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Repeat-use status</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{details.totals?.repeatUseStatus ? 'Repeat use observed' : 'Not yet repeat use'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Adoption tier</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{details.adoptionTier || '-'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Maturity score</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(details.maturity?.score || 0).toFixed(1)}</div>
                  </div>
                </div>
              </PulseSection>

              <PulseSection title="Metadata">
                <div className="PulseDetailGrid">
                  <div>
                    <div className="PulseMuted">Owner</div>
                    <div style={{ fontWeight: 800, fontSize: 16 }}>{details.product?.ownerLogin || '-'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Subtype</div>
                    <div style={{ fontWeight: 800, fontSize: 16 }}>{details.product?.productSubtype || '-'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Created at</div>
                    <div style={{ fontWeight: 800, fontSize: 16 }}>{details.product?.createdAt || '-'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Updated at</div>
                    <div style={{ fontWeight: 800, fontSize: 16 }}>{details.product?.updatedAt || '-'}</div>
                  </div>
                </div>
              </PulseSection>

              <PulseSection title="Activity Over Time">
                <div className="PulseVizGrid">
                  <LineChart title="Events per day" points={details.activityDaily || []} />
                </div>
              </PulseSection>

              <PulseSection title="Maturity Breakdown">
                <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
                  <div className="PulseSummaryTile">
                    <div className="PulseSummaryCount">{String(details.maturity?.tier || 'emerging')}</div>
                    <div className="PulseSummaryLabel">Maturity tier</div>
                  </div>
                  <div className="PulseSummaryTile">
                    <div className="PulseSummaryCount">{details.totals?.collaborativeStatus ? 'Collaborative' : 'Single-user'}</div>
                    <div className="PulseSummaryLabel">Collaboration status</div>
                  </div>
                </div>
                <div className="PulseVizGrid">
                  <BarList
                    title="Maturity Components"
                    rows={[
                      { label: 'Breadth', value: Number(details.maturity?.components?.breadthScore || 0) },
                      { label: 'Repeat use', value: Number(details.maturity?.components?.repeatScore || 0) },
                      { label: 'Collaboration', value: Number(details.maturity?.components?.collaborationScore || 0) },
                      { label: 'Concentration health', value: Number(details.maturity?.components?.concentrationScore || 0) },
                    ]}
                    maxRows={10}
                  />
                </div>
              </PulseSection>

              <PulseSection title="Top Users">
                <div className="PulseVizGrid">
                  <BarList title="Events by User" rows={details.topUsers || []} maxRows={12} />
                </div>
              </PulseSection>
            </>
          ) : null}
        </Modal>
      ) : null}
        </div>
      </FilterPageLayout>
    </div>
  );
}

function inferCodeStudioPortBase(pathname) {
  // Two common Code Studio URL shapes:
  // 1) Direct port mapping:
  //    /code-studios/<PROJECT_KEY>/<STUDIO_ID>/<port>/...
  // 2) Proxy mapping:
  //    /code-studios/<PROJECT_KEY>/<STUDIO_ID>/8080/proxy/<port>/...

  const proxyMatch = pathname.match(/^(\/code-studios\/[^/]+\/[^/]+\/8080\/proxy)\/(\d+)(?:\/|$)/);
  if (proxyMatch) {
    const prefix = proxyMatch[1];
    const port = proxyMatch[2];
    return {
      mode: 'proxy',
      prefix,
      port,
      base: `${prefix}/${port}`,
    };
  }

  const directMatch = pathname.match(/^(\/code-studios\/[^/]+\/[^/]+)\/(\d+)(?:\/|$)/);
  if (directMatch) {
    const prefix = directMatch[1];
    const port = directMatch[2];
    return {
      mode: 'direct',
      prefix,
      port,
      base: `${prefix}/${port}`,
    };
  }

  return null;
}





function FaqPage() {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>FAQ</h1>
        <p>Frequently asked questions regarding Pulse data sources, analytical conventions, operational behavior, and support procedures.</p>
      </div>

      <PulseSection title="Does Pulse present a complete view of all activity?">
        <p>
          No. Pulse is not intended to function as an exhaustive reporting environment. It is designed to surface selected
          high-value signals that support interpretation, prioritization, and discussion. As a result, not all available
          platform activity, events, or operational detail will be displayed.
        </p>
      </PulseSection>

      <PulseSection title="What are the underlying data sources?">
        <p>
          Pulse relies on both upstream data preparation processes and downstream analytical presentation.
        </p>
        <ul>
          <li>
            Upstream collection pipelines gather metadata and audit-log information across DSS instances and publish curated
            GOLD parquet datasets.
          </li>
          <li>
            The web application then loads those curated datasets into a local DuckDB environment for analytical query and
            presentation within the user interface.
          </li>
        </ul>
      </PulseSection>

      <PulseSection title="What should I do if charts appear incomplete, empty, or out of date?">
        <p>
          Use <code>Debug → Reload DuckDB</code> to refresh the analytics layer from the source parquet datasets. If the
          issue persists, it should be raised through the appropriate support channel.
        </p>
      </PulseSection>

      <PulseSection title="How are capabilities defined within Pulse?">
        <p>
          Capability-oriented views are derived from audit-log activity. Individual audit events are first mapped to
          lower-level categories and are then aggregated into broader functional capability groupings, such as Data
          Engineering or GenAI, for interpretive analysis.
        </p>
      </PulseSection>

      <PulseSection title="How should debug and development behavior be understood?">
        <p>
          In debug or development contexts, Pulse may recompute certain outputs more frequently and may expose additional
          operational tooling or diagnostic behavior. Production use prioritizes stability, consistency, and interpretive
          clarity over engineering-level visibility.
        </p>
      </PulseSection>

      <PulseSection title="How should support requests be submitted?">
        <p>
          Pulse-related questions, issues, and interpretation requests should generally be routed through the relevant
          Dataiku account team or Technical Account Manager. When requesting assistance, include the applicable page,
          metric, timeframe, and relevant business context to support efficient triage.
        </p>
      </PulseSection>
    </div>
  );
}




function DisclaimerPage() {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Disclaimer</h1>
        <p>Important notice regarding Pulse support boundaries, release status, analytical interpretation, and product scope.</p>
      </div>

      <PulseSection title="Platinum Support Only">
        <p>
          Pulse is provided for Platinum-support contexts only and does not constitute a fully supported standard Dataiku
          product offering. Any issue, enhancement request, or operational concern relating to Pulse should be directed
          through the applicable Technical Account Manager or designated support contact.
        </p>
      </PulseSection>

      <PulseSection title="Forever Beta">
        <p>
          Pulse is a Platinum Support Level tool and service that remains in an ongoing beta state. It is not
          designated as an official general-availability Dataiku release and should therefore be understood as an
          evolving offering subject to change without notice.
        </p>
      </PulseSection>

      <PulseSection title="Interpretation of Metrics">
        <p>
          All metrics, values, definitions, and classification rules presented in Pulse are subject to revision. Such
          revisions may occur as internal methodologies, product definitions, governance standards, and interpretive
          frameworks evolve over time.
        </p>
        <p>
          Accordingly, values displayed in Pulse should be treated as informational and directional in nature, and not
          as immutable records or definitive contractual representations.
        </p>
      </PulseSection>

      <PulseSection title="Purpose and Scope">
        <p>
          Pulse is intended as an insight-gathering instrument designed on a best-efforts basis to identify and surface
          themes, signals, and priorities that are broadly relevant across the customer community. It is not intended to
          function as a bespoke analytical solution for any single account.
        </p>
        <p>
          Pulse also serves as a mechanism for communicating recurring customer needs and usage patterns to the Dataiku
          Product organization. As equivalent capabilities become natively available within Dataiku products, certain
          views, metrics, or features may be modified, deprecated, or removed from Pulse.
        </p>
      </PulseSection>
    </div>
  );
}



function getUserDisplayName(authState) {
  const user = authState && authState.data && authState.data.user ? authState.data.user : null;
  if (!user) return 'Guest';
  return user.displayName || user.login || user.email || 'Guest';
}

function getNavGroupIcon(label) {
  switch (label) {
    case 'Pulse':
      return '🏠';
    case 'User Insights':
      return '👥';
    case 'Product Lifecycle':
      return '🧩';
    case 'LLM Mesh':
      return '✨';
    case 'Debug':
      return '🛠️';
    default:
      return '•';
  }
}

function getNavItemIcon(label) {
  switch (label) {
    case 'Home':
      return '🏠';
    case 'FAQ':
      return '❓';
    case 'Disclaimer':
      return '📌';
    case 'Export':
      return '📤';
    case 'Activity Performance':
      return '📈';
    case 'License Performance':
      return '🪪';
    case 'Assets':
      return '🗂️';
    case 'Products':
      return '📦';
    case 'Development Activity':
      return '🛠️';
    case 'Consumption Activity':
      return '📊';
    case 'Overview':
      return '✨';
    case 'Reload DuckDB':
      return '🔄';
    case 'Preview DuckDB':
      return '🔍';
    default:
      return '•';
  }
}

function LeftNavRail({ groups, globalGroups, collapsed, onToggle, activeGroup, onGroupToggle }) {
  if (!groups.length && !globalGroups.length) {
    return (
      <aside className={`PulseRail ${collapsed ? 'PulseRailCollapsed' : ''}`}>
        <div className="PulseRailBody" />
        <div className="PulseRailFooter">
          <button
            type="button"
            className="PulseRailToggle"
            onClick={onToggle}
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          >
            <span className="PulseRailToggleIcon">{collapsed ? '»' : '«'}</span>
            {!collapsed ? <span>Collapse</span> : null}
          </button>
        </div>
      </aside>
    );
  }

  const hasGroups = groups.length > 0;
  const hasGlobalGroups = globalGroups.length > 0;
  const onGroupButtonClick = (groupLabel) => {
    if (collapsed) {
      onToggle();
      onGroupToggle(groupLabel, true);
      return;
    }
    onGroupToggle(groupLabel, false);
  };

  return (
    <aside className={`PulseRail ${collapsed ? 'PulseRailCollapsed' : ''}`}>
      <div className="PulseRailBody">
        {hasGroups ? groups.map((group) => {
          const isExpanded = activeGroup === group.label;
          return (
            <div key={group.label} className="PulseRailSection">
              <button
                type="button"
                className={`PulseRailSectionButton ${isExpanded ? 'PulseRailSectionButtonActive' : ''}`}
                onClick={() => onGroupButtonClick(group.label)}
                aria-expanded={isExpanded}
                aria-label={group.label}
                title={group.label}
              >
                <span className="PulseRailSectionButtonLeft">
                  {collapsed ? <span className="PulseRailItemIcon" aria-hidden="true">{getNavGroupIcon(group.label)}</span> : null}
                  {!collapsed ? <span className="PulseRailSectionLabel">{group.label}</span> : null}
                </span>
                {!collapsed ? <span className="PulseRailSectionChevron">{isExpanded ? '−' : '+'}</span> : null}
              </button>

              {isExpanded && !collapsed ? (
                <div className="PulseRailSectionContent">
                  <div className="PulseRailItems">
                    {group.items.map((item) => (
                      item.onClick ? (
                        <button
                          key={item.key || item.label}
                          type="button"
                          className={`PulseRailItem PulseRailItemButton ${item.isActive ? 'PulseRailItemActive' : ''}`}
                          onClick={item.onClick}
                          title={item.description || item.label}
                        >
                          {collapsed ? <span className="PulseRailItemIcon" aria-hidden="true">{getNavItemIcon(item.label) || getNavGroupIcon(group.label)}</span> : null}
                          <span className="PulseRailItemLabel">{item.label}</span>
                        </button>
                      ) : (
                        <a
                          key={item.href}
                          href={item.href}
                          className="PulseRailItem"
                          title={item.description || item.label}
                          aria-label={item.label}
                        >
                          {collapsed ? <span className="PulseRailItemIcon" aria-hidden="true">{getNavItemIcon(item.label) || getNavGroupIcon(group.label)}</span> : null}
                          <span className="PulseRailItemLabel">{item.label}</span>
                        </a>
                      )
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        }) : null}
      </div>
      <div className="PulseRailFooter">
        {hasGlobalGroups ? (
          <div className="PulseRailGlobalSection">
            {globalGroups.map((group) => {
              const isExpanded = activeGroup === group.label;
              return (
                <div key={group.label} className="PulseRailSection">
                  <button
                    type="button"
                    className={`PulseRailSectionButton ${isExpanded ? 'PulseRailSectionButtonActive' : ''}`}
                    onClick={() => onGroupButtonClick(group.label)}
                    aria-expanded={isExpanded}
                    aria-label={group.label}
                    title={group.label}
                  >
                    <span className="PulseRailSectionButtonLeft">
                      {collapsed ? <span className="PulseRailItemIcon" aria-hidden="true">{getNavGroupIcon(group.label)}</span> : null}
                      {!collapsed ? <span className="PulseRailSectionLabel">{group.label}</span> : null}
                    </span>
                    {!collapsed ? <span className="PulseRailSectionChevron">{isExpanded ? '−' : '+'}</span> : null}
                  </button>

                  {isExpanded && !collapsed ? (
                    <div className="PulseRailSectionContent">
                      <div className="PulseRailItems">
                        {group.items.map((item) => (
                          <a
                            key={item.href}
                            href={item.href}
                            className={`PulseRailItem ${item.isActive ? 'PulseRailItemActive' : ''}`}
                            title={item.description || item.label}
                            aria-label={item.label}
                          >
                            {collapsed ? <span className="PulseRailItemIcon" aria-hidden="true">{getNavItemIcon(item.label) || getNavGroupIcon(group.label)}</span> : null}
                            <span className="PulseRailItemLabel">{item.label}</span>
                          </a>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}
        <button
          type="button"
          className="PulseRailToggle"
          onClick={onToggle}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          <span className="PulseRailToggleIcon">{collapsed ? '»' : '«'}</span>
          {!collapsed ? <span>Collapse</span> : null}
        </button>
      </div>
    </aside>
  );
}

function DropdownNav({ groups, globalGroups, homeHref, workspaceOptions, workspace, onWorkspaceChange, userLabel, railCollapsed, onRailToggle, activeGroup, onGroupToggle, children }) {
  return (
    <div className="PulseShell">
      <header className="PulseHeader">
        <div className="PulseHeaderBrand">
          <a href={homeHref} className="PulseHeaderBrandLink" aria-label="Dataiku Pulse Dashboard home">
            <span className="PulseHeaderBrandIcon" aria-hidden="true">❤️</span>
            <span className="PulseHeaderBrandText">Dataiku Pulse Dashboard</span>
          </a>
        </div>

        <div className="PulseHeaderMeta">
          {workspaceOptions && workspaceOptions.length ? (
            <label className="PulseHeaderWorkspace">
              <span className="PulseHeaderMetaLabel">Workspace</span>
              <select
                className="PulseSelect"
                value={workspace}
                onChange={(e) => onWorkspaceChange(e.target.value)}
              >
                {workspaceOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="PulseHeaderUser" title={userLabel}>
            <span className="PulseHeaderMetaLabel">User</span>
            <span className="PulseHeaderUserValue">{userLabel}</span>
          </div>
        </div>
      </header>

      <div className="PulseShellBody">
        <LeftNavRail
          groups={groups}
          globalGroups={globalGroups}
          collapsed={railCollapsed}
          onToggle={onRailToggle}
          activeGroup={activeGroup}
          onGroupToggle={onGroupToggle}
        />
        <main className="PulseShellContent">{children}</main>
      </div>
    </div>
  );
}

function MyInformationPage({ pageKey, userLabel, authState, apiBase, advancedLlmMeshEnabled }) {
  const pages = {
    'my-overview': {
      title: 'Overview',
      description: 'Your personal Dataiku Pulse dashboard.',
    },
    'my-assets': {
      title: 'Assets',
      description: 'Review the Dataiku assets you have created, owned, or contributed to.',
      placeholder: 'Personal asset insights will be added in a later step.',
    },
    'my-products': {
      title: 'Products',
      description: 'Review the products and outputs you have created or helped develop.',
      placeholder: 'Personal product insights will be added in a later step.',
    },
    'my-consumption': {
      title: 'Consumption',
      description: 'Review how your products and outputs are being consumed by others.',
      placeholder: 'Personal consumption insights will be added in a later step.',
    },
    'my-llm-overview': {
      title: 'Overview',
      description: 'Review your personal LLM Mesh usage, models, projects, tokens, and cost.',
      placeholder: 'Coming soon',
    },
  };

  const resolvedPageKey = pageKey === 'my-llm-overview' && !advancedLlmMeshEnabled ? 'my-overview' : pageKey;
  const activePage = pages[resolvedPageKey] || pages['my-overview'];

  if (resolvedPageKey === 'my-overview') {
    const selfLogin = authState?.data?.user?.login || '';

    return (
      <div className="PulseWide">
        <UserDashboard
          apiBase={apiBase}
          login={selfLogin}
          mode="self"
          title={`${userLabel} Dashboard`}
          subtitle="Your personal Dataiku Pulse activity overview."
          showContextBadges={false}
        />
      </div>
    );
  }

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>My Information</h1>
        <p>View your personal Dataiku activity, products, and LLM usage.</p>
      </div>

      <PulseSection title={activePage.title}>
        <p>{activePage.description}</p>
        <p>{activePage.placeholder}</p>
      </PulseSection>
    </div>
  );
}



function AdministrationPlaceholderPage({ apiBase }) {
  const [topologyState, setTopologyState] = useState({ loading: true, data: null, error: '' });

  const sanitizeResponsePreview = (text) => String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);

  const loadTopology = useCallback(async () => {
    setTopologyState({ loading: true, data: null, error: '' });
    try {
      const requestUrl = apiUrl(apiBase, '/api/admin/pulse-topology');
      const response = await fetch(requestUrl, { cache: 'no-store' });
      const contentType = String(response.headers.get('content-type') || '').toLowerCase();
      const rawText = await response.text();

      if (!response.ok || !contentType.includes('application/json')) {
        throw new Error(
          `Topology request failed (${response.status}) url=${requestUrl} contentType=${contentType || 'unknown'} preview=${sanitizeResponsePreview(rawText)}`
        );
      }

      const data = rawText ? JSON.parse(rawText) : null;
      if (!data || data.ok !== true) {
        const message = data?.error?.message || data?.error || 'Failed to load Pulse topology';
        throw new Error(
          `Topology request failed (${response.status}) url=${requestUrl} contentType=${contentType || 'unknown'} message=${message}`
        );
      }
      setTopologyState({ loading: false, data, error: '' });
    } catch (error) {
      setTopologyState({ loading: false, data: null, error: error.message || 'Failed to load Pulse topology' });
    }
  }, [apiBase]);

  useEffect(() => {
    loadTopology();
  }, [loadTopology]);

  const topology = topologyState.data?.topology || { hub: { url: '', label: 'Pulse Hub' }, spokes: [] };
  const hubUrl = topology.hub?.url || '';
  const spokes = Array.isArray(topology.spokes) ? topology.spokes : [];
  const summary = topologyState.data?.summary || { hubCount: hubUrl ? 1 : 0, workerCount: spokes.length, classifications: {} };
  const radius = 180;
  const centerX = 320;
  const centerY = 240;
  const diagramSpokes = spokes.length ? spokes : [];

  const spokePositions = diagramSpokes.map((spoke, index) => {
    const angle = (-Math.PI / 2) + ((Math.PI * 2) / Math.max(diagramSpokes.length, 1)) * index;
    return {
      ...spoke,
      x: centerX + (radius * Math.cos(angle)),
      y: centerY + (radius * Math.sin(angle)),
    };
  });

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Administration Overview</h1>
        <p>View the Pulse dashboard hub and the Dataiku worker instances connected for metadata collection.</p>
      </div>

      <PulseSection title="Pulse Topology">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <span>Hub count: {summary.hubCount || 0}</span>
            <span>Connected workers: {summary.workerCount || 0}</span>
            {Object.entries(summary.classifications || {}).map(([label, count]) => (
              <span key={label}>{label}: {count}</span>
            ))}
          </div>
          <button onClick={loadTopology} disabled={topologyState.loading}>{topologyState.loading ? 'Loading...' : 'Refresh'}</button>
        </div>

        {topologyState.error ? <p className="PulseAlert">{topologyState.error}</p> : null}
        {!topologyState.loading && !topologyState.error && !hubUrl ? (
          <p>Pulse topology configuration is incomplete.</p>
        ) : null}
        {!topologyState.loading && !topologyState.error && hubUrl && !spokes.length ? (
          <p>Pulse Hub is configured, but no worker instances are currently listed.</p>
        ) : null}
        {topologyState.loading ? <p>Loading topology…</p> : null}

        {!topologyState.loading && !topologyState.error && hubUrl ? (
          <div style={{ overflowX: 'auto' }}>
            <svg viewBox="0 0 640 480" style={{ width: '100%', minWidth: 640, height: 'auto' }} role="img" aria-label="Pulse hub and spoke topology">
              {spokePositions.map((spoke, index) => (
                <line key={`line-${index}`} x1={centerX} y1={centerY} x2={spoke.x} y2={spoke.y} stroke="#cbd5e1" strokeWidth="2" />
              ))}
              <g>
                <circle cx={centerX} cy={centerY} r="64" fill="#dbeafe" stroke="#2563eb" strokeWidth="3" />
                <text x={centerX} y={centerY - 10} textAnchor="middle" style={{ fontSize: 16, fontWeight: 700, fill: '#1e3a8a' }}>Pulse Hub</text>
                <text x={centerX} y={centerY + 14} textAnchor="middle" style={{ fontSize: 12, fill: '#334155' }}>
                  {hubUrl.length > 42 ? `${hubUrl.slice(0, 39)}...` : hubUrl}
                </text>
                <title>{hubUrl}</title>
              </g>
              {spokePositions.map((spoke, index) => (
                <g key={`spoke-${index}`}>
                  <circle cx={spoke.x} cy={spoke.y} r="52" fill="#f8fafc" stroke="#64748b" strokeWidth="2" />
                  <text x={spoke.x} y={spoke.y - 12} textAnchor="middle" style={{ fontSize: 12, fontWeight: 700, fill: '#0f172a' }}>
                    {spoke.classification || 'Worker'}
                  </text>
                  <text x={spoke.x} y={spoke.y + 6} textAnchor="middle" style={{ fontSize: 11, fill: '#334155' }}>
                    {(spoke.url || '').length > 28 ? `${spoke.url.slice(0, 25)}...` : (spoke.url || '—')}
                  </text>
                  {spoke.presetName ? (
                    <text x={spoke.x} y={spoke.y + 22} textAnchor="middle" style={{ fontSize: 10, fill: '#64748b' }}>
                      {spoke.presetName.length > 20 ? `${spoke.presetName.slice(0, 17)}...` : spoke.presetName}
                    </text>
                  ) : null}
                  <title>{[spoke.url, spoke.classification, spoke.presetName].filter(Boolean).join(' · ')}</title>
                </g>
              ))}
            </svg>
          </div>
        ) : null}
      </PulseSection>
    </div>
  );
}


function LicensePerformanceSection({
  selectedInstance,
  licenseStatusSummaryAll,
  licenseStatusSummaryInstance,
  userKpisAll,
  userKpisInstance,
  userProfilesAll,
  userProfilesInstance,
  userLicenseGroupProfilesAll,
  userLicenseGroupProfilesInstance,
}) {
  const formatLicenseUsage = (enabledUsers, maxLicenses) => {
    const used = Number(enabledUsers ?? 0);
    const max = Number(maxLicenses);
    if (!Number.isFinite(max) || max <= 0) {
      return used.toLocaleString();
    }
    return `${used.toLocaleString()} / ${max.toLocaleString()}`;
  };

  const selectedLicenseStatusSummary = selectedInstance ? licenseStatusSummaryInstance : null;
  const hasSelectedLicenseStatus = Boolean(
    selectedLicenseStatusSummary &&
    (Object.keys(selectedLicenseStatusSummary.fields || {}).length || (selectedLicenseStatusSummary.addonServices || []).length)
  );
  const activeLicenseStatusSummary = hasSelectedLicenseStatus
    ? selectedLicenseStatusSummary
    : licenseStatusSummaryAll;
  const licenseStatusFields = activeLicenseStatusSummary?.fields || {};
  const licenseStatusMode = activeLicenseStatusSummary?.mode || 'most_common';
  const licenseStatusScopeLabel = selectedInstance && hasSelectedLicenseStatus ? selectedInstance : 'all included instances';
  const addonServices = activeLicenseStatusSummary?.addonServices || [];

  const getLicenseFieldValue = (fieldName) => licenseStatusFields?.[fieldName]?.value;
  const formatFieldCountSuffix = (fieldName) => {
    if (selectedInstance) return '';
    const count = Number(licenseStatusFields?.[fieldName]?.count || 0);
    if (!count) return '';
    const total = Number(activeLicenseStatusSummary?.instanceCount || 0);
    return total > 1 ? ` • ${count}/${total} instances` : '';
  };

  const licenseInfoRows = [
    {
      label: 'License type',
      value: getLicenseFieldValue('license_kind') || 'Unavailable',
      detail: licenseStatusMode === 'most_common' ? `Most common observed license type across ${licenseStatusScopeLabel}.` : `Current license type for ${licenseStatusScopeLabel}.`,
      suffix: formatFieldCountSuffix('license_kind'),
    },
    {
      label: 'Status',
      value: getLicenseFieldValue('valid') === true || String(getLicenseFieldValue('valid')).toLowerCase() === 'true'
        ? 'Valid'
        : (getLicenseFieldValue('expired') === true || String(getLicenseFieldValue('expired')).toLowerCase() === 'true'
            ? 'Expired'
            : (getLicenseFieldValue('has_license') === true || String(getLicenseFieldValue('has_license')).toLowerCase() === 'true' ? 'Present' : 'Unknown')),
      detail: licenseStatusMode === 'most_common' ? `Most common observed validity status across ${licenseStatusScopeLabel}.` : `Current validity status for ${licenseStatusScopeLabel}.`,
      suffix: formatFieldCountSuffix('valid'),
    },
    {
      label: 'Expires on',
      value: getLicenseFieldValue('expires_on') || 'Unavailable',
      detail: getLicenseFieldValue('days_left') != null
        ? `${Number(getLicenseFieldValue('days_left')).toLocaleString()} days remaining.`
        : (licenseStatusMode === 'most_common' ? 'Most common observed expiration date.' : 'Current license expiration date.'),
      suffix: formatFieldCountSuffix('expires_on'),
    },
  ];

  const featureRows = (activeLicenseStatusSummary?.features || [])
    .filter((feature) => !['standard_offer', 'fallback_profile'].includes(String(feature?.key || '')))
    .slice(0, 12);

  const entitlementVolumeTiles = [
    {
      label: 'Total enabled users',
      value: Number(userKpisAll?.enabled_users ?? 0).toLocaleString(),
      detail: 'Current enabled-user count across all included instances.',
    },
    {
      label: 'Total license profile types found',
      value: Number(userProfilesAll?.length ?? 0).toLocaleString(),
      detail: 'Distinct license profile types identified in the current entitlement snapshot.',
    },
    {
      label: 'Total license profile groups found',
      value: Number(userLicenseGroupProfilesAll?.length ?? 0).toLocaleString(),
      detail: 'Entitlement categories derived from the current license mapping configuration.',
    },
  ];

  const selectedInstanceTiles = [
    {
      label: 'Total enabled users',
      value: selectedInstance ? Number(userKpisInstance?.enabled_users ?? 0).toLocaleString() : 'Select an instance',
      detail: selectedInstance ? `Current enabled-user count for ${selectedInstance}.` : 'Choose an instance above to compare local entitlement coverage.',
    },
    {
      label: 'Total license profile types found',
      value: selectedInstance ? Number(userProfilesInstance?.length ?? 0).toLocaleString() : 'Select an instance',
      detail: selectedInstance ? `Distinct license profile types identified for ${selectedInstance}.` : 'Available after selecting an instance above.',
    },
    {
      label: 'Total license profile groups found',
      value: selectedInstance ? Number(userLicenseGroupProfilesInstance?.length ?? 0).toLocaleString() : 'Select an instance',
      detail: selectedInstance ? `Entitlement categories returned for ${selectedInstance}.` : 'Available after selecting an instance above.',
    },
  ];

  const renderLicenseProfileGroups = (groups, emptyLabel) => {
    if (!groups?.length) {
      return <div className="PulseMuted">{emptyLabel}</div>;
    }

    return (
      <div className="PulseVizGrid">
        {groups.map((group) => (
          <BarList
            key={group.license_group || 'Other Licenses'}
            title={`${group.license_group || 'Other Licenses'} — ${group.definition || 'Entitlement category'}`}
            rows={(group.profiles || []).map((profile) => ({
              label: `${profile.profile || 'UNKNOWN'}${Number(profile.max_licenses || 0) > 0 ? ` (${formatLicenseUsage(profile.enabled_users, profile.max_licenses)})` : ''}`,
              value: Number(profile.enabled_users ?? 0),
            }))}
            maxRows={12}
          />
        ))}
      </div>
    );
  };

  return (
    <>
      <div className="PulseCard">
        <h2>License Info</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          {selectedInstance && hasSelectedLicenseStatus
            ? `Current license file details for ${selectedInstance}.`
            : (selectedInstance
                ? `No instance-specific license file snapshot was found for ${selectedInstance}, so this shows the most common current license file values across all included instances.`
                : 'Summary of the most common current license file values across all included instances. If nodes differ, this shows the most common observed value.')}
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          {licenseInfoRows.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              <div className="PulseSummaryDetail">{`${tile.detail}${tile.suffix || ''}`}</div>
            </div>
          ))}
        </div>
        {featureRows.length ? (
          <div className="PulseVizGrid">
            <BarList
              title={selectedInstance ? 'Enabled license features' : 'Most common enabled license features'}
              rows={featureRows.map((feature) => ({
                label: `${feature.label}${!selectedInstance && Number(feature.count || 0) > 0 ? ` (${Number(feature.count).toLocaleString()} instances)` : ''}`,
                value: Number(feature.count || 0),
              }))}
              maxRows={12}
            />
          </div>
        ) : <div className="PulseMuted">No enabled feature flags were found in the latest license status snapshot.</div>}
        {addonServices.length ? (
          <div style={{ marginTop: 16 }}>
            <h3 style={{ marginBottom: 8 }}>Addon Services</h3>
            <div className="PulseMuted" style={{ marginBottom: 8 }}>
              {selectedInstance ? 'Enabled addon services found for the selected instance.' : 'Enabled addon services found across the current license snapshots.'}
            </div>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {addonServices.map((addon) => (
                <li key={addon.key}>{addon.label}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className="PulseCard">
        <h2>Entitlement Summary</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Summary view of enabled-user entitlement and coverage based on the latest available directory and profile records across all instances.
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          {entitlementVolumeTiles.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              {tile.detail ? <div className="PulseSummaryDetail">{tile.detail}</div> : null}
            </div>
          ))}
        </div>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          License groups below reflect configured entitlement mappings rather than observed activity.
        </div>
        {renderLicenseProfileGroups(userLicenseGroupProfilesAll, 'No entitlement categories are available for the current all-instance view.')}
        <div style={{ marginTop: 22 }}>
          <h3 style={{ marginBottom: 8 }}>Select an Instance</h3>
          <div className="PulseMuted" style={{ marginBottom: 10 }}>
            Review the same entitlement metrics and profile breakdown for one DSS instance.
          </div>
          <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
            {selectedInstanceTiles.map((tile) => (
              <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
                <div className="PulseSummaryCount">{tile.value}</div>
                <div className="PulseSummaryLabel">{tile.label}</div>
                {tile.detail ? <div className="PulseSummaryDetail">{tile.detail}</div> : null}
              </div>
            ))}
          </div>
          {selectedInstance
            ? renderLicenseProfileGroups(
                userLicenseGroupProfilesInstance,
                `No entitlement groups found for ${selectedInstance}.`
              )
            : <div className="PulseMuted">Choose an instance above to see its entitlement breakdown.</div>}
        </div>
      </div>

    </>
  );
}

function compareNullableStrings(a, b, direction = 'asc') {
  const left = String(a || '').toLowerCase();
  const right = String(b || '').toLowerCase();
  if (left === right) return 0;
  const base = left < right ? -1 : 1;
  return direction === 'desc' ? -base : base;
}

function compareNullableDates(a, b, direction = 'asc') {
  const left = a ? Date.parse(a) : NaN;
  const right = b ? Date.parse(b) : NaN;
  const leftValid = Number.isFinite(left);
  const rightValid = Number.isFinite(right);
  if (!leftValid && !rightValid) return 0;
  if (!leftValid) return 1;
  if (!rightValid) return -1;
  if (left === right) return 0;
  const base = left < right ? -1 : 1;
  return direction === 'desc' ? -base : base;
}

function SortableHeader({ label, column, sortState, onToggle }) {
  const active = sortState.column === column;
  const direction = active ? sortState.direction : null;
  return (
    <button
      type="button"
      className={`PulseTableSortButton${active ? ' PulseTableSortButtonActive' : ''}`}
      onClick={() => onToggle(column)}
    >
      <span>{label}</span>
      <span className={`PulseTableSortIndicator${active ? ' PulseTableSortIndicatorActive' : ''}`}>
        {direction === 'asc' ? '▲' : direction === 'desc' ? '▼' : '↕'}
      </span>
    </button>
  );
}

function BarList({ title, rows, maxRows = 12, onRowClick, formatValue }) {
  const trimmed = useMemo(() => {
    if (!rows.length) return [];
    const sorted = [...rows].sort((a, b) => b.value - a.value);
    if (sorted.length <= maxRows) return sorted;

    const head = sorted.slice(0, maxRows);
    const tail = sorted.slice(maxRows);
    const otherValue = tail.reduce((acc, r) => acc + r.value, 0);
    return [...head, { label: 'Other', value: otherValue }];
  }, [maxRows, rows]);

  const maxVal = useMemo(() => {
    return Math.max(1, ...trimmed.map((r) => r.value));
  }, [trimmed]);

  return (
    <div className="PulseChartCard">
      <div className="PulseChartTitle">{title}</div>
      <div className="PulseBarList">
        {trimmed.map((r) => {
          const Row = onRowClick ? 'button' : 'div';
          const percent = maxVal ? (Number(r.value || 0) / maxVal) * 100 : 0;
          return (
            <Row
              key={r.label}
              type={onRowClick ? 'button' : undefined}
              className={onRowClick ? 'PulseBarRow PulseBarRowAction' : 'PulseBarRow'}
              onClick={onRowClick ? () => onRowClick(r.label) : undefined}
              title={onRowClick ? `View details for ${r.label}` : r.label}
            >
              <div className="PulseBarLabel" title={r.label}>{r.label}</div>
              <div className="PulseBarTrack">
                <div className="PulseBarFill" style={{ width: `${percent}%` }} />
              </div>
              <div className="PulseBarValue">{formatValue ? formatValue(r.value, r) : Number(r.value || 0).toLocaleString()}</div>
            </Row>
          );
        })}
      </div>
    </div>
  );
}

function LineChart({ title, points }) {
  const chart = useMemo(() => {
    const safePoints = fillDailySeriesGaps(points).map((point) => ({
      ...point,
      value: Number(point?.value || 0),
      label: String(point?.label || ''),
      displayLabel: String(point?.displayLabel || point?.label || ''),
    })).filter((point) => point.label);

    const width = 760;
    const height = 240;
    const marginTop = 16;
    const marginRight = 20;
    const marginBottom = 52;
    const marginLeft = 56;
    const innerWidth = width - marginLeft - marginRight;
    const innerHeight = height - marginTop - marginBottom;
    const yAxisX = marginLeft;
    const xAxisY = marginTop + innerHeight;

    if (!safePoints.length) {
      return {
        width,
        height,
        coords: [],
        linePath: '',
        areaPath: '',
        yTicks: [0, 1],
        xTicks: [],
        innerHeight,
        innerWidth,
        marginTop,
        marginRight,
        marginBottom,
        marginLeft,
        yAxisX,
        xAxisY,
        yMax: 1,
      };
    }

    const maxValue = Math.max(1, ...safePoints.map((point) => point.value));
    const yTickCount = maxValue <= 4 ? maxValue : 4;
    const yStep = Math.max(1, Math.ceil(maxValue / Math.max(1, yTickCount)));
    const yMax = Math.max(yStep, Math.ceil(maxValue / yStep) * yStep);
    const yTicks = Array.from({ length: Math.floor(yMax / yStep) + 1 }, (_, index) => index * yStep);

    const xForIndex = (index) => {
      if (safePoints.length === 1) return marginLeft + innerWidth / 2;
      return marginLeft + (innerWidth * index) / (safePoints.length - 1);
    };
    const yForValue = (value) => marginTop + innerHeight - (innerHeight * value) / yMax;

    const coords = safePoints.map((point, index) => ({
      ...point,
      cx: xForIndex(index),
      cy: yForValue(point.value),
    }));

    const linePath = coords.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.cx},${point.cy}`).join(' ');
    const areaPath = coords.length
      ? `${linePath} L ${coords[coords.length - 1].cx},${marginTop + innerHeight} L ${coords[0].cx},${marginTop + innerHeight} Z`
      : '';

    const maxTickCount = 6;
    const tickIndexes = safePoints.length <= maxTickCount
      ? safePoints.map((_, index) => index)
      : Array.from({ length: maxTickCount }, (_, tickIndex) => (
          Math.round((tickIndex * (safePoints.length - 1)) / (maxTickCount - 1))
        ));
    const xTicks = tickIndexes.map((index) => ({
      index,
      label: safePoints[index].label,
      displayLabel: safePoints[index].displayLabel,
      x: xForIndex(index),
    }));

    return {
      width,
      height,
      coords,
      linePath,
      areaPath,
      yTicks,
      xTicks,
      innerHeight,
      innerWidth,
      marginTop,
      marginRight,
      marginBottom,
      marginLeft,
      yAxisX,
      xAxisY,
      yMax,
    };
  }, [points]);

  return (
    <div className="PulseChartCard">
      <div className="PulseChartTitle">{title}</div>
      <div className="PulseLineWrapEnhanced">
        <svg className="PulseLineSvg" viewBox={`0 0 ${chart.width} ${chart.height}`} preserveAspectRatio="xMidYMid meet">
          {chart.yTicks.map((tick) => {
            const y = chart.marginTop + chart.innerHeight - (chart.innerHeight * tick) / Math.max(1, chart.yMax || 1);
            return (
              <g key={`y-${tick}`}>
                <line className="PulseLineGrid" x1={chart.yAxisX} x2={chart.width - chart.marginRight} y1={y} y2={y} />
                <text className="PulseLineTickLabel PulseLineTickLabelY" x={chart.yAxisX - 10} y={y + 4}>{tick}</text>
              </g>
            );
          })}
          <line className="PulseLineGrid" x1={chart.yAxisX} y1={chart.marginTop} x2={chart.yAxisX} y2={chart.xAxisY} />
          <line className="PulseLineGrid" x1={chart.yAxisX} y1={chart.xAxisY} x2={chart.width - chart.marginRight} y2={chart.xAxisY} />
          {chart.xTicks.map((tick) => (
            <g key={`x-${tick.label}`}>
              <line className="PulseLineGrid" x1={tick.x} y1={chart.xAxisY} x2={tick.x} y2={chart.xAxisY + 6} />
              <text className="PulseLineTickLabel" x={tick.x} y={chart.xAxisY + 22} textAnchor="middle">{tick.displayLabel}</text>
            </g>
          ))}
          {chart.areaPath ? <path d={chart.areaPath} className="PulseLineArea" /> : null}
          {chart.linePath ? <path d={chart.linePath} className="PulseLineStroke" /> : null}
          {chart.coords.map((point) => (
            <g key={point.label}>
              <title>{`${point.displayLabel}: ${point.value}`}</title>
              <circle className="PulseLinePoint" cx={point.cx} cy={point.cy} r="4" />
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}

function MonthlyObservedActorsChart({ title, points }) {
  const chart = useMemo(() => {
    const safePoints = (points || []).filter((point) => point && point.label);

    if (!safePoints.length) {
      return {
        coords: [],
        linePath: '',
        xTicks: [],
        yTicks: [0, 1],
        innerHeight: 0,
        xAxisY: 0,
        yAxisX: 0,
        width: 760,
        height: 280,
        marginLeft: 56,
        marginRight: 20,
        marginTop: 16,
        marginBottom: 52,
      };
    }

    const width = 760;
    const height = 280;
    const marginTop = 16;
    const marginRight = 20;
    const marginBottom = 52;
    const marginLeft = 56;
    const innerWidth = width - marginLeft - marginRight;
    const innerHeight = height - marginTop - marginBottom;
    const yAxisX = marginLeft;
    const xAxisY = marginTop + innerHeight;
    const maxY = Math.max(1, ...safePoints.map((point) => Number(point.value) || 0));
    const yTickCount = maxY <= 4 ? maxY : 4;
    const yStep = Math.max(1, Math.ceil(maxY / Math.max(1, yTickCount)));
    const yMax = Math.max(yStep, Math.ceil(maxY / yStep) * yStep);
    const yTicks = Array.from({ length: Math.floor(yMax / yStep) + 1 }, (_, index) => index * yStep);

    const xForIndex = (index) => {
      if (safePoints.length === 1) {
        return marginLeft + innerWidth / 2;
      }
      return marginLeft + (innerWidth * index) / (safePoints.length - 1);
    };

    const yForValue = (value) => marginTop + innerHeight - (innerHeight * value) / yMax;

    const coords = safePoints.map((point, index) => ({
      ...point,
      value: Number(point.value) || 0,
      cx: xForIndex(index),
      cy: yForValue(Number(point.value) || 0),
    }));

    const linePath = coords.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.cx} ${point.cy}`).join(' ');

    const maxTickCount = 6;
    const tickIndexes = Array.from(
      new Set(
        safePoints.map((_, index) => {
          if (safePoints.length <= maxTickCount) {
            return index;
          }
          return Math.round((index * (safePoints.length - 1)) / (maxTickCount - 1));
        })
      )
    );
    const xTicks = tickIndexes.map((index) => ({
      index,
      label: safePoints[index].label,
      x: xForIndex(index),
    }));

    return {
      coords,
      linePath,
      xTicks,
      yTicks,
      innerHeight,
      xAxisY,
      yAxisX,
      width,
      height,
      marginLeft,
      marginRight,
      marginTop,
      marginBottom,
    };
  }, [points]);

  return (
    <div className="PulseChartCard">
      <div className="PulseChartTitle">{title}</div>
      {!points.length ? (
        <div className="PulseMuted">No monthly data available.</div>
      ) : (
        <div className="PulseMonthlyChartWrap">
          <svg
            className="PulseMonthlyChartSvg"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={`${title} monthly observed actors line chart`}
          >
            {chart.yTicks.map((tick) => {
              const y = chart.marginTop + chart.innerHeight - (chart.innerHeight * tick) / Math.max(...chart.yTicks, 1);
              return (
                <g key={tick}>
                  <line
                    className="PulseMonthlyChartGrid"
                    x1={chart.yAxisX}
                    y1={y}
                    x2={chart.width - chart.marginRight}
                    y2={y}
                  />
                  <text className="PulseMonthlyChartTickLabel PulseMonthlyChartTickLabelY" x={chart.yAxisX - 10} y={y + 4}>
                    {tick}
                  </text>
                </g>
              );
            })}

            <line className="PulseMonthlyChartAxis" x1={chart.yAxisX} y1={chart.marginTop} x2={chart.yAxisX} y2={chart.xAxisY} />
            <line
              className="PulseMonthlyChartAxis"
              x1={chart.yAxisX}
              y1={chart.xAxisY}
              x2={chart.width - chart.marginRight}
              y2={chart.xAxisY}
            />

            <path className="PulseMonthlyChartLine" d={chart.linePath} />

            {chart.coords.map((point) => (
              <g key={point.label}>
                <title>{`${point.label}: ${point.value}`}</title>
                <circle className="PulseMonthlyChartPoint" cx={point.cx} cy={point.cy} r="4" />
              </g>
            ))}

            {chart.xTicks.map((tick) => (
              <g key={tick.label}>
                <line className="PulseMonthlyChartAxisTick" x1={tick.x} y1={chart.xAxisY} x2={tick.x} y2={chart.xAxisY + 6} />
                <text className="PulseMonthlyChartTickLabel" x={tick.x} y={chart.xAxisY + 22} textAnchor="middle">
                  {tick.label}
                </text>
              </g>
            ))}

            <text
              className="PulseMonthlyChartAxisLabel"
              x={chart.marginLeft + (chart.width - chart.marginLeft - chart.marginRight) / 2}
              y={chart.height - 10}
              textAnchor="middle"
            >
              Month
            </text>
            <text
              className="PulseMonthlyChartAxisLabel"
              x={18}
              y={chart.marginTop + chart.innerHeight / 2}
              textAnchor="middle"
              transform={`rotate(-90 18 ${chart.marginTop + chart.innerHeight / 2})`}
            >
              Observed actors
            </text>
          </svg>
        </div>
      )}
    </div>
  );
}


function MonthlyRateChart({ title, points, yAxisLabel = 'Observed actor rate (%)' }) {
  const chart = useMemo(() => {
    const safePoints = (points || []).filter((point) => point && point.label);

    if (!safePoints.length) {
      return {
        coords: [],
        linePath: '',
        xTicks: [],
        yTicks: [0, 25, 50, 75, 100],
        width: 760,
        height: 280,
        marginLeft: 56,
        marginRight: 20,
        marginTop: 16,
        marginBottom: 52,
        innerHeight: 212,
        xAxisY: 228,
        yAxisX: 56,
        yMax: 100,
      };
    }

    const width = 760;
    const height = 280;
    const marginTop = 16;
    const marginRight = 20;
    const marginBottom = 52;
    const marginLeft = 56;
    const innerWidth = width - marginLeft - marginRight;
    const innerHeight = height - marginTop - marginBottom;
    const yAxisX = marginLeft;
    const xAxisY = marginTop + innerHeight;
    const maxValue = Math.max(1, ...safePoints.map((point) => Number(point.value) || 0));
    const yMax = Math.max(100, Math.ceil(maxValue / 10) * 10);
    const yTickStep = yMax <= 20 ? 5 : yMax <= 50 ? 10 : 20;
    const yTicks = Array.from({ length: Math.floor(yMax / yTickStep) + 1 }, (_, index) => index * yTickStep);

    const xForIndex = (index) => {
      if (safePoints.length === 1) {
        return marginLeft + innerWidth / 2;
      }
      return marginLeft + (innerWidth * index) / (safePoints.length - 1);
    };

    const yForValue = (value) => marginTop + innerHeight - (innerHeight * value) / yMax;

    const coords = safePoints.map((point, index) => ({
      ...point,
      value: Number(point.value) || 0,
      cx: xForIndex(index),
      cy: yForValue(Number(point.value) || 0),
    }));

    const linePath = coords.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.cx} ${point.cy}`).join(' ');
    const maxTickCount = 6;
    const tickIndexes = Array.from(new Set(safePoints.map((_, index) => {
      if (safePoints.length <= maxTickCount) {
        return index;
      }
      return Math.round((index * (safePoints.length - 1)) / (maxTickCount - 1));
    })));
    const xTicks = tickIndexes.map((index) => ({
      index,
      label: safePoints[index].label,
      x: xForIndex(index),
    }));

    return {
      coords,
      linePath,
      xTicks,
      yTicks,
      width,
      height,
      marginLeft,
      marginRight,
      marginTop,
      marginBottom,
      innerHeight,
      xAxisY,
      yAxisX,
      yMax,
    };
  }, [points]);

  return (
    <div className="PulseChartCard">
      <div className="PulseChartTitle">{title}</div>
      {!points.length ? (
        <div className="PulseMuted">No monthly rate data available.</div>
      ) : (
        <div className="PulseMonthlyChartWrap">
          <svg
            className="PulseMonthlyChartSvg"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={`${title} monthly rate line chart`}
          >
            {chart.yTicks.map((tick) => {
              const y = chart.marginTop + chart.innerHeight - (chart.innerHeight * tick) / chart.yMax;
              return (
                <g key={tick}>
                  <line
                    className="PulseMonthlyChartGrid"
                    x1={chart.yAxisX}
                    y1={y}
                    x2={chart.width - chart.marginRight}
                    y2={y}
                  />
                  <text className="PulseMonthlyChartTickLabel PulseMonthlyChartTickLabelY" x={chart.yAxisX - 10} y={y + 4}>
                    {tick}%
                  </text>
                </g>
              );
            })}

            <line className="PulseMonthlyChartAxis" x1={chart.yAxisX} y1={chart.marginTop} x2={chart.yAxisX} y2={chart.xAxisY} />
            <line
              className="PulseMonthlyChartAxis"
              x1={chart.yAxisX}
              y1={chart.xAxisY}
              x2={chart.width - chart.marginRight}
              y2={chart.xAxisY}
            />

            <path className="PulseMonthlyChartLine" d={chart.linePath} />

            {chart.coords.map((point) => (
              <g key={point.label}>
                <title>{`${point.label}: ${point.value.toFixed(1)}%`}</title>
                <circle className="PulseMonthlyChartPoint" cx={point.cx} cy={point.cy} r="4" />
              </g>
            ))}

            {chart.xTicks.map((tick) => (
              <g key={tick.label}>
                <line className="PulseMonthlyChartAxisTick" x1={tick.x} y1={chart.xAxisY} x2={tick.x} y2={chart.xAxisY + 6} />
                <text className="PulseMonthlyChartTickLabel" x={tick.x} y={chart.xAxisY + 22} textAnchor="middle">
                  {tick.label}
                </text>
              </g>
            ))}

            <text
              className="PulseMonthlyChartAxisLabel"
              x={chart.marginLeft + (chart.width - chart.marginLeft - chart.marginRight) / 2}
              y={chart.height - 10}
              textAnchor="middle"
            >
              Month
            </text>
            <text
              className="PulseMonthlyChartAxisLabel"
              x={18}
              y={chart.marginTop + chart.innerHeight / 2}
              textAnchor="middle"
              transform={`rotate(-90 18 ${chart.marginTop + chart.innerHeight / 2})`}
            >
              {yAxisLabel}
            </text>
          </svg>
        </div>
      )}
    </div>
  );
}

function ProductInventoryTab({ apiBase }) {
  return (
    <>
      <div className="PulseCard">
        <h2>Inventory</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Product catalog across instances (API endpoints, agents, dashboards, web applications, Dataiku applications).
        </div>
      </div>
      <BuildAssetsInventoryPage
        apiBase={apiBase}
        embedded
        title="Products Inventory"
        description="Browse products across instances with filtering and details capabilities"
        endpointBase="/api/build/products"
        facetsEndpoint="/api/build/products/facets"
        typeFacetLabel="Product type"
        typeColumnLabel="Product type"
        detailsTitle="Product details"
        typeDetailLabel="Product type"
      />
    </>
  );
}



function UsersLicensePage({ apiBase }) {
  const [selectedInstance, setSelectedInstance] = useState('');
  const [licenseFilter, setLicenseFilter] = useState('all_enabled');
  const [draftSelectedInstance, setDraftSelectedInstance] = useState('');
  const [draftLicenseFilter, setDraftLicenseFilter] = useState('all_enabled');
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [facets, setFacets] = useState({ instances: [] });
  const [userKpisAll, setUserKpisAll] = useState(null);
  const [userKpisInstance, setUserKpisInstance] = useState(null);
  const [licenseStatusSummaryAll, setLicenseStatusSummaryAll] = useState(null);
  const [licenseStatusSummaryInstance, setLicenseStatusSummaryInstance] = useState(null);
  const [userProfilesAll, setUserProfilesAll] = useState([]);
  const [userProfilesInstance, setUserProfilesInstance] = useState([]);
  const [userLicenseGroupProfilesAll, setUserLicenseGroupProfilesAll] = useState([]);
  const [userLicenseGroupProfilesInstance, setUserLicenseGroupProfilesInstance] = useState([]);
  const [creatorRiskMeta, setCreatorRiskMeta] = useState(null);
  const [delinquentCreators, setDelinquentCreators] = useState({ rows: [], page: 1, pageSize: 10, totalRows: 0, totalPages: 1 });
  const [underutilizedCreators, setUnderutilizedCreators] = useState({ rows: [], page: 1, pageSize: 10, totalRows: 0, totalPages: 1 });
  const [delinquentPage, setDelinquentPage] = useState(1);
  const [underutilizedPage, setUnderutilizedPage] = useState(1);
  const [delinquentSort, setDelinquentSort] = useState({ column: 'lastActivityAt', direction: 'desc' });
  const [underutilizedSort, setUnderutilizedSort] = useState({ column: 'user', direction: 'asc' });

  const toggleRiskSort = useCallback((setter, column) => {
    setter((prev) => ({
      column,
      direction: prev.column === column && prev.direction === 'asc' ? 'desc' : 'asc',
    }));
  }, []);

  const sortedDelinquentRows = useMemo(() => {
    const rows = [...(delinquentCreators.rows || [])];
    const { column, direction } = delinquentSort;
    rows.sort((a, b) => {
      if (column === 'user') return compareNullableStrings(a.displayName || a.login || a.loginNorm, b.displayName || b.login || b.loginNorm, direction);
      if (column === 'instanceName') return compareNullableStrings(a.instanceName, b.instanceName, direction);
      if (column === 'userProfile') return compareNullableStrings(a.userProfile, b.userProfile, direction);
      if (column === 'creatorShare') {
        const left = Number(a.developingToViewingRatio || 0);
        const right = Number(b.developingToViewingRatio || 0);
        if (left === right) return 0;
        const base = left < right ? -1 : 1;
        return direction === 'desc' ? -base : base;
      }
      return compareNullableDates(a.lastActivityAt, b.lastActivityAt, direction);
    });
    return rows;
  }, [delinquentCreators.rows, delinquentSort]);

  const sortedUnderutilizedRows = useMemo(() => {
    const rows = [...(underutilizedCreators.rows || [])];
    const { column, direction } = underutilizedSort;
    rows.sort((a, b) => {
      if (column === 'user') return compareNullableStrings(a.displayName || a.login || a.loginNorm, b.displayName || b.login || b.loginNorm, direction);
      if (column === 'instanceName') return compareNullableStrings(a.instanceName, b.instanceName, direction);
      if (column === 'userProfile') return compareNullableStrings(a.userProfile, b.userProfile, direction);
      return compareNullableDates(a.lastActivityAt, b.lastActivityAt, direction);
    });
    return rows;
  }, [underutilizedCreators.rows, underutilizedSort]);
  const [selectedLogin, setSelectedLogin] = useState(null);
  const [userDetail, setUserDetail] = useState(null);
  const [topProjects, setTopProjects] = useState([]);
  const [userTrendMode, setUserTrendMode] = useState('developing');
  const [showUserInformation, setShowUserInformation] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const filtersDirty = draftSelectedInstance !== selectedInstance || draftLicenseFilter !== licenseFilter;


  const selectedRiskRow = useMemo(() => {
    if (!selectedLogin) return null;
    const matchesLogin = (row) => (row?.login || row?.loginNorm || '').toLowerCase() === String(selectedLogin || '').toLowerCase();
    return (sortedDelinquentRows || []).find(matchesLogin) || (sortedUnderutilizedRows || []).find(matchesLogin) || null;
  }, [selectedLogin, sortedDelinquentRows, sortedUnderutilizedRows]);

  const topProjectSummary = useMemo(() => {
    if (!(topProjects || []).length) return null;
    const rows = [...topProjects];
    rows.sort((left, right) => {
      const leftTotal = Number(left.developing || 0) + Number(left.viewing || 0);
      const rightTotal = Number(right.developing || 0) + Number(right.viewing || 0);
      return rightTotal - leftTotal;
    });
    return rows[0] || null;
  }, [topProjects]);

  const applyFilters = useCallback(() => {
    setSelectedInstance(draftSelectedInstance);
    setLicenseFilter(draftLicenseFilter);
    setSelectedLogin(null);
    setUserDetail(null);
    setTopProjects([]);
    setUserTrendMode('developing');
    setShowUserInformation(false);
    setDelinquentPage(1);
    setUnderutilizedPage(1);
  }, [draftLicenseFilter, draftSelectedInstance]);

  const resetFilters = useCallback(() => {
    setDraftSelectedInstance('');
    setDraftLicenseFilter('all_enabled');
  }, []);

  useEffect(() => {
    setDelinquentPage(1);
    setUnderutilizedPage(1);
  }, [selectedInstance]);

  useEffect(() => {
    fetch(apiUrl(apiBase, '/api/build/users/facets'))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading user facets');
        setFacets({ instances: data.instances || [] });
      })
      .catch((e) => setError(e.message));
  }, [apiBase]);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('licenseFilter', licenseFilter);

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/build/users/kpis?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading user KPIs');
        setUserKpisAll(data.kpis || null);
        setLicenseStatusSummaryAll(data.licenseStatusSummary || null);
        setUserProfilesAll(data.byProfile || []);
        setUserLicenseGroupProfilesAll(data.byLicenseGroupProfiles || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiBase, licenseFilter]);

  useEffect(() => {
    if (!selectedInstance) {
      setUserKpisInstance(null);
      setLicenseStatusSummaryInstance(null);
      setUserProfilesInstance([]);
      setUserLicenseGroupProfilesInstance([]);
      return;
    }

    const params = new URLSearchParams();
    params.set('licenseFilter', licenseFilter);
    params.set('instance_name', selectedInstance);

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/build/users/kpis?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading user KPIs');
        setUserKpisInstance(data.kpis || null);
        setLicenseStatusSummaryInstance(data.licenseStatusSummary || null);
        setUserProfilesInstance(data.byProfile || []);
        setUserLicenseGroupProfilesInstance(data.byLicenseGroupProfiles || []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [apiBase, licenseFilter, selectedInstance]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (selectedInstance) params.set('instance_name', selectedInstance);
    params.set('delinquentPage', String(delinquentPage));
    params.set('underutilizedPage', String(underutilizedPage));

    fetch(apiUrl(apiBase, `/api/build/users/creator-risk?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading creator risk lists');
        setCreatorRiskMeta(data.meta || null);
        setDelinquentCreators(data.delinquentCreators || { rows: [], page: 1, pageSize: 10, totalRows: 0, totalPages: 1 });
        setUnderutilizedCreators(data.underutilizedCreators || { rows: [], page: 1, pageSize: 10, totalRows: 0, totalPages: 1 });
      })
      .catch((e) => setError(e.message));
  }, [apiBase, selectedInstance, delinquentPage, underutilizedPage]);

  const riskSummaryTiles = [
    {
      label: 'Potentially dormant Creator licenses',
      value: Number(delinquentCreators.totalRows || 0).toLocaleString(),
      detail: 'Creator profiles with no observed meaningful activity in the current review window.',
    },
    {
      label: 'Low Creator utilization signals',
      value: Number(underutilizedCreators.totalRows || 0).toLocaleString(),
      detail: 'Creator profiles where most observed actions are consumption-oriented rather than creation-oriented.',
    },
    {
      label: 'Guidance window',
      value: '6 months',
      detail: selectedInstance ? `Signals currently filtered to ${selectedInstance}.` : 'Signals currently aggregated across all instances.',
    },
  ];

  useEffect(() => {
    if (!selectedLogin) return;

    const params = new URLSearchParams();
    params.set('window', 'last_3_months');
    if (selectedInstance) params.set('instance_name', selectedInstance);

    setLoading(true);
    setError('');

    fetch(apiUrl(apiBase, `/api/build/users/${encodeURIComponent(selectedLogin)}?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading user detail');
        setUserDetail(data);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    fetch(apiUrl(apiBase, `/api/build/users/${encodeURIComponent(selectedLogin)}/top-projects?${params.toString()}`))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || 'Failed loading top projects');
        setTopProjects(data.rows || []);
      })
      .catch((e) => setError(e.message));
  }, [apiBase, selectedLogin, selectedInstance]);

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>License Performance</h1>
        <p>
          Review enabled-user entitlement coverage across DSS instances, including configured license-group distribution and instance-level
          comparisons. Creator, Consumer, Admin, and Other labels on this page are based on configured license mappings and directory profile
          data, rather than observed audit-log behavior.
        </p>
      </div>

      <FilterPageLayout
        filtersExpanded={filtersExpanded}
        onOpenFilters={() => setFiltersExpanded(true)}
        onCloseFilters={() => setFiltersExpanded(false)}
        filterContent={(
          <>
            {loading ? <div className="PulseMuted" style={{ marginTop: 8 }}>Loading…</div> : null}
            {error ? <div className="PulseError">{error}</div> : null}
            <div className="PulseMuted" style={{ marginTop: 8 }}>
              License coverage on this page uses the latest entitlement snapshot. Creator-license risk signals below use a fixed trailing 6-month Pulse guidance window.
            </div>
            <label className="PulseLabel">
              License Filter
              <select className="PulseSelect" value={draftLicenseFilter} onChange={(e) => setDraftLicenseFilter(e.target.value)}>
                <option value="all_enabled">All enabled users</option>
                <option value="license_creator">Creator Licenses</option>
                <option value="license_consumer">Consumer Licenses</option>
                <option value="license_admin">Admin Licenses</option>
                <option value="license_other">Other Licenses</option>
              </select>
            </label>
            <label className="PulseLabel">
              Instance (optional)
              <select className="PulseSelect" value={draftSelectedInstance} onChange={(e) => setDraftSelectedInstance(e.target.value)}>
                <option value="">All instances</option>
                {facets.instances.map((inst) => (
                  <option key={inst} value={inst}>{inst}</option>
                ))}
              </select>
            </label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
              <button className="PulseButton" type="button" onClick={applyFilters} disabled={!filtersDirty}>
                Apply filters
              </button>
              <button className="PulseButton" type="button" onClick={resetFilters}>
                Reset
              </button>
            </div>
            {filtersDirty ? (
              <div className="PulseMuted" style={{ marginTop: 8 }}>
                You have unapplied filter changes.
              </div>
            ) : null}
          </>
        )}
      >

      <LicensePerformanceSection
        selectedInstance={selectedInstance}
        licenseStatusSummaryAll={licenseStatusSummaryAll}
        licenseStatusSummaryInstance={licenseStatusSummaryInstance}
        userKpisAll={userKpisAll}
        userKpisInstance={userKpisInstance}
        userProfilesAll={userProfilesAll}
        userProfilesInstance={userProfilesInstance}
        userLicenseGroupProfilesAll={userLicenseGroupProfilesAll}
        userLicenseGroupProfilesInstance={userLicenseGroupProfilesInstance}
      />

      <div className="PulseCard">
        <h2>Creator-License Risk Signals</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          {creatorRiskMeta?.guidanceLabel || 'These lists are simple review prompts. They help you spot Creator licenses that may deserve a closer look based on the last 6 months of observed activity.'}
        </div>
        <div className="PulseSummaryGrid" style={{ marginBottom: 14 }}>
          {riskSummaryTiles.map((tile) => (
            <div key={tile.label} className="PulseSummaryTile PulseSummaryTileStatic PulseSummaryTileCompact">
              <div className="PulseSummaryCount">{tile.value}</div>
              <div className="PulseSummaryLabel">{tile.label}</div>
              <div className="PulseSummaryDetail">{tile.detail}</div>
            </div>
          ))}
        </div>
        <div className="PulseRiskTableSection PulseRiskTableSectionElevated">
          <div className="PulseRiskSectionHeader">
            <div className="PulseActivityColumnTitle">Potentially Dormant Creator Licenses</div>
            <div className="PulseRiskSectionBadge">Review first</div>
          </div>
          <div className="PulseMuted" style={{ marginBottom: 10 }}>
            These people have a Creator license, but Pulse did not see them use or build anything in the last 6 months. This does not always mean something is wrong — it means this license is worth checking.
          </div>
          {sortedDelinquentRows.length ? (
            <div className="PulseTableWrap">
              <table className="PulseTable">
                <thead>
                  <tr>
                    <th><SortableHeader label="User" column="user" sortState={delinquentSort} onToggle={(column) => toggleRiskSort(setDelinquentSort, column)} /></th>
                    <th><SortableHeader label="Instance" column="instanceName" sortState={delinquentSort} onToggle={(column) => toggleRiskSort(setDelinquentSort, column)} /></th>
                    <th><SortableHeader label="License Type" column="userProfile" sortState={delinquentSort} onToggle={(column) => toggleRiskSort(setDelinquentSort, column)} /></th>
                    <th><SortableHeader label="Last Observed Activity" column="lastActivityAt" sortState={delinquentSort} onToggle={(column) => toggleRiskSort(setDelinquentSort, column)} /></th>
                  </tr>
                </thead>
                <tbody>
                  {sortedDelinquentRows.map((row) => (
                    <tr key={`delinquent-${row.instanceName}-${row.loginNorm}`}>
                      <td>
                        <button type="button" className="PulseLinkButton" onClick={() => setSelectedLogin(row.login || row.loginNorm)}>
                          {row.displayName || row.login || row.loginNorm}
                        </button>
                      </td>
                      <td><Badge>{row.instanceName || '-'}</Badge></td>
                      <td>{row.userProfile || 'Unknown profile'}</td>
                      <td>{row.lastActivityAt || 'No observed activity on record'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="PulseMuted">No potentially dormant Creator licenses found in the current view.</div>
          )}
          <div className="PulseMuted PulseRiskFooter">
            {delinquentCreators.totalRows ? `Showing ${((delinquentCreators.page - 1) * delinquentCreators.pageSize) + 1}–${Math.min(delinquentCreators.page * delinquentCreators.pageSize, delinquentCreators.totalRows)} of ${delinquentCreators.totalRows}` : 'Showing 0 of 0'}
          </div>
          <div className="PulseRiskPager">
            <button className="PulseButton" type="button" disabled={delinquentCreators.page <= 1} onClick={() => setDelinquentPage((p) => Math.max(1, p - 1))}>Previous</button>
            <button className="PulseButton" type="button" disabled={delinquentCreators.page >= delinquentCreators.totalPages} onClick={() => setDelinquentPage((p) => Math.min(delinquentCreators.totalPages || 1, p + 1))}>Next</button>
          </div>
        </div>
        <div className="PulseRiskTableSection PulseRiskTableSectionElevated">
          <div className="PulseRiskSectionHeader">
            <div className="PulseActivityColumnTitle">Low Creator Utilization Signals</div>
            <div className="PulseRiskSectionBadge">Needs context</div>
          </div>
          <div className="PulseMuted" style={{ marginBottom: 10 }}>
            These people are using the platform, but they are mostly looking at or using things rather than building them. This can help highlight Creator licenses that may be more powerful than the person currently needs.
          </div>
          {sortedUnderutilizedRows.length ? (
            <div className="PulseTableWrap">
              <table className="PulseTable">
                <thead>
                  <tr>
                    <th><SortableHeader label="User" column="user" sortState={underutilizedSort} onToggle={(column) => toggleRiskSort(setUnderutilizedSort, column)} /></th>
                    <th><SortableHeader label="Instance" column="instanceName" sortState={underutilizedSort} onToggle={(column) => toggleRiskSort(setUnderutilizedSort, column)} /></th>
                    <th><SortableHeader label="License Type" column="userProfile" sortState={underutilizedSort} onToggle={(column) => toggleRiskSort(setUnderutilizedSort, column)} /></th>
                    <th><SortableHeader label="Creator Share" column="creatorShare" sortState={underutilizedSort} onToggle={(column) => toggleRiskSort(setUnderutilizedSort, column)} /></th>
                    <th><SortableHeader label="Last Observed Activity" column="lastActivityAt" sortState={underutilizedSort} onToggle={(column) => toggleRiskSort(setUnderutilizedSort, column)} /></th>
                  </tr>
                </thead>
                <tbody>
                  {sortedUnderutilizedRows.map((row) => (
                    <tr key={`underutilized-${row.instanceName}-${row.loginNorm}`}>
                      <td>
                        <button type="button" className="PulseLinkButton" onClick={() => setSelectedLogin(row.login || row.loginNorm)}>
                          {row.displayName || row.login || row.loginNorm}
                        </button>
                      </td>
                      <td><Badge>{row.instanceName || '-'}</Badge></td>
                      <td>{row.userProfile || 'Unknown profile'}</td>
                      <td>{(((row.developingToViewingRatio || 0) * 100)).toFixed(1)}%</td>
                      <td>{row.lastActivityAt || 'No observed activity on record'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="PulseMuted">No low Creator-utilization signals found in the current view.</div>
          )}
          <div className="PulseMuted PulseRiskFooter">
            {underutilizedCreators.totalRows ? `Showing ${((underutilizedCreators.page - 1) * underutilizedCreators.pageSize) + 1}–${Math.min(underutilizedCreators.page * underutilizedCreators.pageSize, underutilizedCreators.totalRows)} of ${underutilizedCreators.totalRows}` : 'Showing 0 of 0'}
          </div>
          <div className="PulseRiskPager">
            <button className="PulseButton" type="button" disabled={underutilizedCreators.page <= 1} onClick={() => setUnderutilizedPage((p) => Math.max(1, p - 1))}>Previous</button>
            <button className="PulseButton" type="button" disabled={underutilizedCreators.page >= underutilizedCreators.totalPages} onClick={() => setUnderutilizedPage((p) => Math.min(underutilizedCreators.totalPages || 1, p + 1))}>Next</button>
          </div>
        </div>
      </div>

      {selectedLogin ? (
        <Modal title={`${selectedLogin} user card`} onClose={() => setSelectedLogin(null)}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            <Badge>{selectedLogin}</Badge>
            <Badge>last 3 months</Badge>
            {selectedInstance ? <Badge>{selectedInstance}</Badge> : <Badge>All instances</Badge>}
            <button className="PulseButton" type="button" onClick={() => { setSelectedLogin(null); setUserDetail(null); setTopProjects([]); setUserTrendMode('developing'); setShowUserInformation(false); }}>
              Clear selection
            </button>
          </div>

          <UserInformationSection
            detail={userDetail?.user || userDetail?.detail || null}
            detailInstances={userDetail?.instances || []}
            selectedInstance={selectedInstance}
            expanded={showUserInformation}
            onToggle={() => setShowUserInformation((v) => !v)}
            loadingMessage="Loading user details…"
          />

          <PulseSection title="License Summary">
            {userDetail?.summary ? (
              <>
                <div className="PulseMuted" style={{ marginBottom: 10 }}>
                  License review summary for the fixed trailing 3-month detail window{selectedInstance ? ` on ${selectedInstance}` : ' across all instances'}.
                  {selectedRiskRow?.userProfile ? ` Pulse currently flags this user under the ${selectedRiskRow.userProfile} license profile.` : ''}
                </div>
                <div className="PulseDetailGrid">
                  <div>
                    <div className="PulseMuted">License profile risk</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>
                      {selectedRiskRow
                        ? (selectedRiskRow.developingToViewingRatio != null ? 'Low Creator utilization' : 'Potentially dormant Creator')
                        : 'No current risk flag'}
                    </div>
                  </div>
                  <div>
                    <div className="PulseMuted">Last meaningful activity</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>
                      {selectedRiskRow?.lastActivityAt || userDetail.summary.last_activity_at || 'No observed activity on record'}
                    </div>
                  </div>
                  <div>
                    <div className="PulseMuted">Most active project</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>
                      {topProjectSummary ? `${topProjectSummary.instanceName || '-'} / ${topProjectSummary.projectKey || '-'}` : '-'}
                    </div>
                  </div>
                  <div>
                    <div className="PulseMuted">Total actions</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(userDetail.summary.total_actions || 0).toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Consumption actions</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(userDetail.summary.viewing || 0).toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Creation actions</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(userDetail.summary.developing || 0).toLocaleString()}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Activity mode</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{userDetail.summary.activity_mode ? String(userDetail.summary.activity_mode).replace('_', ' ') : '-'}</div>
                  </div>
                  <div>
                    <div className="PulseMuted">Instances touched</div>
                    <div style={{ fontWeight: 800, fontSize: 20 }}>{Number(userDetail.summary.instances || 0).toLocaleString()}</div>
                  </div>
                </div>
                <div className="PulseMuted" style={{ marginTop: 10 }}>
                  {selectedRiskRow?.developingToViewingRatio != null
                    ? `This person is active, but only ${(((selectedRiskRow.developingToViewingRatio || 0) * 100)).toFixed(1)}% of observed actions in the review window were creation-oriented.`
                    : selectedRiskRow
                      ? 'Pulse did not observe meaningful recent platform activity for this Creator license in the review window.'
                      : 'Use this section to compare the assigned profile with observed recent behavior and project context.'}
                </div>
              </>
            ) : <div className="PulseMuted">Loading user details…</div>}
          </PulseSection>

          <PulseSection title="Activity Over Time">
            {(Number(userDetail?.summary?.viewing || 0) || Number(userDetail?.summary?.developing || 0)) ? (
              <>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                  <button
                    className={`PulseButton ${userTrendMode === 'developing' ? 'PulseButtonToggleActive' : ''}`}
                    type="button"
                    onClick={() => setUserTrendMode('developing')}
                  >
                    Creating
                  </button>
                  <button
                    className={`PulseButton ${userTrendMode === 'viewing' ? 'PulseButtonToggleActive' : ''}`}
                    type="button"
                    onClick={() => setUserTrendMode('viewing')}
                  >
                    Consuming
                  </button>
                </div>
                <div className="PulseVizGrid">
                  <LineChart
                    title={userTrendMode === 'viewing' ? 'Consumption actions by day' : 'Creation actions by day'}
                    points={userTrendMode === 'viewing' ? (userDetail?.activityDailyViewing || []) : (userDetail?.activityDailyDeveloping || [])}
                  />
                  <LineChart
                    title={userTrendMode === 'viewing' ? 'Consumption actions by month' : 'Creation actions by month'}
                    points={userTrendMode === 'viewing' ? (userDetail?.activityMonthlyViewing || []) : (userDetail?.activityMonthlyDeveloping || [])}
                  />
                </div>
              </>
            ) : (
              <div className="PulseMuted">
                No consuming or creating activity was identified for this user in the selected window{selectedInstance ? ` on ${selectedInstance}` : ''}.
              </div>
            )}
          </PulseSection>

          <PulseSection title="Top Projects">
            <div className="PulseMuted" style={{ marginBottom: 8 }}>
              Projects are reported as <code>(instance_name, project_key)</code> pairs.
            </div>
            {topProjects.length ? (
              <div className="PulseTableWrap">
                <table className="PulseTable">
                  <thead>
                    <tr>
                      <th>Instance</th>
                      <th>Project Key</th>
                      <th>Creating</th>
                      <th>Consuming</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topProjects.map((r) => (
                      <tr key={`${r.instanceName}__${r.projectKey}`}>
                        <td><Badge>{r.instanceName}</Badge></td>
                        <td><Badge>{r.projectKey}</Badge></td>
                        <td>{r.developing}</td>
                        <td>{r.viewing}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="PulseMuted">No project activity is available for this user in the selected window.</div>
            )}
          </PulseSection>
        </Modal>
      ) : null}

      </FilterPageLayout>
    </div>
  );
}

function AppLayout({ groups, globalGroups, homeHref, onHomeClick, workspaceOptions, workspace, onWorkspaceChange, userLabel, railCollapsed, onRailToggle, activeGroup, onGroupToggle, children }) {
  return (
    <DropdownNav
      groups={groups}
      globalGroups={globalGroups}
      homeHref={homeHref}
      onHomeClick={onHomeClick}
      workspaceOptions={workspaceOptions}
      workspace={workspace}
      onWorkspaceChange={onWorkspaceChange}
      userLabel={userLabel}
      railCollapsed={railCollapsed}
      onRailToggle={onRailToggle}
      activeGroup={activeGroup}
      onGroupToggle={onGroupToggle}
    >
      <div className="PulsePage">{children}</div>
    </DropdownNav>
  );
}

// TEMP AUTH DEBUG START
function TempAuthDebugPanel({ authState }) {
  const renderValue = (value, fallback = '—') => {
    if (value === null || value === undefined || value === '') return fallback;
    return String(value);
  };

  const renderBool = (value) => (value ? 'Yes' : 'No');

  const panelStyle = {
    marginTop: '24px',
    padding: '20px',
    border: '1px solid #d0d7de',
    borderRadius: '8px',
    background: '#fff',
  };

  const tableStyle = {
    width: '100%',
    borderCollapse: 'collapse',
  };

  const cellStyle = {
    padding: '6px 0',
    borderBottom: '1px solid #eef2f6',
    verticalAlign: 'top',
  };

  const sectionTitleStyle = {
    margin: '16px 0 8px',
    fontSize: '14px',
    fontWeight: 600,
  };

  const rawStyle = {
    marginTop: '12px',
    padding: '12px',
    background: '#f6f8fa',
    borderRadius: '6px',
    overflowX: 'auto',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  };

  const status = authState.status;
  const data = authState.data;
  const user = data && data.user ? data.user : null;
  const configuredGroups = data && data.configuredGroups ? data.configuredGroups : {};
  const permissions = data && data.permissions ? data.permissions : {};
  const groups = user && Array.isArray(user.groups) ? user.groups : [];

  return (
    <PulseSection title="Authentication Debug — Temporary">
      <div style={panelStyle}>
        {status === 'loading' ? <div className="PulseMuted">Loading authentication details…</div> : null}

        {status === 'error' ? <div className="PulseAlert">Failed loading authentication details: {authState.error}</div> : null}

        {status === 'malformed' ? (
          <div className="PulseAlert">Received a malformed authentication response from `/api/me`.</div>
        ) : null}

        {status === 'ready' ? (
          <>
            <div style={sectionTitleStyle}>Authentication</div>
            <table style={tableStyle}>
              <tbody>
                <tr><td style={cellStyle}>Authenticated</td><td style={cellStyle}>{renderBool(Boolean(data.authenticated))}</td></tr>
                <tr><td style={cellStyle}>Login</td><td style={cellStyle}>{renderValue(user && user.login)}</td></tr>
                <tr><td style={cellStyle}>Display name</td><td style={cellStyle}>{renderValue(user && user.displayName)}</td></tr>
                <tr><td style={cellStyle}>Email</td><td style={cellStyle}>{renderValue(user && user.email)}</td></tr>
                <tr><td style={cellStyle}>Highest tier</td><td style={cellStyle}>{renderValue(permissions.highestTier)}</td></tr>
              </tbody>
            </table>

            <div style={sectionTitleStyle}>Configured groups</div>
            <table style={tableStyle}>
              <tbody>
                <tr><td style={cellStyle}>Organization</td><td style={cellStyle}>{renderValue(configuredGroups.organization)}</td></tr>
                <tr><td style={cellStyle}>Administration</td><td style={cellStyle}>{renderValue(configuredGroups.administration)}</td></tr>
              </tbody>
            </table>

            <div style={sectionTitleStyle}>Resolved permissions</div>
            <table style={tableStyle}>
              <tbody>
                <tr><td style={cellStyle}>Self</td><td style={cellStyle}>{renderBool(Boolean(permissions.self))}</td></tr>
                <tr><td style={cellStyle}>Organization</td><td style={cellStyle}>{renderBool(Boolean(permissions.organization))}</td></tr>
                <tr><td style={cellStyle}>Administration</td><td style={cellStyle}>{renderBool(Boolean(permissions.administration))}</td></tr>
              </tbody>
            </table>

            <div style={sectionTitleStyle}>User groups</div>
            {groups.length ? (
              <ul>
                {groups.map((group) => (
                  <li key={group}>{group}</li>
                ))}
              </ul>
            ) : (
              <div className="PulseMuted">No groups reported.</div>
            )}

            {!data.authenticated ? (
              <div className="PulseMuted">This response indicates the current request is unauthenticated.</div>
            ) : null}

            <details>
              <summary>Raw /api/me response</summary>
              <pre style={rawStyle}>{JSON.stringify(data, null, 2)}</pre>
            </details>
          </>
        ) : null}
      </div>
    </PulseSection>
  );
}
// TEMP AUTH DEBUG END

function HomePage({ authState }) {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Home</h1>
        <p>
          Pulse provides a structured executive view of platform adoption, product development, and consumption trends
          across the Dataiku environment.
        </p>
      </div>

      <PulseSection title="Performance">
        <p>
          Pulse is a curated analytics experience designed to help stakeholders understand how the platform is being used,
          where activity is concentrated, and how adoption patterns evolve over time. It brings together operational
          signals and business-facing metrics into a single interface intended to support informed discussion and
          practical decision-making.
        </p>
        <p>
          The dashboard is designed to emphasize clarity over volume. Rather than reproducing raw event data, Pulse
          focuses on high-value signals that help identify momentum, concentration, emerging trends, and areas that may
          warrant additional attention.
        </p>
      </PulseSection>

      <PulseSection title="What Pulse helps answer">
        <ul>
          <li>How adoption and engagement are trending across the platform</li>
          <li>Where development activity is increasing, stabilizing, or declining</li>
          <li>What products and assets are being created and consumed most actively</li>
          <li>How usage patterns differ across instances, users, and product types</li>
          <li>Which signals may merit operational follow-up or strategic discussion</li>
        </ul>
      </PulseSection>

      <PulseSection title="Primary Audience">
        <p>
          Pulse is intended for leadership, customer-facing, and administrative stakeholders who need a concise view of
          platform health and adoption. This typically includes account teams, customer success roles, technical account
          managers, product stakeholders, and platform administrators.
        </p>
      </PulseSection>

      <PulseSection title="Operating Approach">
        <p>
          Pulse should be used as a decision-support and insight-gathering tool. Its purpose is to surface meaningful
          patterns, support prioritization, and provide a consistent basis for discussion across technical and business
          stakeholders.
        </p>
        <p>
          For detailed information regarding support boundaries, release status, and interpretation of reported values,
          refer to the <strong>Disclaimer</strong> page.
        </p>
      </PulseSection>

    </div>
  );
}

function AuthenticationRequiredPage() {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Authentication Required</h1>
        <p>Please log in to Dataiku DSS to access Pulse.</p>
      </div>
    </div>
  );
}

function ExportPage({ apiBase }) {
  const [windowKind, setWindowKind] = useState('last_3_months');
  const [selectedInstance, setSelectedInstance] = useState('');
  const [activityFilter, setActivityFilter] = useState('license_creator');
  const [licenseFilter, setLicenseFilter] = useState('all_enabled');
  const [facets, setFacets] = useState({ instances: [] });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [includeSummary, setIncludeSummary] = useState(true);
  const [includeMonthly, setIncludeMonthly] = useState(true);
  const [includeSegments, setIncludeSegments] = useState(true);
  const [includeLeaderboard, setIncludeLeaderboard] = useState(true);
  const [includeLicenseSummary, setIncludeLicenseSummary] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl(apiBase, '/api/build/users/facets'))
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (!data.ok) throw new Error(data.error || 'Failed loading export facets');
        setFacets({ instances: data.instances || [] });
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || 'Failed loading export options');
      });
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  const sectionCount = [includeSummary, includeMonthly, includeSegments, includeLeaderboard, includeLicenseSummary].filter(Boolean).length;

  const onExport = useCallback(() => {
    if (sectionCount === 0) {
      setError('Select at least one report section.');
      return;
    }
    setError('');
    setLoading(true);
    const params = new URLSearchParams();
    params.set('window', windowKind);
    params.set('activityFilter', activityFilter);
    params.set('licenseFilter', licenseFilter);
    if (selectedInstance) params.set('instance_name', selectedInstance);
    params.set('includeSummary', includeSummary ? 'true' : 'false');
    params.set('includeMonthly', includeMonthly ? 'true' : 'false');
    params.set('includeSegments', includeSegments ? 'true' : 'false');
    params.set('includeLeaderboard', includeLeaderboard ? 'true' : 'false');
    params.set('includeLicenseSummary', includeLicenseSummary ? 'true' : 'false');
    window.open(apiUrl(apiBase, `/api/export/users-report.pdf?${params.toString()}`), '_blank', 'noopener');
    setLoading(false);
  }, [activityFilter, apiBase, includeLeaderboard, includeLicenseSummary, includeMonthly, includeSegments, includeSummary, licenseFilter, sectionCount, selectedInstance, windowKind]);

  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>Export</h1>
        <p>
          Build a downloadable PDF report using the current Pulse Users and License metrics. Select filters, choose which report
          sections to include, and then generate the PDF.
        </p>
      </div>

      <div className="PulseCard">
        <h2>Filters</h2>
        {loading ? <div className="PulseMuted">Preparing export…</div> : null}
        {error ? <div className="PulseError">{error}</div> : null}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <label className="PulseLabel" style={{ maxWidth: 220 }}>
            Time window
            <select className="PulseSelect" value={windowKind} onChange={(e) => setWindowKind(e.target.value)}>
              <option value="this_month">This month</option>
              <option value="last_3_months">Last 3 months</option>
              <option value="last_12_months">Last 12 months</option>
            </select>
          </label>

          <label className="PulseLabel" style={{ maxWidth: 260 }}>
            Activity filter
            <select className="PulseSelect" value={activityFilter} onChange={(e) => setActivityFilter(e.target.value)}>
              <option value="license_creator">Creators</option>
              <option value="license_consumer">Consumers</option>
            </select>
          </label>

          <label className="PulseLabel" style={{ maxWidth: 260 }}>
            License filter
            <select className="PulseSelect" value={licenseFilter} onChange={(e) => setLicenseFilter(e.target.value)}>
              <option value="all_enabled">All enabled users</option>
              <option value="license_creator">Creator Licenses</option>
              <option value="license_consumer">Consumer Licenses</option>
              <option value="license_admin">Admin Licenses</option>
              <option value="license_other">Other Licenses</option>
            </select>
          </label>

          <label className="PulseLabel" style={{ maxWidth: 260 }}>
            Instance (optional)
            <select className="PulseSelect" value={selectedInstance} onChange={(e) => setSelectedInstance(e.target.value)}>
              <option value="">All instances</option>
              {facets.instances.map((inst) => (
                <option key={inst} value={inst}>{inst}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="PulseCard">
        <h2>Sections</h2>
        <div className="PulseMuted" style={{ marginBottom: 10 }}>
          Choose which content blocks to include in the exported PDF.
        </div>
        <div style={{ display: 'grid', gap: 10 }}>
          <label><input type="checkbox" checked={includeSummary} onChange={(e) => setIncludeSummary(e.target.checked)} /> Summary KPIs</label>
          <label><input type="checkbox" checked={includeMonthly} onChange={(e) => setIncludeMonthly(e.target.checked)} /> Monthly activity trend</label>
          <label><input type="checkbox" checked={includeSegments} onChange={(e) => setIncludeSegments(e.target.checked)} /> User segments</label>
          <label><input type="checkbox" checked={includeLeaderboard} onChange={(e) => setIncludeLeaderboard(e.target.checked)} /> Leaderboard</label>
          <label><input type="checkbox" checked={includeLicenseSummary} onChange={(e) => setIncludeLicenseSummary(e.target.checked)} /> License summary</label>
        </div>
      </div>

      <div className="PulseCard">
        <h2>Generate</h2>
        <div className="PulseMuted" style={{ marginBottom: 12 }}>
          {sectionCount} section{sectionCount === 1 ? '' : 's'} selected for export.
        </div>
        <button className="PulseButton" type="button" onClick={onExport}>Download PDF</button>
      </div>
    </div>
  );
}


function normalizeHashRoute(hash) {
  return String(hash || '').replace(/^#/, '').trim();
}

function normalizeHashRouteForRegistry(hash, isAuthenticated = false) {
  const normalized = normalizeHashRoute(hash);
  if (normalized === '' && isAuthenticated) {
    return null;
  }
  return normalized;
}

function TemporaryPage({ title, description, status = 'This page is under development.' }) {
  return (
    <div className="PulseWide">
      <div className="PulseHero">
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <PulseSection title="Coming Soon">
        <p>{status}</p>
      </PulseSection>
    </div>
  );
}

function normalizePageRoute(route) {
  return route == null ? null : String(route).replace(/^#/, '').trim();
}

export function buildPageRegistry({ homeHref, userActivityEnabled, llmMeshEnabled }) {
  const pages = [
    {
      key: 'pulse-home', workspace: 'global', group: 'Pulse', label: 'Home', route: 'home', permission: 'self', capability: null,
      status: 'implemented', component: HomePage, isDefault: true,
      description: 'High-level overview of platform activity, highlighting key lifecycle and consumption trends at a glance',
      active: ({ route }) => route === 'home',
    },
    {
      key: 'pulse-faq', workspace: 'global', group: 'Pulse', label: 'FAQ', route: 'faq', permission: 'self', capability: null,
      status: 'implemented', component: FaqPage, isDefault: false,
      description: 'Answers common questions about metrics, definitions, and how data is derived',
      active: ({ route }) => route === 'faq',
    },
    {
      key: 'pulse-disclaimer', workspace: 'global', group: 'Pulse', label: 'Disclaimer', route: 'disclaimer', permission: 'self', capability: null,
      status: 'implemented', component: DisclaimerPage, isDefault: false,
      description: 'Important notes about support scope, beta status, and how to interpret Pulse metrics',
      active: ({ route }) => route === 'disclaimer',
    },
    {
      key: 'my-overview', workspace: 'me', group: 'My Information', label: 'Overview', route: null, localPage: 'my-overview', permission: 'self', capability: null,
      status: 'implemented', component: MyInformationPage, componentProps: { pageKey: 'my-overview' }, isDefault: true,
      description: 'View your personal Pulse landing page and upcoming individual insights',
      active: ({ workspace, myPage }) => workspace === 'me' && myPage === 'my-overview',
    },
    {
      key: 'my-assets', workspace: 'me', group: 'My Product Lifecycle', label: 'Assets', route: null, localPage: 'my-assets', permission: 'self', capability: null,
      status: 'placeholder', component: TemporaryPage, placeholderTitle: 'Assets',
      description: 'Review the Dataiku assets you have created, owned, or contributed to',
      placeholderStatus: 'Personal asset insights are coming soon.', isDefault: false,
      active: ({ workspace, myPage }) => workspace === 'me' && myPage === 'my-assets',
    },
    {
      key: 'my-products', workspace: 'me', group: 'My Product Lifecycle', label: 'Products', route: null, localPage: 'my-products', permission: 'self', capability: null,
      status: 'placeholder', component: TemporaryPage, placeholderTitle: 'Products',
      description: 'Review the products and outputs you have created or helped develop',
      placeholderStatus: 'Personal product insights are coming soon.', isDefault: false,
      active: ({ workspace, myPage }) => workspace === 'me' && myPage === 'my-products',
    },
    {
      key: 'my-consumption', workspace: 'me', group: 'My Product Lifecycle', label: 'Consumption', route: null, localPage: 'my-consumption', permission: 'self', capability: null,
      status: 'placeholder', component: TemporaryPage, placeholderTitle: 'Consumption',
      description: 'Review how your products and outputs are being consumed by others',
      placeholderStatus: 'Personal consumption insights are coming soon.', isDefault: false,
      active: ({ workspace, myPage }) => workspace === 'me' && myPage === 'my-consumption',
    },
    {
      key: 'my-llm-overview', workspace: 'me', group: 'My LLM Mesh', label: 'My Usage', route: null, localPage: 'my-llm-overview', permission: 'self', capability: 'llmMesh',
      status: 'implemented', component: MyInformationPage, componentProps: { pageKey: 'my-llm-overview' }, isDefault: false,
      description: 'Review your personal LLM Mesh usage, models, projects, tokens, and cost',
      active: ({ workspace, myPage }) => workspace === 'me' && myPage === 'my-llm-overview',
    },
    {
      key: 'org-users', workspace: 'organization', group: 'User Insights', label: 'Activity Performance', route: 'users', permission: 'organization', capability: 'userActivity',
      status: 'implemented', component: UsersActivityPage, isDefault: true,
      description: 'Explore observed activity, engagement, and behavior trends across DSS instances',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'users',
    },
    {
      key: 'org-users-licenses', workspace: 'organization', group: 'User Insights', label: 'License Performance', route: 'users/licenses', permission: 'organization', capability: 'userActivity',
      status: 'implemented', component: UsersLicensePage, isDefault: false,
      description: 'Review enabled users, license profiles, and entitlement coverage across DSS instances',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'users/licenses',
    },
    {
      key: 'org-assets', workspace: 'organization', group: 'Product Lifecycle', label: 'Assets', route: 'product-lifecycle/assets', permission: 'organization', capability: null,
      status: 'implemented', component: BuildAssetsInventoryPage, isDefault: false,
      description: 'Explore all assets (projects, datasets, recipes, etc.) across instances with filtering and details capabilities',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'product-lifecycle/assets',
    },
    {
      key: 'org-products', workspace: 'organization', group: 'Product Lifecycle', label: 'Products', route: 'product-lifecycle/products', permission: 'organization', capability: null,
      status: 'implemented', component: ProductOutputsPage, isDefault: false,
      description: 'Identify and quantify the end products being built (dashboards, APIs, apps, agents) and how they are structured',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'product-lifecycle/products',
    },
    {
      key: 'org-development-activity', workspace: 'organization', group: 'Product Lifecycle', label: 'Development Activity', route: 'product-lifecycle/development-activity', permission: 'organization', capability: null,
      status: 'implemented', component: DevelopmentActivityPage, isDefault: false,
      description: 'Analyze audit-driven build activity and capability adoption across the platform',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'product-lifecycle/development-activity',
    },
    {
      key: 'org-consumption-activity', workspace: 'organization', group: 'Product Lifecycle', label: 'Consumption Activity', route: 'product-lifecycle/consumption-activity', permission: 'organization', capability: null,
      status: 'implemented', component: ConsumptionActivityPage, isDefault: false,
      description: 'Understand who is consuming which products, and how consumption trends evolve over time',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'product-lifecycle/consumption-activity',
    },
    {
      key: 'pulse-export', workspace: 'organization', group: 'Pulse', label: 'Export', route: 'export', permission: 'organization', capability: null,
      status: 'implemented', component: ExportPage, isDefault: false,
      description: 'Select filters and sections, then generate a downloadable PDF report',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'export',
    },
    {
      key: 'organization-llm-mesh-usage-summary', workspace: 'organization', group: 'LLM Mesh', label: 'Usage Summary', route: 'llm-mesh/usage-summary', permission: 'organization', capability: 'llmMesh',
      status: 'placeholder', component: () => <LlmMeshPlaceholderPage title="Usage Summary" description="A cross-instance summary of LLM Mesh consumption, including requests, tokens, estimated cost, active users, active projects, active instances, models, providers, and connections." />, isDefault: false,
      description: 'A cross-instance summary of LLM Mesh consumption, including requests, tokens, estimated cost, active users, active projects, active instances, models, providers, and connections.',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'llm-mesh/usage-summary',
    },
    {
      key: 'organization-llm-mesh-usage-breakdown', workspace: 'organization', group: 'LLM Mesh', label: 'Usage Breakdown', route: 'llm-mesh/usage-breakdown', permission: 'organization', capability: 'llmMesh',
      status: 'placeholder', component: () => <LlmMeshPlaceholderPage title="Usage Breakdown" description="A detailed view of where LLM Mesh usage is occurring, broken down by instance, project, user, connection, provider, and model." />, isDefault: false,
      description: 'A detailed view of where LLM Mesh usage is occurring, broken down by instance, project, user, connection, provider, and model.',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'llm-mesh/usage-breakdown',
    },
    {
      key: 'organization-llm-mesh-reliability-controls', workspace: 'organization', group: 'LLM Mesh', label: 'Reliability & Controls', route: 'llm-mesh/reliability-controls', permission: 'organization', capability: 'llmMesh',
      status: 'placeholder', component: () => <LlmMeshPlaceholderPage title="Reliability & Controls" description="A factual summary of LLM Mesh operational behavior, including latency, errors, throttling, quota usage, rate-limit usage, and guardrail outcomes." />, isDefault: false,
      description: 'A factual summary of LLM Mesh operational behavior, including latency, errors, throttling, quota usage, rate-limit usage, and guardrail outcomes.',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'llm-mesh/reliability-controls',
    },
    {
      key: 'organization-llm-mesh-activity-records', workspace: 'organization', group: 'LLM Mesh', label: 'Activity Records', route: 'llm-mesh/activity-records', permission: 'organization', capability: 'llmMesh',
      status: 'placeholder', component: () => <LlmMeshPlaceholderPage title="Activity Records" description="A searchable detailed record of LLM Mesh activity across instances, projects, users, connections, providers, models, dates, and available consumption metrics." />, isDefault: false,
      description: 'A searchable detailed record of LLM Mesh activity across instances, projects, users, connections, providers, models, dates, and available consumption metrics.',
      active: ({ route, workspace }) => workspace === 'organization' && route === 'llm-mesh/activity-records',
    },
    {
      key: 'admin-overview', workspace: 'administration', group: 'Administration', label: 'Overview', route: null, localPage: 'administration-overview', permission: 'administration', capability: null,
      status: 'implemented', component: AdministrationPlaceholderPage, isDefault: true,
      description: 'Open the current administration page',
      active: ({ workspace, adminPage }) => workspace === 'administration' && adminPage === 'administration-overview',
    },
    {
      key: 'admin-debug-reload', workspace: 'administration', group: 'Debug', label: 'Reload DuckDB', route: 'debug/reload', permission: 'administration', capability: null,
      status: 'implemented', component: DebugReloadPage, isDefault: false,
      description: 'Manually refresh and rebuild the analytics layer from source data',
      active: ({ route, workspace }) => workspace === 'administration' && route === 'debug/reload',
    },
    {
      key: 'admin-debug-preview', workspace: 'administration', group: 'Debug', label: 'Preview DuckDB', route: 'debug/preview', permission: 'administration', capability: null,
      status: 'implemented', component: DebugPreviewPage, isDefault: false,
      description: 'Inspect underlying tables and data powering the Pulse application',
      active: ({ route, workspace }) => workspace === 'administration' && route === 'debug/preview',
    },
  ];

  return pages.filter((page) => {
    if (page.capability === 'llmMesh' && !llmMeshEnabled) return false;
    if (page.capability === 'userActivity' && !userActivityEnabled) return false;
    return true;
  }).map((page) => ({
    ...page,
    route: normalizePageRoute(page.route),
    href: page.route !== null ? `${homeHref}#${page.route}` : null,
  }));
}

export function checkPagePermission(page, permissions) {
  if (!page || !permissions) return false;
  if (page.permission === 'administration') return permissions.administration === true;
  if (page.permission === 'organization') return permissions.organization === true;
  return permissions.self !== false;
}

export function checkPageCapability(page, capabilities) {
  if (!page) return false;
  if (page.capability === 'llmMesh') return capabilities.llmMeshEnabled === true;
  if (page.capability === 'userActivity') return capabilities.userActivityEnabled === true;
  return true;
}

export function validatePageRegistry(pageRegistry) {
  const warnings = [];
  const pageKeys = new Set();
  const routeKeys = new Set();
  const defaultsByWorkspace = new Map();
  for (const page of pageRegistry) {
    if (pageKeys.has(page.key)) warnings.push(`duplicate page key: ${page.key}`);
    pageKeys.add(page.key);
    if (page.route !== null) {
      if (routeKeys.has(page.route)) warnings.push(`duplicate route key: ${page.route}`);
      routeKeys.add(page.route);
    }
    if (page.isDefault) {
      defaultsByWorkspace.set(page.workspace, (defaultsByWorkspace.get(page.workspace) || 0) + 1);
    }
    if (!page.component) warnings.push(`missing component for page: ${page.key}`);
    if (page.workspace === 'administration' && page.permission !== 'administration') {
      warnings.push(`administration page missing administration permission: ${page.key}`);
    }
    if (page.group === 'LLM Mesh' || page.group === 'My LLM Mesh') {
      if (page.capability !== 'llmMesh') warnings.push(`llm page missing llmMesh capability: ${page.key}`);
    }
  }
  for (const workspace of ['global', 'me', 'organization', 'administration']) {
    if ((defaultsByWorkspace.get(workspace) || 0) !== 1) {
      warnings.push(`workspace ${workspace} must have exactly one default page`);
    }
  }
  if (warnings.length && process.env.NODE_ENV !== 'production') {
    warnings.forEach((warning) => console.error(`[pulse-route] ${warning}`));
  }
  if (process.env.NODE_ENV !== 'production' && console.table) {
    console.table(pageRegistry.map((page) => ({ workspace: page.workspace, page: page.key, status: page.status, reachable: true })));
  }
  return warnings;
}

function App() {
  const [hashRoute, setHashRoute] = useState(normalizeHashRoute(window.location.hash));
  const [authState, setAuthState] = useState({ status: 'loading', data: null, error: '' });
  const [startupFlagsLoaded, setStartupFlagsLoaded] = useState(false);
  const [startupFlags, setStartupFlags] = useState({ userActivity: true, debug: false });
  const [advancedLlmMeshCapability, setAdvancedLlmMeshCapability] = useState({ enabled: false, licensedInstances: [] });
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [workspace, setWorkspace] = useState('me');
  const [myPage, setMyPage] = useState('my-overview');
  const [adminPage, setAdminPage] = useState('administration-overview');
  const [activeRailGroup, setActiveRailGroup] = useState('My Information');
  const suppressNextHashChangeRef = useRef(false);

  const currentPortBase = useMemo(() => {
    try {
      if (window?.dataiku && typeof window.dataiku.getWebAppBackendUrl === 'function') {
        const backendUrl = window.dataiku.getWebAppBackendUrl('');
        return backendUrl ? backendUrl.replace(/\/api\/?$/, '') : '';
      }
    } catch (error) {
      return '';
    }
    return '';
  }, []);

  const apiBase = useMemo(() => {
    if (currentPortBase) return `${currentPortBase}/api`;
    return './api';
  }, [currentPortBase]);

  useEffect(() => {
    let cancelled = false;
    async function loadFlags() {
      try {
        const response = await fetch(apiUrl(apiBase, '/api/startup/flags'), { cache: 'no-cache' });
        const data = await response.json();
        if (cancelled) return;
        const flags = data && data.flags ? data.flags : {};
        const config = data && data.config ? data.config : {};
        setStartupFlags({
          userActivity: flags.userActivity !== false,
          debug: flags.debug === true,
          llmMesh: flags.llmMesh === true,
          config,
        });
      } catch (error) {
        if (!cancelled) {
          setStartupFlags({ userActivity: true, debug: false, llmMesh: false, config: {} });
        }
      } finally {
        if (!cancelled) setStartupFlagsLoaded(true);
      }
    }
    loadFlags();
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  useEffect(() => {
    let cancelled = false;
    setAuthState({ status: 'loading', data: null, error: '' });

    async function loadMe() {
      try {
        const response = await fetch(apiUrl(apiBase, '/api/me'), { cache: 'no-cache' });
        const data = await response.json();
        if (cancelled) return;
        if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
        if (!data || typeof data !== 'object' || typeof data.authenticated !== 'boolean') {
          setAuthState({ status: 'malformed', data, error: 'Malformed /api/me response' });
          return;
        }
        setAuthState({ status: 'ready', data, error: '' });
      } catch (error) {
        if (!cancelled) setAuthState({ status: 'error', data: null, error: error.message || 'Failed loading /api/me' });
      }
    }

    loadMe();
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  useEffect(() => {
    let cancelled = false;
    async function loadLlmCapability() {
      try {
        const response = await fetch(apiUrl(apiBase, '/api/startup/flags'), { cache: 'no-cache' });
        const data = await response.json();
        if (cancelled) return;
        const capability = data && data.capabilities ? data.capabilities.advancedLLMMesh : null;
        if (capability && capability.enabled === true) {
          setAdvancedLlmMeshCapability({ enabled: true, licensedInstances: Array.isArray(capability.licensedInstances) ? capability.licensedInstances : [] });
          return;
        }
      } catch (error) {
      }
      if (!cancelled) setAdvancedLlmMeshCapability({ enabled: false, licensedInstances: [] });
    }
    loadLlmCapability();
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  useEffect(() => {
    const onHashChange = () => {
      if (suppressNextHashChangeRef.current) {
        suppressNextHashChangeRef.current = false;
        setHashRoute(normalizeHashRoute(window.location.hash));
        return;
      }
      setHashRoute(normalizeHashRoute(window.location.hash));
    };

    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const homeHref = currentPortBase ? `${currentPortBase}/` : './';
  const permissions = useMemo(() => (authState.data && authState.data.permissions ? authState.data.permissions : {}), [authState.data]);
  const isAuthenticated = authState.status === 'ready' && authState.data && authState.data.authenticated === true;
  const userLabel = getUserDisplayName(authState);
  const userActivityEnabled = startupFlags.userActivity !== false;
  const llmMeshEnabled = advancedLlmMeshCapability.enabled === true;
  const capabilities = useMemo(
    () => ({ startupFlagsLoaded, userActivityEnabled, llmMeshEnabled }),
    [llmMeshEnabled, startupFlagsLoaded, userActivityEnabled]
  );
  const pageRegistry = useMemo(
    () => buildPageRegistry({ homeHref, userActivityEnabled, llmMeshEnabled }),
    [homeHref, llmMeshEnabled, userActivityEnabled]
  );

  useEffect(() => {
    validatePageRegistry(pageRegistry);
  }, [pageRegistry]);

  const pagesByKey = useMemo(() => new Map(pageRegistry.map((page) => [page.key, page])), [pageRegistry]);
  const pagesByRoute = useMemo(() => new Map(pageRegistry.filter((page) => page.route !== null).map((page) => [page.route, page])), [pageRegistry]);
  const defaultPages = useMemo(() => {
    const defaults = new Map();
    pageRegistry.forEach((page) => {
      if (page.isDefault) defaults.set(page.workspace, page);
    });
    return defaults;
  }, [pageRegistry]);

  const workspaceOptions = useMemo(() => {
    if (!isAuthenticated) return [];
    const options = [{ value: 'me', label: 'My Information' }];
    if (permissions.organization === true) options.push({ value: 'organization', label: 'Organization' });
    if (permissions.administration === true) options.push({ value: 'administration', label: 'Administration' });
    return options;
  }, [isAuthenticated, permissions.organization, permissions.administration]);

  const resolveFallbackPage = useCallback((workspaceName, reason, pageKey) => {
    const fallbackWorkspace = workspaceName === 'global' ? 'global' : workspaceName;
    const fallback = defaultPages.get(fallbackWorkspace) || defaultPages.get('global');
    if (process.env.NODE_ENV !== 'production') {
      console.warn(`[pulse-route] Falling back from unknown page: ${pageKey || reason}`);
    }
    return fallback;
  }, [defaultPages]);

  const getPageNavigationTarget = useCallback((page, currentWorkspaceOverride = workspace) => {
    const fallbackMyPage = defaultPages.get('me')?.localPage || 'my-overview';
    const fallbackAdminPage = defaultPages.get('administration')?.localPage || 'administration-overview';
    const nextWorkspace = page.workspace === 'global' ? currentWorkspaceOverride : page.workspace;
    return {
      workspace: nextWorkspace,
      pageKey: page.key,
      route: page.route,
      myPage: page.workspace === 'me' ? (page.localPage || fallbackMyPage) : fallbackMyPage,
      adminPage: page.workspace === 'administration' ? (page.localPage || fallbackAdminPage) : fallbackAdminPage,
      activeRailGroup: page.group,
    };
  }, [defaultPages, workspace]);

  const applyNavigationTarget = useCallback((target, source = 'unknown') => {
    if (!target) return;
    if (process.env.NODE_ENV !== 'production') {
      console.debug('[pulse-nav-target]', target);
    }
    if (process.env.NODE_ENV !== 'production') {
      console.debug('[pulse-nav] transition', {
        source,
        previousWorkspace: workspace,
        nextWorkspace: target.workspace,
        pageKey: target.pageKey,
        route: target.route,
        myPage: target.myPage,
        adminPage: target.adminPage,
        activeRailGroup: target.activeRailGroup,
      });
    }

    if (workspace !== target.workspace) setWorkspace(target.workspace);
    if (myPage !== target.myPage) setMyPage(target.myPage);
    if (adminPage !== target.adminPage) setAdminPage(target.adminPage);
    if (activeRailGroup !== target.activeRailGroup) setActiveRailGroup(target.activeRailGroup);

    const nextHash = target.route == null ? '' : `#${target.route}`;
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    } else if (hashRoute !== (target.route || '')) {
      setHashRoute(target.route || '');
    }
  }, [activeRailGroup, adminPage, hashRoute, myPage, workspace]);

  const switchWorkspace = useCallback((nextWorkspace) => {
    const requestedWorkspace = nextWorkspace === 'organization'
      ? 'organization'
      : nextWorkspace === 'administration'
        ? 'administration'
        : 'me';

    if (requestedWorkspace === 'organization' && permissions.organization !== true) return;
    if (requestedWorkspace === 'administration' && permissions.administration !== true) return;

    const defaults = {
      me: { workspace: 'me', pageKey: 'my-overview', route: null, myPage: 'my-overview', adminPage: 'administration-overview', activeRailGroup: 'My Information' },
      organization: { workspace: 'organization', pageKey: 'org-users', route: 'users', myPage: 'my-overview', adminPage: 'administration-overview', activeRailGroup: 'User Insights' },
      administration: { workspace: 'administration', pageKey: 'admin-overview', route: null, myPage: 'my-overview', adminPage: 'administration-overview', activeRailGroup: 'Administration' },
    };
    const target = defaults[requestedWorkspace];
    if (!target) return;

    if (process.env.NODE_ENV !== 'production') {
      console.debug('[workspace-switch]', {
        requestedWorkspace,
        currentWorkspace: workspace,
        targetPageKey: target.pageKey,
        targetRoute: target.route,
      });
    }

    setWorkspace(target.workspace);
    setMyPage(target.myPage);
    setAdminPage(target.adminPage);
    setActiveRailGroup(target.activeRailGroup);

    const nextHash = target.route == null ? '' : `#${target.route}`;
    if (target.route == null) {
      if (window.location.hash) {
        suppressNextHashChangeRef.current = true;
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }
      setHashRoute('');
    } else if (window.location.hash !== nextHash) {
      suppressNextHashChangeRef.current = true;
      window.location.hash = nextHash;
      setHashRoute(target.route);
    } else {
      setHashRoute(target.route);
    }

    if (process.env.NODE_ENV !== 'production') {
      console.debug('[workspace-switch:committed]', {
        workspace: target.workspace,
        myPage: target.myPage,
        adminPage: target.adminPage,
        hashRoute: target.route || '',
        activeRailGroup: target.activeRailGroup,
      });
    }
  }, [permissions.administration, permissions.organization, workspace]);

  const navigateToPage = useCallback((page, options = {}) => {
    if (process.env.NODE_ENV !== 'production') {
      console.debug('[pulse-link-click]', {
        clickedKey: page?.key,
        registryPage: page ? { key: page.key, route: page.route, workspace: page.workspace, group: page.group } : null,
      });
    }
    if (!page) return;
    if (!checkPagePermission(page, permissions) || !checkPageCapability(page, capabilities)) {
      const fallback = resolveFallbackPage(page.workspace, 'unavailable', page.key);
      if (fallback && fallback.key !== page.key) {
        applyNavigationTarget(getPageNavigationTarget(fallback, workspace), options.source || 'fallback');
      }
      return;
    }
    applyNavigationTarget(getPageNavigationTarget(page, workspace), options.source || 'direct');
  }, [applyNavigationTarget, capabilities, getPageNavigationTarget, permissions, resolveFallbackPage, workspace]);

  const handleWorkspaceChange = useCallback((nextWorkspace) => {
    switchWorkspace(nextWorkspace);
  }, [switchWorkspace]);

  useEffect(() => {
    if (!workspaceOptions.length) return;
    const allowed = new Set(workspaceOptions.map((option) => option.value));
    if (!allowed.has(workspace)) {
      switchWorkspace('me');
    }
  }, [switchWorkspace, workspace, workspaceOptions]);

  const resolvedCurrentPage = useMemo(() => {
    if (!isAuthenticated) return null;

    const normalizedRoute = normalizeHashRouteForRegistry(hashRoute, isAuthenticated);
    const routePage = normalizedRoute == null ? null : pagesByRoute.get(normalizedRoute);
    if (routePage) {
      if (!checkPagePermission(routePage, permissions) || !checkPageCapability(routePage, capabilities)) {
        return resolveFallbackPage(routePage.workspace, 'route-unavailable', routePage.key);
      }
      return routePage;
    }

    if (workspace === 'me') {
      const page = pagesByKey.get(myPage) || resolveFallbackPage('me', 'unknown-page', myPage);
      if (!page || !checkPagePermission(page, permissions) || !checkPageCapability(page, capabilities)) {
        return resolveFallbackPage('me', 'unavailable', myPage);
      }
      return page;
    }

    if (workspace === 'administration') {
      const page = pagesByKey.get(adminPage) || resolveFallbackPage('administration', 'unknown-page', adminPage);
      if (!page || !checkPagePermission(page, permissions) || !checkPageCapability(page, capabilities)) {
        return resolveFallbackPage('administration', 'unavailable', adminPage);
      }
      return page;
    }

    if (workspace === 'organization') {
      if (normalizedRoute == null) {
        return resolveFallbackPage('organization', 'empty-route', 'organization-default');
      }
      return resolveFallbackPage('organization', 'unknown-route', normalizedRoute);
    }

    return resolveFallbackPage('global', 'unknown-route', normalizedRoute || 'global-default');
  }, [adminPage, capabilities, hashRoute, isAuthenticated, myPage, pagesByKey, pagesByRoute, permissions, resolveFallbackPage, workspace]);

  if (process.env.NODE_ENV !== 'production') {
    console.debug('[pulse-route-state]', {
      hash: window.location.hash,
      hashRoute,
      resolvedPageKey: resolvedCurrentPage?.key,
      workspace,
    });
  }

  const buildNavGroups = useCallback((workspaceName) => {
    const groups = [];
    const grouped = new Map();
    pageRegistry.forEach((page) => {
      if (page.workspace !== workspaceName) return;
      if (page.key === 'pulse-export') return;
      if (!checkPagePermission(page, permissions) || !checkPageCapability(page, capabilities)) return;
      if (!grouped.has(page.group)) grouped.set(page.group, []);
      grouped.get(page.group).push(page);
    });
    grouped.forEach((pages, groupLabel) => {
      groups.push({
        label: groupLabel,
        items: pages.map((page) => ({
          key: page.key,
          href: page.href,
          label: page.label,
          description: page.description,
          isActive: resolvedCurrentPage ? resolvedCurrentPage.key === page.key : false,
        })),
      });
    });
    return groups;
  }, [capabilities, pageRegistry, permissions, resolvedCurrentPage]);

  const globalPulseNavGroups = useMemo(() => {
    const groups = [];
    const grouped = new Map();
    pageRegistry.forEach((page) => {
      const isSharedPulsePage = page.workspace === 'global';
      const isOrganizationPulseExport = page.key === 'pulse-export' && workspace === 'organization';
      if (!isSharedPulsePage && !isOrganizationPulseExport) return;
      if (!checkPagePermission(page, permissions) || !checkPageCapability(page, capabilities)) return;
      if (!grouped.has(page.group)) grouped.set(page.group, []);
      grouped.get(page.group).push(page);
    });
    grouped.forEach((pages, groupLabel) => {
      groups.push({
        label: groupLabel,
        items: pages.map((page) => ({
          key: page.key,
          href: page.href,
          label: page.label,
          description: page.description,
          isActive: resolvedCurrentPage ? resolvedCurrentPage.key === page.key : false,
        })),
      });
    });
    return groups;
  }, [capabilities, pageRegistry, permissions, resolvedCurrentPage, workspace]);

  const organizationNavGroups = useMemo(() => buildNavGroups('organization'), [buildNavGroups]);
  const myInformationNavGroups = useMemo(() => buildNavGroups('me'), [buildNavGroups]);
  const administrationNavGroups = useMemo(() => buildNavGroups('administration'), [buildNavGroups]);

  const activeWorkspaceNavGroups = useMemo(() => {
    if (workspace === 'organization' && permissions.organization === true) return organizationNavGroups;
    if (workspace === 'administration' && permissions.administration === true) return administrationNavGroups;
    return myInformationNavGroups;
  }, [administrationNavGroups, myInformationNavGroups, organizationNavGroups, permissions.administration, permissions.organization, workspace]);

  const displayedWorkspaceNavGroups = useMemo(() => {
    if (resolvedCurrentPage && resolvedCurrentPage.workspace === 'global') {
      return activeWorkspaceNavGroups;
    }
    return activeWorkspaceNavGroups;
  }, [activeWorkspaceNavGroups, resolvedCurrentPage]);

  const allowedRailGroups = useMemo(
    () => [...activeWorkspaceNavGroups, ...globalPulseNavGroups].map((group) => group.label),
    [activeWorkspaceNavGroups, globalPulseNavGroups]
  );

  useEffect(() => {
    if (!resolvedCurrentPage || !isAuthenticated) return;
    const target = getPageNavigationTarget(resolvedCurrentPage, workspace);
    const workspaceMatches = workspace === target.workspace;
    const myMatches = myPage === target.myPage;
    const adminMatches = adminPage === target.adminPage;
    const routeMatches = hashRoute === (target.route || '');
    if (!workspaceMatches || !myMatches || !adminMatches || !routeMatches) {
      if (workspace !== resolvedCurrentPage.workspace && resolvedCurrentPage.workspace !== 'global') {
        return;
      }
      applyNavigationTarget(target, 'state-reconcile');
    }
  }, [adminPage, applyNavigationTarget, getPageNavigationTarget, hashRoute, isAuthenticated, myPage, resolvedCurrentPage, workspace]);

  useEffect(() => {
    if (!allowedRailGroups.includes(activeRailGroup)) {
      const nextGroup = resolvedCurrentPage
        ? resolvedCurrentPage.group
        : activeWorkspaceNavGroups[0]?.label || '';
      if (nextGroup !== activeRailGroup) setActiveRailGroup(nextGroup);
    }
  }, [activeRailGroup, activeWorkspaceNavGroups, allowedRailGroups, resolvedCurrentPage]);

  const isPreviewSession = Boolean(authState.data && authState.data.previewMode);
  const effectiveUserLabel = isPreviewSession ? `${userLabel} · Internal Preview` : userLabel;

  const wrap = (page, currentWorkspace = workspace) => (
    <AppLayout
      groups={displayedWorkspaceNavGroups.map((group) => ({
        ...group,
        items: group.items.map((item) => ({
          ...item,
          onClick: () => navigateToPage(pagesByKey.get(item.key)),
        })),
      }))}
      globalGroups={globalPulseNavGroups.map((group) => ({
        ...group,
        items: group.items.map((item) => ({
          ...item,
          onClick: () => navigateToPage(pagesByKey.get(item.key)),
        })),
      }))}
      homeHref={homeHref}
      onHomeClick={() => navigateToPage(pagesByKey.get('pulse-home'))}
      workspaceOptions={workspaceOptions}
      workspace={currentWorkspace}
      onWorkspaceChange={handleWorkspaceChange}
      userLabel={effectiveUserLabel}
      railCollapsed={railCollapsed}
      onRailToggle={() => setRailCollapsed((current) => !current)}
      activeGroup={activeRailGroup}
      onGroupToggle={(groupLabel, shouldForceOpen = false) => {
        setActiveRailGroup((current) => {
          if (shouldForceOpen) return groupLabel;
          return current === groupLabel ? '' : groupLabel;
        });
      }}
    >
      {page}
    </AppLayout>
  );

  if (authState.status === 'loading') {
    return wrap(
      <div className="PulseWide">
        <div className="PulseHero">
          <h1>Loading</h1>
          <p>Loading authenticated workspace access…</p>
        </div>
      </div>,
      'me'
    );
  }

  if (authState.status === 'error' || authState.status === 'malformed' || !isAuthenticated) {
    return wrap(<AuthenticationRequiredPage />, 'me');
  }

  const currentPage = resolvedCurrentPage;

  if (!currentPage) {
    return wrap(<HomePage authState={authState} />);
  }

  const PageComponent = currentPage.component;
  if (process.env.NODE_ENV !== 'production') {
    console.debug('[pulse-render]', {
      resolvedPageKey: currentPage?.key,
      component: currentPage?.component?.name || null,
    });
  }
  const pageProps = currentPage.status === 'placeholder'
    ? {
        title: currentPage.placeholderTitle || currentPage.label,
        description: currentPage.description,
        status: currentPage.placeholderStatus,
      }
    : {
        apiBase,
        authState,
        userLabel,
        advancedLlmMeshEnabled: advancedLlmMeshCapability.enabled === true,
        ...(currentPage.componentProps || {}),
      };

  return wrap(<PageComponent {...pageProps} />, workspace);
}

export default App;
