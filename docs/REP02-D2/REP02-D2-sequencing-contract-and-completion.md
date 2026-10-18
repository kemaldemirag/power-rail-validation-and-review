# REP-02 D2 Sequencing Sözleşmesi ve Sentetik Uygulama Kapanış Raporu

- Durum: `WF-02-03 IMPLEMENTED — D2 SYNTHETIC APPLICATION COMPLETE`
- Tarih: 18 Ekim 2026
- Başlangıç revizyonu: `a72e0f2`
- Paket/araç sürümü: `0.3.0`
- FSM sürümü: `REP02-SEQ-FSM-1.0.0`
- Kural seti sürümü: `REP02-SEQ-RULESET-1.0.0`

WF-02-03, D1 `event` ve `source` kayıtlarını kullanan deterministik bir sequencing çekirdeği ekler. Motor, `VIN_VALID → EN → PGOOD → RESET release` startup zincirini açık zaman pencereleriyle değerlendirir; olay sırasını, kaynak/provenance güvenini, gözlem ufkunu ve ihlal izini makine-okunur biçimde raporlar.

Bu teslimdeki `PASS`, yalnız hash bağlı sentetik mühendislik kurallarına uyumu gösterir. Üretici zaman gereksinimi, gerçek PGOOD/EN/RESET davranışı, fiziksel shutdown sırası, laboratuvar ölçümü veya donanım kapısı onayı değildir.

## İncelenen girdiler

Uygulama aşağıdaki görev ve repo girdileri karşılaştırılarak hazırlanmıştır:

- `REP-02_Codex_Gorev_Plani_Pozisyon_Bazli_v1.docx`: WF-02-03 amaç, teslim, adım, sınır ve kabul maddeleri;
- `REP-02_Is_Akisi_Raporu_v1.pdf`: üç sayfalık iş akışı, GAP-03 ve oturum planı;
- `docs/REP02-D1/REP02-D1-product-contract.md` ve `REP02-D1-decisions.md`: D1 varlık, hash, provenance ve durum kararları;
- `docs/REP02-D2/REP02-D2-rail-budget-contract-and-completion.md`: D2 durum önceliği, kaynak kapısı ve fiziksel iddia sınırı;
- `CLAUDE-02-Power-Sequencing-Research.md`: kullanılabilecek mühendislik zinciri ile gerçek ürün sırası arasındaki ayrım.

PDF'nin üç sayfası metin ve görsel render üzerinden doğrulandı. DOCX'in ilgili tüm paragrafları ve tabloları doğrudan belge yapısından çıkarıldı; çalışma ortamında LibreOffice bulunmadığı için DOCX sayfa render'ı üretilemedi. Bu sınırlama, normatif WF-02-03 metninin okunmasını engellemedi.

## Teslim kapsamı

| Bileşen | İşlev |
|---|---|
| `src/power_rail_validation/sequencing/evaluator.py` | Kapalı girdi doğrulaması, deterministik FSM, Decimal zaman hesabı, kaynak kapısı ve olay izi |
| `src/power_rail_validation/sequencing/__init__.py` | Kararlı genel API ve sürümlü sabitler |
| `tests/fixtures/sequencing/normal.json` | Hash bağlı sentetik normal startup referansı |
| `tests/fixtures/sequencing/synthetic-rules-source.json` | Fixture'dan ayrı, kanonik SHA-256 ile bağlanan sentetik kural kaynağı |
| `tests/fixtures/sequencing/*.json` | Yanlış sıra, timeout, eksik PGOOD, tekrar, bilinmeyen pencere ve power-off vakaları |
| `tests/test_sequencing.py` | Pozitif, negatif, sınır, kanıt ve fail-closed testleri |
| `evidence/REP02-D2/sequencing-normal-result.json` | Normal fixture'ın gerçek motor çıktısı |
| `evidence/REP02-D2/sequencing-test-results.json` | Test, statik doğrulama ve bağımsız QA kanıtı |

## Modül-yerel giriş sözleşmesi

D1 `event` kaydı olay türü, durum, zaman ve kaynak bilgisini taşır; fakat senaryo türü, zaman kuralı ve gözlem ufku taşımaz. Bu nedenle D1 şemaları değiştirilmeden kapalı bir sequencing zarfı kullanılır.

