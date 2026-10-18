# REP-02 D2 Statik Rail Budget Sözleşmesi ve Kapanış Raporu

- Durum: `WF-02-02 IMPLEMENTED — SYNTHETIC STATIC CORE`
- Tarih: 18 Ekim 2026
- Başlangıç revizyonu: `a72e0f2`
- Paket sürümü: `0.2.0`
- Formül sürümü: `REP02-RB-1.0.0`
- Kural kimliği: `REP02.RAIL_BUDGET.STATIC`

WF-02-02, tek bir güç rayının açıkça verilen akım profilleri, gerilim, kapasite, derating ve verim girdileriyle statik bütçesini deterministik olarak hesaplar. Kullanıcının 18 Ekim 2026 tarihli ilerleme talimatı, D1 sözleşmesini bu sentetik çekirdek için ilerleme kapısı olarak kabul etmiştir. Canlı REP-01 eşlemesi ve G1-G6 donanım kapıları bu kabulün dışındadır.

Motorun `PASS` sonucu yalnız sağlanan sentetik veya kaynak bağlı statik sayıların tanımlı cebirsel sınırlar içinde olduğunu gösterir. Termal, transient, kontrol döngüsü kararlılığı, SPICE, laboratuvar ölçümü, EMC, ISO/AEC veya saha uygunluğu göstermez.

## Teslim kapsamı

| Bileşen | İşlev |
|---|---|
| `src/power_rail_validation/rail_budget/calculator.py` | Bağımlılıksız ve deterministik statik hesap motoru |
| `src/power_rail_validation/rail_budget/__init__.py` | Kararlı genel API ve sürümlü sabitler |
| `tests/fixtures/rail_budget/nominal.json` | Elle hesaplanan nominal ve sınır referansı |
| `tests/fixtures/rail_budget/unknown-limit.json` | Bilinmeyen kapasite limiti örneği |
| `tests/fixtures/rail_budget/blocked-source.json` | Erişilemeyen ve hash doğrulanamayan kaynak örneği |
| `tests/test_rail_budget.py` | Pozitif, negatif, bilinmeyen ve sınır kabul testleri |
| `evidence/REP02-D2/nominal-result.json` | Nominal fixture'ın gerçek hesap sonucu |
| `evidence/REP02-D2/test-results.json` | Test, derleme ve statik doğrulama kanıtı |

## Giriş sözleşmesi

Rail-budget girdisi D1 varlıklarını değiştirmeyen, sürümlü ve modül-yerel bir JSON nesnesidir. D1 yük varlığı yalnız `operating_current` ve `peak_current` taşıdığı için `Imin` tahmin edilmez; üç profil de açıkça sağlanır.

| Alan | Zorunluluk | Kural |
|---|---:|---|
| `schema_version` | Evet | Yalnız `1.0.0` |
| `rail` | Evet | `rail_id`, nominal gerilim, minimum/maksimum gerilim limiti, provenance ve kaynak referansları |
| `loads[]` | Evet | En az bir yük; aynı `rail_id`; açık `Imin`, `Ityp`, `Imax`; provenance ve kaynak referansları |
| `loads[].backfeed_permitted` | Hayır | Yoksa negatif akım yasaktır; `true` yalnız işaretli statik cebiri açar |
| `capacity` | Evet | Tek seçilmiş regülatör, ray kimliği, rated current ve current limit |
| `derating.factor` | Evet | Boyutsuz, `0 < factor <= 1`; sayısal varsayılan yoktur |
| `efficiency.factor` | Hayır | Boyutsuz, `0 < factor <= 1`; yoksa giriş ve kayıp gücü ile toplam durum `UNKNOWN` olur |
| `sources[]` | Evet | Tüm kayıtlar kapalı yapısal doğrulamadan geçer; karar girdilerinin referans verdiği kayıtlar ayrıca erişilebilirlik, güven ve SHA-256 kapısından geçer |

Fiziksel büyüklükler `{state, value, unit}` biçimindedir. `KNOWN` sonlu sayı, `UNKNOWN` yalnız `null` taşır. Bilinen `0`, bilinmeyen değer değildir. Giriş birimleri `V`, `A` ve boyutsuz faktörler için `1`; sonuç birimleri `V`, `A`, `W`, `%` ve `1` olur. `W`, `%` ve `1` D1 yanıt şemasına otomatik olarak eklenmez.

## Formüller ve sayısal politika

Her profil için toplam akım ayrı hesaplanır:

```text
Imin,total = Σ Imin,load
Ityp,total = Σ Ityp,load
Imax,total = Σ Imax,load
```

Güç, kapasite ve marj formülleri şunlardır:

