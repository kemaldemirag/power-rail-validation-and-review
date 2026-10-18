# Tests

Bu dizin WF-02-01 sözleşme, WF-02-02 rail-budget ve WF-02-03 sequencing testlerini; ayrıca yalnız sentetik fixture'ları içerir. Testler Python standart kütüphanesindeki `unittest` ile çalışır.

Bir testin `PASS` olması, yalnız makine-okunur veri sözleşmesinin, statik hesap motorunun veya sentetik sequencing kuralının beklenen davranışını doğrular. Fiziksel güç rayı, gerçek PGOOD/EN/RESET eşiği, regülatör kararlılığı veya donanım uygunluğu hakkında kanıt oluşturmaz.