| Alan | Kural |
|---|---|
| `schema_version` | Yalnız `1.0.0` |
| `scenario_id` | D1 kimlik deseniyle uyumlu, kararlı kimlik |
| `scenario_kind` | `STARTUP` veya `POWER_OFF` |
| `timing_rules[]` | Tam dört sıralı startup kuralı; her biri rule ID, beklenen kind/state/subject seti, minimum/maksimum gecikme, limit kökeni, provenance ve kaynak referansı taşır |
| `power_off_rule` | Zorunlu, tekil `POWER_OFF/ENTERED` kuralı; gerçek rule ID, tam subject seti, provenance ve kaynak referansı taşır |
| `events[]` | D1 `event` biçimi; kimlikler tekil, `subject_refs` bağlayıcı, kaynaklar çözülebilir olmalıdır |
| `sources[]` | D1 `source` biçimi; sürüm sabit, erişim `AVAILABLE`, hash `KNOWN/SHA-256` olmalıdır |
| `observation_end_offset` | `KNOWN` veya `UNKNOWN`, birimi yalnız saniye; timeout kararının ufku |
| `observation_end_at` | RFC 3339 audit koordinatı; ortak olay başlangıcı ile ofsete tam uymalıdır |
| `observation_provenance` ve `observation_source_refs` | Gözlem ufkunun karar kanıtı; event/rule kaynaklarıyla aynı gate'lerden geçer |
| `sorting_policy` | Yalnız `TIME_OFFSET_ASCENDING` |
| `simultaneous_policy` | Varsayılan `REJECT` veya açık `ALLOW_ZERO_MIN_EXPECTED_ORDER` |

Beklenen startup veya power-off olayının `subject_refs` seti ile gözlenen olayın seti tam eşleşir. Böylece başka bir rayın PGOOD'u, başka bir EN hattı, başka bir RESET hedefi veya başka bir güç alanının POWER_OFF olayı aynı kind/state değerini taşısa bile FSM'i ilerletemez.

`VIN_VALID`, `EN`, `PGOOD` ve `RESET` olayları `SIGNAL_TRANSITION`; `POWER_OFF` olayı `POWER_STATE` event type profiline bağlıdır. Normal profil RESET'in release olayını `DEASSERTED` olarak tanımlar. Bu polarite sentetik sözleşme kararıdır; üretici gerçeği olarak yorumlanamaz.

## Durum makinesi

Normal startup geçişleri şöyledir:

```text
WAIT_VIN_VALID
  └─ VIN_VALID / ASSERTED → WAIT_EN
       └─ EN / ASSERTED → WAIT_PGOOD
            └─ PGOOD / ASSERTED → WAIT_RESET_RELEASE
                 └─ RESET / DEASSERTED → RUNNING
```

Her geçiş kendi rule ID'si, subject binding'i ve bir önceki kabul edilen geçişe göre zaman penceresiyle değerlendirilir. Yanlış sıradaki olay FSM durumunu ilerletmez; ihlal trace'e yazılır ve kalan olaylar deterministik biçimde işlenir. Tekrarlanan bir geçiş `DUPLICATE_TRANSITION` olur; terminal durumdaki generic sıra ihlali power-off kuralıyla karıştırılmaz.

`POWER_OFF` ayrı bir senaryo türüdür. Bu profilde yalnız `power_off_rule` ile exact subject bağı kurulmuş `POWER_OFF/ENTERED` kabul edilir ve `POWERED_OFF` terminal durumuna geçilir. İkinci semantik POWER_OFF olayı, zamanı bilinmese dahi terminal-sonrası tekrar olarak `FAIL` olur. Motor RESET, EN veya PGOOD kapanış sırası icat etmez. Aynı olay bir `STARTUP` senaryosunda görülürse başarılı kapanış sayılmaz; `UNEXPECTED_POWER_OFF` ile `FAIL` olur.

Brownout, stuck-low, ray kaybı ve restart davranışları D1 olay enumlarında bulunmakla birlikte bu çekirdeğe sessizce eklenmemiştir. Kaynaklı fault ve recovery politikaları `WF-02-04 Fault Injection` kapsamında ele alınacaktır.

