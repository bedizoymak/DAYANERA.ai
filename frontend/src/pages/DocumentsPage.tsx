import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE, api, formatBytes, formatDate } from '../api/client';
import type { DocumentInfo, VersionInfo } from '../api/types';
import { useAuth } from '../auth/AuthContext';

interface PageRow { id: string; page_number: number; locator: string; extraction_method: string; confidence_status: string; chars: number }
interface MemberRow { member_path: string; is_dir: boolean; size_bytes: number | null; status: string; reason: string | null }
interface RelRow { direction: string; relation_type: string; document_id: string; title: string; note: string | null }

const STATUS_TR: Record<string, string> = {
  active: 'etkin', deleted: 'silindi', pending: 'bekliyor', failed: 'başarısız', archived: 'arşiv',
  indexed: 'indekslendi', stored_only: 'yalnızca arşivlendi', queued: 'kuyrukta', processing: 'işleniyor',
  superseded: 'yerine yenisi geldi',
  // verified-corpus lifecycle (document_versions.corpus_status)
  candidate: 'aday', extracted: 'onay bekliyor', needs_review: 'inceleme gerekli', verified: 'doğrulandı',
};
const tr = (s: string | null | undefined) => (s ? STATUS_TR[s] ?? s : '—');
const corpusChip = (s: string | undefined) =>
  s === 'verified' ? 'chip-ok' : s === 'failed' ? 'chip-bad' : 'chip-warn';

