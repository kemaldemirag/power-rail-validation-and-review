# REP 02 D3 Hata Enjeksiyonu ve İlk Kanıt Raporlama Dilimi

## Sonuç ve durum

19 Ekim 2026 tarihli bu çalışma `WF-02-04 Fault Injection` dilimini sentetik yazılım kapsamı içinde tamamladı ve `WF-02-06 Evidence and Integration` için ilk raporlama dilimini başlattı. Durum `WF-02-04_FAULT_INJECTION_IMPLEMENTED — WF-02-06_INITIAL_REPORTING_STARTED — D3_TEST_EVIDENCE_STARTED` olarak kaydedildi. Bu sonuç D3 veya WF-02-06 kapanışı değildir.

Hata enjeksiyonu motoru brownout, stuck-low, delay, rail-loss ve restart senaryolarını kapalı bir sözleşmeyle değerlendirir. Runner mevcut rail-budget ve sequencing motorlarını genel API'lerinden çağırır, fakat hata bağlamını sequencing olay dizisine eklemez. Teknik sonuç, fixture'daki beklenen sonuçtan ayrı tutulur. Bu nedenle bilinen bir hata etkisi, recovery başarılı ve fixture beklentisiyle uyumlu olsa da teknik olarak `FAIL` kalır.

İlk raporlama dilimi, modül-yerel teknik sonuçları tek bir normalize modelde birleştirir ve aynı modelden JSON ile Markdown üretir. Kanıt manifesti gerçek dosya baytlarını SHA-256 ile bağlar. Canlı REP-01 ve REP-03 adaptörleri uygulanmadığı için raporda açıkça `NOT_RUN` durumundadır.

## Kaynaklar ve başlangıç noktası

Çalışma aşağıdaki yönetim ve gereksinim kaynaklarına dayanır:

- `REP-02_Codex_Gorev_Plani_Pozisyon_Bazli_v1.docx`
- `REP-02_Is_Akisi_Raporu_v1.pdf`
- `CALISMA_ALANI_KURALLARI.md`
- D1 ürün sözleşmesi ve karar kaydı
- D2 rail-budget ve sequencing tamamlanma raporları

Başlangıç Git commit'i `4f2a4cc74b4dcb632ede027fbe24b3bee2ee7de1` idi. Çalışma ağacı teslim sırasında `UNCOMMITTED_DELIVERABLES` durumundadır. Commit, push veya paket kurulumu yapılmadı.

| Bileşen | Sürüm veya kimlik |
|---|---|
| Python paketi | `0.3.0` |
| Fault engine | `0.1.0` |
| Fault rule set | `REP02-FI-RULESET-1.0.0` |
| Reporter | `0.1.0` |
| Report schema | `1.0.0` |
| Manifest schema | `1.0.0` |
| Sentetik fault kural kaynağı | `SRC-FI-RULES@fixture-1` |
| Kural kaynağı kanonik SHA-256 | `ae66faf5ca460f6ab1b9be70d7b844b32f512ddd0d3bf1fa486a046baff263b8` |

## Teslim edilen bileşenler

| Alan | Teslim |
|---|---|
| Fault runner | `src/power_rail_validation/fault_injection/runner.py` ve genel paket API'si |
| Fault fixture seti | Beş zorunlu fault ailesi, unknown threshold, recovery-combo ve fault-during-restart senaryoları |
| Fault kural kaynağı | Sürümlü ve kanonik hash ile bağlanan sentetik kural kaydı |
| Fault testleri | Pozitif, negatif, sınır, provenance, source, exact target, overlap, nedensellik ve JSON güvenliği kontrolleri |
| Reporting | Ortak rapor modeli, JSON ve Markdown render'ları, evidence manifest builder |
| Reporting testleri | Durum önceliği, teknik sonuç ayrımı, kaynak bağı, renderer güvenliği ve gerçek dosya hash kontrolleri |
| D3 başlangıç kanıtı | Campaign sonucu, rapor modeli, Markdown görünümü, manifest ve test kaydı |

## Fault giriş sözleşmesi

Kök nesne yalnız `schema_version`, `scenario_id`, `generation`, `rail_budget_input`, `sequencing_input`, `targets`, `faults`, `sources`, `observation` ve `overlap_policy` alanlarını kabul eder. Beklenmeyen alanlar `BLOCKED` üretir.

