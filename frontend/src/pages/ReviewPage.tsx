import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { CorpusReviewItem, DraftPage, ExtractedValue } from '../api/types';
import { useAuth } from '../auth/AuthContext';

type Tab = 'values' | 'pages' | 'corpus';
type CorpusAction = 'approve' | 'revoke' | 'reject';

const corpusStatusTr: Record<string, string> = {
  candidate: 'aday', extracted: 'onay bekliyor', needs_review: 'inceleme gerekli', verified: 'doğrulandı', failed: 'reddedildi',
};

function CorpusRow({
  item, note, busy, onNote, onAction,
}: {
  item: CorpusReviewItem;
  note: string;
  busy: boolean;
  onNote: (value: string) => void;
  onAction: (action: CorpusAction) => void;
}) {
  const needsNote = item.corpus_status === 'needs_review';
  const gates = item.quality_report?.gates ?? item.attention ?? [];
  const canApprove = ['extracted', 'needs_review'].includes(item.corpus_status);
  const canReject = ['candidate', 'extracted', 'needs_review'].includes(item.corpus_status);
  const canRevoke = item.corpus_status === 'verified';

  return (
    <article className="card" data-testid={`corpus-review-${item.document_id}`}>
      <header className="page-head">
        <div>
          <h2>{item.standard_code ?? item.title}</h2>
          <p className="muted small">{item.title} · sürüm {item.version_number} · {item.parser ?? 'ayrıştırıcı bilinmiyor'}</p>
        </div>
        <span className={`chip ${item.corpus_status === 'verified' ? 'chip-ok' : item.corpus_status === 'failed' ? 'chip-bad' : 'chip-warn'}`}>
          {corpusStatusTr[item.corpus_status] ?? item.corpus_status}
        </span>
      </header>
      <dl className="meta">
        <dt>İndeksleme</dt><dd>{item.ingestion_status} · {item.chunks} parça · {item.page_count ?? '—'} sayfa</dd>
        <dt>Kalite kapıları</dt>
        <dd>
          {gates.length === 0 ? <span className="chip chip-ok">tamamı geçti</span> : (
            <ul className="small">
              {gates.map((gate) => (
                <li key={gate.id}>{gate.id}: {gate.status}{gate.detail ? ` — ${gate.detail}` : ''}
                  {gate.pages?.length ? ` (s. ${gate.pages.slice(0, 12).join(', ')})` : ''}</li>
              ))}
            </ul>
          )}
        </dd>
        <dt>Kaynak özeti</dt><dd className="mono small">SHA-256: {item.sha256}</dd>
        {item.review_note && <><dt>Son not</dt><dd>{item.review_note}</dd></>}
      </dl>
      {item.corpus_status !== 'verified' && (
        <p className="muted small">Onay yalnızca bu etkin, indekslenmiş sürümü doğrulanmış ISO kanıtı yapar; taslak veya incelenmemiş parçalar otomatik olarak kanıt sayılmaz.</p>
      )}
      <label>
        İnceleme notu{needsNote || canReject || canRevoke ? ' (zorunlu)' : ' (isteğe bağlı)'}
        <textarea value={note} onChange={(e) => onNote(e.target.value)} rows={3} maxLength={2000}
          placeholder="Kontrol edilen kapılar, sayfalar ve karar gerekçesi" disabled={busy} />
      </label>
      <div className="actions">
        {canApprove && <button type="button" className="btn btn-primary" disabled={busy || (needsNote && !note.trim())}
          onClick={() => onAction('approve')}>Onayla</button>}
        {canReject && <button type="button" className="btn btn-danger" disabled={busy || !note.trim()}
          onClick={() => onAction('reject')}>Reddet</button>}
        {canRevoke && <button type="button" className="btn btn-danger" disabled={busy || !note.trim()}
          onClick={() => onAction('revoke')}>Onayı geri al</button>}
      </div>
    </article>
  );
}

