# Source

`power_rail_validation` paketi WF-02-01 veri sözleşmesi için başlatılmıştır. `contracts` modülü sürümlü şemaları, D1 doğrulama kurallarını ve deterministik içerik hash'i üretimini içerir.

`rail_budget` modülü WF-02-02 için tek bir rayın sentetik statik bütçesini hesaplar. Açık `Imin/Ityp/Imax`, kapasite, derating, verim ve kaynak kayıtları ister; eksik değeri sayısal varsayımla tamamlamaz.

`sequencing` modülü WF-02-03 için D1 olaylarını zaman ofsetine göre deterministik sıralar; sentetik veya kaynaklı kapalı zaman pencerelerini, normal startup zincirini ve açık power-off senaryosunu değerlendirir. Eksik, belirsiz veya kaynağı doğrulanmamış girdiyi `PASS` sonucuna yükseltmez.

`fault_injection` modülü WF-02-04 için brownout, stuck-low, delay, rail-loss ve restart fixture'larını; baseline causality, effect ve recovery kurallarıyla değerlendirir. Analog transient çözmez ve fault olaylarını sequencing olaylarına eklemez.

`reporting` modülü WF-02-06'nın ilk diliminde modül-yerel sonuçları ortak finding modeline bağlar; aynı modelden JSON ve Markdown üretir ve dosya hashli evidence manifest oluşturur. Canlı REP-01/03 adaptörleri uygulanmamıştır.

Regulator review, kalan entegrasyon ve CLI çalışmaları sonraki dilimlerde ele alınır.
