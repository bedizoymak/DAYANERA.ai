import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AuthProvider } from '../auth/AuthContext';
import Layout from '../components/Layout';
import ModeBadge, { MODE_LABELS } from '../components/ModeBadge';
import ChatPage from '../pages/ChatPage';
import { adminUser, calls, installFetch, json, msg, sse } from './mockApi';

const conv = { id: 'c1', title: 'Deneme', status: 'active', owner_id: 'u1', created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z' };

const verifiedSources = [
  {
    rank: 1, document_id: 'd1', version_id: 'v1', version_number: 1, document_title: 'ISO 53:1998 — Cylindrical gears',
    standard_code: 'ISO 53:1998', page_number: 5, locator: 's. 5', excerpt: 'Table 2 — Standard basic rack proportions aP 20°',
    score: 0.9, confidence_status: 'verified_source', cited: true,
  },
];

function baseRoutes(extra: Parameters<typeof installFetch>[0] = []) {
  return [
    ...extra,
    { method: 'GET', path: /\/auth\/me$/, handler: () => json(adminUser) },
    { method: 'GET', path: /\/conversations\?status=active$/, handler: () => json([conv]) },
    { method: 'GET', path: /\/conversations\/c1$/, handler: () => json(conv) },
    { method: 'GET', path: /\/conversations\/c1\/messages$/, handler: () => json([]) },
    { method: 'GET', path: /\/readiness$/, handler: () => json({ ready: true, database: { ok: true }, ollama: { reachable: true, model_available: true }, messages: [] }) },
    { method: 'GET', path: /\/system\/status$/, handler: () => json({ corpus: { empty_verified_corpus: false, jobs: {} } }) },
  ];
}

function renderChat() {
  return render(
    <MemoryRouter initialEntries={['/c/c1']}>
      <AuthProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route path="c/:conversationId" element={<ChatPage />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('Yanıt modu göstergeleri', () => {
  it('beş modun Türkçe etiketlerini gösterir', () => {
    const { rerender } = render(<ModeBadge mode="general" />);
    for (const [mode, label] of Object.entries(MODE_LABELS)) {
      rerender(<ModeBadge mode={mode as keyof typeof MODE_LABELS} />);
      expect(screen.getByTestId('mode-badge')).toHaveTextContent(label);
    }
    expect(Object.values(MODE_LABELS)).toEqual([
      'Genel sohbet', 'Doğrulanmış kaynak cevabı', 'Hesap sonucu', 'Taslak çıkarım', 'Doğrulanamadı',
    ]);
  });
});

describe('Sohbet', () => {
  it('mesaj gönderir, akışı işler, mod rozetini gösterir; kaynaklar yalnızca "kaynak ver" ile görünür', async () => {
    let turn = 0;
    installFetch(
      baseRoutes([
        {
          method: 'POST',
          path: /\/conversations\/c1\/messages\/stream$/,
          handler: (init) => {
            const body = JSON.parse(String(init?.body));
            turn += 1;
            if (turn === 1) {
              return sse([
                { event: 'user_message', data: msg({ role: 'user', content: body.content }) },
                { event: 'status', data: { stage: 'retrieval', text: 'Doğrulanmış ISO kaynakları aranıyor…' } },
                {
                  event: 'assistant_message',
                  data: msg({
                    content: 'Standart temel kremayer profilinde basınç açısı (pressure angle) 20°dir.',
                    answer_mode: 'verified_source', show_sources: false, sources: [],
                  }),
                },
                { event: 'done', data: {} },
              ]);
            }
            expect(body.content).toBe('kaynak ver');
            return sse([
              { event: 'user_message', data: msg({ role: 'user', content: 'kaynak ver' }) },
              {
                event: 'assistant_message',
                data: msg({ content: 'Önceki yanıtın dayandığı yerel kaynaklar:', answer_mode: 'verified_source', show_sources: true, sources: verifiedSources }),
              },
              { event: 'done', data: {} },
            ]);
          },
        },
      ]),
    );
    renderChat();
    const box = await screen.findByLabelText('Mesaj');
    await userEvent.type(box, 'ISO 53 temel kremayer basınç açısı nedir?');
    await userEvent.click(screen.getByRole('button', { name: 'Gönder' }));

    const answer = await screen.findByText(/basınç açısı \(pressure angle\) 20°/);
    const article = answer.closest('article')!;
    expect(within(article).getByTestId('mode-badge')).toHaveTextContent('Doğrulanmış kaynak cevabı');
    // citations are hidden by default
    expect(screen.queryByLabelText('Kaynaklar')).not.toBeInTheDocument();

    await userEvent.click(within(article).getByRole('button', { name: 'Kaynak ver' }));
    const sources = await screen.findByLabelText('Kaynaklar');
    expect(sources).toHaveTextContent('ISO 53:1998');
    expect(sources).toHaveTextContent('s. 5');
    const streamCalls = calls.filter((c) => c.url.endsWith('/messages/stream'));
    expect(streamCalls).toHaveLength(2);
    expect(streamCalls[0].headers['X-DAYANERA-CSRF']).toBe('1');
  });

  it('dosya eklerini yükler ve mesajla birlikte kimliklerini gönderir; ret yanıtını "Doğrulanamadı" olarak gösterir', async () => {
    installFetch(
      baseRoutes([
        {
          method: 'POST', path: /\/attachments$/,
          handler: () => json({ id: 'doc-42', title: 'cizim.png', status: 'pending', current_version: null }, 201),
        },
        {
          method: 'POST', path: /\/conversations\/c1\/messages\/stream$/,
          handler: (init) => {
            const body = JSON.parse(String(init?.body));
            return sse([
              { event: 'user_message', data: msg({ role: 'user', content: body.content, attachments: [{ document_id: 'doc-42', filename: 'cizim.png', status: 'pending', ingestion_status: 'queued', mime_type: 'image/png' }] }) },
              { event: 'assistant_message', data: msg({ content: 'Bu kaynak setinde doğrulayamadım', answer_mode: 'unverified' }) },
              { event: 'done', data: {} },
            ]);
          },
        },
      ]),
    );
    renderChat();
    const input = (await screen.findByTestId('attach-input')) as HTMLInputElement;
    const file = new File([new Uint8Array([137, 80, 78, 71])], 'cizim.png', { type: 'image/png' });
    await userEvent.upload(input, file);
    expect(await screen.findByText('yerel arşive alındı')).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Mesaj'), 'Bu çizimdeki modül değeri standarda uygun mu?');
    await userEvent.click(screen.getByRole('button', { name: 'Gönder' }));
    const refusal = await screen.findByText('Bu kaynak setinde doğrulayamadım');
    expect(within(refusal.closest('article')!).getByTestId('mode-badge')).toHaveTextContent('Doğrulanamadı');
    const stream = calls.find((c) => c.url.endsWith('/messages/stream'));
    expect((stream?.body as { attachment_ids: string[] }).attachment_ids).toEqual(['doc-42']);
    const upload = calls.find((c) => c.url.endsWith('/attachments'));
    expect(upload?.body).toBeInstanceOf(FormData);
  });

  it('hesap sonucunda "Ayrıntılı çözüm" ile formül, birim ve kaynak izini gösterir', async () => {
    const calcMsg = msg({
      id: 'm-calc', content: '**Silindirik evolvent dişli geometrisi** — d = 40 mm', answer_mode: 'calculation',
      metadata: { calculation_id: 'calc-1', calc_status: 'ok', mismatch: true },
    });
    installFetch(
      baseRoutes([
        { method: 'GET', path: /\/conversations\/c1\/messages$/, handler: () => json([calcMsg]) },
        {
          method: 'GET', path: /\/messages\/m-calc\/calculation$/,
          handler: () => json({
            id: 'calc-1', calc_type: 'cylindrical_gear_geometry', status: 'ok', engine_version: '1.0.0', mismatch: true,
            inputs: {}, llm_draft: { outputs: { d: 41 } }, created_at: '2026-09-26T00:00:00Z',
            comparison: { performed: true, mismatch: true, items: [{ key: 'd', engine: 40, llm: 41, status: 'mismatch' }] },
            result: {
              calc_type: 'cylindrical_gear_geometry', status: 'ok', engine_version: '1.0.0', message: 'ok',
              outputs: [{ key: 'd', label: 'Referans çapı d (reference diameter)', value: 40, unit: 'mm', formula_id: 'ISO21771:2007 Eş.(1)', expression: 'd = z·m_n / cos β', display: '40 mm', unrounded: null }],
              inputs: [{ key: 'z', label: 'Diş sayısı z', value: 20, unit: '', provenance: { kind: 'user_input', status: 'user_input' } }],
              constants: [], assumptions: ['Helis açısı verilmedi: düz dişli (spur gear) kabul edildi, β = 0°.'],
              evidence: [{ requirement_id: 'iso21771.eq1', description: 'ISO 21771:2007 4.2.4, Eşitlik (1)', document_id: 'd', version_id: 'v', version_number: 1, document_title: 'ISO 21771', standard_code: 'ISO 21771:2007', page_number: 18, locator: 's. 18', excerpt: 'x', confidence_status: 'verified_source' }],
              diagnostics: [{ level: 'info', code: 'validated', message: 'Tüm formül ve sabitler doğrulandı.' }],
              trace: ['d = z·m_t = 20·2 = 40 mm'],
            },
          }),
        },
      ]),
    );
    renderChat();
    const article = (await screen.findByText(/d = 40 mm/)).closest('article')!;
    expect(within(article).getByTestId('mode-badge')).toHaveTextContent('Hesap sonucu');
    expect(within(article).getByText('Qwen ≠ motor')).toBeInTheDocument();
    await userEvent.click(within(article).getByRole('button', { name: 'Ayrıntılı çözüm' }));
    const detail = await within(article).findByTestId('calc-detail');
    expect(detail).toHaveTextContent('d = z·m_n / cos β');
    expect(detail).toHaveTextContent('ISO 21771:2007');
    expect(detail).toHaveTextContent('s. 18');
    expect(detail).toHaveTextContent('Uyuşmazlık');
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/messages/m-calc/calculation'))).toBe(true));
  });
});
