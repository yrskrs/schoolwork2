const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const {chromium} = require(config.playwright);

(async () => {
    const browser = await chromium.launch({executablePath: config.chrome, headless: true, args: ['--no-sandbox']});
    const errors = [];
    try {
        const context = await browser.newContext({baseURL: config.baseURL, viewport: {width: 1440, height: 1000}});
        await context.addCookies([{name: config.cookieName, value: config.cookie, url: config.baseURL}]);
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await context.addInitScript(() => localStorage.setItem('sn-simplified-site', 'off'));
        const visit = async (name, file) => {
            const item = config.ids[name];
            const response = await page.goto(`/teacher/view-file/${item.submission}/?file_id=${file || item.file}`);
            assert.equal(response.status(), 200);
        };
        const waitOutput = text => page.waitForFunction(value => document.querySelector('[data-python-output]').textContent.includes(value), text, {timeout: 25000});
        await visit('terminal.py');
        await page.locator('[data-python-play]').click();
        await waitOutput('Name:');
        await page.locator('#python-stdin').fill('Привіт');
        await page.locator('#python-stdin').press('Enter');
        await waitOutput('HELLO Привіт');
        await page.waitForFunction(() => document.querySelector('[data-python-play]').disabled === false);
        assert.equal(await page.evaluate(() => window.runnerXss), undefined);
        assert.match(await page.locator('[data-python-output]').textContent(), /<script>/);
        await page.locator('#fv-document-card').screenshot({path: path.join(config.artifacts, 'terminal.png')});
        console.log('PASS UI: Play, terminal input/output and text-only rendering');

        await page.locator('#grade-input').fill('10');
        await page.locator('#save-grade-btn').click();
        await page.waitForFunction(() => document.querySelector('#grade-saved-msg').textContent.length > 0);
        await visit('terminal.py', config.companion);
        await page.locator('[data-python-play]').click();
        await waitOutput('NEXT_FILE');
        console.log('PASS UI: grading stays available; selected file is executed');

        await visit('desktop.py');
        await page.locator('[data-python-play]').click();
        await waitOutput('ENTRY_POS:');
        await page.waitForFunction(() => document.querySelector('[data-python-tab=desktop]').getAttribute('aria-selected') === 'true');
        const screen = page.locator('[data-python-screen]');
        await screen.waitFor({state: 'visible'});
        await page.waitForFunction(() => document.querySelector('[data-python-screen]').naturalWidth === 1024);
        const output = await page.locator('[data-python-output]').textContent();
        async function clickAt(label) {
            const [, x, y] = output.match(new RegExp(label + ': (\\d+) (\\d+)'));
            const box = await screen.boundingBox();
            await screen.click({position: {x: Number(x) / 1024 * box.width, y: Number(y) / 768 * box.height}});
        }
        await clickAt('BUTTON');
        await waitOutput('CLICKED');
        await clickAt('ENTRY_POS');
        await page.keyboard.type('Тест');
        await page.keyboard.press('Enter');
        await waitOutput('ENTRY: Тест');
        await page.waitForTimeout(400); // Wait for the next desktop frame.
        await page.locator('#fv-document-card').screenshot({path: path.join(config.artifacts, 'desktop.png')});
        await page.locator('[data-python-stop]').click();
        await page.waitForFunction(() => document.querySelector('[data-python-status]').textContent.includes('зупинено'));
        console.log('PASS UI: desktop image, scaled mouse coordinates, Unicode keyboard and Stop');

        await page.setViewportSize({width: 390, height: 844});
        await page.locator('[data-review-tab=document]').click();
        await page.locator('[data-python-tab=terminal]').click();
        await page.locator('#python-runner').scrollIntoViewIfNeeded();
        const layout = await page.locator('#python-runner').evaluate(element => ({width: element.clientWidth, scroll: element.scrollWidth, right: element.getBoundingClientRect().right}));
        assert.ok(layout.scroll <= layout.width + 1 && layout.right <= 391, JSON.stringify(layout));
        const playWidth = await page.locator('[data-python-play]').evaluate(element => element.getBoundingClientRect().width);
        assert.ok(playWidth >= 140, 'Play label has room on mobile');
        await page.locator('#fv-document-card').screenshot({path: path.join(config.artifacts, 'mobile.png')});
        assert.deepEqual(errors, []);
        console.log('PASS UI: mobile layout and no JavaScript exceptions');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
