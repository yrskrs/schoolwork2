/* Browser regression checks; run through test_redesign_ui.py for isolated data. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const {chromium} = require(config.playwright);

(async () => {
  const browser = await chromium.launch({executablePath: config.chrome, headless: true, args: ['--no-sandbox']});
  const results = [], errors = [], badResponses = [];
  try {
    const context = await browser.newContext({baseURL: config.baseURL});
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 400) badResponses.push(`${response.status()} ${response.url()}`); });
    await context.addInitScript(() => {
      localStorage.setItem('sn-simplified-site', 'off');
      localStorage.setItem('sn-theme', 'light');
    });
    async function visit(url) {
      const response = await page.goto(url);
      assert.equal(response.status(), 200, url);
      await page.evaluate(() => document.fonts.ready);
    }
    async function layout(label, theme, width, scale) {
      await page.setViewportSize({width, height: 900});
      await page.evaluate(({theme, scale}) => {
        document.documentElement.dataset.theme = theme;
        window.applyFontScale(scale);
      }, {theme, scale});
      await page.waitForTimeout(70);
      const measurement = await page.evaluate(() => {
        const root = document.documentElement;
        const overflowing = [...document.querySelectorAll('main h1, main .btn, main input:not([type="hidden"]), main select')]
          .filter(el => el.getClientRects().length && !el.closest('dialog:not([open]), [style*="display:none"], [style*="display: none"]'))
          .filter(el => {const r = el.getBoundingClientRect(); return r.width > 0 && (r.left < -2 || r.right > innerWidth + 2);})
          .map(el => el.id || el.className).slice(0, 10);
        return {viewport: innerWidth, pageWidth: root.scrollWidth, overflowing,
                dom: document.querySelectorAll('*').length};
      });
      assert(measurement.pageWidth <= width + 2, `${label} ${theme} ${width}/${scale} page overflow: ${JSON.stringify(measurement)}`);
      assert.deepEqual(measurement.overflowing, [], `${label} ${width}/${scale} visible controls overflow`);
      results.push({label, theme, width, scale, ...measurement});
      if ((width === 1366 && scale === 100) || (width === 375 && scale === 125))
        await page.screenshot({path: path.join(config.artifacts, `${label}-${width}-${theme}-${scale}.png`), fullPage: true});
    }
    const studentPages = [['home', `/?class=${config.classId}`],
      ['assignment', `/assignment/${config.assignment}/?class=${config.classId}`],
      ['submission', `/assignment/${config.assignment}/submit/?class=${config.classId}`],
      ['my-work', '/my-submissions/']];
    // Resolve the portal URL from the rendered navigation, so URL changes remain valid.
    await visit('/');
    studentPages[3][1] = await page.locator('a[title="Мої здані роботи"]').getAttribute('href');
    for (const [label, url] of studentPages) {
      await visit(url);
      for (const theme of ['light', 'dark'])
        for (const width of [320, 375, 640, 1024, 1366])
          for (const scale of [100, 125]) await layout(label, theme, width, scale);
    }
    await page.setViewportSize({width: 375, height: 900});
    await visit(`/?class=${config.classId}`);
    await page.selectOption('#school-class-select', String(config.otherClassId));
    await page.waitForFunction(id => new URL(location.href).searchParams.get('class') === String(id), config.otherClassId);
    await page.waitForFunction(() => !document.querySelector('#feed-container')?.classList.contains('loading'));
    await page.locator('.school-more > summary').click();
    await page.locator('#simplified-site-toggle').click();
    assert(await page.locator('html').evaluate(el => el.classList.contains('sn-lite')));
    await page.locator('#simplified-site-toggle').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.school-more').getAttribute('open'), null);

    await visit(`/assignment/${config.assignment}/?class=${config.classId}`);
    await page.locator('.assignment-detail-sticky-tools a[href="#assignment-materials-section"]').click();
    await page.waitForTimeout(500);
    const offsets = await page.evaluate(() => ({
      bottom: document.querySelector('.assignment-detail-sticky-tools').getBoundingClientRect().bottom,
      top: document.querySelector('#assignment-materials-section').getBoundingClientRect().top}));
    assert(offsets.top >= offsets.bottom - 2, `Sticky toolbar hides section: ${JSON.stringify(offsets)}`);
    const href = await page.locator('#assignment-materials-section a[href*="download"]').first().getAttribute('href');
    assert((await context.request.get(href)).ok(), 'Teacher material download');
    await page.locator('.assignment-detail-sticky-tools a[href*="/submit/"]').click();
    await page.waitForURL('**/submit/**');
    await page.locator('#student-roster-select').waitFor({state: 'visible'});
    await page.locator('#submission-send-button').click();
    assert(await page.locator('#form-validation-alert').isVisible(), 'Empty form warning');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'student-roster-select');
    await page.selectOption('#student-roster-select', String(config.studentId));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
    await page.setInputFiles('#id_files', {name: 'browser-answer.txt', mimeType: 'text/plain', buffer: Buffer.from('Виконана тестова робота')});
    await page.locator('#submission-send-button').click();
    await page.waitForURL('**/submit/success/**');
    assert.equal(await page.locator('.submission-page').count(), 0);
    await cdp.send('Emulation.setCPUThrottlingRate', {rate: 1});
    results.push({label: 'real-file-upload', ok: true, cpuThrottle: 4});

    await page.route('**/__redesign_ai__', route => route.fulfill({status: 202, json: {
      status: 'running', status_url: '/__redesign_ai_done__', events: [{sequence: 1, kind: 'model_start', message: 'Тест'}]}}));
    await page.route('**/__redesign_ai_done__', route => route.fulfill({status: 200, json: {status: 'success', success: true}}));
    await page.evaluate(() => {
      const anchor = document.createElement('div'); anchor.id = 'test-ai-anchor'; document.querySelector('main').append(anchor);
      window.__aiDone = SchoolNetAI.fetch('/__redesign_ai__', {}, {anchor, isStudent: true}).then(() => true);
    });
    await page.locator('.school-animation-toggle').click();
    assert.equal(await page.locator('.ai-kid-panel').last().getAttribute('data-animation-paused'), 'true');
    await page.evaluate(() => SchoolNetPerformance.setSimplified(true));
    assert.equal(await page.locator('.ai-kid-panel .ai-robot-eyes').last().evaluate(el => getComputedStyle(el).animationName), 'none');
    await page.evaluate(() => window.__aiDone);
    results.push({label: 'ai-progress-pause-lite-completion', ok: true});

    await visit('/teacher/login/');
    await page.fill('#id_username', 'redesign-test');
    await page.fill('#id_password', 'local-browser-test-only');
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL('**/teacher/**');
    assert(!page.url().includes('/login/'), 'Teacher login');
    for (const [label, url] of [['teacher', '/teacher/'], ['editor', '/teacher/assignment/create/'],
      ['settings', '/teacher/settings/'], ['gradebook', '/teacher/gradebook/']]) {
      // Native navigation links are authoritative for routes outside the dashboard.
      const names = {editor: 'Створити', gradebook: 'Журнал'};
      let target = url;
      if (names[label]) {
        const link = page.locator('a').filter({hasText: names[label]}).first();
        if (await link.count()) target = await link.getAttribute('href');
      }
      await visit(target);
      for (const theme of ['light', 'dark'])
        for (const width of [320, 375, 640, 1024, 1366])
          for (const scale of [100, 125]) await layout(label, theme, width, scale);
      if (label === 'teacher') {
        const menu = page.locator('.school-card-menu').first();
        await menu.locator('summary').click();
        assert(await menu.locator('.school-card-menu-panel').isVisible());
        await page.keyboard.press('Escape');
        assert.equal(await menu.getAttribute('open'), null);
      }
    }
    assert.deepEqual(errors, [], 'Browser JavaScript errors');
    assert.deepEqual(badResponses, [], 'Failed browser requests');
    fs.writeFileSync(path.join(config.artifacts, 'results.json'), JSON.stringify({ok: true, results, errors, badResponses}, null, 2));
    console.log(`Browser: ${results.length} checks passed; no JavaScript errors or failed requests.`);
  } catch (error) {
    fs.writeFileSync(path.join(config.artifacts, 'failure.json'), JSON.stringify({message: error.message, errors, badResponses, completed: results.length}, null, 2));
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
