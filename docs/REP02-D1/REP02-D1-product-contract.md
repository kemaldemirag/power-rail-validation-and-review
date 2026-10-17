# REP-02 D1 Ürün ve Veri Sözleşmesi

Durum: uygulanmış başlangıç paketi  
Paket sürümü: `0.1.0`  
Varlık şema sürümü: `1.0.0`  
JSON Schema: Draft 2020-12  
İş akışı: WF-02-01 / D1

## 1. Amaç ve kapsam

Bu sözleşme, REP-01 tarafından üretilen güç-ağı yapılandırmasının REP-02 tarafından doğrulanması için gereken veri sınırını tanımlar. D1 kapsamı şunlardır:

- `rail`, `load`, `regulator`, `source`, `event` ve `finding` varlıkları;
- REP-01 istek alanları ile REP-02 yanıt alanlarının yapısal ve alan kuralları;
- girdinin deterministik SHA-256 ile bağlanması;
- kanıt, kaynak sürümü, kural ve araç sürümü izlenebilirliği;
- bilinmeyen, eksik ve çelişkili verinin güvenli statüye dönüştürülmesi;
- güç sıralaması olaylarının açık biçimde taşınması.

D1 bir devre çözücüsü, SPICE motoru, şema kitaplığı veya REP-01 çalışma zamanı adaptörü değildir. Başka repodan Python içe aktarımı yapmaz. JSON Schema dosyaları varlık değişim biçiminin otoritesidir; `validation.py` ise kritik D1 güvenlik kontrollerini yalnız Python standart kitaplığı ile uygular.

## 2. Teslim bileşenleri

| Bileşen | İşlev |
|---|---|
| `src/power_rail_validation/contracts/schemas/*.schema.json` | Altı varlık için Draft 2020-12 şemaları |
| `src/power_rail_validation/contracts/contracts.csv` | Şema envanteri ve sahipliği |
| `src/power_rail_validation/contracts/field_dictionary.csv` | Alan yolu, tip, birim ve bilinmeyen değer politikası |
| `src/power_rail_validation/contracts/canonical.py` | Açıkça tanımlanmış deterministik JSON ve SHA-256 |
| `src/power_rail_validation/contracts/validation.py` | Bağımlılıksız yapı ve alan doğrulaması |
| `tests/fixtures/contracts/*.json` | Olumlu ve olumsuz sözleşme örnekleri |
| `tests/test_contracts.py` | Çalıştırılabilir kabul testleri |

## 3. Repo sorumluluk sınırı

### REP-01 üretici sorumlulukları

- İstek zarfını ve tüm koleksiyonları oluşturur.
- `product_contract_version` ve `rule_registry_version` değerlerini sabitler.
- Kaynakların erişilebilirliğini, sürümünü ve içerik karmasını açıkça kaydeder.
- Bölüm 5'teki malzemeden `configuration_sha256` üretir.
- İzin verilen iddia kapsamını `claim_limits` ile bildirir.

### REP-02 tüketici sorumlulukları

- İstek yapısını ve altı varlık tipini doğrular.
- İstek karmasını yeniden hesaplar; uyuşmazlıkta değerlendirmeyi geçersiz sayar.
- Bulguları tam kaynak sürümü ve tam girdi karmasına bağlar.
- Yanıt iddia kapsamını istek kapsamının dışına genişletmez.
- Eksik kanıttan veya tek başına `ASSUMPTION`/`UNKNOWN`/`CONFLICT` sınıflı kanıttan `PASS` üretmez.

REP-02, REP-01 paketini çalışma zamanında içe aktarmaz. Entegrasyon yalnız sürümlenmiş JSON verisi üzerinden gerçekleşir. Bu sınır, bağımsız yeniden üretilebilirliği ve repo yaşam döngülerinin ayrılmasını korur.

## 4. İstek sözleşmesi

İstek `request` nesnesinde taşınır.

| Alan | Tip | Kural |
|---|---|---|
| `contract_version` | string | Zorunlu; istek zarfı sürümü |
| `handoff_id` | string | Zorunlu; taşıma/izleme kimliği |
| `producer.repo` | string | Zorunlu; üretici repo, D1 için `REP-01` |
| `producer.product_contract_version` | string | Zorunlu; ürün sözleşmesi sürümü |
| `producer.rule_registry_version` | string | Zorunlu; kural kayıt sürümü |
| `configuration_id` | string | Zorunlu; teknik yapılandırma kimliği |
| `configuration_sha256` | 64 küçük hex | Zorunlu; Bölüm 5'e göre gerçek içerik karması |
| `collections.sources` | array | Zorunlu; `source` varlıkları |
| `collections.rails` | array | Zorunlu; `rail` varlıkları |
| `collections.loads` | array | Zorunlu; `load` varlıkları |
| `collections.regulators` | array | Zorunlu; `regulator` varlıkları |
| `collections.events` | array | Zorunlu; `event` varlıkları |
| `claim_limits` | array[string] | Zorunlu, benzersiz ve boş olamaz |

