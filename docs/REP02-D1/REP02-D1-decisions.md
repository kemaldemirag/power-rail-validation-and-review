# REP-02 D1 Karar Kaydı

Durum: WF-02-01 başlangıç kararları  
Tarih: 2026-10-17

## D1-DEC-001 — Altı çekirdek varlık ve Draft 2020-12

Karar: `rail`, `load`, `regulator`, `source`, `event` ve `finding` için ayrı, kendi içinde çözülebilen JSON Schema Draft 2020-12 dosyaları kullanılacaktır.

Gerekçe: Varlık bazlı sürümleme ve inceleme kolaylığı sağlar; harici `$ref` çözümleyici zorunluluğu oluşturmaz.

Sonuç: Ortak quantity/provenance tanımları bazı şemalarda tekrar eder. Tekrarlı tanımların testlerle uyumu korunacaktır.

## D1-DEC-002 — Unknown, null ve sıfır ayrımı

Karar: Fiziksel değer `quantity = {state, value, unit}` biçimindedir. `KNOWN` sonlu sayı, `UNKNOWN` yalnız `null` taşır. Sayısal `0` geçerli bilinen değerdir.

Gerekçe: Python/JSON truthiness kullanımının sıfırı yanlışlıkla eksik kabul etmesini ve bilinmeyen değerlerin sahte sıfıra dönüşmesini engeller.

Sonuç: Alanı atmak veya çıplak sayı kullanmak geçersizdir.

## D1-DEC-003 — SI temel birimleri

Karar: D1 zarfında gerilim `V`, akım `A`, frekans `Hz`, zaman `s` kullanır. `mV`, `mA`, `kHz`, `ms` gibi ön ekli gösterimler kabul edilmez.

Gerekçe: Birim dönüştürme belirsizliğini tüketiciden kaldırır ve alan bazlı birim kontrolünü kesinleştirir.

Sonuç: Üretici, değerleri zarfı oluşturmadan önce SI temel birimine dönüştürür. Yeni fiziksel büyüklük veya birim ancak sözleşme ve test değişikliğiyle eklenir.

## D1-DEC-004 — Dar deterministik JSON; RFC 8785 iddiası yok

Karar: Anahtarları sıralanmış, minify edilmiş, Unicode'u koruyan UTF-8 JSON baytları SHA-256 ile karmalanır. Sonlu olmayan sayılar reddedilir. RFC 8785/JCS uyumu iddia edilmez.

Gerekçe: Standart kitaplıkla deterministik ve test edilebilir bir başlangıç sözleşmesi gerekir; farklı sayı kodlayıcıları arasında JCS düzeyinde garanti verilmemelidir.

Sonuç: REP-01 aynı dar algoritmayı uygulamalı veya ürettiği bayt dizisini uyumluluk testiyle doğrulamalıdır. Dosya ayrıştırıcı `NaN`, `Infinity` ve `-Infinity` sabitlerini reddeder; hash kodlama hatası kontrolü atlamak yerine doğrulamayı kapatır.

## D1-DEC-005 — Hash malzemesi

Karar: `contract_version`, `configuration_id`, üretici repo/ürün sözleşmesi/kural kayıt sürümleri, `collections` ve `claim_limits` karmaya dahildir. `handoff_id` ve `configuration_sha256` hariçtir.

Gerekçe: Teknik anlamı ve izin verilen iddia kapsamını etkileyen her unsur sonucu bağlamalıdır. Taşıma kimliği aynı teknik girdiyi gereksiz yere değiştirmemelidir.

Sonuç: Anahtar sırası karmayı değiştirmez; herhangi bir teknik değer, ilgili sürüm veya iddia kapsamı değişikliği karmayı değiştirir.

## D1-DEC-006 — REP-01 ile ortak alan adı uyumu

Karar: Üreticide `product_contract_version` ve `rule_registry_version`; yanıtta `contract_version`, `scenario_results` ve dizi tipinde `tool_versions` doğrudan kullanılır. Basitleştirilmiş adaptör adları kullanılmaz.

Gerekçe: Repo sınırında ikinci bir alan eşleme katmanı ve uyumsuz iki D1 lehçesi oluşmasını önler.

Sonuç: REP-02 başka repodan runtime import yapmaz. REP-01 taslağındaki alan adları korunur; ancak REP-02'nin altı tipli `collections` zarfının REP-01'deki öneri koleksiyonlarına eşlenmesi canlı entegrasyondan önce ayrıca onaylanacaktır.

## D1-DEC-007 — Kaynak yokluğu bir kayıttır

Karar: `source.access_status` zorunludur. `AVAILABLE` kaynak bilinen SHA-256 taşır; `MISSING` ve `UNKNOWN` kaynak `UNKNOWN/null` hash taşır.

Gerekçe: PKG-A/PKG-B gibi beklenen fakat eksik paketlerin koleksiyondan sessizce çıkarılması, kanıtın hiç beklenmediği izlenimini verir.

Sonuç: Yokluk denetlenebilir; değerlendirme bunu `BLOCKED` veya `UNKNOWN` sonucuna taşıyabilir.

## D1-DEC-008 — Sıralama olayları birinci sınıf veridir

Karar: `event` içinde genel `event_type`, güç-anlamlı `event_kind`, açık `state`, mutlak `occurred_at` ve birimli/null destekli `time_offset` zorunludur.

