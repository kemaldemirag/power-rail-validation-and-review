# REP-02 CLI Hızlı Başlangıç

Bu kılavuz, dondurulmuş D4 `power-rail-validation` 0.5.0 wheel'ini ve D5 geliştirme kaynak ağacı/`0.6.0.dev0` paket adayı arayüzünü çalıştırmak içindir. D4 wheel altı komut taşır; D5 kaynak ağacı ve paket adayı bunlara gerçek proje girdi hazırlığını denetleyen `intake` komutunu ekler. Komutlar bir UTF-8 JSON girdisi alır, alan motoru sonucunu tek bir JSON belgesi olarak standart çıktıya yazar ve sonucun `status` değerini proses çıkış koduna yansıtır.

> **Kapsam:** Bu sürüm sentetik fixture'ları ve çevrimdışı dosya doğrulamasını çalıştırır. Bir `PASS`, fiziksel donanım, canlı REP-01/REP-03 entegrasyonu, saha güvenilirliği veya ürün uygunluğu onayı değildir.

## Gereksinimler

- Python 3.9 veya daha yeni bir sürüm
- Komutları repo kökünden çalıştırma
- Çalışma zamanında harici Python paketi gerekmez

Aşağıdaki örnekler Windows PowerShell 7 sözdizimini kullanır.

## Wheel'den kurulum

Temiz bir sanal ortam oluşturun ve D4 çıktısı olan wheel'i kurun:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --no-deps .\dist\power_rail_validation-0.5.0-py3-none-any.whl
& .\.venv\Scripts\power-rail-validation.exe --version
```

Beklenen sürüm satırı:

```text
power-rail-validation 0.5.0
```

Kurulu paketin taşınabilir modül girişi, konsol komutuyla eşdeğerdir:

```powershell
& .\.venv\Scripts\python.exe -m power_rail_validation --version
```

## Kaynak ağacından çalıştırma

Kurulum yapmadan repo kökünden çalıştırmak için:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m power_rail_validation --help
```

Bu kılavuzdaki fixture örnekleri bu kaynak ağacı biçimiyle test edilmiştir. Wheel kurulumunda aynı komutlarda `python -m power_rail_validation` yerine `power-rail-validation` kullanılabilir.

Kaynak ağacında beklenen geliştirme sürümü:

```text
power-rail-validation 0.6.0.dev0
```

`intake` komutu D4 `0.5.0` wheel'inde yoktur; D5 kaynak ağacından veya doğrulanmış `0.6.0.dev0` paket adayından çalıştırılmalıdır.

## Tam komut sözdizimi

```text
power-rail-validation validate INPUT [--kind {contract,file-api}] [--artifact-root PATH]
power-rail-validation rail-budget INPUT
power-rail-validation intake INPUT --artifact-root PATH
power-rail-validation sequence INPUT
power-rail-validation inject-fault INPUT
power-rail-validation review INPUT [--artifact-root PATH]
power-rail-validation report INPUT [--generated-at RFC3339_TIMESTAMP]
```

`INPUT`, UTF-8 kodlu bir JSON dosya yolu veya standart girdiyi seçen `-` işaretidir. `validate` için varsayılan tür `contract`'tır. `intake` için `--artifact-root` zorunludur; gerçek artifact baytlarının doğrulanacağı kökü açıkça seçer. `--artifact-root`, ayrıca `validate --kind file-api` ve `review` ile geçerlidir. Dosya-API doğrulamasında belirtilmezse girdi dosyasının dizini artifact kökü olur.

## Doğrulanmış fixture örnekleri

Önce kaynak-ağacı girişini etkinleştirin:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

### 1. `validate`

D1 sözleşme fixture'ini doğrulayın:

```powershell
$validateResult = python -m power_rail_validation validate .\tests\fixtures\contracts\valid.json
$validateExit = $LASTEXITCODE
$validateResult
```

Beklenen sonuç `PASS / CONTRACT_VALID`, beklenen çıkış kodu `0`'dır.

Dosya-API varyantı da aynı alt komuttadır:

```powershell
$fileApiResult = python -m power_rail_validation validate .\tests\fixtures\integration\valid.json --kind file-api
$fileApiExit = $LASTEXITCODE
$fileApiResult
```

Bu fixture için artifact kökü, girdi dosyasının dizinine otomatik ayarlanır. Beklenen sonuç `PASS / FILE_API_DESCRIPTOR_VALID`, beklenen çıkış kodu `0`'dır.

### 2. `rail-budget`

Test fixture'i hem `input` hem de test beklentisini içerir; CLI'ya yalnız `input` nesnesini standart girdiden verin:

```powershell
$railInput = (Get-Content -Raw .\tests\fixtures\rail_budget\nominal.json | ConvertFrom-Json).input
$railJson = $railInput | ConvertTo-Json -Depth 100 -Compress
$railResult = $railJson | python -m power_rail_validation rail-budget -
$railExit = $LASTEXITCODE
$railResult
```

Beklenen sonuç `PASS / WITHIN_DERATED_CAPACITY_AND_LIMITS`, beklenen çıkış kodu `0`'dır.

