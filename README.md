# power-rail-validation-and-review

Durum: D5_PROJECT_INTAKE_GATE_IMPLEMENTED — CURRENT_REP01_VERTICAL_SLICE_BLOCKED

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

`WF-02-05 Regulator Review` dilimi aşağıdaki çıktıları ekler:

- VIN, IOUT, junction/ambient sıcaklığı ve parça-bağımlı derating için deterministik kurallar
- Absolute maximum ile recommended operating sınırları için ayrı kural kimlikleri
- Exact-part kaynak sürümü, gerçek bölüm haritası, byte-exact SHA-256 ve artifact içeriğinden ayrıştırılan limit bağları
- Rail-budget zarfının byte-doğrulanmış rail artifact’ındaki ray/yük/kapasite/derating semantiğine birebir bağı
- Model hash'i, lisans durumu, üretici kökeni ve artifact içeriğine bağlı geçerlilik sınırları için fail-closed kontroller
- Transient ve loop-stability kanıtlarının tür/araç/durum/kayıt-revizyonu semantiğiyle birbirine dönüştürülmesini engelleyen ayrı kararlar
- Eksik model, limit aşımı, eksik sıcaklık, çelişkili kaynak ve GAP-02 fixture'ları

`WF-02-06 Evidence and Integration` çevrimdışı dilimi de tamamlanmıştır:

- Aynı normalize finding listesinden deterministik JSON ve Markdown üretimi
- Teknik sonuç, girdi, kaynak, araç ve paket sürümü bağları
- `inputs / sources / models / tools / outputs` kategorili, gerçek dosya hashlerini doğrulayan evidence manifest
- REP-01 ve REP-03 için exact-version dosya descriptor sözleşmesi, çoklu configuration/profile ve byte-exact artifact doğrulaması
- Eksik artifact, uyumsuz sürüm, bozuk/tekrarlı/non-finite JSON ve hash uyuşmazlığı için kapalı hata davranışı
- Canlı REP-01/03 adaptörlerini açık `NOT_RUN/BLOCKED` bırakan fail-closed raporlama sınırı

Bu kapsam D3 sentetik test ve kanıt kapısını kapatır. Canlı REP-01/03 adaptörleri, gerçek proje kaynakları, GAP-02/GAP-03 ve G1-G6 donanım kapıları bu kapanışın dışındadır.

`D4 / Yerel Release Adayı` dilimi ürünleştirme katmanını ekler:

- `validate`, `rail-budget`, `sequence`, `inject-fault`, `review` ve `report` alt komutlarından oluşan ince CLI
- `PASS=0`, `FAIL=1`, `UNKNOWN=2`, `BLOCKED=3`, `NOT_RUN=4`, girdi/kullanım hatası `64` ve iç hata `70` çıkış kodu sözleşmesi
- Ortak rapor modeli için paketlenen, kapalı ve sürümlü JSON Schema
- Wheel ve source distribution üretimi; temiz sanal ortamda kurulum, package-data ve altı komut smoke doğrulaması
- Python 3.9 ve 3.12 matrisli minimal CI tanımı, lisans, changelog ve doğrulanmış hızlı başlangıç kılavuzu
- D3 kanıtını değiştirmeden ona hash ile referans veren ayrı D4 test, build ve release manifesti

D4 kapanışı yalnız yerel yazılım release adayını kapsar. Python 3.12 üzerinde tam test ve temiz kurulum doğrulaması yerelde çalıştırılır; Python 3.9 yürütmesi CI matrisinde tanımlıdır ancak commit/push yapılmadığı için bu çalışma ağacında henüz koşturulmuş sayılmaz.

`D5 / Gerçek Proje Girdisi Hazırlık Kapısı` ardıl geliştirme dilimi şunları ekler:

- tek bir sabit REP-01 revizyonu, ray ve exact regülatör seçimi için kapalı intake descriptor'ı;
- artifact rolü, güvenli göreli yol, pinned belge revizyonu, köken/lisans, SHA-256 ve çapraz bağ doğrulaması;
- talep edilen kabiliyete göre datasheet/model gereksinimi ve açık artifact-gap kaydı;
- mevcut durum/çıkış-kodu modelini kullanan `intake INPUT --artifact-root ROOT` komutu;
- gerçek REP-01 `5V` diliminin mevcut eksiklerini varsayımsız biçimde `BLOCKED` bırakan ayrı D5 kanıtı.