Her fault kaydı kimlik, tür, exact target, başlangıç zamanı, süre, type-labelled severity, effect rule, recovery, beklenen durum, provenance ve kaynak referanslarını taşır. Effect rule ve recovery kendi sürümlü kural kimliğine, provenance sınıfına ve kaynak referanslarına sahiptir. `expected_status`, yalnız fixture oracle alanıdır; motor kararını değiştirmez.

### Exact target bağlama

Bir hedef hem kimlik hem tür açısından baseline verisine bağlanır. Aynı kimliğin başka bir tür etiketiyle kullanılması kabul edilmez.

| Target türü | İzinli baseline binding | İlave semantik koşul |
|---|---|---|
| `RAIL` | `RAIL_BUDGET_RAIL` | Baseline rail kimliği birebir eşleşir; polarity `NOT_APPLICABLE` olmalıdır |
| `LOAD` | `RAIL_BUDGET_LOAD` | Baseline load kimliği birebir eşleşir; polarity `NOT_APPLICABLE` olmalıdır |
| `REGULATOR` | `RAIL_BUDGET_REGULATOR` | Baseline regulator kimliği birebir eşleşir; polarity `NOT_APPLICABLE` olmalıdır |
| `SIGNAL` | `SEQUENCING_SUBJECT` | Subject yalnız `VIN_VALID`, `EN`, `PGOOD` veya `RESET` semantiğiyle signal olabilir; açık polarity gerekir |
| `SYSTEM` | `SEQUENCING_SUBJECT` | Subject yalnız `POWER_OFF` semantiğiyle system olabilir; polarity `NOT_APPLICABLE` olmalıdır |

Çelişkili subject türü veya `OTHER` event kind exact binding için kullanılamaz. Fault türü ayrıca izinli hedef türüyle çapraz doğrulanır; örneğin brownout ve rail-loss yalnız rail, stuck-low yalnız signal, restart yalnız system hedefinde değerlendirilir.

### Determinizm ve hash politikası

Bu dilim yalnız `FIXTURE` üretim modunu uygular. `SEEDED` açıkça `BLOCKED` olur; örtük seed veya RNG fallback'i yoktur. Runner dosya I/O'su, global RNG veya otomatik `generated_at` alanı kullanmaz. Aynı JSON değeri aynı sonucu ve aynı kanonik input hash'ini üretir. Nesne anahtar sırası önemsiz, dizi sırası hash açısından anlamlıdır.

## Fault teknik semantiği

| Fault türü | Severity profili | Bilinen etki koşulu | Temsilî fixture sonucu |
|---|---|---|---|
| `BROWNOUT` | `VOLTAGE_RATIO` birim `1` | Gözlenen oran eşikten küçüktür | `FAIL / BROWNOUT_EFFECT_TRIGGERED` |
| `STUCK_LOW` | `LOGIC_LEVEL` birim `logic` | Gözlenen seviye `LOW` değerine eşittir | `FAIL / STUCK_LOW_EFFECT_TRIGGERED` |
| `DELAY` | `TIME_EXTENSION` birim `s` | Ek süre eşikten büyüktür | `FAIL / DELAY_EFFECT_TRIGGERED` |
| `RAIL_LOSS` | `AVAILABILITY` birim `state` | Gözlenen durum `LOST` değerine eşittir | `FAIL / RAIL_LOSS_EFFECT_TRIGGERED` |
| `RESTART` | `RESTART_REQUEST` birim `state` | İstek `REQUESTED` değerine eşittir | `PASS / RESTART_REQUEST_ACCEPTED` |

Fault kaydının, effect rule'un, target'ın veya recovery'nin provenance sınıfı güvenilir değilse motor kesin `PASS` ya da `FAIL` türetmez. `SOURCE_EXPLICIT` ve `ENGINEERING_RULE` dışındaki kanıtlar sonuç üzerinde `UNKNOWN` veya yapısal çelişki varsa `BLOCKED` üretir. Kaynak erişimi, sabit sürüm, bilinen SHA-256, classification uyumu ve çözümlenen referanslar ayrıca kontrol edilir.

## Zaman ve recovery politikası

Fault aralığı başlangıcı dahil, bitişi hariç olacak şekilde yarı açıktır. Süre pozitif olmalıdır; yalnız açık instantaneous restart profili sıfır süreye izin verir. Decimal tabanlı karşılaştırma düşük basamak farklarını korur ve signed zero değerlerini aynı semantik zamana normalize eder.

