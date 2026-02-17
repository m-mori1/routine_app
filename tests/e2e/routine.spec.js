const { test, expect } = require('@playwright/test');

const BASE_URL = process.env.ROUTINE_BASE_URL || 'https://mercury/routine_app/';
const SESSION_COOKIE = (process.env.ROUTINE_SESSION_COOKIE || '').trim();

const FREQUENCY_INDEX = {
  spot: 1,
  weekly: 2,
  monthly: 3,
  quarterly: 4,
  yearly: 6,
};

const CASES = [
  { id: 'O-01', freq: 'monthly', kind: 'individual', week: '1', start: '2026-02', end: '2026-02' },
  { id: 'O-02', freq: 'monthly', kind: 'group',      week: '4', start: '2026-02', end: '2026-04' },
  { id: 'O-03', freq: 'quarterly', kind: 'individual', week: '1', start: '2026-02', end: '2026-04' },
  { id: 'O-04', freq: 'quarterly', kind: 'group',      week: '4', start: '2026-02', end: '2026-04' },
  { id: 'O-05', freq: 'monthly', kind: 'individual', week: '4', start: '2026-02', end: '2026-04' },
  { id: 'O-06', freq: 'monthly', kind: 'group',      week: '1', start: '2026-02', end: '2026-02' },
  { id: 'O-07', freq: 'spot', kind: 'individual', due: '2026-02-20' },
  { id: 'O-08', freq: 'spot', kind: 'group',      due: '2026-02-27' },
  { id: 'O-09', freq: 'quarterly', kind: 'individual', week: '4', start: '2026-02', end: '2026-04' },
  { id: 'O-10', freq: 'quarterly', kind: 'group',      week: '1', start: '2026-02', end: '2026-04' },
  { id: 'O-11', freq: 'monthly', kind: 'individual', week: '1', start: '2026-02', end: '2026-04' },
  { id: 'O-12', freq: 'monthly', kind: 'group',      week: '4', start: '2026-02', end: '2026-02' },
  { id: 'O-13', freq: 'weekly', kind: 'individual', week: '1', start: '2026-02', end: '2027-01' },
  { id: 'O-14', freq: 'yearly', kind: 'group',      week: '4', start: '2026-02', end: '2027-12' },
];

function nowStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

function randomToken(len = 4) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let out = '';
  for (let i = 0; i < len; i += 1) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

function sample(list, n) {
  const arr = [...list];
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr.slice(0, n);
}

function takeSequential(state, n) {
  if (!Array.isArray(state.pool) || state.pool.length === 0) {
    throw new Error('assignee pool is empty');
  }
  if (state.cursor == null) state.cursor = 0;
  if (state.cursor + n > state.pool.length) {
    state.pool = sample(state.pool, state.pool.length);
    state.cursor = 0;
  }
  const out = state.pool.slice(state.cursor, state.cursor + n);
  state.cursor += n;
  return out;
}

async function setInputValueWithEvents(locator, value) {
  await locator.evaluate((el, v) => {
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }, value);
}

async function ensureLoggedIn(page, context) {
  test.skip(!SESSION_COOKIE, 'ROUTINE_SESSION_COOKIE is not set');
  await context.addCookies([
    {
      name: 'session',
      value: SESSION_COOKIE,
      domain: 'mercury',
      path: '/',
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    },
  ]);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  if (page.url().includes('login.microsoftonline.com')) {
    throw new Error('Session cookie is invalid/expired (redirected to Microsoft login).');
  }
  const openCreateBtn = page.locator('#open-create-btn');
  await expect(openCreateBtn).toBeAttached({ timeout: 30000 });
}

async function clearAssignees(page) {
  const chips = page.locator('#creation-assignee-list [data-remove-assignee]');
  while ((await chips.count()) > 0) {
    await chips.first().click();
  }
}

