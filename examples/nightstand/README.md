# NCS-01 — Nightstand 45 W Dar Teknik Vaka

## Sonuç

Geçici portfolyo mimarisi **HYBRID** olarak seçildi: değiştirilebilir hazır güç/USB-PD modülleri ile dar kapsamlı özel dağıtım, ölçüm, kontrol ve test yüzeyi birlikte kullanılacak. Teslim süresi veya modül entegrasyonu sorun çıkarırsa geri dönüş seçeneği **READY_MODULE**; tam **CUSTOM_PCB** ise birincil gereksinim ve tezgâh kanıtları bağlanana kadar ertelendi.

Bu bir donanım `PASS` kararı değildir. Bugünkü doğrulama durumu `BLOCKED`, fiziksel test durumu `NOT_RUN` ve G1 etkisi yoktur (`G1 = BLOCKED`). Mimari tavsiye ile doğrulama durumu özellikle ayrı tutulmuştur.

## Kanıt sınırı

Yerel birincil Nightstand etiketi veya etikete ait görsel bulunamadı. Kullanılabilen kayıt, 23.10.2026 tarihli kritik-yol analizindeki “45 W etiket toplamı” ifadesidir:

- Kaynak: `001-Kayıt Notları ve Dokümantasyon/002-Proje Dokümantasyonu/001-Power-Embedded-Interface-Portfolio/04-Arastirma-Raporlari/002-23.10.2026-Literatur-Entegrasyon-Kritik-Yol-Analizi.docx`
- SHA-256: `9dbd105da8bbb4bf6eb722db05e5296446c253ec5c60a553407d5d586d862721`
- Kanıt sınıfı: `HANDOFF_ONLY` — değer yalnız yerel analiz/handoff kaydında aktarılıyor; üçüncü taraf teknik kaynak veya birincil müşteri kaydı değildir.
- Desteklediği dar iddia: toplam etiket değeri 45 W olarak aktarılmıştır.
- Desteklemediği iddialar: ölçülmüş güç, port sayısı, port başına PD profili, eşzamanlı güç politikası, giriş/çıkış gerilimi veya akımı, verim, derating, termal sınır, exact-part seçimi.

Bu nedenle model 45 W’ı volt/ampere, ray kapasitesine veya PD sözleşmesine dönüştürmez. Ürün gerilimi, akımı, eşik değeri veya parça numarası üretilmemiştir.

## Çalışan teknik model

`ncs01.py`, girdiyi ve yerel kaynak byte’larını SHA-256 ile bağlayan deterministik bir toplam-yük fizibilite modelidir. Yalnız watt alanında sınır hesabı yapar. `ASSUMPTION` girdisi pozitif marj verse bile donanım `PASS` üretemez.

Girdi: `ncs01-nightstand-v1.json`

| Senaryo | Kapasite kaynağı | Brüt marj | Sonuç | Anlam |
|---|---|---:|---|---|
| Mevcut kanıt | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | Kullanılabilir yük-tarafı kapasitesi bağlı değil. |
| 44 W | `ASSUMPTION` | −1 W | `FAIL` | Bu varsayım gerçekleşirse etiket toplamının altında kalır. |
| 45 W | `ASSUMPTION` | 0 W | `UNKNOWN` | Brüt marj yok; kayıp, derating ve termal kanıt yok. |
| 60 W | `ASSUMPTION` | +15 W | `UNKNOWN` | Pozitif aritmetik marj, port dağılımı ve fiziksel kanıt yerine geçmez. |

`FAIL`, “ürünün ölçülmüş arızası” anlamına gelmez; 44 W varsayımının 45 W etiket toplamına göre matematiksel olarak yetersiz olduğunu gösterir. `UNKNOWN`, sıfırla veya varsayılan değerle doldurulmaz.