### 3. `intake`

Pozitif test fixture'ı yalnız sentetik yürütme hazırlığını doğrular:

```powershell
$intakeFixtureRoot = (Resolve-Path .\tests\fixtures\project_intake).Path
$intakeResult = python -m power_rail_validation intake .\tests\fixtures\project_intake\ready-static-buck-pin.json --artifact-root $intakeFixtureRoot
$intakeExit = $LASTEXITCODE
$intakeResult
```

Beklenen sonuç `PASS / INTAKE_EXECUTION_READY`, beklenen çıkış kodu `0`'dır. Sonuç `SYNTHETIC_TEST_ONLY`, `EXECUTION_READINESS_ONLY` ve `DOWNSTREAM_ENGINES_NOT_RUN` sınırlarını taşır.

Gerçek REP-01 snapshot'ı için kanonik D5 senaryosu:

```powershell
$rep01Root = (Resolve-Path ..\esp32-s3-24v-interface-board).Path
$actualIntake = python -m power_rail_validation intake .\scenarios\REP02-D5\project-intake.json --artifact-root $rep01Root
$actualIntakeExit = $LASTEXITCODE
$actualIntake
```

Mevcut snapshot için beklenen sonuç `BLOCKED`, beklenen çıkış kodu `3`'tür. Dört destekleyici REP-01 artifact'ı byte olarak doğrulanır; proje konfigürasyonu, güç profili, ray talepleri, exact regülatör seçimi, exact-part datasheet'i ve model bulunmadığı için downstream motorları `NOT_RUN` kalır.

### 4. `sequence`

```powershell
$sequenceInput = (Get-Content -Raw .\tests\fixtures\sequencing\normal.json | ConvertFrom-Json).input
$sequenceJson = $sequenceInput | ConvertTo-Json -Depth 100 -Compress
$sequenceResult = $sequenceJson | python -m power_rail_validation sequence -
$sequenceExit = $LASTEXITCODE
$sequenceResult
```

Beklenen sonuç `PASS / SEQUENCE_COMPLETE`, beklenen çıkış kodu `0`'dır.

### 5. `inject-fault`

Fault runner, senaryoya ek olarak rail-budget ve sequencing baseline girdilerini ister. Fixture bağlarını testlerin kullandığı biçimde birleştirin:

```powershell
$faultCase = Get-Content -Raw .\tests\fixtures\fault_injection\brownout.json | ConvertFrom-Json
$faultRail = (Get-Content -Raw .\tests\fixtures\rail_budget\nominal.json | ConvertFrom-Json).input
$faultSequence = (Get-Content -Raw .\tests\fixtures\sequencing\normal.json | ConvertFrom-Json).input
$faultCase.input | Add-Member -NotePropertyName rail_budget_input -NotePropertyValue $faultRail
$faultCase.input | Add-Member -NotePropertyName sequencing_input -NotePropertyValue $faultSequence
$faultJson = $faultCase.input | ConvertTo-Json -Depth 100 -Compress
$faultResult = $faultJson | python -m power_rail_validation inject-fault -
$faultExit = $LASTEXITCODE
$faultResult
```

Bu fixture kasıtlı olarak teknik bir brownout hatası üretir. Beklenen sonuç `FAIL / BROWNOUT_EFFECT_TRIGGERED`, beklenen çıkış kodu `1`'dir. Buradaki `1`, CLI arızası değil, alan motorunun doğrulanmış `FAIL` sonucudur.

### 6. `review`

Regülatör fixture'indeki kaynak locator'ları repo köküne göre tanımlıdır:

```powershell
$reviewResult = python -m power_rail_validation review .\tests\fixtures\regulator_review\nominal-synthetic.json --artifact-root .
$reviewExit = $LASTEXITCODE
$reviewResult
```

Beklenen sonuç `PASS / REGULATOR_REVIEW_COMPLETE`, beklenen çıkış kodu `0`'dır. Bu sentetik `PASS`, proje veya donanım onayı değildir.

### 7. `report`

`report`, bir bileşen sonucunu ortak rapor modeline normalleştirir. Yukarıda üretilen `$railResult` doğrudan standart girdiye verilebilir:

```powershell
$reportResult = $railResult | python -m power_rail_validation report -
$reportExit = $LASTEXITCODE
$reportResult
```

Beklenen üst seviye durum `PASS`, beklenen çıkış kodu `0`'dır. Varsayılan çıktıya `generated_at` eklenmez. Bir zaman damgası gerekiyorsa onu açık ve tekrarlanabilir biçimde verin:

```powershell
$reportResult = $railResult | python -m power_rail_validation report - --generated-at "2026-10-24T00:00:00Z"
```

## Standart girdi ve çıktı

- Dosyadan girdi: `power-rail-validation sequence .\input.json`
- Standart girdiden girdi: `Get-Content -Raw .\input.json | power-rail-validation sequence -`
- Başarılı motor sonucu: stdout'ta tek JSON belgesi; stderr boş
- Kullanım/girdi hatası: stdout boş; stderr `error:` ile başlar
- Beklenmeyen iç hata: stdout boş; stderr yalnız hata sınıfını içeren temizlenmiş bir mesaj taşır

