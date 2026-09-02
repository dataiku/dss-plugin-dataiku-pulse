import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App, {
  LicensePerformanceSection,
  buildLicenseUtilizationTrendSeries,
  buildPageRegistry,
  checkPageCapability,
  checkPagePermission,
  validatePageRegistry,
} from './App';

describe('License utilization fact UI', () => {
  const emptyLicenseStatus = { fields: {}, addonServices: [], features: [], instanceCount: 0 };
  const sectionDefaults = {
    historyMode: 'profile',
    setHistoryMode: jest.fn(),
    licenseStatusSummaryAll: emptyLicenseStatus,
    licenseStatusSummaryInstance: null,
  };
  const currentLicenseUtilization = {
    profileRows: [
      {
        license_group: 'Creator Licenses',
        license_profile: 'DESIGNER',
        assigned_count: 17,
        entitled_count: 10,
        available_count: -7,
        utilization_pct: 170,
        instances: [
          { instance_name: 'inst-a', assigned_count: 5 },
          { instance_name: 'inst-b', assigned_count: 12 },
        ],
      },
      {
        license_group: 'Admin Licenses',
        license_profile: 'ADMIN',
        assigned_count: 1,
        entitled_count: null,
        available_count: null,
        utilization_pct: null,
        instances: [{ instance_name: 'inst-c', assigned_count: 1 }],
      },
    ],
    instanceRows: [
      { instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'DESIGNER', assigned_count: 5, entitled_count: 10, available_count: 5, utilization_pct: 50 },
      { instance_name: 'inst-b', license_group: 'Creator Licenses', license_profile: 'DESIGNER', assigned_count: 12, entitled_count: 20, available_count: 8, utilization_pct: 60 },
    ],
  };

  test('groups historical trend rows by separate instance and profile series', () => {
    const series = buildLicenseUtilizationTrendSeries([
      { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_profile: 'DESIGNER', assigned_count: 5, utilization_pct: 50 },
      { snapshot_date: '2024-01-01', instance_name: 'inst-a', license_profile: 'DESIGNER', assigned_count: 4, utilization_pct: 40 },
      { snapshot_date: '2024-01-02', instance_name: 'inst-b', license_profile: 'DESIGNER', assigned_count: 12, utilization_pct: 60 },
    ]);

    expect(series).toHaveLength(2);
    expect(series.map((item) => item.label)).toEqual(['inst-a / DESIGNER', 'inst-b / DESIGNER']);
    expect(series[0].points.map((point) => point.snapshotDate)).toEqual(['2024-01-01', '2024-01-02']);
  });

  test('shows explicit unavailable state when the fact is missing', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={currentLicenseUtilization}
        licenseUtilization={{
          available: false,
          unavailableReason: 'fact_license_utilization_daily is unavailable. Rebuild GOLD tables with the license utilization fact before using License Performance capacity metrics.',
          latestRows: [],
          historyRows: [],
          meta: { latestSnapshotDates: [], seriesCount: 0 },
        }}
        {...sectionDefaults}
      />
    );

    expect(screen.getAllByText(/Rebuild GOLD tables/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Historical Utilization Trend/)).toBeInTheDocument();
  });

  test('defaults to current profile summary with contributing instances', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={currentLicenseUtilization}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'DESIGNER', assigned_count: 5, utilization_pct: 50 },
            { snapshot_date: '2024-01-03', instance_name: 'inst-b', license_group: 'Creator Licenses', license_profile: 'DESIGNER', assigned_count: 12, utilization_pct: 60 },
          ],
          meta: { latestSnapshotDates: ['2024-01-02', '2024-01-03'], historyStartDate: '2024-01-02', historyEndDate: '2024-01-03', seriesCount: 2 },
        }}
        {...sectionDefaults}
      />
    );

    expect(screen.getByText('Profile Summary')).toBeInTheDocument();
    const profileHeader = screen.getAllByText('Profile')[0].closest('th');
    expect(profileHeader).toBeInTheDocument();
    expect(screen.getByText('17')).toBeInTheDocument();
    expect(screen.getByText('-7 (over capacity)')).toBeInTheDocument();
    expect(screen.getByText(/inst-a · 5 users/)).toBeInTheDocument();
    expect(screen.getByText(/inst-b · 12 users/)).toBeInTheDocument();
  });

  test('profile-oriented history groups one profile with expandable instance series', () => {
    const historyRows = [
      { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'A_I_ACCESS_USERS', assigned_count: 5, utilization_pct: 50 },
      { snapshot_date: '2024-01-03', instance_name: 'inst-b', license_group: 'Creator Licenses', license_profile: 'A_I_ACCESS_USERS', assigned_count: 12, utilization_pct: 60 },
    ];

    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ profileRows: [], instanceRows: [] }}
        licenseUtilization={{
          available: true,
          historyRows,
          meta: { latestSnapshotDates: ['2024-01-02', '2024-01-03'], historyStartDate: '2024-01-02', historyEndDate: '2024-01-03', seriesCount: 2 },
        }}
        {...sectionDefaults}
      />
    );

    expect(screen.getByText('A_I_ACCESS_USERS')).toBeInTheDocument();
    expect(screen.getByText('2 separate instance/profile series')).toBeInTheDocument();
    expect(screen.getByText('Instance Trends')).toBeInTheDocument();
    expect(screen.queryByText('Combined Metrics')).not.toBeInTheDocument();
    expect(screen.queryByText('Not combined across instances')).not.toBeInTheDocument();
    const parentProfileCell = screen.getByText('A_I_ACCESS_USERS').closest('td');
    const parentCells = screen.getByText('A_I_ACCESS_USERS').closest('tr').querySelectorAll('td');
    expect(parentProfileCell).not.toHaveTextContent(/Show instance trends/);
    expect(parentCells[3]).toHaveTextContent(/Show instance trends/);
    expect(screen.queryByText('inst-a / A_I_ACCESS_USERS')).not.toBeInTheDocument();
    expect(screen.queryByText('5')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Show instance trends/ }));

    expect(screen.getByText('inst-a / A_I_ACCESS_USERS')).toBeInTheDocument();
    expect(screen.getByText('inst-b / A_I_ACCESS_USERS')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByLabelText(/inst-a \/ A_I_ACCESS_USERS utilization trend, locally scaled 50.0%–50.0%/)).toBeInTheDocument();
  });

  test('locally scales low-valued changing historical sparklines', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ profileRows: [], instanceRows: [] }}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-01', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'LOW_PROFILE', assigned_count: 1, utilization_pct: 0.0 },
            { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'LOW_PROFILE', assigned_count: 2, utilization_pct: 0.1 },
            { snapshot_date: '2024-01-03', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'LOW_PROFILE', assigned_count: 3, utilization_pct: 0.7 },
          ],
          meta: { latestSnapshotDates: ['2024-01-03'], historyStartDate: '2024-01-01', historyEndDate: '2024-01-03', seriesCount: 1 },
        }}
        {...sectionDefaults}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Show instance trends/ }));

    const sparkline = screen.getByLabelText(/inst-a \/ LOW_PROFILE utilization trend, locally scaled 0.0%–0.7%/);
    const path = sparkline.querySelector('path');
    expect(path).toBeInTheDocument();
    const yCoordinates = path.getAttribute('d').match(/,([0-9.]+)/g).map((value) => Number(value.slice(1)));
    expect(new Set(yCoordinates).size).toBeGreaterThan(1);
    expect(Math.max(...yCoordinates) - Math.min(...yCoordinates)).toBeGreaterThan(1);
  });

  test('keeps constant historical sparklines flat', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ profileRows: [], instanceRows: [] }}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-01', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'ZERO_PROFILE', assigned_count: 0, utilization_pct: 0 },
            { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'ZERO_PROFILE', assigned_count: 0, utilization_pct: 0 },
          ],
          meta: { latestSnapshotDates: ['2024-01-02'], historyStartDate: '2024-01-01', historyEndDate: '2024-01-02', seriesCount: 1 },
        }}
        {...sectionDefaults}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Show instance trends/ }));

    const sparkline = screen.getByLabelText(/inst-a \/ ZERO_PROFILE utilization trend, locally scaled 0.0%–0.0%/);
    const yCoordinates = sparkline.querySelector('path').getAttribute('d').match(/,([0-9.]+)/g).map((value) => Number(value.slice(1)));
    expect(new Set(yCoordinates).size).toBe(1);
  });

  test('does not clip over-capacity historical sparklines at 100 percent', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ profileRows: [], instanceRows: [] }}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-01', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'OVER_PROFILE', assigned_count: 10, utilization_pct: 100 },
            { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'OVER_PROFILE', assigned_count: 12, utilization_pct: 120 },
          ],
          meta: { latestSnapshotDates: ['2024-01-02'], historyStartDate: '2024-01-01', historyEndDate: '2024-01-02', seriesCount: 1 },
        }}
        {...sectionDefaults}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Show instance trends/ }));

    const sparkline = screen.getByLabelText(/inst-a \/ OVER_PROFILE utilization trend, locally scaled 100.0%–120.0%/);
    const yCoordinates = sparkline.querySelector('path').getAttribute('d').match(/,([0-9.]+)/g).map((value) => Number(value.slice(1)));
    expect(new Set(yCoordinates).size).toBeGreaterThan(1);
  });

  test('keeps unavailable utilization state when history points are null', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ profileRows: [], instanceRows: [] }}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-01', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'NULL_PROFILE', assigned_count: 1, utilization_pct: null },
          ],
          meta: { latestSnapshotDates: ['2024-01-01'], historyStartDate: '2024-01-01', historyEndDate: '2024-01-01', seriesCount: 1 },
        }}
        {...sectionDefaults}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Show instance trends/ }));

    expect(screen.getByText('Utilization unavailable')).toBeInTheDocument();
    expect(screen.queryByLabelText(/NULL_PROFILE utilization trend/)).not.toBeInTheDocument();
  });

  test('profile-oriented history renders all profile groups without first-eight truncation', () => {
    const historyRows = Array.from({ length: 9 }, (_, index) => ({
      snapshot_date: '2024-01-02',
      instance_name: 'inst-a',
      license_group: 'Creator Licenses',
      license_profile: `PROFILE_${index + 1}`,
      assigned_count: index + 1,
      utilization_pct: 10 + index,
    }));

    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={currentLicenseUtilization}
        licenseUtilization={{
          available: true,
          historyRows,
          meta: { latestSnapshotDates: ['2024-01-02'], historyStartDate: '2024-01-02', historyEndDate: '2024-01-02', seriesCount: 9 },
        }}
        {...sectionDefaults}
      />
    );

    expect(screen.getByText('PROFILE_1')).toBeInTheDocument();
    expect(screen.getByText('PROFILE_9')).toBeInTheDocument();
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Historical profile focus/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Historical instance focus/)).not.toBeInTheDocument();
  });

  test('instance summary provides paged access over all page-filtered rows', () => {
    const manyInstanceRows = Array.from({ length: 21 }, (_, index) => ({
      instance_name: `inst-${String(index + 1).padStart(2, '0')}`,
      license_group: 'Creator Licenses',
      license_profile: 'DESIGNER',
      assigned_count: index + 1,
      entitled_count: 100,
      available_count: 99 - index,
      utilization_pct: index + 1,
    }));

    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ ...currentLicenseUtilization, instanceRows: manyInstanceRows }}
        licenseUtilization={{ available: true, historyRows: [], meta: { seriesCount: 0 } }}
        {...sectionDefaults}
      />
    );

    fireEvent.click(screen.getByText('Instance Summary'));

    expect(screen.queryByText('Search instance/profile rows')).not.toBeInTheDocument();
    expect(screen.getByText('Showing 20 of 21 page-filtered rows.')).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
    expect(screen.queryByText('inst-21')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Next'));
    expect(screen.getByText('inst-21')).toBeInTheDocument();
  });

  test('sorts current summaries and profile history groups by profile', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{
          profileRows: [
            { license_group: 'Creator Licenses', license_profile: 'Z_PROFILE', assigned_count: 2, instances: [] },
            { license_group: 'Admin Licenses', license_profile: 'A_PROFILE', assigned_count: 1, instances: [] },
          ],
          instanceRows: [
            { instance_name: 'inst-b', license_group: 'Creator Licenses', license_profile: 'A_PROFILE', assigned_count: 1 },
            { instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'Z_PROFILE', assigned_count: 1 },
          ],
        }}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-02', instance_name: 'inst-b', license_group: 'Creator Licenses', license_profile: 'A_PROFILE', assigned_count: 2, utilization_pct: 20 },
            { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'Z_PROFILE', assigned_count: 1, utilization_pct: 10 },
          ],
          meta: { latestSnapshotDates: ['2024-01-02'], historyStartDate: '2024-01-02', historyEndDate: '2024-01-02', seriesCount: 2 },
        }}
        {...sectionDefaults}
      />
    );

    const profileCells = screen.getAllByText(/^[AZ]_PROFILE$/);
    expect(profileCells[0]).toHaveTextContent('A_PROFILE');
    expect(profileCells[1]).toHaveTextContent('Z_PROFILE');
    expect(profileCells[2]).toHaveTextContent('A_PROFILE');
    expect(profileCells[3]).toHaveTextContent('Z_PROFILE');
  });

  test('instance-oriented history remains ungrouped and ordered by instance then profile', () => {
    render(
      <LicensePerformanceSection
        selectedInstance=""
        currentLicenseUtilization={{ profileRows: [], instanceRows: [] }}
        historyMode="instance"
        setHistoryMode={jest.fn()}
        licenseUtilization={{
          available: true,
          historyRows: [
            { snapshot_date: '2024-01-02', instance_name: 'inst-b', license_group: 'Creator Licenses', license_profile: 'A_PROFILE', assigned_count: 2, utilization_pct: 20 },
            { snapshot_date: '2024-01-02', instance_name: 'inst-a', license_group: 'Creator Licenses', license_profile: 'Z_PROFILE', assigned_count: 1, utilization_pct: 10 },
          ],
          meta: { latestSnapshotDates: ['2024-01-02'], historyStartDate: '2024-01-02', historyEndDate: '2024-01-02', seriesCount: 2 },
        }}
        licenseStatusSummaryAll={emptyLicenseStatus}
        licenseStatusSummaryInstance={null}
      />
    );

    expect(screen.queryByText(/separate instance\/profile series/)).not.toBeInTheDocument();
    expect(screen.getByText('inst-a / Z_PROFILE').compareDocumentPosition(screen.getByText('inst-b / A_PROFILE')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

function buildSharedPulseItems(pages, permissions, activeWorkspace) {
  return pages
    .filter((page) => page.workspace === 'global' || (page.key === 'pulse-export' && activeWorkspace === 'organization'))
    .filter((page) => checkPagePermission(page, permissions))
    .map((page) => page.key);
}

describe('Pulse page registry', () => {
  const build = (overrides = {}) => buildPageRegistry({
    homeHref: './',
    userActivityEnabled: true,
    llmMeshEnabled: true,
    ...overrides,
  });

  test('defines exactly one default per workspace', () => {
    const pages = build();
    expect(validatePageRegistry(pages)).toEqual([]);
  });

  test('every registry page resolves to a component', () => {
    const pages = build();
    pages.forEach((page) => {
      expect(page.component).toBeDefined();
      expect(page.component).not.toBeNull();
    });
  });

  test('self-only user cannot access organization or administration pages', () => {
    const pages = build();
    const permissions = { self: true, organization: false, administration: false };
    expect(pages.filter((page) => page.workspace === 'me').every((page) => checkPagePermission(page, permissions))).toBe(true);
    expect(pages.filter((page) => page.workspace === 'organization').every((page) => !checkPagePermission(page, permissions))).toBe(true);
    expect(pages.filter((page) => page.workspace === 'administration').every((page) => !checkPagePermission(page, permissions))).toBe(true);
  });

  test('organization user can access organization pages but not administration pages', () => {
    const pages = build();
    const permissions = { self: true, organization: true, administration: false };
    expect(pages.filter((page) => page.workspace === 'organization').every((page) => checkPagePermission(page, permissions))).toBe(true);
    expect(pages.filter((page) => page.workspace === 'administration').every((page) => !checkPagePermission(page, permissions))).toBe(true);
  });

  test('export is registered only once under organization', () => {
    const pages = build();
    const exportPages = pages.filter((page) => page.key === 'pulse-export');
    expect(exportPages).toHaveLength(1);
    expect(exportPages[0]).toMatchObject({
      workspace: 'organization',
      group: 'Pulse',
      route: 'export',
      permission: 'organization',
    });
  });

  test('export uses the existing shared Pulse navigation group', () => {
    const pages = build();
    const items = buildSharedPulseItems(pages, { self: true, organization: true, administration: false }, 'organization');
    expect(items).toEqual(['pulse-home', 'pulse-faq', 'pulse-disclaimer', 'pulse-export']);
  });

  test('organization does not create a duplicate workspace-specific Pulse group', () => {
    const pages = build();
    const organizationPulsePages = pages.filter((page) => page.workspace === 'organization' && page.group === 'Pulse');
    expect(organizationPulsePages).toEqual([
      expect.objectContaining({ key: 'pulse-export' }),
    ]);
  });

  test('export is inaccessible without organization permission including administration-only users', () => {
    const pages = build();
    const exportPage = pages.find((page) => page.key === 'pulse-export');
    expect(exportPage).toBeDefined();
    expect(checkPagePermission(exportPage, { self: true, organization: false, administration: false })).toBe(false);
    expect(checkPagePermission(exportPage, { self: true, organization: false, administration: true })).toBe(false);
    expect(checkPagePermission(exportPage, { self: true, organization: true, administration: true })).toBe(true);
  });

  test('administration user can access all permission-gated pages', () => {
    const pages = build();
    const permissions = { self: true, organization: true, administration: true };
    expect(pages.every((page) => checkPagePermission(page, permissions))).toBe(true);
  });

  test('llm mesh pages disappear when capability disabled', () => {
    const pages = build({ llmMeshEnabled: false });
    expect(pages.some((page) => page.key === 'my-llm-overview')).toBe(false);
    expect(pages.some((page) => page.key === 'organization-llm-mesh-usage-summary')).toBe(false);
    expect(pages.some((page) => page.key === 'organization-llm-mesh-usage-breakdown')).toBe(false);
    expect(pages.some((page) => page.key === 'organization-llm-mesh-reliability-controls')).toBe(false);
    expect(pages.some((page) => page.key === 'organization-llm-mesh-activity-records')).toBe(false);
  });

  test('llm mesh pages keep expected labels', () => {
    const pages = build({ llmMeshEnabled: true });
    expect(pages.find((page) => page.key === 'my-llm-overview')?.label).toBe('My Usage');
    expect(pages.find((page) => page.key === 'organization-llm-mesh-usage-summary')?.label).toBe('Usage Summary');
    expect(pages.find((page) => page.key === 'organization-llm-mesh-usage-breakdown')?.label).toBe('Usage Breakdown');
    expect(pages.find((page) => page.key === 'organization-llm-mesh-reliability-controls')?.label).toBe('Reliability & Controls');
    expect(pages.find((page) => page.key === 'organization-llm-mesh-activity-records')?.label).toBe('Activity Records');
  });

  test('eligible user sees my llm mesh when capability enabled', () => {
    const pages = build({ llmMeshEnabled: true });
    const permissions = { self: true, organization: false, administration: false };
    const page = pages.find((entry) => entry.key === 'my-llm-overview');
    expect(page).toBeDefined();
    expect(checkPagePermission(page, permissions)).toBe(true);
    expect(checkPageCapability(page, { llmMeshEnabled: true, userActivityEnabled: true, startupFlagsLoaded: true })).toBe(true);
  });

  test('organization llm mesh pages require organization permission and capability', () => {
    const pages = build({ llmMeshEnabled: true });
    const pageKeys = [
      'organization-llm-mesh-usage-summary',
      'organization-llm-mesh-usage-breakdown',
      'organization-llm-mesh-reliability-controls',
      'organization-llm-mesh-activity-records',
    ];
    pageKeys.forEach((key) => {
      const page = pages.find((entry) => entry.key === key);
      expect(page).toBeDefined();
      expect(checkPagePermission(page, { self: true, organization: false, administration: false })).toBe(false);
      expect(checkPagePermission(page, { self: true, organization: true, administration: false })).toBe(true);
      expect(checkPageCapability(page, { llmMeshEnabled: false, userActivityEnabled: true, startupFlagsLoaded: true })).toBe(false);
      expect(checkPageCapability(page, { llmMeshEnabled: true, userActivityEnabled: true, startupFlagsLoaded: true })).toBe(true);
    });
  });

  test('organization llm mesh pages appear in the correct order', () => {
    const pages = build({ llmMeshEnabled: true });
    const llmMeshLabels = pages
      .filter((page) => page.workspace === 'organization' && page.group === 'LLM Mesh')
      .map((page) => page.label);
    expect(llmMeshLabels).toEqual([
      'Usage Summary',
      'Usage Breakdown',
      'Reliability & Controls',
      'Activity Records',
    ]);
  });

  test('old adoption page is no longer registered', () => {
    const pages = build({ llmMeshEnabled: true });
    expect(pages.some((page) => page.key === 'org-llm-mesh')).toBe(false);
    expect(pages.some((page) => page.key === 'organization-llm-mesh-adoption')).toBe(false);
  });

  test('administration duckdb pages are not filtered by debug capability', () => {
    const pages = build();
    expect(pages.some((page) => page.key === 'admin-debug-reload')).toBe(true);
    expect(pages.some((page) => page.key === 'admin-debug-preview')).toBe(true);
  });

  test('user activity pages disappear when capability disabled', () => {
    const pages = build({ userActivityEnabled: false });
    expect(pages.some((page) => page.key === 'org-users')).toBe(false);
    expect(pages.some((page) => page.key === 'org-users-licenses')).toBe(false);
  });

  test('all visible pages pass capability checks in authorized context', () => {
    const pages = build();
    const capabilities = { llmMeshEnabled: true, userActivityEnabled: true, startupFlagsLoaded: true };
    expect(pages.every((page) => checkPageCapability(page, capabilities))).toBe(true);
  });

  test('internal preview user behaves like authorized administration context when permissions allow it', () => {
    const pages = build();
    const previewPermissions = { self: true, organization: true, administration: true };
    expect(pages.every((page) => checkPagePermission(page, previewPermissions))).toBe(true);
  });

  test('my information assets stays local and does not imply pulse home active', () => {
    const pages = build();
    const assets = pages.find((page) => page.key === 'my-assets');
    const pulseHome = pages.find((page) => page.key === 'pulse-home');
    expect(assets.active({ workspace: 'me', myPage: 'my-assets', adminPage: 'administration-overview', route: '' })).toBe(true);
    expect(pulseHome.active({ workspace: 'me', myPage: 'my-assets', adminPage: 'administration-overview', route: '' })).toBe(false);
  });

  test('pulse home uses explicit home route', () => {
    const pages = build();
    const pulseHome = pages.find((page) => page.key === 'pulse-home');
    expect(pulseHome.route).toBe('home');
    expect(pulseHome.href).toBe('./#home');
  });

  test('global pulse pages retain hash hrefs for progressive enhancement', () => {
    const pages = build();
    expect(pages.find((page) => page.key === 'pulse-home')?.href).toBe('./#home');
    expect(pages.find((page) => page.key === 'pulse-faq')?.href).toBe('./#faq');
    expect(pages.find((page) => page.key === 'pulse-disclaimer')?.href).toBe('./#disclaimer');
    expect(pages.find((page) => page.key === 'pulse-export')?.href).toBe('./#export');
  });

  test('administration overview remains a local administration page', () => {
    const pages = build();
    const adminOverview = pages.find((page) => page.key === 'admin-overview');
    expect(adminOverview.localPage).toBe('administration-overview');
    expect(adminOverview.workspace).toBe('administration');
    expect(adminOverview.permission).toBe('administration');
  });

  test('global pulse pages are registry-owned and preserve workspace externally', () => {
    const pages = build();
    const globals = pages.filter((page) => page.workspace === 'global').map((page) => page.key);
    expect(globals).toEqual(expect.arrayContaining(['pulse-home', 'pulse-faq', 'pulse-disclaimer']));
    expect(globals).not.toContain('pulse-export');
  });

  test('export does not appear under my information or administration workspaces', () => {
    const pages = build();
    expect(pages.some((page) => page.key === 'pulse-export' && page.workspace === 'me')).toBe(false);
    expect(pages.some((page) => page.key === 'pulse-export' && page.workspace === 'administration')).toBe(false);
  });

  test('shared Pulse group excludes export outside organization workspace', () => {
    const pages = build();
    const selfOnly = { self: true, organization: false, administration: false };
    const adminOnly = { self: true, organization: false, administration: true };
    expect(buildSharedPulseItems(pages, selfOnly, 'me')).toEqual(['pulse-home', 'pulse-faq', 'pulse-disclaimer']);
    expect(buildSharedPulseItems(pages, adminOnly, 'administration')).toEqual(['pulse-home', 'pulse-faq', 'pulse-disclaimer']);
  });

  test('shared Pulse group keeps home faq and disclaimer visible across workspaces', () => {
    const pages = build();
    const orgItems = buildSharedPulseItems(pages, { self: true, organization: true, administration: false }, 'organization');
    const meItems = buildSharedPulseItems(pages, { self: true, organization: false, administration: false }, 'me');
    const adminItems = buildSharedPulseItems(pages, { self: true, organization: true, administration: true }, 'administration');
    expect(orgItems).toEqual(expect.arrayContaining(['pulse-home', 'pulse-faq', 'pulse-disclaimer']));
    expect(meItems).toEqual(['pulse-home', 'pulse-faq', 'pulse-disclaimer']);
    expect(adminItems).toEqual(['pulse-home', 'pulse-faq', 'pulse-disclaimer']);
  });

  test('pulse faq registry entry resolves as a global page with faq route', () => {
    const pages = build();
    const faq = pages.find((page) => page.key === 'pulse-faq');
    expect(faq).toMatchObject({ key: 'pulse-faq', workspace: 'global', route: 'faq', group: 'Pulse' });
  });

  test('manual group selection stays available across groups', () => {
    const pages = build();
    const myGroups = [...new Set(pages.filter((page) => page.workspace === 'me').map((page) => page.group))];
    expect(myGroups).toEqual(expect.arrayContaining(['My Information', 'My Product Lifecycle']));
    const globalGroups = [...new Set(pages.filter((page) => page.workspace === 'global').map((page) => page.group))];
    expect(globalGroups).toEqual(['Pulse']);
  });

  test('pulse remains a valid rail group in every workspace', () => {
    const pages = build();
    const workspaceGroups = (workspace) => [...new Set(pages.filter((page) => page.workspace === workspace).map((page) => page.group))];
    expect(['Pulse', ...workspaceGroups('me')]).toContain('Pulse');
    expect(['Pulse', ...workspaceGroups('organization')]).toContain('Pulse');
    expect(['Pulse', ...workspaceGroups('administration')]).toContain('Pulse');
  });

  test('workspace defaults are fixed and repeatable', () => {
    const pages = build();
    expect(pages.find((page) => page.key === 'my-overview')?.group).toBe('My Information');
    expect(pages.find((page) => page.key === 'org-users')?.route).toBe('users');
    expect(pages.find((page) => page.key === 'admin-overview')?.group).toBe('Administration');
  });

  test('unknown hash falls back to workspace defaults deterministically', () => {
    const pages = build();
    const defaults = pages.filter((page) => page.isDefault).map((page) => page.key);
    expect(defaults).toEqual(expect.arrayContaining(['pulse-home', 'my-overview', 'org-users', 'admin-overview']));
  });
});

describe('Organization LLM Mesh placeholder pages', () => {
  const originalLocation = window.location;
  const originalFetch = global.fetch;

  beforeEach(() => {
    delete window.location;
    window.location = { hash: '#llm-mesh/usage-summary' };
    global.fetch = jest.fn((url) => {
      const target = String(url);
      if (target.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            authenticated: true,
            user: { login: 'tester', displayName: 'Tester' },
            permissions: { self: true, organization: true, administration: false },
          }),
        });
      }
      if (target.includes('/api/startup/flags')) {
        return Promise.resolve({ ok: true, json: async () => ({ capabilities: { advancedLLMMesh: { enabled: true, licensedInstances: ['instance-a'] } } }) });
      }
      if (target.includes('/api/debug/startup-flags')) {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true, flags: {} }) });
      }
      if (target.includes('/api/home')) {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
    });
    window.dataiku = {
      getWebAppBackendUrl: () => '',
      getUserDisplayName: () => 'Tester',
    };
  });

  afterEach(() => {
    window.location = originalLocation;
    global.fetch = originalFetch;
    delete window.dataiku;
  });

  test('Usage Summary placeholder renders Coming Soon and approved description', async () => {
    render(<App />);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Authentication Required' })).not.toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: 'Usage Summary' })).toBeInTheDocument();
    expect(screen.getByText('Coming Soon')).toBeInTheDocument();
    expect(screen.getAllByText('A cross-instance summary of LLM Mesh consumption, including requests, tokens, estimated cost, active users, active projects, active instances, models, providers, and connections.').length).toBeGreaterThan(0);
    expect(global.fetch).not.toHaveBeenCalledWith(expect.stringContaining('/api/build/llm-mesh/overview'), expect.anything());
  });

  test('Usage Breakdown placeholder renders approved description', async () => {
    window.location.hash = '#llm-mesh/usage-breakdown';
    render(<App />);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Authentication Required' })).not.toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: 'Usage Breakdown' })).toBeInTheDocument();
    expect(screen.getByText('Coming Soon')).toBeInTheDocument();
    expect(screen.getAllByText('A detailed view of where LLM Mesh usage is occurring, broken down by instance, project, user, connection, provider, and model.').length).toBeGreaterThan(0);
  });

  test('Reliability & Controls placeholder renders approved description', async () => {
    window.location.hash = '#llm-mesh/reliability-controls';
    render(<App />);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Authentication Required' })).not.toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: 'Reliability & Controls' })).toBeInTheDocument();
    expect(screen.getByText('Coming Soon')).toBeInTheDocument();
    expect(screen.getAllByText('A factual summary of LLM Mesh operational behavior, including latency, errors, throttling, quota usage, rate-limit usage, and guardrail outcomes.').length).toBeGreaterThan(0);
  });

  test('Activity Records placeholder renders approved description', async () => {
    window.location.hash = '#llm-mesh/activity-records';
    render(<App />);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Authentication Required' })).not.toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: 'Activity Records' })).toBeInTheDocument();
    expect(screen.getByText('Coming Soon')).toBeInTheDocument();
    expect(screen.getAllByText('A searchable detailed record of LLM Mesh activity across instances, projects, users, connections, providers, models, dates, and available consumption metrics.').length).toBeGreaterThan(0);
  });

  test('old adoption route does not render Adoption content or trigger overview fetch', async () => {
    window.location.hash = '#llm-mesh';
    render(<App />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByText('Adoption')).not.toBeInTheDocument();
    expect(global.fetch.mock.calls.some(([url]) => String(url).includes('/api/build/llm-mesh/overview'))).toBe(false);
  });
});

