# Source

`power_rail_validation` paketi WF-02-01 veri sözleşmesi için başlatılmıştır. `contracts` modülü sürümlü şemaları, D1 doğrulama kurallarını ve deterministik içerik hash'i üretimini içerir.

`rail_budget` modülü WF-02-02 için tek bir rayın sentetik statik bütçesini hesaplar. Açık `Imin/Ityp/Imax`, kapasite, derating, verim ve kaynak kayıtları ister; eksik değeri sayısal varsayımla tamamlamaz.

`sequencing` modülü WF-02-03 için D1 olaylarını zaman ofsetine göre deterministik sıralar; sentetik veya kaynaklı kapalı zaman pencerelerini, normal startup zincirini ve açık power-off senaryosunu değerlendirir. Eksik, belirsiz veya kaynağı doğrulanmamış girdiyi `PASS` sonucuna yükseltmez.

Fault injection, regulator review, evidence reporting ve CLI motorları ilgili sonraki çalışma dilimlerinde eklenir.