Recovery son tarihi `fault_end + maximum_delay` olarak hesaplanır ve üst sınır dahildir. Bilinen completion tam son tarihteyse `PASS`, son tarihten sonraysa `FAIL` olur. Completion henüz gözlenmemiş ve observation son tarihte veya öncesindeyse sonuç `UNKNOWN`; güvenilir observation son tarihi deadline'ı geçmişse `FAIL` olur. Completion zamanı fault bitiminden önceyse veya observation ufkunun dışındaysa yapısal nedensellik ihlali nedeniyle `BLOCKED` olur. Completion state ile completion time birbiriyle tutarlı olmalıdır.

Başarılı recovery, bilinen non-restart fault etkisini maskelemez. Restart senaryosunda kabul edilmiş istek ve zamanında recovery birlikte teknik `PASS` üretebilir.

## Baseline nedensellik kapısı

Runner önce public `calculate_rail_budget` ve `evaluate_sequencing` fonksiyonlarını çalıştırır. Fault target'ının bağlı olduğu baseline bileşen `FAIL` veya `BLOCKED` ise fault etkisi güvenilir biçimde atfedilemez ve sonuç `BLOCKED` olur. İlgili baseline `UNKNOWN` ise attribution `UNKNOWN` olur. İlgisiz bir baseline sonucu başka domain'deki fault değerlendirmesini gereksiz yere engellemez.

Yapısal kurallar baseline belirsizliğinden önce uygulanır. Böylece recovery'nin fault bitiminden önce tamamlanması, observation ufkunun dışına taşması veya target türünün yanlış etiketlenmesi `UNKNOWN` arkasında gizlenmez.

## Durum ve beklenti ayrımı

Fault motorunun durum önceliği `BLOCKED > FAIL > UNKNOWN > PASS` şeklindedir. Reporting katmanı canlı adapter durumunu da taşıdığı için `BLOCKED > FAIL > UNKNOWN > NOT_RUN > PASS` sırasını kullanır.

Her fault sonucundaki `status` ve `reason_code` gerçek teknik karardır. `expectation.expected_status`, `expectation.observed_status` ve `expectation.matches` yalnız fixture oracle kontrolünü gösterir. Örneğin brownout fixture'ında teknik `FAIL` ile beklenen `FAIL` eşleşir; bu eşleşme teknik sonucu `PASS`e çevirmez. Campaign raporunun aggregate `FAIL` olması da test paketinin başarısız olduğu anlamına gelmez; en ağır gözlenen teknik injected-fault bulgusunu gösterir.

## Çıktı ve izlenebilirlik

Runner sonucu engine ve rule set sürümlerini, package sürümünü, kanonik input hash'ini, kaynak bağlarını, baseline engine çalıştırmalarını, her fault için effect ve recovery kararlarını, expectation eşliğini, sıralı trace'i, finding listesini, sınırlamaları ve iddia sınırlarını içerir.

Fault bulguları D1 finding şemasına zorla dönüştürülmez. D1 şeması fault subject, fault timing ve recovery ayrıntılarını kayıpsız temsil etmediği için sonuç modül-yerel kalır. Canlı zarf eşlemesi kalan WF-02-06 işidir.

## İlk WF 02 06 raporlama dilimi

`build_report_model` teknik component sonuçlarını immutable bir ortak modele normalleştirir. `render_json_report` ve `render_markdown_report` aynı modeli tüketir; finding kimlikleri, `(rule_id, status, reason_code)` üçlüleri ve kaynak bağları iki görünümde semantik olarak aynıdır. `generated_at` açıkça verilir, ancak teknik sonuç ve rapor model hash'lerinin dışında tutulur.

Evidence manifest beş kategori taşır: `inputs`, `sources`, `models`, `tools` ve `outputs`. Bu dilimde model kullanılmadığı için `models` açıkça boş dizidir. Diğer kategoriler gerçek dosya yollarına, sabit sürümlere ve byte-exact SHA-256 değerlerine bağlanır. Manifest kendisini kapsamaz.

Canlı REP-01 ve REP-03 adaptörleri uygulanmadı. Reporter bu durumu `LIVE_ADAPTER_NOT_IMPLEMENTED` nedeni ile `NOT_RUN` olarak gösterir; sentetik sonuçları canlı entegrasyon sonucu gibi sunmaz.

## Fixture kampanyası

