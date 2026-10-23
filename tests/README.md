# Tests

Bu dizin WF-02-01 sözleşme, WF-02-02 rail-budget, WF-02-03 sequencing, WF-02-04 fault-injection, WF-02-05 regulator-review, WF-02-06 raporlama/dosya-API ve D4 CLI/public rapor şeması testlerini; ayrıca yalnız sentetik fixture'ları içerir. Testler Python standart kütüphanesindeki `unittest` ile çalışır.

Bir testin `PASS` olması, yalnız makine-okunur sözleşmenin, statik hesabın, sentetik sequencing/fault/regülatör kuralının, dosya bütünlüğünün veya rapor eşliğinin beklenen davranışını doğrular. Fiziksel güç rayı, gerçek PGOOD/EN/RESET eşiği, gerçek transient davranış, regülatör kararlılığı veya donanım uygunluğu hakkında kanıt oluşturmaz. Canlı REP-01/03 adaptörleri bu test setinde çalıştırılmaz.

`test_cli.py`, dosya/stdin girdisini, deterministik stdout'u, durum çıkış kodlarını, temizlenmiş hata kanalını ve modül entry point'ini doğrular. `test_report_schema.py`, paketlenen public rapor şemasının runtime model şekliyle eşleştiğini ve D1 finding şemasından ayrı kaldığını denetler. Kurulu wheel ve console-script davranışı `scripts/verify_release.py` ile temiz sanal ortamda ayrıca smoke edilir.
