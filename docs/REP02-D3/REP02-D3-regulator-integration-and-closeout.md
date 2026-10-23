# REP-02 Oturum 5 — Regülatör İncelemesi, Dosya API ve D3 Kapanışı

| Alan | Değer |
|---|---|
| Tarih | 19 Ekim 2026 |
| Paket | `power-rail-validation 0.4.0` |
| Başlangıç commit’i | `79ae5ba` |
| Teslim durumu | `UNCOMMITTED_DELIVERABLES` |
| Sonuç | `D3_SYNTHETIC_TEST_EVIDENCE_CLOSED_WITH_LIVE_INTEGRATION_BLOCKED` |

Bu oturum `WF-02-05 Regulator Review` kapsamını ve `WF-02-06 Evidence and Integration` işinin çevrimdışı dosya-API dilimini tamamladı. D3 sentetik test/kanıt kapısı kapanmıştır. Sonuç yalnız sürümlü yazılım sözleşmeleri, sentetik fixture’lar ve yerel byte bütünlüğü için geçerlidir; canlı REP-01/03 entegrasyonu, exact-part proje onayı veya donanım kapısı sonucu değildir.

GAP-02 ve GAP-03 açıktır. G1 kaynak kapısı `BLOCKED`, G2–G6 ise açılmamıştır. Gerçek üretici datasheet/modeli, yetkili proje artifact’ları ve canlı repo adaptörleri sağlanmadan regülatör ya da sistem için proje düzeyinde `PASS` üretilemez.

<!-- PAGEBREAK -->

## 1. Kapsam ve karar özeti

Çalışmanın girdileri `REP-02_Codex_Gorev_Plani_Pozisyon_Bazli_v1.docx` ile `REP-02_Is_Akisi_Raporu_v1.pdf` belgeleridir. Oturum 5 planı; regülatör limit/provenance incelemesini, REP-01/03 dosya sınırını ve D3 test/kanıt kapanışını birlikte ister. Uygulama bu ayrımı korur:

- `WF-02-05`: VIN, IOUT, sıcaklık, derating, exact-part kaynak ve model/analiz kanıtları.
- `WF-02-06`: sürümlü çevrimdışı descriptor, artifact byte doğrulaması, ortak rapor modeli ve evidence manifest.
- D3: önceki fault-injection dilimiyle birlikte sentetik test/kanıt bütünlüğü.
- D4/Oturum 6: CLI, CI, clean-install/package-data smoke ve release hazırlığı.

| İş paketi | Bu oturumdaki sonuç | Kapsam sınırı |
|---|---|---|
| WF-02-05 Regulator Review | `IMPLEMENTED` | Sentetik ve kayıtlı kanıt incelemesi |
| WF-02-06 Offline File API | `IMPLEMENTED` | Yerel dosya descriptor ve byte bütünlüğü |
| D3 Test/Evidence | `CLOSED` | Sentetik yazılım kapsamı |
| Canlı REP-01/03 | `NOT_RUN / BLOCKED` | Harici repo ve gerçek artifact sağlanmadı |
| GAP-02 / GAP-03 | `OPEN` | Exact-part/model ve üretici zaman kaynakları eksik |
| G1 | `BLOCKED` | Kaynak kapısı kapanmadı |
| G2–G6 | `NOT_OPENED` | Fiziksel doğrulama yapılmadı |

## 2. WF-02-05 Regulator Review

### 2.1 Giriş ve bağlama sözleşmesi

Regülatör inceleme motoru, modül-yerel kapalı bir giriş zarfı tüketir. İnceleme kimliği, fixture kapsamı, exact regülatör kimliği, çalışma noktası, public rail-budget girdisi, limitler, kaynak kataloğu, model kaydı ve transient/loop-stability kayıtları zorunludur. Beklenmeyen alanlar, yanlış birimler, non-finite sayılar, çevrimli yapılar ve eksik zorunlu alanlar fail-closed `BLOCKED` olur.