async function pickAssignees(page, count, state) {
  const assigneeSelect = page.locator('#creation-assignee-select');
  const assigneeHidden = page.locator('#routine-form [name="assignee"]');
  await expect(assigneeSelect).toBeAttached({ timeout: 30000 });

  if (!Array.isArray(state.pool) || state.pool.length === 0) {
    const candidates = await assigneeSelect.locator('option').evaluateAll((opts) =>
      opts
        .slice(2)
        .map((o) => (o.value || '').trim())
        .filter((v) => v && v !== '社員全員')
    );
    if (candidates.length < count) {
      throw new Error(`Not enough assignee candidates. need=${count}, got=${candidates.length}`);
    }
    state.pool = sample(candidates, candidates.length);
    state.cursor = 0;
    state.used = new Set();
  }

  await clearAssignees(page);
  const picked = takeSequential(state, count);
  for (const name of picked) {
    await assigneeSelect.selectOption(name);
    state.used?.add(name);
  }
  for (const name of picked) {
    await expect.poll(async () => assigneeHidden.inputValue(), { timeout: 10000 }).toContain(name);
  }
  return picked;
}

async function openCreateForm(page) {
  const openCreateBtn = page.locator('#open-create-btn');
  if (await openCreateBtn.isVisible()) {
    await openCreateBtn.click({ timeout: 10000 });
  } else {
    await page.evaluate(() => document.getElementById('open-create-btn')?.click());
  }
  await expect(page.locator('#creation-overlay')).not.toHaveClass(/hidden/, { timeout: 10000 });
}

async function switchScreen(page, name) {
  const tab = page.locator(`[data-screen="${name}"]`);
  await expect(tab).toBeVisible({ timeout: 10000 });
  if (!(await tab.isDisabled())) {
    await tab.click({ timeout: 10000 });
  }
}