Koleksiyonun boş olması ile koleksiyon alanının hiç bulunmaması farklıdır. Örneğin kaynak paketi bekleniyor fakat bulunamıyorsa `sources` dizisinden sessizce çıkarılmaz; `access_status: "MISSING"` olan bir `source` kaydı gönderilir.

## 5. Kanonikleştirme ve yapılandırma karması

### 5.1 Kodlama sözleşmesi

`canonical.py` şu dar ve açık sözleşmeyi uygular:

1. Yalnız JSON değer tipleri kabul edilir.
2. Nesne anahtarları Unicode anahtar değerine göre sıralanır.
3. Ayırıcılar `,` ve `:` olup önemsiz boşluk yazılmaz.
4. Unicode karakterler kaçışa zorlanmadan UTF-8 kodlanır.
5. `NaN`, pozitif/negatif sonsuzluk reddedilir.
6. Son UTF-8 bayt dizisinin SHA-256 karması küçük hex olarak yazılır.

Bu algoritma için RFC 8785/JCS uyumu iddiası yoktur. Özellikle farklı sayı serileştiricileri arasında genel bir JCS birlikte çalışabilirliği vaat edilmez. Üretici, D1 için aynı tanımlı kodlama sözleşmesini uygulamalıdır.

D1 adayında dizi sırası hash açısından anlamlıdır. REP-01'in anlamsal olarak sırasız dizileri için ortak normalizasyon kuralı canlı entegrasyon öncesi açık karar olarak onaylanmalıdır.

### 5.2 Hash malzemesi

`configuration_hash_payload(request)` tam olarak şu alanları seçer:

```json
{
  "contract_version": "...",
  "configuration_id": "...",
  "producer": {
    "repo": "...",
    "product_contract_version": "...",
    "rule_registry_version": "..."
  },
  "collections": {},
  "claim_limits": []
}
```

`contract_version` ve `claim_limits` anlamı ve izin verilen sonucu etkileyebildiği için karmaya dahildir. `handoff_id` yalnız taşıma kimliği olduğu için hariçtir. `configuration_sha256` kendi kendini karmaya katmaz.

Doğrulayıcı, bildirilen değer ile yeniden hesaplanan değer farklıysa `REQUEST_CONFIGURATION_HASH_MISMATCH` üretir. JSON anahtar sırasının değişmesi karmayı değiştirmez; teknik değer, sürüm veya iddia kapsamındaki değişiklik karmayı değiştirir.

## 6. Yanıt sözleşmesi

Yanıt `response` nesnesinde taşınır ve REP-01 D1 alan adlarını doğrudan kullanır.

| Alan | Tip | Kural |
|---|---|---|
| `contract_version` | string | İstek sürümüyle birebir aynı olmalıdır |
| `assessment_id` | string | Zorunlu değerlendirme kimliği |
| `input_configuration_sha256` | 64 küçük hex | İstek karmasının birebir yankısı |
| `tool_versions` | array[object] | En az bir kayıt; `name`, `version`, `package_version` zorunlu |
| `model_records` | array[object] | Kullanılan model kayıtları; boş olabilir |
| `checks` | array[object] | Kural kontrolleri |
| `scenario_results` | array[object] | Senaryo sonuçları |
| `findings` | array[finding] | Denetlenebilir bulgular |
| `summary_status` | status enum | Toplam durum |
| `claim_limits` | array[string] | İstek değer kümesini eksiksiz ve genişletmeden yankılar; D1'de küme eşitliği zorunludur |
| `generated_at` | RFC 3339 string | Zaman dilimi zorunlu |

Her `model_records` girdisi en az `model_id`, `source`, `sha256`, `license_status`, `model_type`, `validity_bounds` ve `access_status` alanlarını taşır.

Her `checks` ve `scenario_results` girdisi şu alanları taşır:

- `id`, `status`, `reason_code`, `reason` ve `next_action`;
- `inputs_used`, `evidence_refs`, `model_refs`, `limitations` dizileri;
- değerleri birimli quantity nesneleri olan `metrics` nesnesi.

Bir kontrol veya senaryo `PASS` ise `inputs_used` ve `evidence_refs` boş olamaz.