İncelenen çıkış gerilimi `KNOWN` olmalı ve bağlı rail-budget nominal gerilimiyle Decimal düzeyinde birebir eşleşmelidir. Regülatör kimliği ile rail kimliği de public rail-budget girdisine birebir bağlanır. Bu kimlik koşulları limit kontrolü değil, kanıt bağlama kapısıdır; uyuşmazlık fiziksel `FAIL` olarak değil `BLOCKED` olarak kaydedilir.

Nested rail-budget girdisinin kullandığı her kaynak; dış katalogdaki byte-doğrulanmış kayıtla kaynak kimliği, sürüm, locator, erişim durumu, sınıflandırma ve SHA-256 düzeyinde eşleşir. İç ve dış kataloglar arasında farklı metadata taşıyan aynı source ID kabul edilmez. Rail kimliği, nominal gerilim, profil akımları, regülatör kapasitesi, derating/verim ve entity source-ref bağları ayrıca rail artifact’ından ayrıştırılan semantik kayıtla birebir doğrulanır.

### 2.2 Limit ve derating kuralları

| Kural ailesi | Karar | Ayrım |
|---|---|---|
| VIN absolute maximum | Bilinen VIN kaynaklı mutlak üst sınırı aşmamalı | Tavsiye edilen çalışma aralığı değildir |
| VIN recommended operating | VIN kapalı min–max aralığında olmalı | Absolute maksimumdan ayrı kural kimliği |
| IOUT absolute maximum | Yeniden hesaplanan rail-budget Imax mutlak limiti aşmamalı | Model tahmini kullanılmaz |
| IOUT recommended operating | Imax tavsiye edilen sürekli akımı aşmamalı | Absolute maksimumdan ayrı karar |
| Junction temperature | Bilinen Tj mutlak sınırı aşmamalı | Ta değeri Tj yerine geçirilmez |
| Ambient temperature | Bilinen Ta tavsiye edilen aralıkta olmalı | Eksik sıcaklık `UNKNOWN` kalır |
| Derating | Imax, Ta noktasındaki kapalı piecewise-linear limiti aşmamalı | Her eğri noktası `KNOWN` ve geçerli olmalı |

Derating eğrisinde bilinmeyen veya geçersiz bir nokta sessizce atlanmaz; komşu noktalar arasında yapay interpolasyon yapılmaz. Herhangi bir eksik nokta `SOURCE_LIMIT_UNKNOWN / BLOCKED` üretir. Aralık dışı sıcaklık da extrapolation yerine açıkça bloklanır.

### 2.3 Kaynak ve model provenance

`AVAILABLE` olarak bildirilen dış kaynaklar yalnız güvenli, normalize edilmiş, göreli POSIX locator altında gerçek dosya baytları okunup bildirilen SHA-256 ile eşleştiğinde kullanılabilir. `artifact_root` verilmezse byte doğrulaması çalıştırılmış sayılmaz ve kaynak-bağımlı `PASS` yasaktır. Mutlak yol, traversal, eksik dosya ve hash uyuşmazlığı bloklanır; eksik kaynak için sahte hash üretilmez.

Exact-part datasheet kararı; sabit sürüm, sayfa/bölüm, SHA-256, üretici, parça numarası, sınıflandırma, lisans ve erişim durumunu birlikte doğrular. Kaynak zarfındaki `page_section`, byte-doğrulanmış artifact’ın bölüm haritasındaki gerçek bölümlerden biri olmalıdır. Zarf içindeki VIN, IOUT, sıcaklık ve derating sınırları artifact’tan ayrıştırılan değerlerle birebir bağlanır; metadata değiştirilerek kaynakta olmayan bir limit üretilemez. Model kapısı ayrıca modelin üretici kökenini, exact parça eşleşmesini, model sürümünün kaynak sürüm/revizyonuyla eşliğini, lisans durumunu, dosya hash’ini ve çalışma noktası geçerlilik sınırlarını denetler. Byte-doğrulanmış model metninde tam bir adet kimlik başlığı ve exact-part kimliğiyle dengeli, çelişkisiz `.SUBCKT/.ENDS` yapısı zorunludur; zarf içindeki model çalışma sınırları da exact-part çalışma zarfıyla bağlanır.