## Zaman, sıralama ve sınır politikası

Karar saati `time_offset` alanıdır. `occurred_at` sıralama kaynağı değil, bağımsız audit koordinatıdır. Tüm bilinen olayların `occurred_at − time_offset` sonucu ortak bir başlangıç vermeli; gözlem sonu da bu başlangıçla tam uyuşmalıdır. Çelişki `BLOCKED` olur.

Zaman aritmetiği `Decimal(str(value))` ile yapılır. Toplama ve çıkarma, karar değiştiren düşük basamakları koruyan dinamik ve üstten sınırlı hassasiyet bağlamında yürütülür. Yayınlanan her bilinen zaman miktarı hem sonlu JSON sayısını hem de tam `decimal_text` değerini taşır. Böylece `1 − 1e-100` gibi bir fark ekranda `1.0` olarak yuvarlansa bile karar kanıtı kaybolmaz.

Pencereler kapalıdır:

```text
minimum_delay ≤ measured_delay ≤ maximum_delay
```

- Tam minimum ve tam maksimum `PASS` olabilir.
- Minimumun hemen altı `TIMING_BELOW_MINIMUM`; maksimumun hemen üstü `TIMING_ABOVE_MAXIMUM` ile `FAIL` olur.
- Sınırların biri `UNKNOWN` olsa bile diğer bilinen sınır bağımsız denetlenir. Bilinen sınır kesin ihlal edilmişse sonuç `FAIL`; ihlal yok fakat diğer sınır bilinmiyorsa `UNKNOWN` olur.
- Negatif, sonlu olmayan veya saniye dışında bir zaman yapısal/kontrat hatasıdır ve `PASS` üretemez.

Girdi dizisi fiziksel sıra sayılmaz. Bilinen olaylar Decimal ofsete göre sıralanır; `input_index` denetim için, `processing_index` teknik sıra için korunur. Aynı girdinin iki çalıştırması aynı sonucu verir. Olay dizisinin yeniden sıralanması D1 kanonik hash'ini değiştirir, fakat farklı zamanlı aynı olay kümesinin teknik sonucu ve olay kimliği sırası değişmez.

Aynı zamanlı bağımlı olaylar varsayılan olarak `SIMULTANEOUS_EVENT_CONFLICT` üretir. Yalnız `ALLOW_ZERO_MIN_EXPECTED_ORDER` açıkça seçilmişse, ilgili sonraki kuralların minimumu sıfırsa ve kind/state/subject zinciri FSM'in açık beklenen sırasına uyuyorsa topolojik sıra uygulanabilir. Event ID'ye göre nedensellik türetilmez.

## Eksik olay ve gözlem ufku

Eksik olay hiçbir zaman otomatik `PASS` değildir. Bir önceki geçişin ofseti `t0`, beklenen kuralın maksimumu `tmax` ve gözlem sonu `tobs` için:

| Koşul | Sonuç |
|---|---|
| `tobs ≤ t0 + tmax` | `UNKNOWN / OBSERVATION_WINDOW_INCOMPLETE` |
| `tobs > t0 + tmax` | `FAIL / REQUIRED_EVENT_TIMEOUT` |
| Maksimum veya gözlem ufku bilinmiyor | `UNKNOWN` |
| İlgili beklenen olayı sağlayabilecek zaman/durum belirsiz bir event var | `UNKNOWN` |

Tam maksimumda eksik olayın `UNKNOWN` kalması bilinçli profil kararıdır: pencerenin üst sınırı kapsayıcıdır ve timeout ancak sınır geçildikten sonra kesinleştirilir. Alakasız veya yanlış subject'e bağlı bir `UNKNOWN` event kesin timeout'u ya da kesin yanlış sıra ihlalini maskeleyemez.

## Kaynak, provenance ve hash kapıları

Tüm karar event'leri, zaman kuralları ve gözlem ufku kaynak referansı taşır. Referanslı kaynak:

- girişte çözülmeli;
- `AVAILABLE` olmalı;
- gerçek, 64 haneli küçük harf SHA-256 taşımalı;
- boş veya hareketli sürüm kullanmamalıdır.