Portfolyo senaryoları karşılaştırma girdisidir; senaryodaki `FAIL` tercih edilen mimarinin genel doğrulama durumuna otomatik olarak toplanmaz. Genel durum yalnız `PREFERRED_PROVISIONAL` mimarinin zorunlu kanıt kontrollerinden hesaplanır. Böylece 44 W varsayımının matematiksel reddi ürün arızası gibi sunulmaz, ancak senaryo tablosunda görünür kalır.

## Mimari karşılaştırma

| Mimari | Teknik ayrım | Güçlü taraf | Temel risk | Portfolyo kararı | Doğrulama |
|---|---|---|---|---|---|
| `READY_MODULE` | Güç ve PD fonksiyonları hazır modüllerde; özel kısım en az | En kısa entegrasyon yolu ve en güçlü geri dönüş seçeneği | İç davranış, paylaşım, koruma ve telemetri görünürlüğü modüle bağlı | `FALLBACK_BASELINE` | `BLOCKED / NOT_RUN` |
| `HYBRID` | Güç/PD modülleri değiştirilebilir; dağıtım, ölçüm, kontrol ve test noktaları dar özel yüzeyde | Bitirme süresi ile teknik gösterim değeri arasında en dengeli yol | İki katman arasındaki sözleşme ve fault davranışı tezgâhta doğrulanmalı | `PREFERRED_PROVISIONAL` | `BLOCKED / NOT_RUN` |
| `CUSTOM_PCB` | Dönüşüm, PD kontrolü, koruma ve dağıtım tamamen özel | En yüksek mimari kontrol ve gelecekte optimizasyon alanı | Exact gereksinim, controller kanıtı, layout ve daha geniş doğrulama yükü olmadan pahalı geri dönüş riski | `DEFERRED` | `BLOCKED / NOT_RUN` |

Seçimin gerekçesi: HYBRID, bilinmeyen PD profilini özel PCB’ye dondurmadan ölçülebilir bir özel mühendislik yüzeyi üretir. Hazır modül değiştirilebilir kaldığı için profil veya kapasite varsayımı yanlış çıkarsa bütün kartın yeniden tasarlanması gerekmez.

## Durum vektörü

| Kontrol | Durum | Kapanış kanıtı |
|---|---|---|
| Birincil kaynak bağlama | `BLOCKED` | Birincil etiket/görsel ve ürün kimliği |
| Ölçülmüş yük tanımı | `UNKNOWN` | Yük-tarafı ölçüm kaydı |
| PD port sayısı/profilleri | `UNKNOWN` | Kaynak ve sink profilleri, port kimliğiyle |
| Eşzamanlı güç politikası | `UNKNOWN` | Tek-port ve eşzamanlı çalışma kaydı |
| Statik kapasite | `UNKNOWN` | Kaynak bağlı kullanılabilir yük-tarafı kapasitesi |
| Derating | `UNKNOWN` | Kaynak bağlı derating kuralı |
| Verim/kayıp yolu | `UNKNOWN` | Ölçülmüş giriş ve çıkış güçleri |
| Başlatma/kapatma | `NOT_RUN` | Zaman sıralı tezgâh kaydı |
| Güç-kapalı geri besleme | `NOT_RUN` | Power-off ölçümü |
| PD anlaşması | `NOT_RUN` | Protokol/ölçüm günlüğü |
| Fault recovery | `NOT_RUN` | Enjekte edilen arıza ve toparlanma kaydı |
| Termal davranış | `NOT_RUN` | Kararlı durum sıcaklık ve yük kaydı |
| Uygunluk kapsamı | `UNKNOWN` | Hedef ürün/pazar kapsamı |
| Ölçüm bütünlüğü | `BLOCKED` | Enstrüman, kalibrasyon ve fixture bağı |
| Güç/kontrol/fault arayüz haritası | `UNKNOWN` | Yön ve power-off durum tablosu |
| Yazılım modeli tekrarlanabilirliği | `NOT_RUN` | Bağımsız run/test kanıtı sonuç zarfına bağlanmamış |

