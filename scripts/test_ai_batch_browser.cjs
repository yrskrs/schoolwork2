/* Render /tmp/ai-batch-browser.html using the Django template first.
 * Every browser request is served from local files or synthetic responses.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = process.env.AI_BROWSER_OUTPUT || '/tmp/ai-batch-browser';

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const executablePath = process.env.CHROME_EXECUTABLE || (fs.existsSync('/opt/google/chrome/chrome') ? '/opt/google/chrome/chrome' : undefined);
  const browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let batch = null, submissions = 0, cancellations = 0, cancelled = false, individualOverrides = 0;
    const rows = [1, 2].map(id => ({id, student_name: id === 1 ? '<img src=x onerror=alert(1)>' : 'Синтетичний учень',
      class_name: '7-А', assignment_title: 'Синтетичне завдання', current_grade: '—', ai_grade: '—', ai_status: 'none'}));
    function snapshot() {
      return {batch_id: batch, pending: cancelled ? 0 : 2, cancelled,
        items: rows.map((row, index) => ({...row, job_status: cancelled ? (index ? 'cancelled' : 'succeeded') : 'queued',
          ai_status: cancelled ? (index ? 'cancelled' : 'success') : 'processing', ai_grade: cancelled && !index ? '9' : '—'}))};
    }
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.hostname !== 'synthetic.local') return route.abort();
      const pathname = url.pathname;
      if (pathname === '/teacher/ai/batch-check/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync('/tmp/ai-batch-browser.html', 'utf8')});
      if (pathname === '/teacher/view-file/1/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync('/tmp/ai-viewer-override.html', 'utf8')});
      if (pathname.startsWith('/static/')) {
        const file = path.resolve(root, pathname.slice(1));
        if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
        return route.fulfill({path: file, contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : undefined});
      }
      let response = {};
      if (pathname === '/api/ai/batches/latest/') response = batch ? snapshot() : {batch_id: null};
      else if (pathname === '/api/ai/get-batch-queue/') response = {queue: rows};
      else if (pathname === '/api/ai/batches/' && request.method() === 'POST') {
        submissions++;
        assert.equal((request.postData().match(/name="submission_ids"/g) || []).length, 2);
        assert.match(request.postData(), /name="ignore_strict_privacy"\r?\n\r?\n1/);
        batch = '12345678-1234-4234-8234-123456789012';
        response = snapshot();
      } else if (pathname.endsWith('/cancel/')) {
        cancellations++;
        cancelled = true;
        response = snapshot();
      } else if (pathname.startsWith('/api/ai/batches/')) response = snapshot();
      else if (pathname === '/teacher/ai/check/1/') {
        assert.match(request.postData(), /name="ignore_strict_privacy"\r?\n\r?\n1/);
        individualOverrides++;
        response = {status: 'success', suggested_grade: '9', level: 'Достатній', feedback: 'Синтетичний відгук.',
          clean_feedback: 'Перевір пояснення.', gr_results: [], is_traditional: true, request_metadata: {}};
      }
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(response)});
    });
    await page.goto('http://synthetic.local/teacher/ai/batch-check/');
    await page.waitForFunction(() => document.getElementById('queue-count').textContent === '2');
    assert.equal(await page.locator('#queue-tbody img').count(), 0, 'Student text must stay escaped');
    assert.equal(await page.locator('#batch-ignore-strict-privacy').isChecked(), false);
    await page.locator('#batch-ignore-strict-privacy').check();
    await page.locator('#btn-start-batch').click();
    await page.waitForFunction(() => document.getElementById('btn-stop-batch').style.display !== 'none');
    assert.equal(submissions, 1);
    assert.equal(await page.locator('#batch-ignore-strict-privacy').isChecked(), false);
    await page.screenshot({path: path.join(output, 'batch-wide.png'), fullPage: true});
    await page.reload();
    await page.waitForFunction(() => document.getElementById('btn-stop-batch').style.display !== 'none');
    assert.equal(submissions, 1, 'Reload must restore the same batch, not enqueue another one');
    assert.equal(await page.locator('#batch-ignore-strict-privacy').isChecked(), false);
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(output, 'batch-mobile.png'), fullPage: true});
    await page.locator('#btn-stop-batch').click();
    await page.waitForFunction(() => document.getElementById('progress-status-text').textContent.includes('скасовано'));
    assert.equal(cancellations, 1);
    await page.setViewportSize({width: 1440, height: 1000});
    await page.goto('http://synthetic.local/teacher/view-file/1/');
    await page.evaluate(() => window.selectReviewPanel?.('ai'));
    assert.equal(await page.locator('#ignore-strict-privacy-checkbox').isChecked(), false);
    await page.locator('#ignore-strict-privacy-checkbox').check();
    await page.locator('#btn-run-ai-check').click();
    await page.waitForFunction(() => !document.getElementById('btn-run-ai-check').disabled);
    assert.equal(individualOverrides, 1);
    assert.equal(await page.locator('#ignore-strict-privacy-checkbox').isChecked(), false);
    await page.screenshot({path: path.join(output, 'teacher-override-wide.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => window.selectReviewPanel?.('ai'));
    await page.screenshot({path: path.join(output, 'teacher-override-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    const result = {single_batch_submission: true, reload_restores_batch: true, cancellation_works: true,
      student_text_escaped: true, teacher_override_submitted: true, teacher_override_resets: true,
      javascript_errors: errors, screenshots: ['batch-wide.png', 'batch-mobile.png', 'teacher-override-wide.png', 'teacher-override-mobile.png']};
    fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