CLI'nın ayrı bir `--output` seçeneği yoktur. JSON'u dosyaya almak için stdout'u yönlendirin. PowerShell 7'de UTF-8 BOM'suz yazım için:

```powershell
$sequenceResult | Set-Content -Encoding utf8NoBOM .\sequence-result.json
```

Alan sonucu `FAIL`, `UNKNOWN`, `BLOCKED` veya `NOT_RUN` ise komut kasıtlı olarak sıfırdan farklı döner. Otomasyonlarda JSON'u kaydetmeden önce proses kodunu yakalayın:

```powershell
$result = $sequenceJson | python -m power_rail_validation sequence -
$exitCode = $LASTEXITCODE
$result | Set-Content -Encoding utf8NoBOM .\sequence-result.json
exit $exitCode
```

## Çıkış kodları

| Kod | Anlam | stdout / stderr davranışı |
|---:|---|---|
| `0` | `PASS` | Sonuç JSON'u stdout'ta |
| `1` | `FAIL` | Sonuç JSON'u stdout'ta |
| `2` | `UNKNOWN` | Sonuç JSON'u stdout'ta |
| `3` | `BLOCKED` | Sonuç JSON'u stdout'ta |
| `4` | `NOT_RUN` | Sonuç JSON'u stdout'ta |
| `64` | Kullanım veya girdi hatası | stdout boş, ayrıntı stderr'de |
| `70` | Beklenmeyen iç hata | stdout boş, temizlenmiş hata türü stderr'de |

`-h`, `--help` ve `--version` başarılı olduğunda `0` döner.

## Deterministik JSON sözleşmesi

- Girdi UTF-8 ve geçerli JSON olmalıdır. Tekrarlı nesne anahtarları, `NaN`, `Infinity`, sonlu olmayan sayılar, desteklenmeyen sayısal aralık ve bozuk JSON `64` ile reddedilir.
- Alan motoru sonuçlarında nesne anahtarları sıralı, girinti iki boşluk ve son satır sonu vardır; JSON'a sonlu olmayan sayı yazılmaz.
- Aynı girdi, aynı paket sürümü, aynı artifact byte'ları ve aynı çalışma kodlamasıyla byte-eş stdout üretir. Fixture `sequence` örneği iki kez çalıştırılarak aynı SHA-256 ile doğrulanmıştır.
- `report`, kendiliğinden saat okumaz; `generated_at` varsayılan olarak yoktur. `--generated-at` kullanılırsa tekrarlanabilirlik için aynı RFC 3339 değeri verilmelidir.
- Dizi sırası hash açısından anlamlıdır. Semantik olarak benzer görünse bile girdideki dizileri rastgele yeniden sıralamayın.
- `input_hash`, kaynak ve artifact hashleri yalnız tanımlı kanonikleştirme ve byte-bütünlüğü kapsamını kanıtlar; fiziksel veya semantik sistem doğruluğunu tek başına kanıtlamaz.

## İddia ve model sınırları

| Alan | Bu sürümün yaptığı | Yapmadığı / iddia etmediği |
|---|---|---|
| Rail budget | Tek ray için statik cebirsel akım, güç, derating ve marj hesabı | Transient, termal, kararlılık veya fiziksel doğrulama |
| Sequencing | Sentetik olayları ve zaman pencerelerini deterministik FSM ile değerlendirme | Evrensel gerçek donanım sırası ya da exact-part eşik/timing çıkarımı |
| Power-down | Açık `POWER_OFF` olayında senaryoyu sonlandırma | POWER_OFF sonrası ray/sinyal kapanma sırasını varsayma veya çıkarım yapma |
| Fault injection | Brownout, stuck-low, delay, rail-loss ve restart için deterministik sentetik fixture kuralları | Analog transient fiziği, waveform çözümü veya donanım güvenilirlik onayı |
| Regulator review | Exact-part kimliğiyle bağlı kayıt, kaynak, hash, limit ve kanıt kapılarını denetleme | SPICE çalıştırma; transient kaydından loop stability çıkarımı; G1-G6 kapılarını değiştirme |
| File API | Sürüm, descriptor yapısı ve yerel artifact byte/hash bütünlüğünü çevrimdışı doğrulama | Canlı REP-01/REP-03 adaptörü çalıştırma veya semantik mapping onayı |
| Project intake | Tek bir REP-01 rayı için bildirilen kimlik, sürüm, köken, çapraz bağ ve artifact byte hazırlığını denetleme | Artifact içeriğinden elektriksel değer çıkarma; buck/pin/transient/stability veya donanım onayı |
| Reporting | Mevcut bileşen sonucunu ortak bulgu modeline normalleştirme | Eksik kanıtı tamamlama veya alt sonucun statüsünü yükseltme |

Gerçek proje kullanımında exact-part veri sayfaları, sürümlü model artifact'ları, gerçek ölçüm kayıtları ve yetkili entegrasyon girdileri ayrı kaynak kapılarından geçmelidir. Eksik veya belirsiz kanıt, varsayımla `PASS`e yükseltilmez.
