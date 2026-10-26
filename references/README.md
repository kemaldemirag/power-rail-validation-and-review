# Reference source policy

Bu dizin REP-02 D6 kapsamında kullanılan dış referansların kimlik, lock ve deterministik çıkarım kayıtları içindir.

## Varsayılan politika

- Ham üçüncü taraf PDF, ZIP, PCB/CAD, model veya binary dosyalarını Git'e ekleme.
- Her kaynağı canonical URL, document id/revision veya Git commit, SHA-256, retrieval kaydı ve kullanım/lisans kaydıyla sabitle.
- PDF locator'larında sayfa + bölüm/tablo/şekil; Git locator'larında commit + path + satır/blob; model locator'larında artifact path + hash kullan.
- Normalize veri yalnız izinli olgusal/sayısal çıkarımları içermeli ve exact kaynağa çözümlenebilir locator taşımalıdır.
- Datasheet/tasarım paketi lisansı ile simülasyon modeli lisansını ayrı değerlendir.
- Kullanıcı-sağlanan dosya, lock SHA-256 değeriyle eşleşmedikçe işlenmemelidir.

## Planlanan yapı

```text
references/
  README.md
  <reference-id>/
    reference.lock.json
    extract.py
    normalized/
```

`raw/`, `downloads/`, `cache/`, `vendor/` ve `third_party/` içerikleri yerel/özel kalır. Bu dizinler yeniden üretim sırasında kullanılabilir ancak kanıt manifestine dosya yolu yerine yalnız kontrollü kimlik ve hash bilgisi girer.

## Lock için önerilen ilk teknik aday

İlk aday `TI-TLVM13660EVM` ve exact regülatör `TLVM13660RDLR` olarak teknik yeterlilik kapısını geçmiş ve lock hazırlığı için önerilmiştir. Ayrıntılı kayıt ve kaynaklar:

- [`docs/REP02-D6/REP02-D6-reference-candidate-qualification.md`](../docs/REP02-D6/REP02-D6-reference-candidate-qualification.md)

Durum `QUALIFIED_FOR_LOCK`, portföy onayı `PENDING`dir; exact artifact hashleri ve sözleşme v2 tamamlanana kadar `EXECUTION_READY` değildir.