## 7. Ortak veri semantiği

### 7.1 Quantity biçimi

Fiziksel bir değer hiçbir zaman çıplak sayı değildir:

```json
{"state": "KNOWN", "value": 0, "unit": "A"}
```

```json
{"state": "UNKNOWN", "value": null, "unit": "A"}
```

`0`, ölçülmüş/bildirilmiş sayısal bir değerdir; `null` veya `UNKNOWN` değildir. `KNOWN` her zaman sonlu sayı, `UNKNOWN` her zaman `null` gerektirir.

D1 yalnız SI temel gösterimlerini kabul eder:

| Alan sınıfı | Birim |
|---|---|
| Gerilim | `V` |
| Akım | `A` |
| Frekans | `Hz` |
| Zaman/ofset | `s` |

Üretici `mV`, `mA`, `kHz`, `ms` gibi ön ekli değerleri zarfı oluşturmadan önce temel birime dönüştürür. Birimsiz sayı kadar, alan için yanlış birim de doğrulama hatasıdır.

### 7.2 Kanıt sınıfları

Tüm provenance alanları şu kapalı enumu kullanır:

`SOURCE_EXPLICIT`, `DERIVED`, `ENGINEERING_RULE`, `ASSUMPTION`, `UNKNOWN`, `CONFLICT`.

### 7.3 Durumlar

Tüm değerlendirme durumları şu kapalı enumu kullanır:

`PASS`, `FAIL`, `UNKNOWN`, `BLOCKED`, `NOT_RUN`.

`UNKNOWN`, değerlendirmenin girdisinin bilinmediğini; `BLOCKED`, gerekli kanıt/model/erişimin mevcut olmadığını; `NOT_RUN`, yürütmenin yapılmadığını belirtir. Bu üç durumun hiçbiri `PASS` ile birleştirilemez.

## 8. Varlık kuralları

### 8.1 Rail

Nominal gerilim ile minimum/maksimum gerilim limitlerini `V` quantity olarak taşır. Bilinen minimum, bilinen maksimumdan büyük olamaz. Limitin bilinmemesi alanın atılmasıyla değil `UNKNOWN/null` ile gösterilir.

### 8.2 Load

Beslendiği `rail_id`, çalışma akımı ve tepe akımı zorunludur. Akımlar `A` kullanır. Sıfır akım geçerli ve bilinen bir değerdir.

### 8.3 Regulator

Giriş referansı, çıkış rayı, çıkış gerilimi, anma çıkış akımı ve anahtarlama frekansı zorunludur. Birimler sırasıyla `V`, `A`, `Hz` olarak sabittir.

### 8.4 Source

Kaynak sürümü hiçbir zaman örtük değildir. `access_status` şu değerlerden biridir: `AVAILABLE`, `MISSING`, `BLOCKED`, `UNKNOWN`.

`content_hash` her zaman nesnedir:

- erişilebilir kaynak: `{"state":"KNOWN","algorithm":"SHA-256","value":"<64hex>"}`;
- eksik/bilinmeyen kaynak: `{"state":"UNKNOWN","algorithm":"SHA-256","value":null}`.

`AVAILABLE` kaynakta bilinen hash zorunludur. `MISSING` veya `UNKNOWN` kaynakta hash durumu `UNKNOWN` ve değer `null` olmalıdır.

### 8.5 Event

Olay sözleşmesi hem kanıt olaylarını hem güç sıralamasını taşır:

- `event_type`: genel kategori; `SIGNAL_TRANSITION`, `POWER_STATE` ve `FAULT` dahil;
- `event_kind`: `VIN_VALID`, `EN`, `PGOOD`, `RESET`, `POWER_OFF`, `BROWNOUT`, `RAIL_LOSS`, `RESTART` veya `OTHER`;
- `state`: `ASSERTED`, `DEASSERTED`, `ENTERED`, `EXITED`, `ACTIVE`, `INACTIVE` veya `UNKNOWN`;
- `occurred_at`: mutlak RFC 3339 zamanı;
- `time_offset`: senaryo başlangıcına göre `s` quantity; bilinmiyorsa `UNKNOWN/null`.

### 8.6 Finding

Her bulgu en az şu denetim izini taşır:

- `rule_id`, `status`, `reason_code` ve mesaj;
- `source_refs[].source_id` ile tam `source_refs[].version`;
- `input_hash`;
- `tool_version` ve `package_version`;
- değerlendirilen limitler için `limit_context`.

`limit_context.required: true` ise en az bir limit referansı gerekir. D1 limit referans biçimi `rail:<rail_id>.voltage_limits.minimum|maximum` şeklindedir.

