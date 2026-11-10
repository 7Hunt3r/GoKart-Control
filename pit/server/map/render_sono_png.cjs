/* Development-only PNG fallback. Run after render_sono.py with Playwright installed.
 * No browser, Node or network is needed on the kart to use the committed assets. */
const { chromium } = require('playwright');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1449 }, deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(path.resolve(__dirname, '../../../kart/server/static/sono-light.svg')).href);
    const dimensions = await page.locator('svg').evaluate(svg => ({
      width: Number(svg.getAttribute('width')), height: Number(svg.getAttribute('height'))
    }));
    await page.setViewportSize(dimensions);
    await page.locator('svg').screenshot({ path: path.join(__dirname, 'sono-light.png') });
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
