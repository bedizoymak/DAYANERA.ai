import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Step 2 acceptance (DAYANERA_AI_STEP2_ORDERS.md §6) in a real browser against the RUNNING stack.
const REFUSAL = 'Bu kaynak setinde doğrulayamadım';

function adminCredentials(): { user: string; pass: string } {
  const env = readFileSync(resolve(process.cwd(), '..', '.env'), 'utf-8'); // run from frontend/
  const get = (k: string) => env.match(new RegExp(`^${k}=(.*)$`, 'm'))?.[1]?.trim() ?? '';
  return { user: get('INITIAL_ADMIN_USERNAME') || 'admin', pass: get('INITIAL_ADMIN_PASSWORD') };
}

const FAST = { timeout: 30_000 };

// Fresh conversation per question; waits until the previous conversation's bubbles are gone.
async function ask(page: Page, text: string) {
  await page.getByRole('button', { name: '+ Yeni sohbet' }).click();
  await expect(page.getByTestId('assistant-message')).toHaveCount(0, FAST);
  await page.getByLabel('Mesaj').fill(text);
  const t0 = Date.now();
  await page.getByRole('button', { name: 'Gönder' }).click();
  const answer = page.getByTestId('assistant-message').first();
  await expect(answer).toBeVisible();
  const seconds = (Date.now() - t0) / 1000;
  await expect(page.getByTestId('pending-message')).toHaveCount(0);
  return { answer, seconds };
}

test('Step 2 kabul kapıları: envanter, hızlı ret + ipucu, H7 doğrulanmış sonuç, genel sohbet', async ({ page }, info) => {
  const { user, pass } = adminCredentials();
  await page.goto('/');
  await page.getByLabel('Kullanıcı adı').fill(user);
  await page.getByLabel('Parola').fill(pass);
  await page.getByRole('button', { name: 'Giriş yap' }).click();
  await expect(page.getByRole('button', { name: '+ Yeni sohbet' })).toBeVisible();
  const timings: Record<string, number> = {};

  // 1. T2 -> standards list in ~1 s
  const t2 = await ask(page, 'peki elinde hangi ISO standartları var, listeler misin?');
  await expect(t2.answer).toContainText('ISO 53:1998', FAST);
  await expect(t2.answer).toContainText('Sistem bilgisi', FAST);
  timings.T2 = t2.seconds;
  expect(t2.seconds).toBeLessThan(3);
  await page.screenshot({ path: info.outputPath('t2-inventory.png') });

  // 2. T1 and T3 -> exact refusal within seconds, with the correct hint under the bubble
  const t1 = await ask(page, 'M10 civata 8.8 kalite, çekme dayanımı ne kadar? sıkma torku kaç Nm');
  await expect(t1.answer.locator('.msg-body')).toHaveText(REFUSAL, FAST);
  await expect(t1.answer.getByTestId('refusal-hint')).toHaveText('Bu konu yüklü standartlarda bulunamadı.', FAST);
  timings.T1 = t1.seconds;
  expect(t1.seconds).toBeLessThan(10);
  const t3 = await ask(page, 'ISO 2768 m sınıfı 30-120 mm tolerans ne? kaynak ver');
  await expect(t3.answer.locator('.msg-body')).toHaveText(REFUSAL, FAST);
  await expect(t3.answer.getByTestId('refusal-hint')).toHaveText(
    'İstenen standart yüklü değil: ISO 2768. "hangi standartlar var" yazarak listeyi görebilirsiniz.', FAST);
  timings.T3 = t3.seconds;
  expect(t3.seconds).toBeLessThan(10);
  await page.screenshot({ path: info.outputPath('t3-refusal-hint.png') });

  // 3. ISO 286 50 mm H7 -> verified result (calculation mode)
  const g3 = await ask(page, "ISO 286'ya göre 50 mm H7 toleransı nedir?");
  await expect(g3.answer.getByTestId('mode-badge')).toHaveText(/Hesap sonucu|Doğrulanmış kaynak cevabı/);
  await expect(g3.answer).toContainText('25 µm');
  await expect(g3.answer).toContainText('50,025 mm');
  timings.G3 = g3.seconds;
  await page.screenshot({ path: info.outputPath('g3-h7.png') });

  // 4. ordinary chat unchanged
  const t4 = await ask(page, 'bana kısaca kendini tanıtır mısın');
  await expect(t4.answer.getByTestId('mode-badge')).toHaveText('Genel sohbet');
  await expect(t4.answer.getByTestId('refusal-hint')).toHaveCount(0);
  timings.T4 = t4.seconds;

  console.log('STEP2_TIMINGS', JSON.stringify(timings));
});