Intake `PASS`, yalnız downstream analiz girdilerinin kimlik ve byte bütünlüğü bakımından hazır olduğunu söyler. Artifact içeriğinden elektriksel semantik çıkarmaz; buck, pin, transient ve loop-stability motorlarını çalıştırmaz ve donanım/G1-G6 onayı vermez. Kaynak ağacı D5 geliştirmesi için `0.6.0.dev0` sürümündedir; dondurulmuş D4 dağıtım artifact'ları `0.5.0` olarak korunur.

## Güvenlik ve iddia sınırı

G1 kaynak kapısı `BLOCKED` durumundadır. Bu repo şu aşamada gerçek müşteri şeması, exact-part regülatör modeli, fiziksel ölçüm veya EMC, ISO, AEC ve saha uygunluk kanıtı üretmez. Testlerdeki `PASS`, yalnız test edilen sözleşme, sentetik fixture ve yerel dosya bütünlüğü davranışını ifade eder; donanım, proje kapısı veya canlı entegrasyon onayı değildir. Eksik karar girdisi sayısal varsayılanla doldurulmaz ve `PASS` sonucuna yükseltilmez.

## Kurulum ve CLI

Python 3.9 veya yenisiyle yerel wheel'i kurun:

```powershell
python -m pip install --no-deps .\dist\power_rail_validation-0.5.0-py3-none-any.whl
power-rail-validation --version
```

D4 dağıtımındaki altı komutun ve D5 kaynak ağacı/`0.6.0.dev0` paket adayındaki `intake` komutunun doğrulanmış örnekleri, stdin/stdout davranışı ve çıkış kodları için [CLI hızlı başlangıç kılavuzuna](docs/QUICKSTART.md) bakın. Aynı arayüz `python -m power_rail_validation` ile de kullanılabilir.

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

Testler yalnız Python standart kütüphanesini kullanır. Rail-budget örnekleri `W`, `%` ve `1` birimlerini; sequencing ve fault-injection örnekleri zaman penceresi, recovery ve olay izini; regulator-review örnekleri kaynak/model/limit bağlarını modül-yerel sonuç sözleşmelerinde taşır. Dosya-API testleri yalnız yerel sentetik artifact'ları doğrular. Raporlayıcı bu sonuçları ortak görünümde birleştirir ancak D1 finding sözleşmesine zorla dönüştürmez.

Release artifact'larını yeniden üretmek ve temiz kurulumda doğrulamak için:

```powershell
python scripts/build_release.py
python scripts/verify_release.py
```

## D5 gerçek senaryosunu çalıştırma

Kardeş REP-01 deposunun yolu açıkça verilmelidir:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m power_rail_validation intake .\scenarios\REP02-D5\project-intake.json --artifact-root ..\esp32-s3-24v-interface-board
```

Mevcut snapshot için beklenen sonuç `BLOCKED`, proses kodu `3`'tür. Bu, hazırlık kapısının doğru çalıştığını; altı zorunlu artifact sınıfı ve bazı yetkili sayısal/kimlik girdileri henüz bulunmadığı için downstream analizin başlatılmadığını gösterir.

## Sonraki çalışma dilimi

Bir sonraki ana dilim tek bir gerçek dikey dilimdir: bir exact regülatör, bir ray, yetkili datasheet profili ve bir REP-01 artifact'ını uçtan uca bağlamak. Bu dilimde buck güç katı kontrolleri, gerilim/pin kısıtları ve gerçek transient/loop-stability metrikleri küçük ve kanıta bağlı biçimde ele alınmalıdır. Canlı REP-01/03 entegrasyonu; yetkili repo/artifact erişimi, semantik eşleme ve GAP-02/GAP-03 kaynakları çözülene kadar `BLOCKED` kalır. Repo bağımsız Git deposudur; D5 finalizer otomasyonu commit veya push gerçekleştirmez.
