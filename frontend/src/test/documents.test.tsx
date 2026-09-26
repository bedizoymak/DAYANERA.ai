import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AuthProvider } from '../auth/AuthContext';
import DocumentsPage from '../pages/DocumentsPage';
import { adminUser, calls, installFetch, json } from './mockApi';

const version = {
  id: 'v1', version_number: 1, sha256: 'a'.repeat(64), size_bytes: 1024, mime_type: 'application/pdf', file_extension: '.pdf',
  state: 'active', is_active: true, ingestion_status: 'indexed', ingestion_error: null, page_count: 10, category: 'pdf',
  extraction_summary: { counts: { pages: 10 }, warnings: [] }, metadata: {}, created_at: '2026-09-26T00:00:00Z',
  ingested_at: '2026-09-26T00:00:00Z', superseded_at: null, source_mtime: null,
};
const doc = {
  id: 'd1', title: 'ISO 53:1998 — Cylindrical gears', standard_code: 'ISO 53:1998', source_kind: 'watched',
  source_relpath: 'iso53.pdf', original_filename: 'iso53.pdf', status: 'active', knowledge_area: 'iso-disli',
  is_verified_corpus: true, current_version: version, created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z',
};

describe('Belge arşivi', () => {
  it('yeniden indeksleme mesajı kalıcıdır ve silme ayrı onay ister', async () => {
    installFetch([
      { method: 'GET', path: /\/auth\/me$/, handler: () => json(adminUser) },
      { method: 'GET', path: /\/documents\?/, handler: () => json({ items: [doc], total: 1 }) },
      { method: 'GET', path: /\/documents\/d1$/, handler: () => json(doc) },
      { method: 'GET', path: /\/documents\/d1\/versions$/, handler: () => json([version]) },
      { method: 'GET', path: /\/documents\/d1\/relationships$/, handler: () => json([]) },
      { method: 'GET', path: /\/documents\/d1\/versions\/v1\/pages$/, handler: () => json([]) },
      { method: 'POST', path: /\/documents\/d1\/reindex$/, handler: () => json({ queued: true, document: doc }) },
      { method: 'DELETE', path: /\/documents\/d1$/, handler: () => json({ ...doc, status: 'deleted' }) },
    ]);
    render(<MemoryRouter><AuthProvider><DocumentsPage /></AuthProvider></MemoryRouter>);
    await userEvent.click(await screen.findByRole('button', { name: /^ISO 53:1998/ }));
    expect(await screen.findByRole('heading', { name: /^ISO 53:1998/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Yeniden indeksle' }));
    expect(await screen.findByText('Yeniden indeksleme kuyruğa alındı.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Sil (mantıksal)' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false); // nothing deleted before confirmation
    expect(screen.getByRole('dialog', { name: 'Silme onayı' })).toHaveTextContent('ham sürümler korunur');
    await userEvent.type(screen.getByLabelText('Gerekçe'), 'eski revizyon');
    await userEvent.click(screen.getByRole('button', { name: 'Silmeyi onayla' }));
    expect(await screen.findByText('Belge mantıksal olarak silindi.')).toBeInTheDocument();
    expect(calls.find((c) => c.method === 'DELETE')?.body).toEqual({ reason: 'eski revizyon' });
  });
});