## 9. Güvenli sonuç kapıları

Aşağıdaki kurallar normatiftir:

1. Bildirilen istek karması yeniden hesaplanan karma ile uyuşmazsa istek geçersizdir.
2. Hash malzemesi güvenli biçimde kodlanamıyorsa doğrulama `REQUEST_HASH_UNCOMPUTABLE` ile kapanır; hash kontrolü atlanamaz.
3. Yanıt `contract_version` değeri istek sürümüyle aynı olmalıdır.
4. Yanıttaki `input_configuration_sha256`, istek karmasıyla birebir aynı değilse yanıt geçersizdir.
5. Her bulgunun `input_hash` değeri aynı istek karması olmalıdır.
6. Bulgudaki kaynak kimliği istek kaynaklarında çözülmeli ve sürümü birebir eşleşmelidir.
7. Varlıkların kaynak referansları `collections.sources` içinde çözülmelidir; aynı koleksiyonda yinelenen varlık kimliği geçersizdir.
8. Tek başına `ASSUMPTION`, `UNKNOWN` veya `CONFLICT` sınıflı kaynağa dayalı bulgu ya da değerlendirme `PASS` olamaz.
9. `PASS` kaynağı `AVAILABLE` olmalı ve bilinen SHA-256 taşımalıdır; eksik ya da hashlenmemiş kaynaktan `PASS` üretilemez.
10. `PASS` değerlendirmesindeki her model referansı çözülmeli; model erişilebilir, lisans durumu açık ve geçerlilik sınırları dolu olmalıdır.
11. Gerekli limit `MISSING`, `UNKNOWN` veya `CONFLICT` ise bulgu `PASS` olamaz.
12. `summary_status: PASS`, en az bir değerlendirme ve tüm kontrol/senaryo/bulgu durumlarının `PASS` olmasını gerektirir.
13. Yanıt `claim_limits`, istek `claim_limits` kümesini ne genişletebilir ne daraltabilir. Alanın REP-01'deki güvenlik kısıtı ile REP-02'deki iddia kapsamı anlamı uzlaştırılana kadar tam küme eşitliği fail-closed ara kuralıdır.
14. Beklenmeyen istek alanları, yinelenen JSON nesne anahtarları ve çözülemeyen rail/subject/evaluation-input referansları geçersizdir.

Bu kapılardan biri ihlal edildiğinde doğrulayıcı hata listesi üretir; sessiz düzeltme veya otomatik `PASS` yükseltmesi yapmaz.

## 10. Kullanım ve doğrulama

Kurulum yapmadan test çalıştırma:

```powershell
python -m unittest discover -s tests -v
```

Python kullanımı:

```python
from power_rail_validation.contracts import validate_file

issues = validate_file("handoff.json")
if issues:
    for issue in issues:
        print(issue.path, issue.code, issue.message)
```

`validate_file`, bozuk JSON'u `MALFORMED_JSON`; yinelenen nesne anahtarını `DUPLICATE_JSON_KEY`; JSON standardı dışındaki `NaN`, `Infinity` ve `-Infinity` sabitlerini `NON_FINITE_JSON_NUMBER` olarak döndürür. `assert_valid`, sorunları `ContractValidationError` içinde yükseltir.

## 11. Fixture kabul matrisi

| Fixture | Beklenen sonuç | Kanıtlanan kural |
|---|---|---|
| `valid.json` | geçerli | Altı varlık, gerçek istek karması, REP-01 alanları, bilinen sıfır, PASS kapsamı |
| `missing.json` | geçerli kaynak kaydı | Eksik PKG-A sessiz omission değildir; `MISSING` + `UNKNOWN/null` hash |
| `wrong-type.json` | geçersiz | Çıplak/birimsiz fiziksel sayı reddedilir |
| `unknown-limit.json` | geçersiz | Bilinmeyen gerekli limitten PASS üretilemez |
| `conflict.json` | geçersiz | Çelişkili kaynaktan PASS üretilemez |
| `malformed.json` | geçersiz | Sözdizimi bozuk JSON kararlı hata kodu üretir |

## 12. Sürümleme

Alan kaldırma, alan anlamı değiştirme veya enum daraltma ana sürüm değişikliğidir. İsteğe bağlı alan ekleme ve enum genişletme, tüketicilerin bilinmeyen değeri güvenli biçimde reddetme davranışı korunuyorsa küçük sürüm olabilir. Her sözleşme değişikliği şema, alan sözlüğü, fixture, test ve bu belgeyi aynı değişiklikte güncellemelidir.