```text
Pout,x = Vnominal × Ix,total
Cderated = Irated × derating_factor
Ceffective = min(Cderated, Icurrent_limit)
Iworst = max(abs(Imin,total), abs(Imax,total))
utilization_percent = 100 × Iworst / Ceffective
margin_A = Ceffective − Iworst
Pin,x = Pout,x / efficiency
Ploss,x = Pin,x − Pout,x
```

JSON sayıları `Decimal(str(value))` ile dönüştürülür. Çalışma hassasiyeti giriş büyüklüklerinden türetilir; ara sonuçlar yuvarlanmaz ve limit karşılaştırmaları yuvarlanmamış değerle yapılır. Yayımlanan türetilmiş sayılar `ROUND_HALF_EVEN` ile 12 anlamlı basamağa yuvarlanır. Sonlu bir Decimal değeri sonlu JSON sayısına dönüştürülemiyorsa sonuç `NUMERIC_RESULT_UNREPRESENTABLE` ile `BLOCKED` olur. Talebin etkin kapasiteye tam eşit olması sıfır marjlı `PASS`; en küçük pozitif aşım `FAIL` olur. Hesap toleransı sıfırdır.

Dizi sırası mevcut D1 kanonikleştirmesinde hash açısından anlamlıdır. Motor bunu değiştirmez ve sonucu `ARRAY_ORDER_IS_HASH_SIGNIFICANT_BY_D1_CANONICALIZATION` sınırlamasıyla işaretler. REP-01 ile ortak sırasız-dizi normalizasyonu canlı entegrasyon öncesinde karara bağlanmalıdır.

Kaynak sürümü boş bırakılamaz; `latest`, `HEAD`, `main`, `master`, `current`, `stable`, `nightly`, geliştirme dalı adları ve yol-sonu eşdeğerleri gibi açık hareketli alias'lar `BLOCKED` olur. Bununla birlikte, serbest metin bir sürüm etiketinin gerçekten değişmez olduğunu motor tek başına kanıtlayamaz; bu güvence canlı entegrasyonda yetkili kaynak deposu ve integrity state ile doğrulanmalıdır.

## Durum kararı

| Durum | Koşul örnekleri |
|---|---|
| `PASS` | Tüm zorunlu değerler bilinir; referanslı kaynaklar erişilebilir, açık hareketli alias içermeyen sürümlü ve hash bağlıdır; provenance güvenilirdir; gerilim limit içindedir; en kötü akım etkin kapasiteyi aşmaz |
| `FAIL` | Birim uyuşmazlığı, yetkisiz negatif akım, etkin ray için sıfır/negatif gerilim, geçersiz faktör/kapasite, limit dışı gerilim veya aşırı kapasite |
| `UNKNOWN` | Açık `UNKNOWN` değer, varsayımsal ya da lineage'sız türetilmiş provenance, sağlanmamış verim veya ters-yön verim modeli olmadan backfeed |
| `BLOCKED` | Zorunlu nesne/alan yokluğu, çelişkili provenance, çözülemeyen/erişilemeyen/hashsiz kaynak veya belirsiz kapasite seçimi |

Birden çok sorun bulunduğunda öncelik `BLOCKED`, `FAIL`, `UNKNOWN`, `PASS` sırasıdır. Bilinen metrikler daha düşük bir toplam durum oluşsa bile denetim için korunur; bilinmeyen metrikler sıfıra dönüştürülmez.

## Sonuç sözleşmesi

Her sonuç aşağıdaki denetim alanlarını taşır:

- `formula_version`, `rule_id`, araç/paket adı ve sürümü, `status`, `reason_code` ve tüm `reason_codes`;
- kanonik JSON olarak bağlanabilen ve denetim kopyası üretilebilen girişlerde `input_hash` ile değiştirilmeyen `calculation_inputs` kopyası; aksi durumda açık `BLOCKED` kodu ve `null`;
- kullanılan kaynak kimlikleri ile sürümleri;
- Decimal, yuvarlama ve kapasite seçimi politikası;
- uygulanan formüller;
- birimli akım, çıkış/giriş/kayıp gücü, kapasite, kullanım, akım marjı ve güç marjı metrikleri;
- alan yolu, önem, kod ve açıklama içeren sorunlar;
- statik kapsamı ve fiziksel iddia yasağını belirten sınırlamalar.

Sonuç, D1 `response` nesnesi değildir. Canlı entegrasyon öncesinde ayrı request/response JSON şemaları ve assessment-result hash kararı gerekir.

## Elle hesaplanan referans

Nominal fixture iki yük için aşağıdaki değerleri kullanır:

| Profil | Toplam akım | Çıkış gücü | Giriş gücü | Kayıp gücü |
|---|---:|---:|---:|---:|
| Minimum | 0,15 A | 0,75 W | 0,9375 W | 0,1875 W |
| Tipik | 0,30 A | 1,50 W | 1,875 W | 0,375 W |
| Maksimum | 0,60 A | 3,00 W | 3,75 W | 0,75 W |

