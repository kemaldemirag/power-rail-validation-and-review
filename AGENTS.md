# REP-02 geliştirme talimatları

Bu dosya depo kökünün tamamına uygulanır. Amaç, D1–D5 tarihsel kanıtını korurken D6 referans-tasarım dilimini geriye uyumlu, fail-closed ve yeniden üretilebilir biçimde geliştirmektir.

## Geçerli durum ve iddia sınırı

- Kaynak ağacı sürümü `0.6.0.dev0`, yerel D4 dağıtım artifact sürümü `0.5.0`'dır.
- D1–D4 sentetik/offline çekirdek ve D5 REP-01 project-intake kapısı uygulanmıştır.
- Mevcut REP-01 dikey dilimi `BLOCKED` kalır. Referans-tasarım sonucu REP-01 veya fiziksel donanım doğrulaması sayılmaz.
- `PASS`, yalnız çalıştırılan sözleşme ve kanıt kapsamı için kullanılabilir. Eksik veri `UNKNOWN`, eksik zorunlu artifact/karar `BLOCKED`, hiç çalıştırılmayan analiz `NOT_RUN` kalır.
- 26.10.2026 itibarıyla `66f20739feae5689cbc4620fb38ee7817796c2e9` commit'i için GitHub Actions Python 3.9 ve 3.12 matrisi PASS'tir. D6 değişiklikleri yeni bir CI koşusu gerektirir.

## Değişmez tarihsel çıktılar

Açık kullanıcı talimatı ve yeni bir milestone finalizer'ı olmadan aşağıdaki dosyaları silme, yeniden üretme veya üzerine yazma:

- `dist/power_rail_validation-0.5.0-py3-none-any.whl`
  - SHA-256: `27ba10f6e14345c39f70241e56abd67469eb7e8130dc6a0abfdb37ca1a8c5027`
- `dist/power_rail_validation-0.5.0.tar.gz`
  - SHA-256: `8f0e7da652ffba7307a122442a4d314e686696f1969392b341a9de49701e27a8`
- `evidence/REP02-D4/**`
  - `release-manifest.json` SHA-256: `5fcbf70de457386f220ca22e86f1168350bfb37219bb1a799bb51ec6f55b4176`
  - manifest iç kimliği: `a0c758fcc74089a3f474559d7564775c6228d122c4cf40dad48411b474be69c9`
- `evidence/REP02-D5/**`
  - `checkpoint-manifest.json` SHA-256: `196a08590377a664a5062a91b916642d74c4a008028514e5becf0755d65ba700`
  - manifest iç kimliği: `6dafc137ee4ee182c01e63f009106b4c799e86bfdfe5ba2acab74bdb18005c3d`
- `scenarios/REP02-D5/project-intake.json`

D4 ve D5 manifestleri kapanış anlarının tarihsel snapshot'larıdır. Sonraki kaynak değişiklikleri bu dosyalara geriye dönük uygulanmaz. Yeni sözleşmeler `src/`, test fixture'ları `tests/fixtures/`, kanonik çalışma senaryoları `scenarios/REP02-D6/` altında tutulur; yalnız yeni kanonik sonuç, rapor ve manifestler `evidence/REP02-D6/` altında üretilir.

`scripts/build_release.py` ve `scripts/verify_release.py` sıradan yerel geliştirme sırasında mevcut `dist/` dizinine karşı çalıştırılmamalıdır. Release doğrulamasını geçici/izole checkout veya D6 için açıkça tanımlanmış yeni çıktı konumunda çalıştır.

`scripts/finalize_d4_release.py` ve `scripts/finalize_d5_intake.py` yeniden çalıştırılmamalıdır. D6 kapanışı ayrı bir finalizer ve ayrı evidence dizini kullanmalıdır.

## D6 kapsamı

İlk D6 referans dilimi varsayılan olarak yalnız `STATIC + BUCK + PIN` kapsamındadır:

- tek yayımlanmış referans tasarım;
- tek exact üretici ve MPN;
- tek ray;
- revizyonu ve hash'i sabit datasheet/şema/BOM girdileri;
- rail-budget ve doğrudan karşılaştırılabilen statik/pin kontrolleri.

Exact parça ile eşleşen, lisansı doğrulanmış model ve ölçüm/simülasyon kaydı yoksa transient ve loop-stability sonuçları `NOT_RUN` kalır. Portföy kararı, varsayım veya genel uygulama notu bu sonuçları `PASS` yapamaz.

