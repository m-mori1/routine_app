const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.resolve(__dirname, '../../index.html'), 'utf8');
const parent = {
  task_no: 123, title: 'Parent attachment test', frequency: '\u6708\u6b21',
  start_month: '2026-10', end_month: '2026-12', assignee: 'tester', assignees: ['tester'],
  registrant: 'tester', task_kind: '\u500b\u4eba', week_num: 1, summary: 'memo',
};

async function setup(page, notification = { status: 'partial' }) {
  const mutations = [];
  let attachments = [{ attachment_id: 7, filename: '\u8cc7\u6599.pdf', file_size: 1024 }];
  page.on('dialog', dialog => dialog.accept());
  await page.route('http://routine.test/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.split('api.py/')[1];
    if (!endpoint) return route.fulfill({ contentType: 'text/html', body: html });
    if (request.method() !== 'GET') {
      mutations.push({ method: request.method(), endpoint, body: request.postData() });
      if (request.method() === 'DELETE') {
        attachments = [];
        return route.fulfill({ status: 204 });
      }
      if (request.method() === 'PATCH') return route.fulfill({ status: 204 });
      return route.fulfill({ status: 201, json: { task_no: 456, google_chat: notification } });
    }
    if (endpoint === 'current-user') return route.fulfill({ json: {
      employee_name: 'tester', department_cd: 'D000013', department_name: 'Test',
    } });
    if (endpoint === 'employees') return route.fulfill({ json: {
      employees: [{ EmployeeName: 'tester', DepartmentCD: 'D000013' }],
    } });
    if (endpoint === 'parents') return route.fulfill({ json: { parents: [parent], pagination: {} } });
    if (endpoint === 'routines') return route.fulfill({ json: { routines: [], pagination: {} } });
    if (endpoint.endsWith('/attachments')) return route.fulfill({ json: { attachments } });
    return route.fulfill({ status: 404, json: { message: 'not found' } });
  });
  await page.goto('http://routine.test/routine_app/#parents');
  await expect(page.locator('.parent-edit-trigger')).toBeVisible();
  return mutations;
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`parent attachments can be removed and saved at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const mutations = await setup(page);
    await page.locator('.parent-edit-trigger').click();
    await expect(page.locator('#parent-attachment-list a')).toHaveText('\u8cc7\u6599.pdf');
    await expect(page.locator('#parent-attachment-list a')).toHaveAttribute('href', /parent\/123\/attachments\/7$/);
    await page.locator('#parent-attachment-list button').click();
    await expect(page.locator('#parent-attachment-list li')).toHaveCount(0);
    const longName = 'long-attachment-file-name-with-more-than-sixty-characters-for-responsive-layout.pdf';
    await page.locator('#parent-attachment-input').setInputFiles({ name: longName, mimeType: 'application/pdf', buffer: Buffer.from('binary-content') });
    await expect(page.locator('#parent-attachment-list li')).toHaveCount(1);
    await page.locator('.attachment-section').scrollIntoViewIfNeeded();
    const fits = await page.locator('.attachment-section').evaluate(el => el.scrollWidth <= el.clientWidth);
    expect(fits).toBe(true);
    expect(await page.locator('#creation-overlay .creation-panel').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`attachments-${viewport.width}.png`), fullPage: true });
    await page.locator('#routine-form [type=submit]').click();
    await expect(page.locator('#creation-overlay')).toHaveClass(/hidden/);
    expect(mutations.map(item => item.method)).toEqual(['DELETE', 'PATCH']);
    expect(mutations[1].body).toContain('name="attachments"');
    expect(mutations[1].body).toContain(longName);
    await page.locator('#open-create-btn').click();
    await expect(page.locator('#parent-attachment-list li')).toHaveCount(0);
  });
}

test('creation reports notification failures without asking to recreate the task', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const mutations = await setup(page);
  await page.locator('#open-create-btn').click();
  await page.locator('#routine-form [name=title]').fill('new task');
  await page.locator('#routine-form [name=frequency]').selectOption('\u30b9\u30dd\u30c3\u30c8');
  await page.locator('#routine-form [name=due_date]').fill('2026-10-16');
  await page.locator('#parent-attachment-input').setInputFiles({ name: '\u8cc7\u6599.txt', mimeType: 'text/plain', buffer: Buffer.from('memo') });
  await page.locator('#routine-form [type=submit]').click();
  await expect(page.locator('#creation-overlay')).toHaveClass(/hidden/);
  await expect(page.locator('#action-message')).toHaveClass('warning');
  await expect(page.locator('#action-message')).toContainText('\u767b\u9332\u3057\u307e\u3057\u305f');
  expect(mutations).toHaveLength(1);
  expect(mutations[0].method).toBe('POST');
  expect(errors).toEqual([]);
});

for (const reason of ['sender_is_recipient', 'bot_not_installed', 'bot_not_configured']) {
  test(`creation displays the concrete notification failure: ${reason}`, async ({ page }) => {
    await setup(page, { status: 'failed', failures: [{ reason }] });
    await page.locator('#open-create-btn').click();
    await page.locator('#routine-form [name=title]').fill('new task');
    await page.locator('#routine-form [name=frequency]').selectOption('\u30b9\u30dd\u30c3\u30c8');
    await page.locator('#routine-form [name=due_date]').fill('2026-10-16');
    await page.locator('#routine-form [type=submit]').click();
    await expect(page.locator('#creation-overlay')).toHaveClass(/hidden/);
    await expect(page.locator('#action-message')).toContainText(reason === 'sender_is_recipient'
      ? '\u9001\u4fe1\u30a2\u30ab\u30a6\u30f3\u30c8\u672c\u4eba'
      : reason === 'bot_not_configured'
        ? 'Google Chat\u901a\u77e5\u30a2\u30d7\u30ea\u304c\u672a\u8a2d\u5b9a'
        : '\u30dc\u30c3\u30c8\u304c\u672a\u5c0e\u5165');
  });
}

for (const [status, posted, expected] of [
  ['sent', true, '\u30e1\u30f3\u30b7\u30e7\u30f3\u4ed8\u304d\u901a\u77e5'],
  ['partial', true, '\u4e00\u90e8\u62c5\u5f53\u8005\u3092\u30e1\u30f3\u30b7\u30e7\u30f3\u3067\u304d\u307e\u305b\u3093'],
  ['failed', false, '\u9001\u4fe1\u5148\u8a2d\u5b9a\u304c\u4e0d\u6b63'],
  ['skipped', false, '\u901a\u77e5\u5bfe\u8c61\u306e\u62c5\u5f53\u8005\u304c\u3044\u306a\u3044'],
]) {
  test(`space notification result: ${status}`, async ({ page }) => {
    const mutations = await setup(page, { status, delivery: 'space', posted,
      failures: status === 'failed' ? [{ reason: 'webhook_invalid' }] : [] });
    await page.locator('#open-create-btn').click();
    await page.locator('#routine-form [name=title]').fill('space notification');
    await page.locator('#routine-form [name=frequency]').selectOption('\u30b9\u30dd\u30c3\u30c8');
    await page.locator('#routine-form [name=due_date]').fill('2026-10-16');
    await page.locator('#routine-form [type=submit]').click();
    await expect(page.locator('#creation-overlay')).toHaveClass(/hidden/);
    await expect(page.locator('#action-message')).toContainText(expected);
    await expect(page.locator('#action-message')).not.toContainText('DM');
    if (['sent', 'skipped'].includes(status)) {
      await expect(page.locator('#action-message')).not.toHaveClass(/warning/);
    } else {
      await expect(page.locator('#action-message')).toHaveClass('warning');
    }
    expect(mutations).toHaveLength(1);
  });
}
