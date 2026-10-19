# İnceleme Raporları

Her bulgu; kimlik, önem, kanıt, hipotez, düzeltici eylem, doğrulama durumu ve kaynak sınıfı taşıyacaktır. Müşteri varlıkları yerine yalnız izinli veya sentetik örnekler kullanılacaktır.

## WF-02-01 / D1 kanıtları

- `REP02-D1/test-results.json`: D1 veri sözleşmesi paketinin otomatik test, derleme ve statik veri ayrıştırma sonuçları.

Bu kanıt yalnız sözleşme kodu ve sentetik fixture kapsamındadır. Şematik/PCB, üretici modeli, fiziksel ölçüm, SPICE, EMC, ISO/AEC veya saha doğrulaması anlamına gelmez.

## WF-02-02 / D2 rail-budget kanıtları

- `REP02-D2/test-results.json`: Tam test komutu, ortam, sayımlar ve doğrulama kontrolleri.
- `REP02-D2/nominal-result.json`: Elle hesaplanmış sentetik nominal fixture'ın gerçek motor çıktısı.

Bu çıktılardaki `PASS`, yalnız statik ve sentetik cebirsel bütçe hesabını ifade eder. Termal, transient, kararlılık, fiziksel ölçüm veya donanım kapısı kanıtı değildir.

## WF-02-03 / D2 sequencing kanıtları

- `REP02-D2/sequencing-test-results.json`: Tam test komutu, ortam, sayımlar, statik kontroller ve bağımsız inceleme özeti.
- `REP02-D2/sequencing-normal-result.json`: Normal sentetik startup fixture'ının gerçek, deterministik motor çıktısı.

Sequencing kanıtındaki `PASS`, yalnız hash bağlı sentetik mühendislik kuralına uyumu ifade eder. GAP-03 açık olduğu için üretici zaman gereksinimi, gerçek PGOOD/EN/RESET davranışı veya donanım kapısı sonucu değildir.

## WF-02-04 ve ilk WF-02-06 / D3 kanıtları

- `REP02-D3/fault-campaign-results.json`: Zorunlu fault ailesi ve recovery kombinasyonlarının gerçek runner çıktıları.
- `REP02-D3/report-model.json`: Aynı teknik finding setinin normalize JSON raporu.
- `REP02-D3/report.md`: `report-model.json` ile aynı finding setinden üretilen Markdown görünümü.
- `REP02-D3/manifest.json`: Girdi, kaynak, model, araç ve çıktı dosyalarının byte-exact SHA-256 manifesti.
- `REP02-D3/test-results.json`: Test, statik doğrulama, replay ve bağımsız QA kanıtı.

D3 kanıtındaki `FAIL`, bilinen sentetik fault kuralının tetiklendiğini veya recovery kuralının sağlanmadığını belirtir. `PASS`, yalnız sürümlü sentetik teknik kural ve recovery değerlendirmesinin geçtiğini gösterir; fixture beklentisiyle eşleşme ayrı bir alandır ve teknik durumu değiştirmez. Bu dilim D3 ya da canlı entegrasyon kapanışı değildir.
