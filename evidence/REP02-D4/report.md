# REP-02 D4 yerel sürüm adayı doğrulama kaydı

Oluşturulma: `2026-10-24T01:19:57+03:00`

Yerel D4 doğrulama sonucu: **PASS**

Yayına hazırlık: **BLOCKED**

Aday durumu: `LOCAL_SYNTHETIC_RELEASE_CANDIDATE_VERIFIED`

## Gerçekleştirilen kapılar

- Tam `unittest` keşfi: **PASS** — 346/346 test geçti; hata, başarısızlık ve atlama yok.
- Python 3.9 AST dilbilgisi uyumluluğu: **PASS** — 21 dosya. Bu statik kontrol gerçek bir Python 3.9 runtime koşusu değildir.
- Wheel ve kaynak dağıtımı oluşturma: **PASS**.
- Dağıtım içeriği, yalıtılmış kurulum, console-script ve altı CLI komutunun duman testi: **PASS**.
- `git diff --check`: **PASS**.

## Dağıtım artefaktları

- `dist/power_rail_validation-0.5.0-py3-none-any.whl` — `da721bc2370d3e52f3d403ca858ca318c28ec5066d3fd4ea4ea35eb4c2f61ca0` (117705 bayt)
- `dist/power_rail_validation-0.5.0.tar.gz` — `fa514847b0d80c6446b1eb3b579cac2848aa8eb88d8c7cd1a687e68274e49e9e` (194893 bayt)

## Kaynak ve ortam izi

- Git HEAD: `79ae5ba1a842cf2cc206eed408d71214ff2c43bc`
- Git dalı: `main`
- Çalışma ağacı: `DIRTY` (70 kayıt; D4 kanıt çıktıları sayıma dahil değil)
- Python: `3.12.14` (CPython)
- Paket sürümü: `0.5.0`
- D3 manifest dosya SHA-256: `2f7946d75874a501b8dfb973e3dbf7ff99cc24b9609474cc856add674fbfc06a`
- D3 iç manifest hash doğrulaması: `PASS`
- D3 kaynak bağı: **BLOCKED / UNCOMMITTED_SOURCE**

D3 kanıtı yeniden çalıştırılmamış ve yeniden yazılmamıştır. Bu kayıt yalnızca mevcut D3 manifestinin kesin bayt hashini ve manifest iç hashini doğrular. D3 tabanı temiz bir commit'e bağlı değilse bu durum `UNCOMMITTED_SOURCE` olarak korunur.

## Yayına hazırlık engelleri

- `D3_BASELINE_UNCOMMITTED_SOURCE`
- `PYTHON_3_9_LOCAL_RUNTIME_NOT_EXECUTED`
- `LIVE_REP01_REP03_INTEGRATION_NOT_RUN`
- `PHYSICAL_HARDWARE_VALIDATION_NOT_RUN`

## İddia sınırları

- `SYNTHETIC_FIXTURES_ONLY`
- `SYNTHETIC_PASS_IS_NOT_PROJECT_OR_HARDWARE_PASS`
- `NO_LIVE_REP01_OR_REP03_INTEGRATION_CLAIM`
- `NO_ANALOG_TRANSIENT_OR_WAVEFORM_EXECUTION`
- `NO_PHYSICAL_HARDWARE_OR_LAB_VALIDATION`
- `NO_EMC_ISO_AEC_OR_FIELD_COMPLIANCE_CLAIM`
- `PYTHON_3_9_NOT_EXECUTED_IN_THIS_LOCAL_RUN_UNLESS_RECORDED_AS_CURRENT_RUNTIME`
- `NO_COMMIT_TAG_PUSH_OR_PACKAGE_PUBLISH`

Bu PASS sonucu sentetik/çevrimdışı yazılım kapsamına aittir; canlı REP-01/REP-03, laboratuvar, donanım veya uygunluk sonucu değildir. Commit, etiket, push ve paket yayını yapılmamıştır.

## Kanıt bütünlüğü

- `test-results.json`: `bb1d2cd91b235a95b7734ea5b789984eae3a3a86c7029e2c27dbf0071f6938e7`
- `build-results.json`: `9ec17e208a118960f29bb8fb9cb5595abad7a7307971eca5aa9e98da6df77298`
- `release-manifest.json`, kendi dosya hashini envantere almaz. `manifest_hash`, belgenin `manifest_hash` alanı hariç tüm alanlarının sıralı, boşluksuz UTF-8 JSON gösterimi üzerinden hesaplanır.