`latest`, `HEAD`, `main`, wildcard/range gösterimleri (`1.x`, `1.*`, `^1.2.3`, `>=1.2`, `~1.2`) ve branch yolları sabit sürüm sayılmaz. `SOURCE_EXPLICIT` ve `ENGINEERING_RULE` güvenilir sınıflardır; trusted event/rule/observation provenance değeri referanslanan her kaynağın classification değeriyle uyuşmalıdır. `ASSUMPTION`, `UNKNOWN` ve lineage sözleşmesi bulunmayan `DERIVED`, `PASS` desteklemez; `CONFLICT` değerlendirmeyi bloklar.

Sentetik normal kurallar ve zorunlu power-off kuralı ayrı `synthetic-rules-source.json` dosyasındadır. Dosyanın REP-02 kanonik JSON SHA-256 değeri `fc77ce66e85ad5f03019774f3135476d98cd9bc197422a7b00830d8c13b8278a` olarak fixture'a bağlanmış; test, hem hash'i hem de kural içeriğinin birebir eşitliğini doğrulamıştır.

Tam girdi, orijinal dizi sırasını da kapsayan kanonik JSON SHA-256 ile sonuca bağlanır. Hash hesaplanamazsa değerlendirme `BLOCKED / INPUT_HASH_UNCOMPUTABLE` olur.

## Durum ve reason-code kararı

Toplam durum önceliği şöyledir:

```text
BLOCKED > FAIL > UNKNOWN > PASS
```

| Durum | Örnek nedenler |
|---|---|
| `PASS` | Normal sentetik zincirin tüm geçişleri kapalı pencere içinde; dedicated power-off olayı geçerli |
| `FAIL` | Yanlış sıra/subject/type/polarite, minimum veya maksimum ihlali, kesin timeout, tekrar, beklenmeyen power-off |
| `UNKNOWN` | İlgili event zamanı/durumu, gerekli sınır veya gözlem ufku belirsiz; provenance PASS desteklemiyor |
| `BLOCKED` | Kapalı sözleşme ihlali, duplicate ID/rule, çözülemeyen/erişilemeyen/hashsiz/hareketli kaynak, provenance çatışması, zaman koordinatı çelişkisi |

Temel reason code ailesi `SEQUENCE_COMPLETE`, `POWER_OFF_TERMINATED`, `EVENT_SEQUENCE_VIOLATION`, `SUBJECT_BINDING_MISMATCH`, `POWER_OFF_SUBJECT_BINDING_MISMATCH`, `EVENT_AFTER_POWER_OFF`, `EVENT_TYPE_INCOMPATIBLE`, `WRONG_EVENT_POLARITY`, `TIMING_BELOW_MINIMUM`, `TIMING_ABOVE_MAXIMUM`, `REQUIRED_EVENT_TIMEOUT`, `OBSERVATION_WINDOW_INCOMPLETE`, `DUPLICATE_TRANSITION`, `SIMULTANEOUS_EVENT_CONFLICT`, `EVENT_TIME_UNKNOWN`, `TIMING_WINDOW_UNKNOWN`, kaynak/provenance kodları ve kapalı sözleşme kodlarından oluşur.

## Sonuç ve olay izi sözleşmesi

Sonuç; FSM/kural seti/araç/paket sürümü, senaryo kimliği/türü, durum ve reason code'lar, input hash, sabitlenmiş kaynak kimliği+sürümü+hash'i, gözlem kanıtı, politika, terminal durum, trace, issue ve limitation alanlarını taşır. Çalışma zamanından gelen `generated_at` eklenmez; aynı girdi byte düzeyinde aynı teknik sonucu üretir.

Her trace kaydı şunları görünür kılar:

- `processing_index` ve özgün `input_index`;
- event kimliği, kind/state ve subject referansları;
- önceki/sonraki FSM durumu;
- beklenen event kind/state/subject seti ve `rule_id`;
- `outcome` ile `reason_code`;
- olay ofseti;
- anchor event, beklenen minimum/maksimum, ölçülen gecikme ve `violated_bound`.

Timeout trace'inde gözlenen event `null`, ölçülen süre gözlem ufku eksi anchor zamanı, ihlal edilen sınır `MAXIMUM` olur. Bu modül-yerel sonuç D1 `response` nesnesi değildir; canlı entegrasyondan önce ayrı sürümlü JSON Schema ve REP-01 eşleme kararı gerekir.

