# power-rail-validation-and-review

Durum: WF-02-03_SEQUENCING_IMPLEMENTED — D2_SYNTHETIC_APPLICATION_COMPLETE

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

Testler yalnız Python standart kütüphanesini kullanır. Rail-budget örnekleri `W`, `%` ve `1` birimlerini; sequencing örnekleri ise zaman penceresi ve olay izini modül-yerel sonuç sözleşmelerinde taşır. Bu sonuçlar D1 yanıtına sözleşme değişikliği olmadan eklenmez.

## Sonraki çalışma dilimi

Bir sonraki ana dilim `WF-02-04 Fault Injection`dır. Brownout, stuck-low, delay, rail-loss ve restart; açık fault sözleşmesi, sabit fixture veya açık seed ve gözlenebilir kurtarma iziyle ele alınacaktır. Canlı REP-01 entegrasyonu; zarf eşlemesi, hash normalizasyonu, kanıt bütünlüğü ve GAP-03 zaman/eşik kaynakları çözülene kadar `BLOCKED` kalır. Repo bağımsız Git deposudur; bu çalışma commit veya push yapmaz.