`PROJECT` kapsamı bu sürümde doğrulanmış canlı entegrasyon artifact bağı olmadığı için sentetik metadata değiştirilerek `PASS`e yükseltilemez. Böyle bir kayıt açıkça `PROJECT_SCOPE_INTEGRATION_NOT_VERIFIED / BLOCKED` olur.

### 2.4 Transient ve loop stability ayrımı

Transient ve loop-stability ayrı rule ID, evidence kind, source role, tool sürümü ve result hash taşır. Transient kaydı loop-stability kanıtı değildir. İki kayıt aynı locator’ı veya aynı exact byte digest’i paylaşırsa stability kararı `TRANSIENT_NOT_STABILITY_EVIDENCE / BLOCKED` olur; metadata etiketi değiştirilerek aynı artifact başka kanıt türüne yükseltilemez.

Motor SPICE çalıştırmaz. Yalnız kayıtlı analiz artifact’ının execution state, araç adı/sürümü, analiz türü, exact bytes, exact-part/model kimliği, sabit kayıt revizyonu, source binding ve model applicability zincirini inceler. Zarf alanları byte-doğrulanmış artifact içeriğinden ayrıştırılan semantik kayıtla; source `version` ve `document_revision` alanları da artifact revizyonuyla birebir eşleşmedikçe sonuç bloklanır. `latest`, `snapshot`, `dev`, `next`, `canary`, `edge`, `rolling`, `vNext`, `release`, `origin/main` ve benzeri hareketli adlar sabit sürüm sayılmaz. Bu nedenle sentetik analiz `PASS`i gerçek transient veya kararlılık doğrulaması değildir.

### 2.5 Regülatör negatif ve adversarial kapsamı

Zorunlu negatif fixture’lar; recommended limit aşımı, eksik sıcaklık, çelişkili exact kaynak, eksik model, model validity dışı çalışma ve transient’in stability yerine kullanılmasıdır. Ek adversarial denetim aşağıdaki yanlış-`PASS` yollarını regresyon testine dönüştürdü:

- çıkış geriliminin rail nominalinden kopması;
- eksik derating noktasının sessiz atlanması;
- transient byte’larının stability metadata’sıyla yeniden etiketlenmesi;
- bildirilen fakat gerçek dosya byte’larıyla doğrulanmamış source/model hash’leri;
- datasheet/model zarf sınırlarının doğrulanmış artifact içeriğindeki semantik değerlerden kopması;
- analiz türü, araç/sürüm, durum veya parça/model kimliğinin kanıt artifact’ından kopması;
- rail-budget zarf değerlerinin byte-doğrulanmış rail artifact’ından kopması;
- datasheet `page_section` göstergesinin artifact bölüm haritasında bulunmaması;
- nested rail kaynak kimliğinin outer katalogdan ayrışması;
- cyclic source alanı, shared-container input alias’ı ve input/result alias’ı;
- sentetik fixture’ın yalnız scope etiketi değiştirilerek proje sonucuna yükseltilmesi;
- model sürümünün provenance kaynağından kopması;
- model artifact’ında birden çok kimlik başlığı, eksik/dengesiz SPICE yapısı veya başka parça adı kullanılması;
- analysis source sürüm/revizyonunun byte-bound artifact revizyonundan kopması veya hareketli sürüm adı kullanması;
- hash-seed’e bağlı issue sıralaması;
- claim limitlerinin ortak raporda ya da erken-hata fallback sonuçlarında kaybolması;
- versioned rule catalog bytes’ının sabit hash’e bağlanmaması;
- engine’in emit ettiği rule ID’lerin katalogda eksik kalması.

## 3. WF-02-06 çevrimdışı REP-01/03 dosya API

### 3.1 Sözleşme

Dosya API `api_version=1.0.0` ve `schema_version=1.0.0` için exact-match ister. Producer ve consumer repository kimlikleri, configuration/profile çiftleri ve artifact kayıtları kapalı bir zarf içindedir. Descriptor input hash’i, `input_hash` alanı dışındaki sonlu UTF-8 JSON’un sıralı ve minified temsilinden üretilir; dizi sırası anlamlıdır.

