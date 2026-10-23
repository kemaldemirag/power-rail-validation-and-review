# Source

`power_rail_validation` paketi WF-02-01 veri sözleşmesi için başlatılmıştır. `contracts` modülü sürümlü şemaları, D1 doğrulama kurallarını ve deterministik içerik hash'i üretimini içerir.

`rail_budget` modülü WF-02-02 için tek bir rayın sentetik statik bütçesini hesaplar. Açık `Imin/Ityp/Imax`, kapasite, derating, verim ve kaynak kayıtları ister; eksik değeri sayısal varsayımla tamamlamaz.

`sequencing` modülü WF-02-03 için D1 olaylarını zaman ofsetine göre deterministik sıralar; sentetik veya kaynaklı kapalı zaman pencerelerini, normal startup zincirini ve açık power-off senaryosunu değerlendirir. Eksik, belirsiz veya kaynağı doğrulanmamış girdiyi `PASS` sonucuna yükseltmez.

`fault_injection` modülü WF-02-04 için brownout, stuck-low, delay, rail-loss ve restart fixture'larını; baseline causality, effect ve recovery kurallarıyla değerlendirir. Analog transient çözmez ve fault olaylarını sequencing olaylarına eklemez.

`regulator_review` modülü WF-02-05 için VIN, IOUT, sıcaklık ve parça-bağımlı derating kurallarını; absolute-maximum ve recommended-operating sınırlarını ayrı kimliklerle değerlendirir. Exact-part limitlerini ve bölüm göstergesini, rail-budget zarfını, model çalışma sınırlarını ve analiz türü/araç/durum/kayıt-revizyonu alanlarını byte-doğrulanmış artifact içeriğinden ayrıştırılan semantik kayıtlarla bağlar; hareketli sürüm adlarını reddeder, model kimlik başlığı ile dengeli exact-part `.SUBCKT/.ENDS` yapısını denetler ve transient ile loop-stability kanıtlarını ayrı fail-closed kapılarda tutar. Sentetik `PASS`, proje veya donanım onayı değildir.

`integration` modülü WF-02-06 için REP-01/REP-03 dosya descriptor'larını sürümlü, kapalı ve byte-exact hash politikasıyla çevrimdışı doğrular. Çoklu configuration/profile, eksik artifact, uyumsuz sürüm ve bozuk JSON sonuçları açık durumlarla kaydedilir. Bu modül canlı adaptör çalıştırmaz.

`reporting` modülü modül-yerel sonuçları ortak finding modeline bağlar; aynı modelden JSON ve Markdown üretir ve dosya hashli evidence manifest oluşturur. Canlı REP-01/03 adaptörleri uygulanmamış ve `NOT_RUN/BLOCKED` durumunda bırakılmıştır.

Paket kökündeki CLI, bu motorları değiştirmeden `validate`, `rail-budget`, `sequence`, `inject-fault`, `review` ve `report` alt komutlarıyla sunar. `contracts/schemas/report-model.schema.json`, ortak rapor modelinin ayrı public sözleşmesidir; D1 `finding` entity şemasına zorla dönüştürülmez. D4 wheel/sdist, clean-install, package-data ve release smoke işleri ayrı scriptlerle doğrulanır.
