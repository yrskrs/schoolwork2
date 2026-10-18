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
      if (!localStorage.getItem('sn-simplified-site')) localStorage.setItem('sn-simplified-site', 'off');
      if (!localStorage.getItem('sn-theme')) localStorage.setItem('sn-theme', 'light');
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
          .map(el => ({id: el.id, className: el.className, text: el.textContent.trim().slice(0, 80),
                       left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right})).slice(0, 10);
        return {viewport: innerWidth, pageWidth: root.scrollWidth, overflowing,
                dom: document.querySelectorAll('*').length};
      });
      if (measurement.overflowing.length || measurement.pageWidth > width + 2)
        await page.screenshot({path: path.join(config.artifacts, 'failure-layout.png'), fullPage: true});
      assert(measurement.pageWidth <= width + 2, `${label} ${theme} ${width}/${scale} page overflow: ${JSON.stringify(measurement)}`);
      assert.deepEqual(measurement.overflowing, [], `${label} ${width}/${scale} visible controls overflow`);
      results.push({label, theme, width, scale, ...measurement});
      if (['teacher', 'settings'].includes(label)) {
        const gap = await page.evaluate(() => document.querySelector('.page-hero').getBoundingClientRect().top - document.querySelector('.navbar').getBoundingClientRect().bottom);
        assert(gap >= 0 && gap <= (width === 1366 && scale === 100 ? 24 : 180), `${label} excessive top gap: ${gap}`);
      }
      if (label === 'teacher' && width <= 640) {
        const height = await page.locator('.live-lesson-card').evaluate(el => el.getBoundingClientRect().height);
        assert(height <= 320, `Lesson widget has excessive blank space: ${height}`);
      }
      if ((width === 1366 && scale === 100) || (width === 375 && scale === 125)) {
        await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
        await page.screenshot({path: path.join(config.artifacts, `${label}-${width}-${theme}-${scale}.png`), fullPage: true});
      }
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
    await page.setViewportSize({width: 1366, height: 900});
    await visit('/');
    await page.evaluate(() => applyFontScale(100));
    const classBounds = await page.evaluate(() => {
      const all = document.querySelector('#filter-class-all a').getBoundingClientRect();
      const items = [...document.querySelectorAll('.sidebar-class-grid .class-grid-item')].map(el => el.getBoundingClientRect());
      return {left: all.left, right: all.right, gridLeft: Math.min(...items.map(r => r.left)), gridRight: Math.max(...items.map(r => r.right))};
    });
    assert(Math.abs(classBounds.left - classBounds.gridLeft) <= 1 && Math.abs(classBounds.right - classBounds.gridRight) <= 1, `All classes alignment: ${JSON.stringify(classBounds)}`);
    await page.setViewportSize({width: 375, height: 900});
    await visit(`/?class=${config.classId}`);
    await page.selectOption('#school-class-select', String(config.otherClassId));
    await page.waitForFunction(id => new URL(location.href).searchParams.get('class') === String(id), config.otherClassId);
    await page.waitForFunction(() => !document.querySelector('#feed-container')?.classList.contains('loading'));
    await page.locator('.school-more > summary').click();
    assert(await page.locator('#theme-toggle-btn').evaluate(el => Boolean(el.closest('.school-more-panel'))));
    const previousTheme = await page.locator('html').getAttribute('data-theme');
    await page.locator('#theme-toggle-btn').click();
    assert.notEqual(await page.locator('html').getAttribute('data-theme'), previousTheme);
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
    assert(await page.locator('#today-schedule-nav-btn').isVisible());
    assert(await page.locator('#today-schedule-nav-btn').evaluate(el => !el.closest('.user-menu-panel')));
    assert(await page.locator('#theme-toggle-btn').evaluate(el => Boolean(el.closest('.user-menu-panel'))));
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
      if (label === 'editor') {
        assert.equal(await page.locator('input[name="classes"]:checked').count(), 0, 'Fresh class selection');
        assert.equal(await page.locator('#assignment-ai-options').getAttribute('open'), null);
        assert(await page.locator('#assignment-ai-options').evaluate(el => el.parentElement.classList.contains('assignment-editor-main') && el.nextElementSibling.id === 'assignment-custom-criteria'));
      }
      for (const theme of ['light', 'dark'])
        for (const width of [320, 375, 640, 1024, 1366])
          for (const scale of [100, 125]) await layout(label, theme, width, scale);
      if (label === 'editor') {
        await page.locator(`input[name="classes"][value="${config.classId}"]`).check();
        await page.locator('#schedule-targeting-block > summary').click();
        await page.locator('#assignment-ai-options > summary').click();
        await page.locator('#assignment-custom-criteria > summary').click();
        for (const width of [320, 375, 640, 1024, 1366]) {
          await layout('editor-expanded', 'dark', width, 125);
          const sizes = await page.locator('.class-check-pill').evaluateAll(elements => elements.map(el => ({width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height})));
          assert(Math.max(...sizes.map(r => r.width)) - Math.min(...sizes.map(r => r.width)) <= 1, 'Equal class card widths');
          assert(Math.max(...sizes.map(r => r.height)) - Math.min(...sizes.map(r => r.height)) <= 1, 'Equal class card heights');
        }
      }
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