function ValueRow({ v, onDone }: { v: ExtractedValue; onDone: (msg: string) => void }) {
  const [value, setValue] = useState(v.value !== null ? String(v.value) : '');
  const [unit, setUnit] = useState(v.unit ?? '');
  const [note, setNote] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(kind: 'confirm' | 'reject') {
    setBusy(true);
    setErr(null);
    try {
      if (kind === 'confirm') {
        const num = Number(value.replace(',', '.'));
        if (!Number.isFinite(num)) throw new Error('Geçerli bir sayı girin.');
        await api.post(`/extractions/values/${v.id}/confirm`, { value: num, unit, note: note || null });
        onDone(`“${v.label}” onaylandı (kullanıcı onaylı).`);
      } else {
        await api.post(`/extractions/values/${v.id}/reject`, { note: note || null });
        onDone(`“${v.label}” reddedildi.`);
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr data-testid="draft-value-row">
      <td>
        <span className="mode-badge mode-draft_extraction">Taslak çıkarım</span>
        <div className="small">{v.document_title}</div>
        <div className="muted small">{v.locator} · sürüm {v.version_number} · {v.extraction_method}</div>
      </td>
      <td><strong>{v.label}</strong><div className="muted small mono">{v.raw_text}</div></td>
      <td className="small">{v.context}</td>
      <td>
        <label className="sr-only" htmlFor={`val-${v.id}`}>Değer</label>
        <input id={`val-${v.id}`} className="num-input" value={value} onChange={(e) => setValue(e.target.value)} />
        <label className="sr-only" htmlFor={`unit-${v.id}`}>Birim</label>
        <input id={`unit-${v.id}`} className="unit-input" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="birim" />
      </td>
      <td>
        <label className="sr-only" htmlFor={`note-${v.id}`}>Not</label>
        <input id={`note-${v.id}`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="not (isteğe bağlı)" />
        <div className="row-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void run('confirm')}>Onayla</button>
          <button type="button" className="btn" disabled={busy} onClick={() => void run('reject')}>Reddet</button>
        </div>
        {err && <p className="error small">{err}</p>}
      </td>
    </tr>
  );
}

export default function ReviewPage() {
  const { user } = useAuth();
  const owner = user?.role === 'owner_admin';
  const [tab, setTab] = useState<Tab>('values');
  const [values, setValues] = useState<ExtractedValue[]>([]);
  const [pages, setPages] = useState<DraftPage[]>([]);
  const [corpus, setCorpus] = useState<CorpusReviewItem[]>([]);
  const [corpusNotes, setCorpusNotes] = useState<Record<string, string>>({});
  const [corpusBusy, setCorpusBusy] = useState<string | null>(null);
  const [pageTotal, setPageTotal] = useState(0);
  const [selected, setSelected] = useState<DraftPage | null>(null);
  const [text, setText] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const v = await api.get<{ items: ExtractedValue[] }>('/extractions/values?status=draft_extraction');
      setValues(v.items);
      const p = await api.get<{ items: DraftPage[]; total: number }>('/extractions/pages?status=draft_extraction&limit=200');
      setPages(p.items);
      setPageTotal(p.total);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  const loadCorpus = useCallback(async () => {
    if (!owner) return;
    try {
      const result = await api.get<{ items: CorpusReviewItem[] }>('/corpus/status');
      setCorpus(result.items.filter((item) => item.is_verified_corpus));
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [owner]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (tab === 'corpus') void loadCorpus();
  }, [tab, loadCorpus]);

  async function openPage(p: DraftPage) {
    const full = await api.get<DraftPage>(`/extractions/pages/${p.id}`);
    setSelected(full);
    setText(full.text);
  }

  async function decidePage(kind: 'confirm' | 'reject') {
    if (!selected) return;
    setErr(null);
    try {
      if (kind === 'confirm') await api.post(`/extractions/pages/${selected.id}/confirm`, { text, note: null });
      else await api.post(`/extractions/pages/${selected.id}/reject`, { note: null });
      setMsg(kind === 'confirm' ? 'Sayfa metni onaylandı; artık kullanıcı onaylı kaynak olarak aranabilir.' : 'Sayfa reddedildi.');
      setSelected(null);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function decideCorpus(item: CorpusReviewItem, action: CorpusAction) {
    const note = (corpusNotes[item.document_id] ?? '').trim();
    if ((item.corpus_status === 'needs_review' || action !== 'approve') && !note) {
      setErr('Bu karar için inceleme notu zorunludur.');
      return;
    }
    setCorpusBusy(item.document_id);
    setErr(null);
    try {
      await api.post(`/documents/${item.document_id}/corpus/${action}`, { note: note || null });
      setMsg(action === 'approve' ? 'Sürüm owner onayıyla doğrulanmış korpusa alındı.'
        : action === 'revoke' ? 'Korpus onayı geri alındı; yeniden inceleme gerekiyor.' : 'Sürüm korpusa alınmadı ve reddedildi.');
      await loadCorpus();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setCorpusBusy(null);
    }
  }

  return (
    <div className="page">
      <header className="page-head"><h1>İnceleme kuyruğu</h1></header>
      <p className="muted small">
        OCR, görsel çıkarım ve ses dökümünden gelen değerler <strong>Taslak çıkarım</strong>dır. Yetkili kullanıcı onaylamadan
        hesaplara, doğrulanmış hafızaya veya etkin teknik bilgilere giremez. Onay; işlem yapan kişi, zaman, özgün değer ve
        kaynak sürümüyle birlikte denetim kaydına yazılır.
      </p>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'values'} className={tab === 'values' ? 'tab active' : 'tab'} onClick={() => setTab('values')}>
          Değerler ({values.length})
        </button>
        <button type="button" role="tab" aria-selected={tab === 'pages'} className={tab === 'pages' ? 'tab active' : 'tab'} onClick={() => setTab('pages')}>
          OCR/döküm sayfaları ({pageTotal})
        </button>
        {owner && (
          <button type="button" role="tab" aria-selected={tab === 'corpus'} className={tab === 'corpus' ? 'tab active' : 'tab'} onClick={() => setTab('corpus')}>
            ISO korpusu ({corpus.filter((item) => item.corpus_status !== 'verified').length} bekliyor)
          </button>
        )}
      </div>
      {msg && <p className="ok" role="status">{msg}</p>}
      {err && <p className="error" role="alert">{err}</p>}
      {tab === 'values' && (
        <div className="table-wrap">
          {values.length === 0 ? <p className="muted">Bekleyen taslak değer yok.</p> : (
            <table className="table">
              <thead><tr><th>Kaynak</th><th>Etiket / ham metin</th><th>Bağlam</th><th>Değer (düzenlenebilir)</th><th>Karar</th></tr></thead>
              <tbody>
                {values.map((v) => (
                  <ValueRow key={v.id} v={v} onDone={(m) => { setMsg(m); void load(); }} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {tab === 'pages' && (
        <div className="split">
          <ul className="page-list">
            {pages.map((p) => (
              <li key={p.id}>
                <button type="button" className="link-btn" onClick={() => void openPage(p)}>
                  {p.document_title} — {p.locator}
                </button>
                <span className="muted small"> · {p.extraction_method}{p.ocr_mean_confidence ? ` · güven ${(p.ocr_mean_confidence * 100).toFixed(0)}%` : ''}</span>
              </li>
            ))}
          </ul>
          {selected && (
            <section className="detail">
              <h2>{selected.document_title} — {selected.locator}</h2>
              <span className="mode-badge mode-draft_extraction">Taslak çıkarım</span>
              <label htmlFor="page-edit">Metni kontrol edip düzeltin</label>
              <textarea id="page-edit" className="page-edit" value={text} onChange={(e) => setText(e.target.value)} rows={18} />
              <div className="actions">
                <button type="button" className="btn btn-primary" onClick={() => void decidePage('confirm')}>Onayla</button>
                <button type="button" className="btn" onClick={() => void decidePage('reject')}>Reddet</button>
              </div>
            </section>
          )}
        </div>
      )}
      {tab === 'corpus' && owner && (
        <section aria-label="ISO korpusu owner incelemesi">
          <p className="muted small">
            Her etkin ISO sürümü ayrı değerlendirilir. Kalite kapıları, kaynak özeti ve çıkarım durumu incelenmeden onay vermeyin;
            toplu onay yoktur. Onay yalnızca sürümün mevcut fingerprint&apos;ine bağlanır ve yeniden çıkarımda tekrar değerlendirme gerekir.
          </p>
          {corpus.length === 0 ? <p className="muted">İncelenecek ISO sürümü yok.</p> : (
            <div className="split">
              {corpus.map((item) => (
                <CorpusRow key={item.document_id} item={item} note={corpusNotes[item.document_id] ?? ''}
                  busy={corpusBusy === item.document_id}
                  onNote={(value) => setCorpusNotes((current) => ({ ...current, [item.document_id]: value }))}
                  onAction={(action) => void decideCorpus(item, action)} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
