import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StatusBanner from '../components/StatusBanner';
import { installFetch, json } from './mockApi';

function renderStatus(state: 'EMPTY' | 'INDEXED_UNAPPROVED' | 'READY') {
  installFetch([
    {
      method: 'GET',
      path: /\/readiness$/,
      handler: () => json({ ready: true, database: { ok: true }, ollama: { reachable: true, model_available: true }, messages: [] }),
    },
    {
      method: 'GET',
      path: /\/system\/status$/,
      handler: () => json({ corpus: {
        state,
        indexed_active_documents: state === 'EMPTY' ? 0 : 12,
        indexed_active_chunks: state === 'EMPTY' ? 0 : 4045,
        owner_approved_active_versions: state === 'READY' ? 1 : 0,
        verified_active_chunks: state === 'READY' ? 100 : 0,
        jobs: {},
      } }),
    },
  ]);
  render(<StatusBanner />);
}

describe('Korpus durum bannerı', () => {
  it('EMPTY durumunda indekslenmiş korpus yok mesajını gösterir', async () => {
    renderStatus('EMPTY');
    expect(await screen.findByText(/İndekslenmiş ISO korpusu bulunamadı/)).toBeInTheDocument();
  });

  it('INDEXED_UNAPPROVED durumunu boş korpus olarak göstermeden açıklar', async () => {
    renderStatus('INDEXED_UNAPPROVED');
    expect(await screen.findByText(/ISO korpusu indekslendi \(12 belge, 4045 parça\)/)).toBeInTheDocument();
    expect(screen.queryByText(/İndekslenmiş ISO korpusu bulunamadı/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Doğrulanmış ISO korpusu boş/)).not.toBeInTheDocument();
  });

  it('READY durumunda korpus uyarısı üretmez', async () => {
    renderStatus('READY');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
