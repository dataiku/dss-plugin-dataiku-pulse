import { buildPageRegistry, checkPageCapability, checkPagePermission, validatePageRegistry } from './App';

describe('Pulse page registry', () => {
  const build = (overrides = {}) => buildPageRegistry({
    homeHref: './',
    userActivityEnabled: true,
    llmMeshEnabled: true,
    debugEnabled: true,
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

  test('administration user can access all permission-gated pages', () => {
    const pages = build();
    const permissions = { self: true, organization: true, administration: true };
    expect(pages.every((page) => checkPagePermission(page, permissions))).toBe(true);
  });

  test('llm mesh pages disappear when capability disabled', () => {
    const pages = build({ llmMeshEnabled: false });
    expect(pages.some((page) => page.key === 'my-llm-overview')).toBe(false);
    expect(pages.some((page) => page.key === 'org-llm-mesh')).toBe(false);
  });

  test('debug pages disappear when debug disabled', () => {
    const pages = build({ debugEnabled: false });
    expect(pages.some((page) => page.key === 'admin-debug-reload')).toBe(false);
    expect(pages.some((page) => page.key === 'admin-debug-preview')).toBe(false);
  });

  test('user activity pages disappear when capability disabled', () => {
    const pages = build({ userActivityEnabled: false });
    expect(pages.some((page) => page.key === 'org-users')).toBe(false);
    expect(pages.some((page) => page.key === 'org-users-licenses')).toBe(false);
  });

  test('all visible pages pass capability checks in authorized context', () => {
    const pages = build();
    const capabilities = { llmMeshEnabled: true, userActivityEnabled: true, debugEnabled: true, startupFlagsLoaded: true };
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
    expect(globals).toEqual(expect.arrayContaining(['pulse-home', 'pulse-faq', 'pulse-disclaimer', 'pulse-export']));
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