async function findRoutineRowByTaskNo(page, taskNoText) {
  await page.evaluate(() => {
    const year = document.querySelector('[name="filter-routine-year"]');
    const month = document.querySelector('[name="filter-routine-month"]');
    const assignee = document.querySelector('[name="filter-routine-assignee"]');
    [year, month, assignee].forEach((el) => {
      if (!el) return;
      el.value = '';
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });
  const filterBtn = page.locator('#routine-filter-btn');
  if (await filterBtn.count()) {
    await filterBtn.click({ timeout: 10000 });
  }

  const prevBtn = page.locator('#routine-page-prev');
  const nextBtn = page.locator('#routine-page-next');

  // Move to first page
  for (let i = 0; i < 30; i += 1) {
    if (await prevBtn.isDisabled()) break;
    await prevBtn.click({ timeout: 10000 });
    await page.waitForTimeout(150);
  }

  for (let i = 0; i < 60; i += 1) {
    const row = page.locator('#routine-table-body tr', { hasText: taskNoText }).first();
    if (await row.count()) {
      await expect(row).toBeVisible({ timeout: 10000 });
      return row;
    }
    if (await nextBtn.isDisabled()) break;
    await nextBtn.click({ timeout: 10000 });
    await page.waitForTimeout(150);
  }

  throw new Error(`routine row not found by task_no=${taskNoText}`);
}

async function runCase(page, c, assigneeState) {
  const title = `PW_E2E_${c.id}_${nowStamp()}_${randomToken(4)}`;
  const updatedTitle = `${title}_UPD`;
  const parentSummary = `parent update ${c.id}`;
  const routineSummary = `routine update ${c.id}`;

  await openCreateForm(page);
  await page.locator('#routine-form [name="title"]').fill(title);
  await page.locator('#routine-form [name="task_kind"]').selectOption({ index: c.kind === 'group' ? 1 : 0 });
  await page.locator('#routine-form [name="frequency"]').selectOption({ index: FREQUENCY_INDEX[c.freq] });

  if (c.freq === 'spot') {
    await setInputValueWithEvents(page.locator('#routine-form [name="due_date"]'), c.due);
  } else {
    await setInputValueWithEvents(page.locator('#routine-form [name="start_month"]'), c.start);
    await setInputValueWithEvents(page.locator('#routine-form [name="end_month"]'), c.end);
    const weekSelect = page.locator('#routine-form [name="week"]');
    if (await weekSelect.isEnabled()) {
      await weekSelect.selectOption(c.week);
    }
  }

  const picked = await pickAssignees(page, c.kind === 'group' ? 2 : 1, assigneeState);
  console.log(`[CASE ${c.id}] assignees=${picked.join('; ')}`);

  await page.locator('#routine-form button[type="submit"]').click({ timeout: 10000 });
  await expect(page.locator('#creation-overlay')).toHaveClass(/hidden/, { timeout: 30000 });

  await switchScreen(page, 'parents');
  const parentRow = page.locator('#parent-table-body tr', { hasText: title }).first();
  await expect(parentRow).toBeVisible({ timeout: 30000 });
  const taskNoText = (await parentRow.locator('button.parent-edit-trigger').innerText()).trim();

  await parentRow.locator('button.parent-edit-trigger').evaluate((el) => el.click());
  await expect(page.locator('#creation-overlay')).not.toHaveClass(/hidden/, { timeout: 10000 });
  await page.locator('#routine-form [name="title"]').fill(updatedTitle);
  await page.locator('#routine-form [name="summary"]').fill(parentSummary);
  await page.locator('#routine-form button[type="submit"]').click({ timeout: 10000 });
  await expect(page.locator('#creation-overlay')).toHaveClass(/hidden/, { timeout: 30000 });

  await switchScreen(page, 'routines');
  const routineRow = await findRoutineRowByTaskNo(page, taskNoText);

  await routineRow.click({ timeout: 10000 });
  await expect(page.locator('#routine-edit-overlay')).not.toHaveClass(/hidden/, { timeout: 10000 });
  await page.locator('#routine-edit-form [name="status"]').selectOption({ index: 1 });
  await page.locator('#routine-edit-form [name="summary"]').fill(routineSummary);
  await page.locator('#routine-edit-form button[type="submit"]').click({ timeout: 10000 });
  await expect(page.locator('#routine-edit-overlay')).toHaveClass(/hidden/, { timeout: 30000 });

  const updatedRoutineRow = page.locator('#routine-table-body tr', { hasText: routineSummary }).first();
  await expect(updatedRoutineRow).toBeVisible({ timeout: 30000 });
  await updatedRoutineRow.locator('.routine-complete-checkbox').check({ force: true, timeout: 10000 });
  await expect(page.locator('#routine-table-body tr', { hasText: routineSummary })).toHaveCount(0, { timeout: 30000 });

  await switchScreen(page, 'parents');
  const parentRows = page
    .locator('#parent-table-body tr')
    .filter({ has: page.locator(`button.parent-edit-trigger[data-task-no="${taskNoText}"]`) });
  const parentCount = await parentRows.count();
  if (parentCount > 0) {
    const finalParentRow = parentRows.first();
    await expect(finalParentRow).toBeVisible({ timeout: 30000 });
    await finalParentRow.locator('.parent-complete-checkbox').check({ force: true, timeout: 10000 });
    await expect(parentRows).toHaveCount(0, { timeout: 30000 });
  } else {
    console.log(`[CASE ${c.id}] parent auto-completed task_no=${taskNoText}`);
  }

  return { caseId: c.id, title: updatedTitle, taskNo: taskNoText, assignees: picked, result: 'PASS' };
}

test.describe('routine_app E2E', () => {
  test('O-01 to O-14 full run', async ({ page, context }) => {
    test.setTimeout(20 * 60 * 1000);

    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });

    await ensureLoggedIn(page, context);

    const results = [];
    const assigneeState = {};
    for (const c of CASES) {
      console.log(`[CASE ${c.id}] START`);
      const r = await runCase(page, c, assigneeState);
      console.log(`[CASE ${c.id}] PASS task_no=${r.taskNo} title=${r.title} assignees=${r.assignees.join('; ')}`);
      results.push(r);
    }

    const usedCount = assigneeState.used ? assigneeState.used.size : 0;
    const candidateCount = assigneeState.pool ? assigneeState.pool.length : 0;
    console.log(`[ASSIGNEE COVERAGE] used=${usedCount}/${candidateCount}`);
    expect(usedCount).toBe(candidateCount);

    const fs = require('fs');
    fs.writeFileSync('test-results/ortho-results.json', JSON.stringify(results, null, 2), 'utf-8');
    console.log('[DONE] all cases passed');
  });
});
