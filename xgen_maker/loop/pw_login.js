// Playwright 인증 세션 캡처 — 로그인 → 보호 라우트 스냅샷.
// 인자: JSON {base, email, password, routes[], outDir, storageState?}
// stdout: JSON {ok, shots:[{route, path}], error?}
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  // argv[2] = config 파일 경로(JSON). 셸 이스케이프 회피.
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
  const out = { ok: false, shots: [] };
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    // 로그인
    await page.goto(cfg.base, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2500);
    const email = page.locator('input[type=email], input[name=email], input[placeholder*="메일"], input[placeholder*="mail"]').first();
    const pw = page.locator('input[type=password], input[name=password]').first();
    if (await email.count()) {
      await email.fill(cfg.email);
      await pw.fill(cfg.password);
      await Promise.all([
        page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {}),
        page.locator('button[type=submit], button:has-text("로그인"), button:has-text("Login")').first().click().catch(() => {}),
      ]);
      await page.waitForTimeout(3000);
    }
    if (cfg.storageState) {
      await ctx.storageState({ path: cfg.storageState });
    }
    // 보호 라우트 스냅샷
    for (const route of (cfg.routes || ['/'])) {
      const url = cfg.base.replace(/\/$/, '') + (route === '/' ? '' : route);
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(cfg.waitMs || 3500);
      const slug = route.replace(/[^\w]+/g, '_').replace(/^_|_$/g, '') || 'root';
      const shot = path.join(cfg.outDir, `auth_${slug}.png`);
      await page.screenshot({ path: shot, fullPage: true });
      out.shots.push({ route, path: shot });
    }
    out.ok = true;
  } catch (e) {
    out.error = String(e).slice(0, 400);
  } finally {
    if (browser) await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})();
