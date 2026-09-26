# Docker ↔ Supabase senkronizasyonu

Yerel uygulama Docker PostgreSQL ile çalışmaya devam eder. Supabase bağlantısı
açıldığında 17 uygulama tablosu periyodik olarak iki yönde eşitlenir. Bu özellik
önceki yalnızca yerel beta sözleşmesine kullanıcının isteğiyle eklenmiştir.
Canlı Supabase bağlantısı, gerçek kimlik bilgileriyle ayrıca doğrulanmalıdır.

## Kurulum

1. Supabase **Connect → Session pooler** (5432) veya Direct connection adresini
   kopyalayın. Veritabanı parolasını URI içinde URL-encode ederek doldurun.
2. Git dışında tutulan kök `.env` dosyasına ekleyin:

   ```dotenv
   SUPABASE_URL=https://emzxjbcsxmuhucorjrfv.supabase.co
   SUPABASE_DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres
   SUPABASE_ENABLED=false
   SUPABASE_SYNC_INTERVAL_SECONDS=60
   ```

   `USER`, `HOST`, `PASSWORD` yer tutucudur; paneldeki değerleri kullanın.
   Publishable/secret API anahtarı gerekli değildir. Parolayı sohbete, Git'e
   veya frontend `VITE_*` ayarlarına koymayın. TLS sertifikası ve sunucu adı
   doğrulanır; sistem sertifika deposu kullanılır. Gerekirse resmi Supabase
   CA dosyası `sslrootcert` parametresiyle seçilebilir. TLS hatasında doğrulamayı kapatmayın.
3. Proje kökünden:

   ```powershell
   Push-Location backend
   .\.venv\Scripts\python.exe -m app.cli sync-init
   .\.venv\Scripts\python.exe -m app.cli sync-once
   .\.venv\Scripts\python.exe -m app.cli sync-status
   Pop-Location
   ```

4. İlk aktarım başarılı olduktan sonra `SUPABASE_ENABLED=true` yapıp uygulamayı
   yeniden başlatın. Sistem durumu sayfası son başarıyı ve çakışma durumunu gösterir.

`sync-init`, bulutta yalnızca yeni **dayanera** şemasını kurar; Supabase'in
`public`, `auth`, `storage` şemalarını değiştirmez. Önceden var olan ve bu yerel
veritabanıyla eşleşmeyen `dayanera` şemasını sahiplenmez veya silmez.
Yerelde `dayanera_sync` ilerleme tablolarını oluşturur; senkronize tablolardaki
yabancı anahtarları transaction sonunda denetlenebilir hale getirir. Normal
uygulama işlemlerinde anında denetleme devam eder.

## Kapsam ve bulutta düzenleme

- Kullanıcılar (parola hashleri ve roller dahil), erişim izinleri, bilgi alanları,
  belge/sürüm/sayfa/parça kayıtları, arşiv üyeleri, belge ilişkileri, çıkarılmış
  değerler, sohbetler, mesajlar, ek ve kaynak ilişkileri, hesaplamalar, hafıza,
  öneri notlarının veritabanı kayıtları eşitlenir.
- Oturumlar (`auth_sessions`), işler (`ingestion_jobs`), makine durumu
  (`system_state`) ve değiştirilemez denetim günlüğü (`audit_events`) yereldir.
- Dosyalar, PDF'ler, not dosyaları, model ağırlıkları ve klasörler **taşınmaz**.
  Bulutta eklenen bir belge kaydı, dosyasını yerel diskte oluşturmaz. Dosya
  indirme ve OCR için ilgili asıl dosyaların ayrıca yerelde bulunması gerekir.
- Supabase SQL Editor veya yetkili veritabanı bağlantısıyla `dayanera` şemasındaki
  gerçek tablolarda düzenleme yapılabilir. İlişkiler, UUID anahtarlar ve uygulama
  kuralları korunmalıdır. Bu yetki uygulama yetkilendirmesini atlayan yönetici
  yetkisidir; Supabase Auth ile uygulama girişi entegre edilmemiştir.
- Şema API'ye açılmaz. `anon`, `authenticated`, `service_role` erişimleri geri
  alınır; tablolar RLS ile korunur. Yalnızca yetkili DB bağlantısı kullanılır.
- Şema/DDL değişiklikleri otomatik eşitlenmez; kolon uyumsuzluğunda aktarım durur.

## Çakışma ve kesinti

Her kaydın iki tarafta ortaklaşa görülen son halinin hash'i yerelde tutulur.
Tek taraftaki değişiklik diğer tarafa gider. İki taraf aynı sonuca ulaşmışsa
ek yazma yapılmaz. Aynı kayıt iki tarafta farklı değişmişse **tüm tur yazma
yapılmadan durur**. Silme/güncelleme çatışması da aynı şekilde korunur.
İlk eşleştirmede aynı kimlikle farklı kayıtlar varsa taraf seçilmez.

`sync-status` çakışan tablo ve anahtarları verir; içerikleri veya parolaları
loglara dökmez. İki kaydı inceleyip istenen ortak içeriği iki tarafta da aynı
yapın, ardından yeniden çalıştırın. Otomatik “en son yazan kazanır” yoktur.
Şimdilik arayüzde çakışma düzenleyicisi bulunmaz.

Başarılı turda her veritabanı kendi içinde transaction kullanır. İki veritabanı
arasında dağıtık atomik commit yoktur: uzak commit sonrası yerel commit kesilirse
eski karşılaştırma bilgisi korunur; sonraki tur eşitler veya çakışma bildirir.
Başarısız tur yerel kullanımı kapatmaz; bekleme süresi kademeli olarak en fazla
15 dakikaya çıkar. Yeniden başlatmada ilerleme PostgreSQL'den okunur.

## Sınırlar

Bu sürüm **bir yerel veritabanı + bir Supabase veritabanı** çifti içindir.
Birden fazla bağımsız Docker kurulumu aynı bulut şemasına bağlanamaz. Eşleşme
kimliklerini veya `dayanera_sync` kayıtlarını elle silmeyin. Yerel yedeklere
`dayanera_sync` şemasını da dahil edin. Senkronizasyon, silmeleri de aktardığı
için bağımsız yedeklemenin yerini tutmaz.

Küçük ekip beta sürümü tam tablo karşılaştırması kullanır: taraf başına en fazla
100.000 kayıt / 64 MiB JSON. Sınır aşılırsa durur; veri atlamaz. Karşılaştırma
boyunca yazmalar kısa süre bekleyebilir; okumalar devam eder. Büyük veri veya
yüksek yazma trafiği için değişiklik kuyruğu / CDC tasarımına geçilmelidir.
Yabancı anahtar veya benzersizlik kuralı çatışırsa tur geri alınır. İki benzersiz
değerin yer değiştirmesi gibi işlemler elle uzlaştırma gerektirebilir.

Özgün `IMPLEMENTATION_REPORT.md` önceki yerel beta sürümünü anlatır; bu ek
özelliğin canlı Supabase kabul testi sayılmaz.