Mimariye özgü kontroller de zorunludur: hazır-modül değişim tekrarı `NOT_RUN`; hibrit sınır sözleşmesi `BLOCKED`, kontrol/telemetri fail-safe ve interconnect drop/termal testleri `NOT_RUN`; custom PCB exact-part provenance `BLOCKED`, şematik/layout, loop-stability ve bring-up `NOT_RUN`.

Kaynak byte/hash doğrulaması model içinde `PASS` olsa da `supports` alanı yalnız beyan edilmiş iddia metadatasıdır; model DOCX içeriğini çıkarıp semantik olarak doğrulamaz. Bu nedenle birincil kaynak bağlama kontrolü, doğru hash ve `SOURCE_EXPLICIT` sınıfı bulunduğunda bile `UNKNOWN` kalır. Ayrıca bu byte doğrulaması bağımsız bir test çalıştırma kanıtı değildir. Aşağıdaki 21 birim testi bu teslimde ayrıca çalıştırılmıştır; sonuç zarfı kendi kendine yazılım `PASS`’i üretmez.

## Uygulanabilir tezgâh planı

Bir sonraki fiziksel adım, exact controller veya özel güç kartı seçmek değil, tek bir **değiştirilebilir upstream modülü** ölçüm düzeneğine almaktır. Modül kimliği seçildiğinde ilgili üretici kanıtı ayrıca bağlanmalıdır.

| Test | Kapsam | Kaydedilecek kanıt | Kaynaklı eşik yoksa |
|---|---|---|---|
| Kaynak bağlama | Birincil etiket, ürün/revizyon kimliği, modül dokümanı | Dosya, sürüm, locator, SHA-256 | `BLOCKED` |
| Arayüz haritası | Güç, kontrol, ölçüm, fault ve power-off yönleri | Pin/sinyal yönü ve durum tablosu | `UNKNOWN/BLOCKED` |
| PD keşfi | Her port için source–sink–cable kombinasyonu | Advertised ve negotiated profil günlüğü | `UNKNOWN` |
| Tek-port statik yük | Kaynak-bağlı çalışma noktaları | Giriş/çıkış V-I-P ve fault kayıtları | `UNKNOWN` |
| Eşzamanlı çalışma | Gerekli port kombinasyonları | Port başına/toplam güç ve paylaşım davranışı | `UNKNOWN` |
| Attach/detach ve yeniden tahsis | Hot-plug, port ekleme/çıkarma | Zaman eşli protokol ve güç izleri | `UNKNOWN` |
| Load-step/transient | Tek-port ve aggregate yük değişimleri | Droop, overshoot ve recovery dalga şekilleri | `UNKNOWN` |
| Startup/shutdown/inrush | Soğuk/sıcak başlatma ve kapatma | Giriş, çıkış, enable/fault ve akım izleri | `UNKNOWN` |
| Brownout/dropout | Kontrollü giriş kesintisi | Çıkış tepkisi ve toparlanma | `UNKNOWN` |
| Backfeed/power-off | Besleme açık/kapalı kombinasyonları | Enerjisiz port/rail ve kontrol pinleri | `UNKNOWN` |
| Fault isolation | Overload, short, modül kaybı, controller reset, telemetri kaybı | Etkilenen/etkilenmeyen portlar ve recovery | `UNKNOWN` |
| Verim/kayıp | Ortak zaman tabanında giriş/çıkış ölçümü | Ham ölçümler ve türetilmiş hesap | `UNKNOWN` |
| Termal/derating | Kaynak-bağlı yük, ortam ve enclosure | Hotspot, ortam, yük ve süre kaydı | `UNKNOWN` |
| Ölçüm bütünlüğü | Enstrüman ve zaman tabanı | Kimlik, kalibrasyon, aralık, probe noktası, belirsizlik | `BLOCKED/UNKNOWN` |
| Tekrarlanabilirlik | Güç çevrimi ve modül değişimi | Run kimliği ve run-arası karşılaştırma | `UNKNOWN` |
| Uygunluk kapsamı | Hedef pazar ve sistem sınırı | Uygulanacak standardın kaynak kaydı | `UNKNOWN` |

