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

D3 kanıtındaki `FAIL`, bilinen sentetik fault kuralının tetiklendiğini veya recovery kuralının sağlanmadığını belirtir. `PASS`, yalnız sürümlü sentetik teknik kural ve recovery değerlendirmesinin geçtiğini gösterir; fixture beklentisiyle eşleşme ayrı bir alandır ve teknik durumu değiştirmez.

## WF-02-05 ve kalan WF-02-06 / D3 kapanış kanıtları

- `REP02-D3/regulator-review-results.json`: nominal, GAP-02, transient/stability ayrımı ve negatif regulator-review kampanyası.
- `REP02-D3/integration-results.json`: REP-01/03, çoklu profil, eksik artifact, uyumsuz sürüm ve bozuk JSON dosya-API sonuçları.
- `REP02-D3/source-model-provenance-register.json`: kullanılan sentetik kaynak/model kayıtları ile eksik proje girdilerinin açık kaydı.
- `REP02-D3/report-model-closeout.json` ve `report-closeout.md`: aynı normalize finding setinin iki görünümü.
- `REP02-D3/test-results-closeout.json`: tam/odaklı test, statik doğrulama ve teslim QA kaydı.
- `REP02-D3/manifest-closeout.json`: D3 kapanış girdileri, kaynakları, modeli, araçları ve çıktıları için byte-exact SHA-256 manifesti.

Bu paket D3 sentetik test/kanıt kapsamını kapatır. GAP-02, GAP-03, G1 ve canlı REP-01/03 adaptörleri açık kaldığından proje/donanım doğrulaması ve canlı entegrasyon kapalıdır.

## D4 yerel release adayı kanıtları

- `REP02-D4/test-results.json`: tam unittest çalışması, Python 3.9 AST uyumluluk kontrolü ve ortam kaydı.
- `REP02-D4/build-results.json`: wheel/sdist üretimi, arşiv envanteri, temiz kurulum ve kurulu CLI smoke sonuçları.
- `REP02-D4/release-manifest.json`: D4 girdileri ile artifact'larının byte-exact SHA-256 envanteri, Git durumu ve D3 manifest referansı.
- `REP02-D4/report.md`: sonuçların, açık kapıların ve release iddia sınırının insan-okunur özeti.

D4 kanıtı, D3 manifestini yeniden üretmez veya geçmiş D3 byte bağını bugünkü kaynak ağacına genişletmez. D3 kaynakları incelenmiş bir commit/tag ile dondurulmadığı için bu provenance sınırlaması açık `BLOCKED / UNCOMMITTED_SOURCE` olarak kalır. Yerel `PASS`, Python 3.12 yazılım release adayına aittir; Python 3.9 CI yürütmesi, canlı adaptörler ve donanım kapıları ayrıca çalıştırılmalıdır.
