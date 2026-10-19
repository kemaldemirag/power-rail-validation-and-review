# Tests

Bu dizin WF-02-01 sözleşme, WF-02-02 rail-budget, WF-02-03 sequencing, WF-02-04 fault-injection ve ilk WF-02-06 raporlama testlerini; ayrıca yalnız sentetik fixture'ları içerir. Testler Python standart kütüphanesindeki `unittest` ile çalışır.

Bir testin `PASS` olması, yalnız makine-okunur sözleşmenin, statik hesabın, sentetik sequencing/fault kuralının veya rapor eşliğinin beklenen davranışını doğrular. Fiziksel güç rayı, gerçek PGOOD/EN/RESET eşiği, transient davranış, regülatör kararlılığı veya donanım uygunluğu hakkında kanıt oluşturmaz.