Mimariye özgü ek doğrulamalar:

- `READY_MODULE`: gizli ortak kapasite/paylaşım davranışı ve modül değişiminden sonra eşdeğerlik.
- `HYBRID`: hazır modül–özel düşük gerilim yüzeyi sözleşmesi, interconnect gerilim düşümü/ısınması, ölçüm doğruluğu, controller reset/unpowered davranışı ve port fault isolation.
- `CUSTOM_PCB`: exact-part kaynakları, rail budget, şematik/layout inceleme, transient/kararlılık ve bring-up kanıtları. Bu kol şimdilik `DEFERRED` kalır.

Her fiziksel run; donanım/BOM revizyonu, bağlantı şeması, port ve cable kimliği, enstrüman/kalibrasyon bilgisi, ortam, ham log/dalga şekilleri, hesap aracı sürümü/hash’i ve test bazlı durum içermelidir.

Eşikler kaynak veya müşteri girdisiyle bağlanmadan bu testler yalnız gözlem üretir ve sonuç `UNKNOWN` kalır. Ölçüm kapasite sınırını aşarsa ilgili varsayım senaryosu `FAIL` olabilir; eksik kanıt `PASS`’e çevrilmez.

## Çalıştırma

Repo kökünden:

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -S -m power_rail_validation.ncs01 `
  examples/nightstand/ncs01-nightstand-v1.json `
  --source-root ..\..
```

Beklenen üst durum `BLOCKED`, üst neden `PRIMARY_LABEL_ARTIFACT_MISSING`’dir. Komut yalnız stdout’a JSON yazar; repo içinde çıktı veya önbellek üretmez.

Hedefli test:

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B -S -m unittest tests.test_ncs01 -v
```

Kısıtlı bir çalışma ortamında `%TEMP%` yazılamıyorsa `TEMP` ve `TMP`, repo dışındaki yazılabilir bir geçici alana yönlendirilmelidir. Doğrulanan test kümesi 21 testten oluşur: geçici dizinde üretilen sentetik kaynak byte/hash bağı, NCS-01'i tanımlayan 45 W değerinin 44/46/100 W sapmalarına karşı kapalı kalması, tam ve sıralı güvenlik `claim_limits` kümesinin daraltılamaması/genişletilememesi, legacy locale altında açık UTF-8 CLI çıktısı, sınır senaryoları, mimari/verification ayrımı, eksik kaynak, hash uyuşmazlığı, deterministik sonuç, reporting uyumu, portfolyo senaryosunun genel duruma karışmaması, path traversal reddi, known-gap koruması, çift JSON anahtarı reddi, varsayımın sahte `PASS` üretememesi, typed profil/politika gösterimi, kaynak `supports`–fact `basis` bağının zorunluluğu, kaynak sürümünün raporlayıcıyla aynı sabit-sürüm sözleşmesini izlemesi, üç mimari rolün çiftler halinde ayrıklığı, zorunlu mimari kanıt kümesinin daraltılamaması ve CLI `BLOCKED`/exit-code sözleşmesi. Gerçek dış DOCX kaynağı bu hermetik test kapsamında doğrulanmaz; ayrı, açıkça yetkilendirilmiş bir smoke çalışması gerektirir.

## Güvenli sonraki adım

`NCS01-BENCH-01`: Birincil etiket kaydını bağla ve tek bir değiştirilebilir upstream modül üzerinde PD profili, tek/eşzamanlı güç, startup/shutdown, backfeed, fault-recovery ve termal gözlemleri topla. Bu veri gelene kadar HYBRID yalnız geçici portfolyo yönüdür; PCB üretimi veya exact-part dondurma yapılmamalıdır.
