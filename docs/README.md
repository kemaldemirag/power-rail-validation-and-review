# Documentation

`REP02-D1` dizini WF-02-01 ürün sözleşmesini, alınan kararları, açık kararları ve donanım iddiası sınırlarını kaydeder.

`REP02-D2` dizini WF-02-02 statik rail-budget ile WF-02-03 sequencing sözleşmelerini, sayısal/zamansal politikaları, durum kararlarını, doğrulama sonuçlarını ve sonraki entegrasyon kapılarını kaydeder.

`REP02-D3` dizini WF-02-04 fault-injection sözleşmesini, WF-02-05 regulator review uygulamasını ve WF-02-06 çevrimdışı dosya-API/evidence entegrasyonunu; recovery, causality, kaynak/model provenance, ortak finding görünümü, evidence manifest ve açık canlı entegrasyon kapılarıyla birlikte kaydeder.

`REP02-D5` dizini gerçek REP-01 snapshot'ı için yedinci CLI yolu olan `intake` hazırlık kapısını, dar `PASS` anlamını, byte-bağlı kaynak envanterini, bugünkü zorunlu artifact gap'lerini ve tek exact buck/tek ray dikey dilimine giden kritik yolu kaydeder. Bu kapı yalnız yürütme hazırlığını değerlendirir; elektriksel veya donanım uygunluğu iddiası üretmez.

`REP02-D6` dizini referans-tasarım doğrulamasının yönetişim, aday yeterlilik ve sözleşme v2 tasarım-sınırı kayıtlarını taşır. Oturum 1'de `TI-TLVM13660EVM` / `TLVM13660RDLR`, `STATIC + BUCK + PIN` ilk dilimi için teknik olarak yeterli bulunmuş ve `QUALIFIED_FOR_LOCK` durumunda önerilmiştir; portföy onayı `PENDING`dir. Exact artifact hashleri, sözleşme v2 ve reference-intake tamamlanana kadar bu kayıt D6 teknik `PASS` sonucu değildir.

`QUICKSTART.md`, D4 yerel release adayının kurulumunu, altı CLI komutunu, proses çıkış kodlarını, deterministik çıktı davranışını ve sentetik/offline iddia sınırlarını doğrulanmış PowerShell örnekleriyle açıklar.

D1 sözleşmeleri ve veri modeli, sentetik WF-02-02, WF-02-03, WF-02-04 ve WF-02-05 çekirdeklerinin girdilerini sınırlar. D3 sentetik test/kanıt kapsamı ve D4 yerel ürünleştirme kapsamı kapanmıştır; canlı REP-01/03 eşlemesini, fiziksel doğrulamayı veya G1-G6 donanım kapılarını onaylamaz.
