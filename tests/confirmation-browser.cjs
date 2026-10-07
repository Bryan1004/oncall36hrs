const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../app/static');
const origin = 'http://oncall.test';

// Run with Node and Playwright; PLAYWRIGHT_CHROMIUM_EXECUTABLE can select local Chrome.
(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
  try {
    async function fixture(url = '/confirm') {
      const page = await browser.newPage({viewport: {width: 390, height: 844}});
      const requests = [], errors = [], queued = new Map(), waiting = new Map();
      let pageLoads = 0;
      page.on('pageerror', error => errors.push(error.message));
      await page.route(origin + '/**', async route => {
        const request = route.request(), pathname = new URL(request.url()).pathname;
        if (pathname === '/confirm' && request.method() === 'POST') {
          const token = request.postDataJSON().token;
          assert.equal(request.headers()['x-requested-with'], 'oncall');
          requests.push(token);
          if (waiting.has(token)) {
            waiting.get(token)(route);
            waiting.delete(token);
          } else {
            const entries = queued.get(token) || [];
            entries.push(route);
            queued.set(token, entries);
          }
          return;
        }
        const file = pathname === '/confirm' ? 'confirm.html' : pathname === '/confirm/client.js' ? 'confirmation.js' : null;
        if (pathname === '/confirm') pageLoads++;
        if (file) await route.fulfill({path: path.join(root, file)});
        else await route.fulfill({status: 404, body: ''});
      });
      await page.goto(origin + url);
      return {page, requests, errors, pageLoads: () => pageLoads,
        take(token) {
          const entries = queued.get(token);
          if (entries?.length) return Promise.resolve(entries.shift());
          return new Promise(resolve => waiting.set(token, resolve));
        }};
    }
    const state = (page, name) => page.waitForFunction(name => document.body.className === 'state-' + name, name);
    const success = route => route.fulfill({json: {status: 'acknowledged'}});

    // A response updates the page immediately; a second notification reuses the same document.
    {
      const f = await fixture('/confirm#first');
      await state(f.page, 'loading');
      const first = await f.take('first'), started = Date.now();
      await success(first);
      await state(f.page, 'success');
      assert(Date.now() - started < 2000, 'Confirmation must not wait for the old five-second timer');
      await f.page.evaluate(() => {window.instanceMarker = 'same-document';});
      await f.page.goto(origin + '/confirm#second');
      const second = await f.take('second');
      await state(f.page, 'loading');
      assert.equal(await f.page.locator('#confirmed-time').evaluate(el => el.classList.contains('show')), false);
      assert.equal(await f.page.evaluate(() => window.instanceMarker), 'same-document');
      await success(second);
      await state(f.page, 'success');
      assert.deepEqual(f.requests, ['first', 'second']);
      assert.equal(f.pageLoads(), 1);
      assert.equal(await f.page.evaluate(() => location.hash), '');
      await f.page.evaluate(() => {
        window.dispatchEvent(new Event('focus'));
        window.dispatchEvent(new Event('pageshow'));
        document.dispatchEvent(new Event('visibilitychange'));
      });
      assert.deepEqual(f.requests, ['first', 'second']);
      assert.deepEqual(f.errors, []);
      await f.page.close();
      console.log('PASS immediate confirmation and consecutive clicks without reload');
    }

    // New fragments are also consumed when returning from background or browser history.
    for (const event of ['focus', 'pageshow', 'visibilitychange']) {
      const f = await fixture('/confirm?source=test');
      await state(f.page, 'warn');
      await f.page.evaluate(event => {
        // replaceState models a restored URL without emitting hashchange.
        history.replaceState(null, '', '/confirm?source=test#resumed');
        (event === 'visibilitychange' ? document : window).dispatchEvent(new Event(event));
      }, event);
      await success(await f.take('resumed'));
      await state(f.page, 'success');
      assert.equal(await f.page.evaluate(() => location.search), '?source=test');
      await f.page.close();
      console.log('PASS resumed page', event);
    }

    // Network/server failures retain a retry, which submits the most recent notification.
    for (const failure of ['network', 'server']) {
      const f = await fixture('/confirm#retry-me');
      const request = await f.take('retry-me');
      if (failure === 'network') await request.abort('failed');
      else await request.fulfill({status: 503, json: {detail: '暂时不可用'}});
      await state(f.page, 'error');
      assert.equal(await f.page.locator('#retry').isVisible(), true);
      await f.page.locator('#retry').click();
      await success(await f.take('retry-me'));
      await state(f.page, 'success');
      assert.deepEqual(f.requests, ['retry-me', 'retry-me']);
      await f.page.close();
      console.log('PASS failure and retry', failure);
    }

    // A late result for an older click must never overwrite the latest click's result.
    for (const latestSucceeds of [true, false]) {
      const f = await fixture('/confirm#old');
      const old = await f.take('old');
      await f.page.goto(origin + '/confirm#new');
      const latest = await f.take('new');
      if (latestSucceeds) await success(latest);
      else await latest.fulfill({status: 403, json: {detail: '新链接已过期'}});
      await state(f.page, latestSucceeds ? 'success' : 'error');
      // Observe both completed response bodies before checking the final state.
      const oldFinished = f.page.waitForResponse(response => response.request().postDataJSON()?.token === 'old');
      if (latestSucceeds) await old.fulfill({status: 503, json: {detail: '旧请求失败'}});
      else await success(old);
      await (await oldFinished).finished();
      await f.page.waitForTimeout(100);
      assert.equal(await f.page.locator('body').getAttribute('class'), 'state-' + (latestSucceeds ? 'success' : 'error'));
      if (!latestSucceeds) {
        assert.equal(await f.page.locator('#detail').textContent(), '新链接已过期');
        assert.equal(await f.page.locator('#retry').isVisible(), false);
      }
      assert.deepEqual(f.requests, ['old', 'new']);
      assert.deepEqual(f.errors, []);
      await f.page.close();
      console.log('PASS out-of-order results', latestSucceeds ? 'latest success' : 'latest failure');
    }

    // Missing credentials never become a simulated success on a real page.
    {
      const f = await fixture();
      await f.page.clock.install();
      await f.page.clock.fastForward(6000);
      await state(f.page, 'warn');
      assert.deepEqual(f.requests, []);
      assert.equal(await f.page.locator('#confirmed-time').evaluate(el => el.classList.contains('show')), false);
      await f.page.close();
      console.log('PASS missing credentials never show success');
    }
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
