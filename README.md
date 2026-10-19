# power-rail-validation-and-review

Durum: WF-02-04_FAULT_INJECTION_IMPLEMENTED — WF-02-06_INITIAL_REPORTING_STARTED — D3_TEST_EVIDENCE_STARTED

Bu repo güç rayı bütçesi, derating, sıralama, sentetik hata senaryoları ve kanıt sınıflı inceleme raporları için yeniden kullanılabilir bir araç seti geliştirir.

## Tamamlanan çalışma dilimleri

`WF-02-01 Veri Sözleşmesi`, kullanıcının 18 Ekim 2026 tarihli ilerleme talimatıyla sentetik çekirdek geliştirme için kabul edilmiştir. Bu kabul, açık REP-01 entegrasyon kararlarını veya donanım kapılarını kapatmaz. D1 dilimi aşağıdaki çıktıları kapsar:

- `rail`, `load`, `regulator`, `source`, `event` ve `finding` için sürümlü JSON Schema dosyaları
- CSV alan sözlüğü
- Geçerli, eksik, yanlış tipli, bilinmeyen limitli ve çelişkili sentetik fixture'lar
- Standart kütüphane ile sözleşme doğrulama ve deterministik JSON SHA-256 üretimi
- Harici paket kurulumu gerektirmeyen sözleşme testleri

`WF-02-02 Rail Budget` dilimi aşağıdaki çıktıları ekler:

- Açık `Imin`, `Ityp` ve `Imax` profilleriyle çalışan modül-yerel giriş sözleşmesi
- `Decimal` tabanlı toplam akım, `P=VI`, derated/effective kapasite, kullanım ve marj hesapları
- Açık verim sağlandığında giriş ve kayıp gücü; sağlanmadığında `UNKNOWN`
- Birim, negatif akım, sıfır gerilim, aşırı yük, bilinmeyen limit ve kaynak kapısı kontrolleri
- Elle hesaplanmış sentetik fixture'lar ile pozitif, negatif ve sınır testleri

`WF-02-03 Sequencing` dilimi aşağıdaki çıktıları ekler:

- D1 `event` ve `source` kayıtlarını kullanan kapalı, modül-yerel sequencing zarfı
- `VIN_VALID → EN → PGOOD → RESET release` zinciri için deterministik FSM ve ayrıntılı olay izi
- `Decimal` tabanlı kapalı zaman pencereleri; kanonik zaman sıralaması ve açık aynı-zaman politikası
- Yanlış sıra, sınır ihlali, timeout, eksik olay, tekrar, polarite ve power-off senaryoları
- Kaynak, sürüm, hash ve provenance kapıları ile `PASS / FAIL / UNKNOWN / BLOCKED` ayrımı

Rail-budget ve sequencing sentetik çekirdeklerinin birlikte tamamlanmasıyla D2 sentetik uygulama kapısı kapanmıştır. Bu kapanış yalnız yazılım sözleşmeleri ve sentetik fixture'lar içindir.

`WF-02-04 Fault Injection` dilimi aşağıdaki çıktıları ekler:

- Brownout, stuck-low, delay, rail-loss ve restart için kapalı, sürümlü fault sözleşmesi
- Mevcut rail-budget ve sequencing motorlarını değiştirmeden çağıran deterministik fixture runner'ı
- Exact target, kaynak, sürüm, hash, provenance, baseline causality ve overlap kapıları
- Fault effect ile recovery sonucunu ayrı gösteren olay izi ve finding seti
- Teknik `FAIL`i başarılı recovery veya fixture beklentisi nedeniyle `PASS`e çevirmeyen durum kararı

`WF-02-06 Evidence and Integration` için ilk raporlama dilimi de başlamıştır:

- Aynı normalize finding listesinden deterministik JSON ve Markdown üretimi
- Teknik sonuç, girdi, kaynak, araç ve paket sürümü bağları
- `inputs / sources / models / tools / outputs` kategorili, gerçek dosya hashlerini doğrulayan evidence manifest
- Canlı REP-01/03 adaptörlerini açık `NOT_RUN` bırakan fail-closed raporlama sınırı

Bu kapsam D3 test ve kanıt başlangıcıdır; D3 veya WF-02-06 kapanışı değildir.

## Güvenlik ve iddia sınırı

G1 kaynak kapısı `BLOCKED` durumundadır. Bu repo şu aşamada gerçek müşteri şeması, exact-part regülatör modeli, fiziksel ölçüm veya EMC, ISO, AEC ve saha uygunluk kanıtı üretmez. Testlerdeki `PASS`, yalnız test edilen sözleşme davranışını ifade eder; donanım veya proje kapısı onayı değildir. Eksik karar girdisi sayısal varsayılanla doldurulmaz ve `PASS` sonucuna yükseltilmez.

## Test

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

POSIX kabuk:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

Testler yalnız Python standart kütüphanesini kullanır. Rail-budget örnekleri `W`, `%` ve `1` birimlerini; sequencing ve fault-injection örnekleri zaman penceresi, recovery ve olay izini modül-yerel sonuç sözleşmelerinde taşır. Raporlayıcı bu sonuçları ortak görünümde birleştirir ancak D1 finding sözleşmesine zorla dönüştürmez.

## Sonraki çalışma dilimi

Bir sonraki ana dilim `WF-02-05 Regulator Review` ile kalan `WF-02-06 Evidence and Integration` çalışmasıdır. Canlı REP-01/03 entegrasyonu; zarf eşlemesi, hash normalizasyonu, kanıt bütünlüğü ve GAP-02/GAP-03 kaynakları çözülene kadar `BLOCKED` kalır. Repo bağımsız Git deposudur; bu çalışma commit veya push yapmaz.