describe('Pulse export rendered navigation', () => {
  const originalLocation = window.location;
  const originalFetch = global.fetch;

  function mockAuth({ permissions, hash = '#export' }) {
    delete window.location;
    window.location = { hash };
    global.fetch = jest.fn((url) => {
      const target = String(url);
      if (target.includes('/api/me')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            authenticated: true,
            user: { login: 'tester', displayName: 'Tester' },
            permissions,
          }),
        });
      }
      if (target.includes('/api/startup/flags')) {
        return Promise.resolve({ ok: true, json: async () => ({ capabilities: { advancedLLMMesh: { enabled: true, licensedInstances: ['instance-a'] } } }) });
      }
      if (target.includes('/api/debug/startup-flags')) {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true, flags: {} }) });
      }
      if (target.includes('/api/home')) {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
    });
    window.dataiku = {
      getWebAppBackendUrl: () => '',
      getUserDisplayName: () => 'Tester',
    };
  }

  afterEach(() => {
    window.location = originalLocation;
    global.fetch = originalFetch;
    delete window.dataiku;
  });

  test('authorized organization user sees export exactly once in rendered navigation', async () => {
    mockAuth({ permissions: { self: true, organization: true, administration: false } });
    render(<App />);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Authentication Required' })).not.toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: 'Export' })).toBeInTheDocument();
    expect(screen.getAllByText('Export')).toHaveLength(1);
    expect(screen.queryByText('Administration')).not.toBeInTheDocument();
  });

  test('self-only user direct export navigation is blocked and nav does not show export', async () => {
    mockAuth({ permissions: { self: true, organization: false, administration: false } });
    render(<App />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByRole('heading', { name: 'Export' })).not.toBeInTheDocument();
    expect(screen.queryByText('Select filters and sections, then generate a downloadable PDF report')).not.toBeInTheDocument();
    expect(screen.queryByText('Export')).not.toBeInTheDocument();
  });

  test('administration workspace does not show export in rendered navigation without organization access', async () => {
    mockAuth({ permissions: { self: true, organization: false, administration: true }, hash: '' });
    render(<App />);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByText('Export')).not.toBeInTheDocument();
  });
});