## Fixture ve kabul kapsamı

| Fixture | Beklenen karar |
|---|---|
| `normal.json` | `PASS / SEQUENCE_COMPLETE / RUNNING` |
| `wrong-order.json` | Yanlış sıra `FAIL` |
| `timeout.json` | Maksimum sınır geçildikten sonra eksik PGOOD `FAIL` |
| `missing-pgood.json` | Tam kapsayıcı deadline'da eksik PGOOD `UNKNOWN` |
| `duplicate-event.json` | Tekrarlanan semantik geçiş `FAIL` |
| `unknown-window.json` | Bilinmeyen pencere `UNKNOWN`; bilinen karşı sınır yine uygulanır |
| `power-off.json` | Dedicated power-off profili `PASS / POWER_OFF_TERMINATED` |

Test paketi ayrıca tam min/max, hemen alt/üst, yanlış subject, event type, polarite, duplicate ID/rule, aynı zaman politikaları, kaynak sürümü/hash/erişim/classification, observation koordinatı, sourced-limit ihlali, key-order/hash davranışı, unsorted olay dizisi, bilinmeyen event, non-finite/negatif zaman, çok farklı Decimal üsleri, derin bozuk girdi ve JSON güvenli çıktıyı kapsar.

Son doğrulamada sequencing alt paketi `60/60`, tam D1+D2 paketi `143/143` geçmiştir. Normal kanıt sonucu `PASS / SEQUENCE_COMPLETE`, terminal durum `RUNNING`, input hash `74b230ac940eb36d4e54e76a4d27125b423ebe94f007bbc7196928337016a724` olmuştur. Komutlar, ortam, dosya hashleri ve bağımsız QA özeti `evidence/REP02-D2/sequencing-test-results.json` içindedir.

## İddia sınırları ve açık kararlar

1. GAP-03 kapanmamıştır. Gerçek PGOOD/EN/RESET eşikleri ve zamanları yetkili üretici/proje kaynağıyla sağlanana kadar sentetik pencereler üretici gereksinimi değildir.
2. RESET active polaritesi kaynakta kapanmamıştır; `RESET/DEASSERTED` bu sentetik profilin açık engineering-rule seçimidir.
3. Power-off için ray/sinyal kapanış sırası kaynakta yoktur; motor yalnız dedicated `POWER_OFF/ENTERED` olayını sonlandırır.
4. Brownout, stuck-low, rail-loss ve restart fault/recovery davranışları WF-02-04'e bırakılmıştır; mevcut çekirdek bunları sessizce başarılı saymaz.
5. D1 sonucunda sequencing zaman kanıtını taşıyacak sürümlü request/response şeması yoktur.
6. REP-01 zarf eşlemesi, sırasız dizi hash normalizasyonu, source integrity state ve derived lineage kararları canlı entegrasyon öncesinde kapanmalıdır.
7. Serbest metin exact source version kimliğinin gerçekten immutable olduğu yetkili kaynak deposunda doğrulanmalıdır; motor bilinen hareketli desenleri reddeder fakat dış depo garantisi vermez.
8. Fiziksel şema/PCB, SPICE, laboratuvar, termal/transient/loop-stability, EMC, ISO/AEC ve saha doğrulaması yapılmamıştır. G1-G6 kapıları açılmamıştır.

## D2 kapanışı ve sonraki dilim

WF-02-01 veri sözleşmesi, WF-02-02 statik rail-budget ve WF-02-03 sequencing sentetik çekirdekleri birlikte tamamlanmıştır. Bu kapsamla D2 sentetik uygulama kapısı kapanır; canlı entegrasyon ve donanım kapıları kapalı kalır.

Bir sonraki ana çalışma `WF-02-04 Fault Injection`dır. Brownout, stuck-low, delay, rail-loss ve restart; hedef, başlangıç, süre, şiddet, recovery ve beklenen status alanlarıyla sürümlü fault sözleşmesine bağlanacaktır. Runner rail-budget ve sequencing motorlarından ayrık, sabit fixture veya açık seed ile deterministik olacaktır.

Bu çalışma commit, push veya paket kurulumu yapmamıştır.
