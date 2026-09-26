import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Readiness } from '../api/types';

interface CorpusInfo {
  corpus?: { empty_verified_corpus?: boolean; failed?: Array<{ title: string; error: string }>; jobs?: Record<string, number> };
}

/** Actionable Turkish warnings: Ollama / DB unavailable, empty corpus, failed ingestion. */
export default function StatusBanner() {
  const [msgs, setMsgs] = useState<string[]>([]);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function check() {
      const out: string[] = [];
      let note: string | null = null;
      try {
        const r = await api.get<Readiness>('/readiness');
        out.push(...r.messages);
        if (r.database.ok) {
          const s = await api.get<CorpusInfo>('/system/status');
          if (s.corpus?.empty_verified_corpus) {
            const queued = (s.corpus.jobs?.queued ?? 0) + (s.corpus.jobs?.running ?? 0);
            out.push(
              queued > 0
                ? `Doğrulanmış ISO korpusu henüz indeksleniyor (${queued} iş kuyrukta). Teknik sorular bu süreçte "Bu kaynak setinde doğrulayamadım" yanıtı alabilir.`
                : "Doğrulanmış ISO korpusu boş: 'iso booklets' klasörüne PDF ekleyin veya Belge arşivinden yeniden indeksleyin.",
            );
          } else if (s.corpus?.jobs && (s.corpus.jobs.queued ?? 0) + (s.corpus.jobs.running ?? 0) > 0) {
            note = `Arka planda ${(s.corpus.jobs.queued ?? 0) + (s.corpus.jobs.running ?? 0)} belge işleniyor (OCR sayfaları zaman alabilir).`;
          }
          if (s.corpus?.failed?.length) {
            out.push(`${s.corpus.failed.length} belgenin alımı başarısız oldu; Belge arşivinden ayrıntıya bakıp yeniden indeksleyin.`);
          }
        }
      } catch (e) {
        out.push((e as Error).message);
      }
      if (alive) {
        setMsgs(out);
        setInfo(note);
      }
    }
    void check();
    const t = setInterval(check, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (!msgs.length && !info) return null;
  return (
    <div className="status-banner" role="status" aria-live="polite">
      {msgs.map((m) => (
        <p key={m} className="warn">⚠ {m}</p>
      ))}
      {info && <p className="info">ℹ {info}</p>}
    </div>
  );
}
