import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import MessageView, { refusalHint } from '../components/MessageView';
import type { Message } from '../api/types';
import { msg } from './mockApi';

const REFUSAL = 'Bu kaynak setinde doğrulayamadım';

function renderMsg(partial: Record<string, unknown>) {
  return render(<MemoryRouter><MessageView message={msg(partial) as unknown as Message} /></MemoryRouter>);
}

describe('Ret açıklaması (Step 2 Order B)', () => {
  it('eksik standart için ipucu gösterir, ret metnine dokunmaz', () => {
    renderMsg({ content: REFUSAL, answer_mode: 'unverified',
      metadata: { refusal: { reason: 'no_passages', codes_requested: ['ISO 2768'], codes_missing: ['ISO 2768'] } } });
    expect(screen.getByText(REFUSAL)).toBeInTheDocument();
    const hint = screen.getByTestId('refusal-hint');
    expect(hint).toHaveTextContent('İstenen standart yüklü değil: ISO 2768. "hangi standartlar var" yazarak listeyi görebilirsiniz.');
    // the hint is rendered outside the message body, the content stays exact
    expect(document.querySelector('.msg-body')?.textContent).toBe(REFUSAL);
  });

  it('neden türüne göre Türkçe ipucu üretir', () => {
    expect(refusalHint({ reason: 'no_passages', codes_missing: [] })).toBe('Bu konu yüklü standartlarda bulunamadı.');
    expect(refusalHint({ reason: 'low_relevance' })).toBe('Bu konu yüklü standartlarda bulunamadı.');
    expect(refusalHint({ reason: 'unsupported_numbers' })).toBe('Model yanıtındaki bazı değerler kaynakta doğrulanamadı.');
    expect(refusalHint(undefined)).toBeNull();
  });

  it('yüklü ama onaylanmamış standardı ayrı açıklar', () => {
    expect(refusalHint({ reason: 'document_not_verified', codes_missing: [], codes_unverified: ['ISO 54'] })).toBe(
      'İstenen standart yüklü ama henüz doğrulanmış korpusa alınmadı (inceleme/onay bekliyor): ISO 54.');
  });

  it('ret olmayan yanıtta ipucu göstermez; envanter yanıtında "Sistem bilgisi" çipi vardır', () => {
    renderMsg({ content: '- ISO 53:1998 — Cylindrical gears', answer_mode: 'general', metadata: { kind: 'corpus_inventory' } });
    expect(screen.queryByTestId('refusal-hint')).not.toBeInTheDocument();
    expect(screen.getByText('Sistem bilgisi')).toBeInTheDocument();
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('Genel sohbet');
  });
});