| Fixture | Root teknik durum | Fault sonuçları | Beklenti eşliği |
|---|---|---|---|
| `brownout.json` | `FAIL` | brownout `FAIL`, recovery `PASS` | Evet |
| `stuck-low.json` | `FAIL` | stuck-low `FAIL`, recovery `PASS` | Evet |
| `delay.json` | `FAIL` | delay `FAIL`, recovery `PASS` | Evet |
| `rail-loss.json` | `FAIL` | rail-loss `FAIL`, recovery `PASS` | Evet |
| `restart.json` | `PASS` | restart effect `PASS`, recovery `PASS` | Evet |
| `unknown-threshold.json` | `UNKNOWN` | effect threshold bilinmiyor, recovery `PASS` | Evet |
| `recovery-combo.json` | `FAIL` | brownout `FAIL`, restart `PASS` | Evet |
| `fault-during-restart.json` | `FAIL` | restart `PASS`, rail-loss `FAIL` | Evet |

Sekiz senaryonun root dağılımı altı `FAIL`, bir `PASS` ve bir `UNKNOWN` sonucudur. Rapor modelinde 28 finding vardır ve aggregate durum `FAIL`dir. Rapor modeli SHA-256 değeri `f65c501df92e6fec8f63135a59c0a795faab19412fd4fdd09422491d2873ecb5` olarak üretildi.

## Doğrulama sonuçları

Nihai doğrulama aşağıdaki kontrolleri kapsar:

- Tam unittest paketi: `228/228 PASS`
- Fault-injection odaklı testler: `62/62 PASS`
- Reporting odaklı testler: `23/23 PASS`
- Python 3.9 sözdizimi uyumluluğu için tüm Python dosyalarının AST ayrıştırması: `PASS`
- Dokuz fault JSON fixture dosyasının strict parse kontrolü: `PASS`
- Sekiz campaign kaydının güncel runner ile birebir replay'i: `PASS`
- Saklanan report modelinin yeniden üretimi ve hash eşliği: `PASS`
- JSON ile Markdown finding kimliği ve üçlü eşliği: `PASS`
- Manifestteki dosya bayt hash'lerinin yeniden doğrulanması: `PASS`
- `git diff --check`: `PASS`
- Repo içindeki `.pyc` sayısı: `0`
- Bağımsız adversarial son denetim: `CLEAN — açık işlevsel veya sözleşmesel bulgu yok`

Bağımsız denetim; provenance belirsizliğinin kesin karar üretmesi, target türünün baseline domain veya sequencing subject semantiğiyle uyuşmaması, polarity profilinin yanlış kullanılması, recovery nedenselliğinin baseline belirsizliğiyle maskelenmesi, observation provenance'ı, signed zero ve completion state/time tutarlılığı için adversarial örnekler çalıştırdı. Bulunan açıklar regresyon testlerine dönüştürüldü.

## İddia sınırları ve açık işler

Bu teslim yalnız yazılım sözleşmeleri ve sentetik fixture'lar için kanıt üretir. Aşağıdaki sonuçlar bu teslimden çıkarılamaz:

- Analog transient veya gerçek waveform davranışı
- SPICE, fiziksel şema veya PCB doğrulaması
- Laboratuvar ölçümü, termal davranış veya loop stability
- Gerçek regülatör part modeli ya da üretici garantisi
- EMC, ISO, AEC veya saha uygunluğu
- Donanım güvenilirliği veya G1-G6 kapı onayı

G1 kaynak kapısı `BLOCKED` kalır ve G1-G6 donanım kapıları açılmamıştır. GAP-02 ve GAP-03 açıktır. REP-01/03 canlı adaptörleri, modül-yerel sonuçların D1 response/finding zarfına kayıpsız eşlenmesi ve ortak hash normalizasyonu tamamlanmamıştır. CLI sağlanmamıştır. Bu dilimde model kullanılmamıştır.

## Sonraki çalışma

Sıradaki ana çalışma `WF-02-05 Regulator Review` ile kalan `WF-02-06 Evidence and Integration` kapsamıdır. Exact-part üretici verisi veya yetkili proje kaynağı gelmeden regülatör, zamanlama ya da fiziksel davranış için `PASS` üretilmemelidir. Canlı REP-01/03 entegrasyonu; zarf eşlemesi, provenance sınıfları, hash normalizasyonu ve kanıt bütünlüğü kararları tamamlandıktan sonra ele alınmalıdır.

## İşlem kaydı

| İşlem | Durum |
|---|---|
| Commit | Yapılmadı |
| Push | Yapılmadı |
| Paket kurulumu | Yapılmadı |
| Fiziksel test | Yapılmadı |
| Canlı REP-01/03 entegrasyonu | `NOT_RUN` |