export default function DocumentsPage() {
  const { user } = useAuth();
  const owner = user?.role === 'owner_admin';
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [area, setArea] = useState('');
  const [selected, setSelected] = useState<DocumentInfo | null>(null);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [pages, setPages] = useState<PageRow[]>([]);
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [rels, setRels] = useState<RelRow[]>([]);
  const [previewPage, setPreviewPage] = useState(1);
  const [pageText, setPageText] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [deleteReason, setDeleteReason] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (status) params.set('status', status);
    if (category) params.set('category', category);
    if (area) params.set('area', area);
    try {
      const res = await api.get<{ items: DocumentInfo[]; total: number }>(`/documents?${params.toString()}`);
      setDocs(res.items);
      setTotal(res.total);
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [q, status, category, area]);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(d: DocumentInfo) {
    setErr(null);
    setMsg(null);
    setConfirmDelete(false);
    setPageText(null);
    setPreviewPage(1);
    try {
      const full = await api.get<DocumentInfo>(`/documents/${d.id}`);
      setSelected(full);
      setVersions(await api.get<VersionInfo[]>(`/documents/${d.id}/versions`));
      setRels(await api.get<RelRow[]>(`/documents/${d.id}/relationships`));
      if (full.current_version) {
        setPages(await api.get<PageRow[]>(`/documents/${d.id}/versions/${full.current_version.id}/pages`));
        setMembers(
          full.current_version.category === 'zip' || full.current_version.category === 'tar'
            ? await api.get<MemberRow[]>(`/documents/${d.id}/versions/${full.current_version.id}/archive-members`)
            : [],
        );
      } else {
        setPages([]);
        setMembers([]);
      }
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function act(fn: () => Promise<unknown>, okMsg: string) {
    setErr(null);
    try {
      await fn();
      await load();
      if (selected) await open(selected); // open() clears messages, so set the success text afterwards
      setMsg(okMsg);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function upload(files: FileList | null) {
    if (!files) return;
    for (const f of Array.from(files)) {
      await act(() => api.upload('/documents/upload', f), `${f.name} arşive yüklendi; arka planda işleniyor.`);
    }
    if (fileRef.current) fileRef.current.value = '';
  }

  async function showPage(n: number) {
    if (!selected?.current_version) return;
    try {
      const p = await api.get<{ text: string }>(`/documents/${selected.id}/versions/${selected.current_version.id}/pages/${n}`);
      setPageText(p.text);
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const cv = selected?.current_version;
  const canPreview = cv && (cv.category === 'pdf' || ((cv.metadata?.media as string[] | undefined)?.length ?? 0) > 0);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Belge arşivi</h1>
        <div className="actions">
          <input ref={fileRef} type="file" multiple id="doc-upload" className="sr-only" onChange={(e) => void upload(e.target.files)} />
          <label htmlFor="doc-upload" className="btn">Dosya yükle (tüm türler)</label>
          {owner && (
            <>
              <button type="button" className="btn" onClick={() => void act(() => api.post('/ingestion/scan'), 'İzlenen klasörler tarandı.')}>
                Klasörü şimdi tara
              </button>
              <button type="button" className="btn" onClick={() => void act(() => api.post('/ingestion/reindex-all'), 'Tam yeniden indeksleme kuyruğa alındı.')}>
                Tümünü yeniden indeksle
              </button>
            </>
          )}
        </div>
      </header>
      <p className="muted small">
        Doğrulanmış korpus yalnızca <code>iso booklets</code> klasöründeki etkin dosyalardır. Yüklemeler arşivlenir ve aranabilir,
        ancak doğrulanmış kaynak sayılmaz. Silme mantıksaldır: ham sürümler asla silinmez.
      </p>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); void load(); }} role="search">
        <label>Ara <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="başlık, dosya adı, ISO kodu" /></label>
        <label>Durum
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">tümü</option><option value="active">etkin</option><option value="pending">bekliyor</option>
            <option value="deleted">silindi</option><option value="failed">başarısız</option>
          </select>
        </label>
        <label>Tür
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">tümü</option><option value="pdf">PDF</option><option value="docx">Word</option><option value="xlsx">Excel</option>
            <option value="pptx">PowerPoint</option><option value="text">Metin</option><option value="image">Görüntü</option>
            <option value="audio">Ses</option><option value="video">Video</option><option value="zip">ZIP</option><option value="unknown">Diğer</option>
          </select>
        </label>
        <label>Alan
          <select value={area} onChange={(e) => setArea(e.target.value)}>
            <option value="">tümü</option><option value="iso-disli">ISO dişli korpusu</option><option value="ekler">Ekler/yüklemeler</option>
          </select>
        </label>
        <button type="submit" className="btn">Filtrele</button>
      </form>
      {msg && <p className="ok" role="status">{msg}</p>}
      {err && <p className="error" role="alert">{err}</p>}
      <div className={selected ? 'split' : ''}>
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">Belgeler</caption>
            <thead>
              <tr><th>Başlık</th><th>Durum</th><th>Tür</th><th>Sürüm</th><th>İşleme</th><th>Güncellendi</th></tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className={selected?.id === d.id ? 'row-selected' : ''}>
                  <td>
                    <button type="button" className="link-btn" onClick={() => void open(d)}>{d.title}</button>
                    {d.is_verified_corpus && (
                      <span className={`chip ${corpusChip(d.current_version?.corpus_status)}`}>
                        ISO korpusu · {tr(d.current_version?.corpus_status)}
                      </span>
                    )}
                  </td>
                  <td><span className={`chip st-${d.status}`}>{tr(d.status)}</span></td>
                  <td>{d.current_version?.category ?? '—'}</td>
                  <td>{d.current_version ? `v${d.current_version.version_number}` : '—'}</td>
                  <td>{tr(d.current_version?.ingestion_status)}</td>
                  <td>{formatDate(d.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">{total} belge</p>
        </div>
        {selected && (
          <section className="detail" aria-label="Belge ayrıntısı">
            <h2>{selected.title}</h2>
            <dl className="meta">
              <dt>ISO kodu</dt><dd>{selected.standard_code ?? '—'}</dd>
              <dt>Dosya</dt><dd>{selected.original_filename}</dd>
              <dt>Kaynak</dt><dd>{selected.source_kind}{selected.source_relpath ? ` · ${selected.source_relpath}` : ''}</dd>
              <dt>Durum</dt><dd>{tr(selected.status)}{selected.delete_reason ? ` (${selected.delete_reason})` : ''}</dd>
              {cv && (
                <>
                  <dt>Etkin sürüm</dt><dd>v{cv.version_number} · {formatBytes(cv.size_bytes)} · {cv.mime_type}</dd>
                  <dt>SHA-256</dt><dd className="mono small">{cv.sha256}</dd>
                  <dt>İşleme</dt><dd>{tr(cv.ingestion_status)}{cv.ingestion_error ? ` — ${cv.ingestion_error}` : ''}</dd>
                  {cv.corpus_status && (
                    <>
                      <dt>Korpus durumu</dt>
                      <dd>
                        <span className={`chip ${corpusChip(cv.corpus_status)}`}>{tr(cv.corpus_status)}</span>
                        {cv.parser ? ` · ayrıştırıcı: ${cv.parser}` : ''}
                        {cv.review_note ? ` · not: ${cv.review_note}` : ''}
                      </dd>
                    </>
                  )}
                  {(cv.quality_report?.gates ?? []).filter((g) => g.status !== 'pass').length > 0 && (
                    <>
                      <dt>Kalite kapıları</dt>
                      <dd>
                        <ul className="small">
                          {(cv.quality_report?.gates ?? []).filter((g) => g.status !== 'pass').map((g) => (
                            <li key={g.id}>
                              {g.id}: {g.status}{g.detail ? ` — ${g.detail}` : ''}
                              {g.pages && g.pages.length > 0 ? ` (s. ${g.pages.slice(0, 12).join(', ')})` : ''}
                            </li>
                          ))}
                        </ul>
                      </dd>
                    </>
                  )}
                  {cv.extraction_summary?.counts && (
                    <><dt>Çıkarım</dt><dd>{Object.entries(cv.extraction_summary.counts).map(([k, v]) => `${k}: ${v}`).join(' · ')}</dd></>
                  )}
                  {cv.extraction_summary?.stored_only_reason && (<><dt>Not</dt><dd>{cv.extraction_summary.stored_only_reason}</dd></>)}
                </>
              )}
            </dl>
            {cv?.extraction_summary?.warnings && cv.extraction_summary.warnings.length > 0 && (
              <ul className="warnings">{cv.extraction_summary.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            )}
            <div className="actions">
              {cv && (
                <a className="btn" href={`${API_BASE}/documents/${selected.id}/versions/${cv.id}/download`}>İndir</a>
              )}
              {selected.status !== 'deleted' && (owner || selected.source_kind !== 'watched') && (
                <button type="button" className="btn" onClick={() => void act(() => api.post(`/documents/${selected.id}/reindex`), 'Yeniden indeksleme kuyruğa alındı.')}>
                  Yeniden indeksle
                </button>
              )}
              {selected.status !== 'deleted' && !confirmDelete && (owner || selected.source_kind !== 'watched') && (
                <button type="button" className="btn btn-danger" onClick={() => setConfirmDelete(true)}>Sil (mantıksal)</button>
              )}
              {selected.status === 'deleted' && owner && (
                <button type="button" className="btn" onClick={() => void act(() => api.post(`/documents/${selected.id}/restore`), 'Belge geri yüklendi ve yeniden indeksleniyor.')}>
                  Geri yükle
                </button>
              )}
            </div>
            {confirmDelete && (
              <div className="confirm-box" role="dialog" aria-label="Silme onayı">
                <p>Belge etkin kaynaklardan çıkarılacak; ham sürümler korunur ve işlem denetim kaydına yazılır.</p>
                <label>Gerekçe <input value={deleteReason} onChange={(e) => setDeleteReason(e.target.value)} /></label>
                <button type="button" className="btn btn-danger" onClick={() => void act(() => api.del(`/documents/${selected.id}`, { reason: deleteReason || 'Kullanıcı tarafından silindi' }), 'Belge mantıksal olarak silindi.')}>
                  Silmeyi onayla
                </button>
                <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>Vazgeç</button>
              </div>
            )}
            {canPreview && cv && (
              <div className="preview">
                <h3>Önizleme</h3>
                {cv.category === 'pdf' && (
                  <label>Sayfa{' '}
                    <input type="number" min={1} max={cv.page_count ?? 1} value={previewPage}
                      onChange={(e) => setPreviewPage(Math.max(1, Number(e.target.value)))} />
                  </label>
                )}
                <img alt={`${selected.title} önizleme sayfa ${previewPage}`} src={`${API_BASE}/documents/${selected.id}/versions/${cv.id}/preview?page=${previewPage}`} />
              </div>
            )}
            <h3>Sürüm geçmişi</h3>
            <table className="table compact">
              <thead><tr><th>Sürüm</th><th>Durum</th><th>İşleme</th><th>Boyut</th><th>Tarih</th><th /></tr></thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.id}>
                    <td>v{v.version_number}{v.is_active ? ' (etkin)' : ''}</td>
                    <td>{tr(v.state)}</td>
                    <td>{tr(v.ingestion_status)}</td>
                    <td>{formatBytes(v.size_bytes)}</td>
                    <td>{formatDate(v.created_at)}</td>
                    <td><a href={`${API_BASE}/documents/${selected.id}/versions/${v.id}/download`}>indir</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rels.length > 0 && (
              <>
                <h3>İlişkiler</h3>
                <ul>{rels.map((r) => <li key={`${r.direction}-${r.document_id}`}>{r.direction === 'out' ? '→' : '←'} {r.relation_type}: {r.title}</li>)}</ul>
              </>
            )}
            {members.length > 0 && (
              <>
                <h3>Arşiv üyeleri</h3>
                <table className="table compact">
                  <thead><tr><th>Yol</th><th>Boyut</th><th>Durum</th></tr></thead>
                  <tbody>{members.map((m) => (
                    <tr key={m.member_path}><td className="mono small">{m.member_path}</td><td>{m.size_bytes ?? '—'}</td><td>{m.status}{m.reason ? ` (${m.reason})` : ''}</td></tr>
                  ))}</tbody>
                </table>
              </>
            )}
            {pages.length > 0 && (
              <>
                <h3>Sayfalar / bölümler ({pages.length})</h3>
                <div className="page-grid">
                  {pages.slice(0, 300).map((p) => (
                    <button key={p.id} type="button" className={`page-chip cs-${p.confidence_status}`} title={`${p.extraction_method} · ${p.confidence_status}`} onClick={() => void showPage(p.page_number)}>
                      {p.page_number}
                    </button>
                  ))}
                </div>
                <p className="muted small">Renkler: yeşil doğrulanmış kaynak, turuncu taslak çıkarım (OCR/döküm), mavi kullanıcı onaylı, gri doğrulanmamış.</p>
                {pageText !== null && <pre className="page-text">{pageText}</pre>}
              </>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
