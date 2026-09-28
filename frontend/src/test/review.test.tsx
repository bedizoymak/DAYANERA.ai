import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AuthProvider } from '../auth/AuthContext';
import ReviewPage from '../pages/ReviewPage';
import { adminUser, calls, installFetch, json } from './mockApi';

const draftValue = {
  id: 'ev1', document_id: 'd1', document_title: 'cizim.png', version_id: 'v1', version_number: 1, version_active: true,
  page_number: 1, locator: 'görüntü', label: 'm', raw_text: 'm = 2,5 mm', context: 'Modül m = 2,5 mm z = 31',
  value: 2.5, unit: 'mm', quantity_kind: 'length', extraction_method: 'ocr', status: 'draft_extraction',
  original: { raw_text: 'm = 2,5 mm', value: 2.5, unit: 'mm' }, confirmed_value: null, confirmed_unit: null,
  confirmed_at: null, review_note: null,
};

function renderReview() {
  return render(<MemoryRouter><AuthProvider><ReviewPage /></AuthProvider></MemoryRouter>);
}

describe('Taslak çıkarım onay akışı', () => {
  it('taslak değeri etiketler, düzenleyerek onaylar ve kuyruğu yeniler', async () => {
    let confirmed = false;
    installFetch([
      { method: 'GET', path: /\/auth\/me$/, handler: () => json(adminUser) },
      {
        method: 'GET', path: /\/extractions\/values\?status=draft_extraction$/,
        handler: () => json({ items: confirmed ? [] : [draftValue], total: confirmed ? 0 : 1 }),
      },
      { method: 'GET', path: /\/extractions\/pages\?/, handler: () => json({ items: [], total: 0 }) },
      {
        method: 'POST', path: /\/extractions\/values\/ev1\/confirm$/,
        handler: () => {
          confirmed = true;
          return json({ ...draftValue, status: 'user_confirmed', confirmed_value: 2.75, confirmed_unit: 'mm' });
        },
      },
    ]);
    renderReview();
    const row = await screen.findByTestId('draft-value-row');
    expect(within(row).getByText('Taslak çıkarım')).toBeInTheDocument();
    expect(within(row).getByText('m = 2,5 mm')).toBeInTheDocument();
    const valueInput = within(row).getByLabelText('Değer');
    await userEvent.clear(valueInput);
    await userEvent.type(valueInput, '2,75');
    await userEvent.click(within(row).getByRole('button', { name: 'Onayla' }));
    expect(await screen.findByRole('status')).toHaveTextContent('onaylandı');
    const confirm = calls.find((c) => c.url.endsWith('/extractions/values/ev1/confirm'));
    expect(confirm?.body).toEqual({ value: 2.75, unit: 'mm', note: null });
    expect(confirm?.headers['X-DAYANERA-CSRF']).toBe('1');
    expect(await screen.findByText('Bekleyen taslak değer yok.')).toBeInTheDocument();
  });

  it('reddetme kararını gönderir', async () => {
    installFetch([
      { method: 'GET', path: /\/auth\/me$/, handler: () => json(adminUser) },
      { method: 'GET', path: /\/extractions\/values\?status=draft_extraction$/, handler: () => json({ items: [draftValue], total: 1 }) },
      { method: 'GET', path: /\/extractions\/pages\?/, handler: () => json({ items: [], total: 0 }) },
      { method: 'POST', path: /\/extractions\/values\/ev1\/reject$/, handler: () => json({ ...draftValue, status: 'rejected' }) },
    ]);
    renderReview();
    const row = await screen.findByTestId('draft-value-row');
    await userEvent.click(within(row).getByRole('button', { name: 'Reddet' }));
    expect(await screen.findByRole('status')).toHaveTextContent('reddedildi');
    expect(calls.some((c) => c.url.endsWith('/extractions/values/ev1/reject'))).toBe(true);
  });

  it('owner ISO sürümünü kalite kapılarıyla inceler ve needs_review için not olmadan onay veremez', async () => {
    let approved = false;
    const item = {
      document_id: 'd-iso', standard_code: 'ISO 53:1998', title: 'Cylindrical gears', document_status: 'active',
      area: 'iso-disli', is_verified_corpus: true, version_id: 'v-iso', version_number: 1,
      ingestion_status: 'indexed', corpus_status: 'needs_review', parser: 'pymupdf', page_count: 10,
      sha256: 'a'.repeat(64), verified_at: null, review_note: null, outcome: 'needs_review', chunks: 33,
      attention: [{ id: 'formula_extraction', status: 'review', detail: 'Eşitlik (3) kontrol edilmeli', pages: [3] }],
      quality_report: { gates: [{ id: 'formula_extraction', status: 'review', detail: 'Eşitlik (3) kontrol edilmeli', pages: [3] }] },
    };
    installFetch([
      { method: 'GET', path: /\/auth\/me$/, handler: () => json(adminUser) },
      { method: 'GET', path: /\/extractions\/values\?status=draft_extraction$/, handler: () => json({ items: [], total: 0 }) },
      { method: 'GET', path: /\/extractions\/pages\?/, handler: () => json({ items: [], total: 0 }) },
      { method: 'GET', path: /\/corpus\/status$/, handler: () => json({ items: [{ ...item, corpus_status: approved ? 'verified' : 'needs_review' }] }) },
      {
        method: 'POST', path: /\/documents\/d-iso\/corpus\/approve$/,
        handler: (init) => {
          approved = true;
          expect(JSON.parse(String(init?.body))).toEqual({ note: 'Eşitlik ve sembol sayfası kontrol edildi.' });
          return json({ current_version: { corpus_status: 'verified' } });
        },
      },
    ]);
    renderReview();
    await userEvent.click(await screen.findByRole('tab', { name: /ISO korpusu/ }));
    const row = await screen.findByTestId('corpus-review-d-iso');
    expect(within(row).getByText(/formula_extraction/)).toHaveTextContent('review');
    expect(within(row).getByRole('button', { name: 'Onayla' })).toBeDisabled();
    const note = within(row).getByLabelText(/İnceleme notu/);
    await userEvent.type(note, 'Eşitlik ve sembol sayfası kontrol edildi.');
    await userEvent.click(within(row).getByRole('button', { name: 'Onayla' }));
    expect(await screen.findByRole('status')).toHaveTextContent('doğrulanmış korpusa');
  });
});
