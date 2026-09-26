import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Credentials come from the untracked root .env (never hard-coded here).
function adminCredentials(): { user: string; pass: string } {
  const env = readFileSync(resolve(process.cwd(), '..', '.env'), 'utf-8'); // run from frontend/
  const get = (k: string) => env.match(new RegExp(`^${k}=(.*)$`, 'm'))?.[1]?.trim() ?? '';
  return { user: get('INITIAL_ADMIN_USERNAME') || 'admin', pass: get('INITIAL_ADMIN_PASSWORD') };
}

test('login → ISO belgesi indeksli → doğrulanmış yanıt → kaynak ver → hesap → denetim kaydı', async ({ page }) => {
  const { user, pass } = adminCredentials();
  await page.goto('/');
  await page.getByLabel('Kullanıcı adı').fill(user);
  await page.getByLabel('Parola').fill(pass);
  await page.getByRole('button', { name: 'Giriş yap' }).click();
  await expect(page.getByRole('button', { name: '+ Yeni sohbet' })).toBeVisible();

  // Document archive: ISO 53 is active and indexed; trigger a re-index from the UI
  await page.getByRole('button', { name: /Yönetici/ }).click();
  await page.getByRole('menuitem', { name: 'Belge arşivi' }).click();
  await page.getByPlaceholder('başlık, dosya adı, ISO kodu').fill('ISO 53');
  await page.getByRole('button', { name: 'Filtrele' }).click();
  await page.getByRole('button', { name: /^ISO 53:1998/ }).click();
  await expect(page.getByRole('heading', { name: /^ISO 53:1998/ })).toBeVisible();
  await page.getByRole('button', { name: 'Yeniden indeksle', exact: true }).click();
  await expect(page.getByText('Yeniden indeksleme kuyruğa alındı.', { exact: true })).toBeVisible();
  await expect(async () => {
    await page.getByRole('button', { name: /^ISO 53:1998/ }).click();
    await expect(page.locator('.detail dl')).toContainText('indekslendi', { timeout: 3000 });
  }).toPass({ timeout: 180_000 });

  // Verified question; sources hidden by default
  await page.getByRole('button', { name: '+ Yeni sohbet' }).click();
  await page.getByLabel('Mesaj').fill('ISO 53 standart temel kremayer profilinde basınç açısı nedir?');
  await page.getByRole('button', { name: 'Gönder' }).click();
  const answer = page.getByTestId('assistant-message').last();
  await expect(answer.getByTestId('mode-badge')).toHaveText('Doğrulanmış kaynak cevabı');
  await expect(answer).toContainText('20');
  await expect(page.getByLabel('Kaynaklar')).toHaveCount(0);

  // Explicit source request
  await answer.getByRole('button', { name: 'Kaynak ver' }).click();
  await expect(page.getByLabel('Kaynaklar').last()).toContainText('ISO 53:1998');

  // Calculation (deterministic engine) from the calculator page
  await page.getByRole('button', { name: /Yönetici/ }).click();
  await page.getByRole('menuitem', { name: 'Dişli hesapları' }).click();
  await page.getByLabel('Hesap türü').selectOption('cylindrical_gear_geometry');
  await page.locator('#in-z').fill('20');
  await page.locator('#in-m_n').fill('2');
  await page.getByLabel(/Qwen taslağıyla karşılaştır/).uncheck();
  await page.getByRole('button', { name: 'Hesapla' }).click();
  const result = page.getByLabel('Hesap sonucu');
  await expect(result.getByTestId('mode-badge')).toHaveText('Hesap sonucu');
  await expect(result).toContainText('Referans çapı d (reference diameter): 40 mm');
  await result.getByText('Ayrıntılı çözüm').click();
  await expect(result.getByTestId('calc-detail')).toContainText('ISO 21771:2007');

  // Audit log shows the calculation and source reveal
  await page.getByRole('button', { name: /Yönetici/ }).click();
  await page.getByRole('menuitem', { name: 'Denetim kaydı' }).click();
  await expect(page.getByTestId('audit-table')).toContainText('calculation.run');
  await expect(page.getByTestId('audit-table')).toContainText('sources.revealed');
});