Package-owned sözleşme dosyası yalnız kimlik/sürümle değil, sabit byte SHA-256 değeriyle doğrulanır. Nihai sözleşme SHA-256 değeri `e1a40ffd49fb126152a89e37e173ca8aeb87c76f9dc6477540a4b0708c2857da` değeridir. Eksik veya tahrif edilmiş package spec fail-closed `BLOCKED` olur.

### 3.2 Artifact bütünlüğü ve hata davranışı

| Durum | Sonuç politikası | Kanıt davranışı |
|---|---|---|
| Geçerli REP-01/REP-03 descriptor | `PASS` | Yalnız yapı ve yerel byte bütünlüğü |
| Artifact root verilmedi | `NOT_RUN` | Hash uydurulmaz |
| Açık eksik artifact/source | `NOT_RUN` | `artifact_gaps`, SHA-256 yok |
| Diskte olmayan AVAILABLE artifact | `BLOCKED` | Dosya yok, hash iddiası kabul edilmez |
| Hash uyuşmazlığı | `BLOCKED` | Exact bytes politikası |
| API/schema uyumsuzluğu | `BLOCKED` | Moving alias kabul edilmez |
| Malformed/duplicate/non-finite JSON | `BLOCKED` | Tür etiketli load-error envelope hash’i |
| Path traversal/absolute/Windows path | `BLOCKED` | Yalnız güvenli göreli POSIX yol |

Malformed, duplicate-key, non-finite, encoding hatalı veya erişilemeyen descriptor sonuçları ortak raporlayıcıyla uyumludur. Dosya okunabildiyse canonical load-error envelope raw-file SHA-256 değerini içerir; dosya yoksa sahte raw hash üretilmez.

### 3.3 Canlı adaptör sınırı

Bu dilim hiçbir başka repository’yi import etmez, çağırmaz veya değiştirmez. REP-01 ve REP-03 canlı adaptörleri her sonuçta yalnız `NOT_RUN` ya da descriptor doğrulaması bloklandıysa `BLOCKED` olabilir; `PASS` yasaktır. Descriptor `PASS`, yalnız yerel sentetik dosya sözleşmesi ve byte bütünlüğü anlamına gelir.

## 4. Ortak raporlama ve D3 evidence paketi

Regülatör ve dosya-API sonuçları mevcut ortak reporter’a bağlanır. JSON ve Markdown görünümü aynı normalize finding setinden üretilir. Status önceliği `BLOCKED > FAIL > UNKNOWN > NOT_RUN > PASS` şeklindedir. Modül claim limitleri `CLAIM_LIMIT:<değer>` biçiminde ortak rapor limitations alanında korunur.

Yeni kapanış paketi aşağıdaki dosyaları içerir:

| Dosya | Rol |
|---|---|
| `regulator-review-results.json` | Nominal, GAP-02, transient/stability ve negatif kampanya sonuçları |
| `integration-results.json` | REP-01/03, çoklu profil ve hata kampanyası sonuçları |
| `source-model-provenance-register.json` | Sentetik kaynak/model ve açık proje eksikleri |
| `report-model-closeout.json` | Normalize ortak finding modeli |
| `report-closeout.md` | Aynı modelin Markdown görünümü |
| `test-results-closeout.json` | Test, statik kontrol, audit ve doküman QA kaydı |
| `manifest-closeout.json` | Girdi/kaynak/model/araç/çıktı byte-exact SHA-256 manifesti |

Evidence manifest kendisini kapsamaz. Kayıtlı `AVAILABLE` dosyalar gerçek baytları üzerinden yeniden hashlenir; eksik proje kaynakları manifestte sahte dosya olarak gösterilmez, provenance register içinde açık `MISSING/UNKNOWN` kayıt olarak tutulur.

## 5. Doğrulama sonuçları

