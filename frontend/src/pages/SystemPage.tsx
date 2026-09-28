import { useCallback, useEffect, useState } from 'react';
import { api, formatBytes, formatDate } from '../api/client';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Status = any;

function Ok({ ok }: { ok: boolean | null | undefined }) {
  return <span className={`chip ${ok ? 'chip-ok' : 'chip-bad'}`}>{ok ? 'çalışıyor' : 'sorun var'}</span>;
}

export default function SystemPage() {
  const [s, setS] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setS(await api.get<Status>('/system/status')); setErr(null); } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (err) return <div className="page"><h1>Sistem durumu</h1><p className="error">{err}</p></div>;
  if (!s) return <div className="page"><p role="status">Yükleniyor…</p></div>;
  const c = s.corpus ?? {};
  return (
    <div className="page">
      <header className="page-head">
        <h1>Sistem durumu</h1>
        <button type="button" className="btn" onClick={() => void load()}>Yenile</button>
      </header>
      <div className="cards">
        <section className="card">
          <h2>Uygulama</h2>
          <p>{s.app.name} · {s.app.env}</p>
          <p>Bağlama: <code>{s.app.bind}</code> <span className="chip chip-ok">yalnızca localhost</span></p>
        </section>
        <section className="card">
          <h2>Veritabanı <Ok ok={s.database.ok} /></h2>
          <p>{s.database.kind} · {s.database.host}:{s.database.port}</p>
          <p>Migrasyon: {s.database.migration_revision ?? 'yok'}</p>
          {!s.database.ok && <p className="error">Docker Desktop ve PostgreSQL konteynerini başlatın: scripts\start-local.ps1</p>}
        </section>
        <section className="card">
          <h2>Ollama <Ok ok={s.ollama.reachable && s.ollama.model_available} /></h2>
          <p>Model: <code>{s.ollama.model}</code> {s.ollama.model_available ? '' : '(indirilmemiş)'}</p>
          <p>Sürüm: {s.ollama.version ?? '—'} · {s.ollama.base_url}</p>
          {s.ollama.detail && <p className="warn">{s.ollama.detail}</p>}
        </section>
        <section className="card">
          <h2>Klasör izleyici <Ok ok={s.watcher.running} /></h2>
          <p>Aralık: {s.watcher.interval_seconds} sn</p>
          <ul className="small">{s.watcher.roots.map((r: string) => <li key={r} className="mono">{r}</li>)}</ul>
          {s.watcher.last_scan && (
            <p className="small">Son tarama {formatDate(s.watcher.last_scan.finished_at)}: yeni {s.watcher.last_scan.new}, değişen {s.watcher.last_scan.changed},
              kaybolan {s.watcher.last_scan.missing}, değişmeyen {s.watcher.last_scan.unchanged}</p>
          )}
          {s.watcher.last_error && <p className="error">{s.watcher.last_error}</p>}
        </section>
        <section className="card">
          <h2>Korpus / indeks</h2>
          <p>Durum: <strong>{c.state === 'EMPTY' ? 'boş' : c.state === 'INDEXED_UNAPPROVED' ? 'indekslendi, onay bekliyor' : c.state === 'READY' ? 'hazır' : 'bilinmiyor'}</strong></p>
          <p>İndekslenmiş etkin parça: <strong>{c.indexed_active_chunks ?? 0}</strong> · Owner onaylı etkin sürüm: <strong>{c.owner_approved_active_versions ?? 0}</strong> · Kanıt için uygun parça: <strong>{c.verified_active_chunks ?? 0}</strong></p>
          <p className="small">Belgeler: {Object.entries(c.areas ?? {}).map(([a, st]) => `${a}: ${Object.entries(st as object).map(([k, v]) => `${k} ${v}`).join(', ')}`).join(' | ')}</p>
          <p className="small">Sayfalar: {Object.entries(c.pages_by_status ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ')}</p>
          <p className="small">İşler: {Object.entries(c.jobs ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'} · bekleyen taslak değer: {c.draft_values_pending ?? 0}</p>
          <p className="small">İşçi: {s.worker.running ? 'çalışıyor' : 'durdu'} · başlangıçtan beri {s.worker.processed_since_start} iş</p>
          {c.failed?.length > 0 && <ul className="error small">{c.failed.map((f: any) => <li key={f.title}>{f.title}: {f.error}</li>)}</ul>}
        </section>
        {s.self_maintenance && (
          <section className="card" data-testid="knowledge-card">
            <h2>Mühendislik bilgisi / öz-bakım</h2>
            <p>Formül kaydı: {s.self_maintenance.registry.rules} kural · {Object.entries(s.self_maintenance.registry.by_status).map(([k, v]) => `${k} ${v}`).join(' · ')}</p>
            <p className="small">Dış uygulama çelişkisi: {s.self_maintenance.registry.conflict_records} · motorla karşılaştırılan vaka: {s.self_maintenance.registry.engine_checked_cases}</p>
            <p className="small">Uyuşmazlık olayı: {s.self_maintenance.mismatch_events}{Object.keys(s.self_maintenance.mismatch_classes ?? {}).length > 0 && ` (${Object.entries(s.self_maintenance.mismatch_classes).map(([k, v]) => `${k} ${v}`).join(', ')})`}</p>
            <p className="small">Düzeltmeler: {Object.entries(s.self_maintenance.corrections).map(([k, v]) => `${k} ${v}`).join(' · ')}</p>
            <p className="small muted">Motor {s.self_maintenance.engine_version} · kayıt {s.self_maintenance.registry_fingerprint}</p>
          </section>
        )}
        <section className="card">
          <h2>Yerel yetenekler</h2>
          <p>OCR (RapidOCR/ONNX): {s.capabilities.ocr ? 'açık' : 'kapalı'}</p>
          <p>Ses dökümü (faster-whisper): {s.capabilities.transcription ? 'hazır' : 'model yok — scripts\\fetch-local-models.ps1'}</p>
          <p className="small muted">Video: arşiv + meta veri + temsili kareler; ağır video analizi ertelendi.</p>
        </section>
        {s.storage && (
          <section className="card">
            <h2>Yerel depolama</h2>
            <p className="mono small">{s.storage.data_root}</p>
            <p>Toplam: {formatBytes(s.storage.total_bytes)} · boş disk: {s.storage.disk?.free_bytes ? formatBytes(s.storage.disk.free_bytes) : '—'}</p>
            <ul className="small">{Object.entries(s.storage.sizes_bytes).map(([k, v]) => <li key={k}>{k}: {formatBytes(v as number)}</li>)}</ul>
          </section>
        )}
        {s.supabase && (
          <section className="card">
            <h2>Supabase senkronizasyonu</h2>
            <p>Çift yönlü · {s.supabase.enabled_flag ? 'etkin' : 'kapalı'}</p>
            <p>Veritabanı bağlantısı: {s.supabase.database_url_configured ? 'yapılandırıldı' : 'bekleniyor'}</p>
            <p>Durum: {({ ok: 'eşitlendi', conflict: 'çakışma var; iki sürüm de korundu', waiting: 'bağlantı veya kurulum bekleniyor', not_initialized: 'kurulum bekleniyor' } as Record<string, string>)[s.supabase.sync?.status] ?? 'bekleniyor'}</p>
            {s.supabase.sync?.last_success && <p>Son eşitleme: {formatDate(s.supabase.sync.last_success)}</p>}
            {s.supabase.sync?.conflict_count > 0 && <p className="warn">{s.supabase.sync.conflict_count} kayıt için inceleme gerekiyor. Aktarım durduruldu.</p>}
            <p className="small muted">{s.supabase.note}</p>
          </section>
        )}
        {s.providers && (
          <section className="card">
            <h2>Model sağlayıcıları</h2>
            <ul>{s.providers.providers.map((p: any) => (
              <li key={p.name}>{p.name}: {p.enabled ? 'etkin' : 'devre dışı'}{p.configured && !p.enabled ? ' (anahtar alanı dolu, yine de kullanılmaz)' : ''}</li>
            ))}</ul>
            <p className="small">Çevrimiçi çağrılar: {s.providers.online_calls_allowed ? 'izinli' : 'kapalı'}</p>
          </section>
        )}
      </div>
      {s.warnings?.length > 0 && <ul className="warn">{s.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
    </div>
  );
}