Gerekçe: VIN, EN, PGOOD, RESET, brownout, rail loss ve restart sırası yalnız serbest metin veya ölçüm etiketiyle güvenilir biçimde doğrulanamaz.

Sonuç: Bilinmeyen ofset `UNKNOWN/null` olarak taşınır; sıfır ofset gerçek başlangıç anıdır.

## D1-DEC-009 — PASS kapalı ve kanıt bağlıdır

Karar: İstek/yanıt/bulgu karmaları eşleşmeden; kaynak kimliği ve sürümü çözülmeden; gerekli limit bilinmeden; tüm kapsam içi sonuçlar PASS olmadan özet PASS üretilemez.

Gerekçe: Eksik limit veya kanıtın olumlu sonuç gibi yorumlanması güvenlik açısından kabul edilemez.

Sonuç: Tek başına `ASSUMPTION`, `UNKNOWN` veya `CONFLICT` kaynağı PASS'i destekleyemez; `BLOCKED` ve `NOT_RUN` da özet PASS'i engeller. `claim_limits` semantiği REP-01 ile uzlaştırılana kadar yanıt istekteki kümeyi tam olarak yankılar.

## D1-DEC-010 — Bulgu denetim izi

Karar: Her bulgu `rule_id`, `status`, `reason_code`, sürümlü kaynak referansları, `input_hash`, `tool_version`, `package_version` ve `limit_context` taşır.

Gerekçe: Aynı sonucun hangi girdi, kural, araç ve kaynak sürümüyle üretildiği sonradan yeniden kurulabilmelidir.

Sonuç: Kaynak sürümü veya bulgu girdi karması uyuşmazlığı doğrulama hatasıdır.

## D1-DEC-011 — Bağımlılıksız güvenlik doğrulayıcısı

Karar: Kritik D1 kuralları Python standart kitaplığıyla çalışan `validation.py` içinde uygulanır. Bu kod genel amaçlı bir JSON Schema motoru değildir.

Gerekçe: Başlangıç paketinin kurulum yapmadan test edilebilmesi ve temel güvenlik kapılarının harici paket durumundan bağımsız olması gerekir.

Sonuç: Tam JSON Schema uyumluluk testi gelecekte ayrı bir geliştirme bağımlılığıyla eklenebilir; mevcut şemalar yine normatif değişim tanımıdır.

## D1-DEC-012 — REP-01 kontrol ve senaryo kayıtları

Karar: Her `checks`/`scenario_results` kaydı `id`, `status`, `reason_code`, `reason`, `inputs_used`, `metrics`, `evidence_refs`, `model_refs`, `limitations`, `next_action` alanlarını taşır. PASS kayıtları girdi ve kanıt referansı içermelidir.

Gerekçe: Yalnız durum ve kısa neden, kararın kapsamını ve yeniden üretilebilirliğini kanıtlamaz.

Sonuç: Metrikler birimli quantity kullanır; serbest çıplak sayılar kabul edilmez.

## D1-DEC-013 — Fail-closed JSON ve hash ayrıştırması

Karar: `NaN`, `Infinity`, `-Infinity`, yinelenen JSON nesne anahtarı, beklenmeyen istek alanı veya kodlanamayan hash malzemesi doğrulamayı hata ile kapatır.

Gerekçe: Python JSON ayrıştırıcısının genişletmeleri ve hash kapsamı dışındaki alanlar sessiz kabul edilirse eski bir karma üzerinden false PASS üretilebilir.

Sonuç: Dosya düzeyinde kararlı hata kodları, in-memory doğrulamada `REQUEST_HASH_UNCOMPUTABLE` ve beklenmeyen alanlar için `UNEXPECTED_FIELD` üretilir.

## D1-DEC-014 — Referans bütünlüğü

Karar: Load ve regulator rail referansları, event/finding subject referansları ve değerlendirme `inputs_used` kimlikleri istek koleksiyonlarında çözülmelidir.

Gerekçe: Yapısal olarak geçerli fakat var olmayan girdiye bağlanan bir PASS yeniden üretilemez ve denetlenemez.

Sonuç: Çözülemeyen referanslar PASS'ten bağımsız olarak doğrulama hatasıdır.

## Açık kararlar

1. İstek/yanıt zarfı için altı varlık şemasından ayrı bir Draft 2020-12 şeması eklenip eklenmeyeceği.
2. D1 limit referans dilinin rail gerilim limitleri dışındaki akım, güç, termal ve transient limitlere nasıl genişletileceği.
3. RFC 8785/JCS veya başka diller için resmi kanonikleştirme uyumluluk vektörlerinin sonraki sürümde zorunlu olup olmayacağı.
4. Model `validity_bounds` nesnesinin sıcaklık, VIN, yük ve frekans eksenleri için kapalı alt şeması.
5. REP-01 taslağındaki `power_input_profile`, `rail_demands`, `power_control_signals`, `temporal_constraints` ve `interface_power_behaviors` alanlarının REP-02 `collections` zarfına kayıpsız eşleme kuralı.
6. REP-01'deki `SECONDARY_SOURCE`, `HANDOFF_ONLY` ve `INTERNAL_RULE` değerlerinin REP-02 provenance sınıflarına eşlenmesi.
7. Anlamsal olarak sırasız dizilerin hash öncesi normalizasyon kuralı.
8. `claim_limits` alanının güvenlik kısıtı ve iddia kapsamı anlamlarının ortak tanımı.