Ray gerilimi 5 V, rated kapasite 1 A, derating faktörü 0,8, açık current limit 0,9 A ve verim 0,8'dir. Derated kapasite 0,8 A, etkin kapasite 0,8 A, maksimum kullanım yüzde 75, akım marjı 0,2 A ve güç marjı 1 W'tır. Sonuç `WITHIN_DERATED_CAPACITY_AND_LIMITS` reason code ile `PASS` olur.

## Kabul doğrulaması

Rail-budget kabul testleri şu davranışları kapsar:

- elle hesaplanan tüm nominal formüller ve deterministik tekrar;
- tam kapasite sınırı, aşırı yük ve negatif marj;
- limit dışı ve sıfır gerilim;
- yanlış birim, sonlu olmayan sayı ve geçersiz provenance tipi;
- eksik `Imin`, bilinmeyen current limit ve sağlanmamış verim;
- verim varsayımının hesaplanması fakat `PASS` desteklememesi;
- negatif akımın açık backfeed iznine bağlanması;
- sıfır kapasitede bölme yapılmaması;
- erişilemeyen, hashsiz, çelişkili veya çözülemeyen kaynakların `PASS` üretememesi;
- tek kapasite kaynağı zorunluluğu ve dizi-sırası hash davranışının görünür olması;
- çok büyük tamsayılarda karar hassasiyetinin korunması ve sonlu JSON çıktı garantisi;
- `1 + 1e-100` gibi farklı üs büyüklüklerindeki toplamların aşımı gizlememesi;
- beklenmeyen iç alanların, hareketli kaynak sürümlerinin ve lineage'sız `DERIVED` girdilerin `PASS` üretememesi;
- kullanılmayan kaynak kayıtlarının da yapısal olarak doğrulanması;
- D1 kimlik deseninin ray, yük, regülatör ve kaynak referanslarında uygulanması;
- izinli veya izinsiz ters akımda ileri-yön verim formülünün kullanılmaması;
- aşırı derin ama JSON-uyumlu biçimlerde exception sızdırmadan `BLOCKED` sonucu üretilmesi.

WF-02-02 kapanışındaki bağımsız yeniden incelemede o günkü tam paket `83/83`, rail-budget alt paketi `34/34` geçmiştir. WF-02-03 eklendikten sonraki güncel birleşik D2 sonucu `143/143`; rail-budget regresyon alt paketi yine `34/34` geçmiştir. Güncel komut, test sayıları, Python sürümü, dosya hashleri ve doğrulama sonuçları `evidence/REP02-D2/test-results.json` içinde kayıtlıdır.

## Açık entegrasyon kararları

WF-02-02 sentetik çekirdeğini engellemeyen fakat canlı entegrasyon öncesinde kapanması gereken kararlar şunlardır:

1. REP-01 `power_input_profile`, `rail_demands` ve diğer doğal alanlarının REP-02 zarfına kayıpsız eşlenmesi.
2. Üretici yapılandırma hash'i ile handoff payload hash'inin ayrılması ve sırasız dizilerin ortak normalizasyonu.
3. `LOCAL_HASH_VERIFIED` benzeri integrity state, decisive/supporting evidence ve türetilmiş kaynak parent-chain alanları. Bu alanlar eklenene kadar `DERIVED` provenance `PASS` desteklemez.
4. D1 yanıt sözleşmesinde `W`, `%` ve `1` metrikleri ile akım/güç limit referanslarının sürümlenmesi.
5. Paralel, redundant veya birden çok regülatörün aynı rayı sürmesi için kapasite/topoloji kuralı.
6. Sonuç nesnesinin kendi assessment hash'i ve request-only doğrulama şeması.
7. Serbest metin kaynak sürümünün gerçekten değişmez olduğunun yetkili depo kimliğiyle doğrulanması.

Q-C04 USB azami akımı, Q-C05 ekran gerilim/backlight akımı, Q-C06 ortam ve soğutma şartları ile PKG-A/B/C kaynak paketleri hâlâ mevcut değildir. Bu nedenle gerçek ray bütçesi ve G2 sonucu `NOT_RUN/BLOCKED` kalır.

## Oturum 2 devri ve güncel durum

Bu belgenin ilk tesliminde sıradaki dilim `WF-02-03 Sequencing` idi. Oturum 3'te sequencing çekirdeği tamamlanmış ve rail-budget ile birlikte D2 sentetik uygulama kapısı kapanmıştır. Güncel sequencing sözleşmesi, testleri ve açık donanım sınırları `REP02-D2-sequencing-contract-and-completion.md` belgesindedir. Bir sonraki ana dilim `WF-02-04 Fault Injection`dır.