| Kontrol | Sonuç |
|---|---|
| Tam unittest paketi | `323/323 PASS` |
| Regulator-review odaklı | `61/61 PASS` |
| Integration odaklı | `34/34 PASS` |
| Fault-injection regresyonu | `62/62 PASS` |
| Reporting odaklı | `23/23 PASS` |
| Rail-budget ve sequencing regresyonu | `PASS` |
| Python 3.9 AST | `PASS` |
| Tüm JSON fixture’ları strict parse | `PASS` — bilinçli malformed dosyalar ayrıca reddedildi |
| Cross-process determinism | `PASS` |
| Input immutability / finite JSON | `PASS` |
| Report JSON–Markdown eşliği | `PASS` |
| Manifest byte hash replay | `PASS` |
| `git diff --check` | `PASS` |
| Repo `.pyc` hijyeni | `0` |
| Bağımsız entegrasyon re-audit | `CLEAN` |
| Bağımsız regülatör re-audit | `CLEAN` |
| DOCX görsel QA | `PASS`; `6` Letter sayfa |

Regülatör rule catalog SHA-256: `92ab4059050fb5d381fa4b7c7cbf2301212c6956be0255d39f8da15ccb85187b`.

Ortak kapanış report model SHA-256: `1007fc3a5f8963acb16f13de7a5958fb5a81f109fd4e21c945bc22deb6b83508`.

Kapanış manifesti kendisini kapsam dışı tutar; teknik self-hash değeri `manifest-closeout.json` içindeki `manifest_hash` alanında kayıtlıdır.

## 6. Gate durumu ve iddia sınırları

| Kapı veya boşluk | Durum | Kapanış için gereken |
|---|---|---|
| GAP-02 exact-part/model | `OPEN` | Yetkili üretici datasheet/modeli ve lisans/provenance |
| GAP-03 zaman gereksinimleri | `OPEN` | Üretici zaman kaynağı ve REP-01/03 eşlemesi |
| G1 kaynak bütünlüğü | `BLOCKED` | Gerçek proje kaynak seti ve hash kayıtları |
| G2–G6 donanım kapıları | `NOT_OPENED` | Şema/PCB/lab/uygunluk kanıtı |
| Canlı REP-01 | `NOT_RUN` | Repo/artifact erişimi ve semantik mapping |
| Canlı REP-03 | `NOT_RUN` | Repo/artifact erişimi ve semantik mapping |

Bu teslimden aşağıdaki iddialar çıkarılamaz:

- exact-part proje onayı veya regülatör seçimi;
- gerçek transient waveform, SPICE yakınsaması veya loop-stability marjı;
- junction sıcaklığı tahmini, termal davranış veya laboratuvar ölçümü;
- şematik, PCB, EMC, ISO, AEC ya da saha uygunluğu;
- REP-01/03 canlı entegrasyonunun çalıştığı;
- G1–G6 donanım kapılarından herhangi birinin geçtiği.

## 7. İşlem kaydı

| İşlem | Durum |
|---|---|
| Commit | Yapılmadı |
| Push | Yapılmadı |
| Paket kurulumu | Yapılmadı |
| Harici repo mutasyonu | Yapılmadı |
| Canlı adaptör çalıştırma | Yapılmadı |
| Fiziksel test | Yapılmadı |

Çalışma ağacı bilinçli olarak `UNCOMMITTED_DELIVERABLES` durumunda bırakılmıştır. Bu rapor, evidence paketi ve testler aynı çalışma ağacının parçasıdır.

## 8. Sonraki oturum

D4/Oturum 6 için önerilen sıra:

1. Kullanıcıya dönük CLI ve exit-code sözleşmesini eklemek.
2. Wheel/sdist package-data, clean-install ve minimum Python 3.9 smoke testini çalıştırmak.
3. CI iş akışında unittest, AST, JSON, evidence replay ve `git diff --check` kapılarını kurmak.
4. Release notu ve sürümleme politikasını tamamlamak.
5. Gerçek REP-01/03 erişimi sağlanırsa canlı adaptörleri ayrı yetki ve kanıt sınırıyla uygulamak.

Canlı kaynaklar gelmeden GAP-02/GAP-03 veya G1 için durum yükseltilmemelidir. D3 kapanışı korunmalı, fiziksel ve entegrasyon iddiaları D4 ve sonraki kapılarda ayrıca kanıtlanmalıdır.
