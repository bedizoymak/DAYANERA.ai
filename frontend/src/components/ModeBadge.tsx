import type { AnswerMode } from '../api/types';

export const MODE_LABELS: Record<AnswerMode, string> = {
  general: 'Genel sohbet',
  verified_source: 'Doğrulanmış kaynak cevabı',
  calculation: 'Hesap sonucu',
  draft_extraction: 'Taslak çıkarım',
  unverified: 'Doğrulanamadı',
};

const HINTS: Record<AnswerMode, string> = {
  general: 'Yerel modelin genel sohbet yanıtı; ISO ile doğrulanmış bilgi değildir.',
  verified_source: 'Yalnızca etkin, indekslenmiş yerel ISO pasajlarına dayanır.',
  calculation: 'Deterministik hesap motoru tarafından doğrulanmış sonuç.',
  draft_extraction: 'OCR/döküm kaynaklı taslak; onaylanmadan teknik girdi değildir.',
  unverified: 'Yanıt doğrulanamadı veya hata oluştu.',
};

export default function ModeBadge({ mode }: { mode: AnswerMode | null }) {
  if (!mode) return null;
  return (
    <span className={`mode-badge mode-${mode}`} title={HINTS[mode]} data-testid="mode-badge">
      {MODE_LABELS[mode]}
    </span>
  );
}