## Sözleşme ve kanıt kuralları

- `1.0.0` şemalarını yerinde genişletme. V2 şemalarını yan yana ekle ve sürüm dispatcher'ı kullan.
- Değerin nasıl üretildiğini belirten `provenance_type` ile hangi bağlamda kullanıldığını belirten `evidence_context` kavramlarını ayır.
- `REFERENCE_DESIGN`, yalnız doğrulanmış `reference_id`, scope ve lock bütünlüğüyle teknik kanıta katkı verebilir.
- `PORTFOLIO_DECISION` teknik evidence context değildir; ayrı `approval_record` olarak yalnız kapsam/lisans/ürün/yayın kararı taşır ve tek başına elektriksel veya sayısal `PASS` kanıtı değildir.
- PDF locator'ı belge revizyonu + sayfa/bölüm; Git locator'ı commit + path + satır aralığı/blob; model/veri locator'ı artifact path + içerik hash'i taşımalıdır.
- GAP kapanışları subject/reference kapsamlı olmalıdır. Bir referans için kapanan GAP-02/03, REP-01 GAP-02/03 kaydını değiştirmez.
- Kanonik JSON sabit sıralı olmalı; mutlak yollar ve değişken zaman damgaları byte-hash kapsamına girmemelidir.
- Mevcut motor mantığını yalnız yeniden üretilmiş ve testle kanıtlanmış bir hata varsa değiştir.

## Harici referans politikası

- Aday, `docs/REP02-D6/REP02-D6-reference-candidate-qualification.md` kapısını geçmeden extractor, normalize fixture veya motor adaptörü yazma.
- Ham üçüncü taraf PDF/CAD/model dosyalarını varsayılan olarak repoya ekleme.
- Kaynağı exact commit veya belge revizyonu, SHA-256, lisans/kullanım kaydı ve çözümlenebilir locator ile sabitle.
- Yeniden dağıtım izni yoksa kullanıcı-sağlanan dosya + hash doğrulama veya izinli özel cache akışı kullan.
- Normalize edilmiş çıkarımın hangi kaynak alanlarından üretildiğini kaydet; türetilmiş değerleri `DERIVED` olarak koru.
- Model lisansı datasheet veya tasarım paketinin lisansından ayrı doğrulanır.

## Standart doğrulama

Windows PowerShell için güvenilir tam test komutu:

```powershell
$repoRoot = (Resolve-Path .).Path
$testTemp = Join-Path $repoRoot "out\test-temp"
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:TEMP = $testTemp
$env:TMP = $testTemp
$env:TMPDIR = $testTemp
python -m unittest discover -s tests -v
git diff --check
```

POSIX/CI karşılığı:

```sh
mkdir -p out/test-temp
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src TMPDIR="$PWD/out/test-temp" \
  python -m unittest discover -s tests -v
git diff --check
```

Değişiklik kapsamına göre önce odaklı testleri çalıştır; teslimden önce tam paketi çalıştır. Test sayısı artabilir, ancak mevcut 404 testin hiçbirini sessizce dışlama veya gevşetme.

## Git ve release yetkisi

- Kullanıcı açıkça istemedikçe commit, push, tag veya GitHub release oluşturma.
- Repo lisansını Codex seçmez; lisans kararı kullanıcıya aittir ve yalnız public release kapısını bloke eder.
- Mevcut `0.6.0.dev0` HEAD'ini `v0.5.0` olarak etiketleme. Tarihsel D4 tag hedefi release öncesi kanıtla ayrıca uzlaştırılmalıdır.
- D6 kapanışı; yeni commit için uzak CI PASS, yeni evidence manifesti ve kullanıcı lisans/tag/release kararı olmadan yayınlanmış sayılmaz.

## Tamamlanma ölçütü

Bir D6 değişikliği ancak aşağıdakilerin tümü sağlandığında tamamlanmış sayılır:

1. V1 fixture'ları ve davranışları değişmeden PASS.
2. V2 pozitif ve negatif sözleşme testleri PASS.
3. Portföy kararının teknik PASS üretemediğini gösteren negatif test PASS.
4. İki temiz çalıştırmada kanonik çıktılar byte-identical.
5. D4/D5 hash ankrajları değişmemiş.
6. Yeni kanıt yalnız `evidence/REP02-D6/` altında.
7. Tam regresyon, `git diff --check` ve yeni commit için GitHub CI PASS.
