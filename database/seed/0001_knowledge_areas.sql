-- DAYANERA.ai - idempotent reference seed data.
-- Knowledge areas define data scopes. Only areas with
-- is_verified_corpus = true can support "Doğrulanmış kaynak cevabı".
INSERT INTO knowledge_areas (slug, name, description, is_verified_corpus)
VALUES
    ('iso-disli', 'ISO dişli korpusu',
     'Doğrulanmış mühendislik korpusu: iso booklets klasöründeki etkin ISO belgeleri (dişli tasarımı, üretim, ölçüm ve kalite).',
     true),
    ('ekler', 'Ekler ve yüklemeler',
     'Sohbet ekleri ve arşive yüklenen dosyalar. Doğrulanmış teknik kaynak değildir.',
     false),
    ('izlenen-diger', 'Diğer izlenen klasörler',
     'EXTRA_WATCH_ROOTS altında izlenen dosyalar. Doğrulanmış teknik kaynak değildir.',
     false)
ON CONFLICT (slug) DO NOTHING;
