# REP-02 D6 — Referans Adayı Yeterlilik Kaydı

## 1. Karar özeti

| Alan | Değer |
|---|---|
| Yeterlilik kayıt kimliği | `QUAL-REP02-D6-CANDIDATE-001` |
| Tarih | `2026-10-26` |
| Durum | `QUALIFIED_FOR_LOCK` |
| Portföy onayı | `PENDING` |
| Lock için önerilen aday | `TI-TLVM13660EVM` |
| Kaynak türü | `MANUFACTURER_EVALUATION_MODULE` |
| Evidence context | `REFERENCE_DESIGN` |
| Exact regülatör | Texas Instruments `TLVM13660RDLR` |
| İlk dilim | `STATIC + BUCK + PIN` |
| Ham kaynak dağıtımı | `REVIEW_REQUIRED`; repoya alınmaz |
| D6 yürütme hazırlığı | `BLOCKED`; artifact SHA-256 lock ve sözleşme v2 henüz yok |

**TI TLVM13660EVM**, teknik olarak yeterli bulunmuş ve lock hazırlığı için önerilmiştir. Aday gerçek synchronous buck topolojisini, 24 V → 5 V çalışma noktasını, exact üretici/MPN'yi, iki doğrudan sayısal verim oracle'ını, revizyonlu şema/BOM'u ve EN/PGOOD/pin kanıtını aynı üretici kaynak setinde sağlar. Resmî portföy approval record'u henüz oluşturulmamıştır.

Bu yeterlilik sonucu yalnız `reference.lock.json` hazırlığına geçiş önerisidir. Bir portföy onayı, lisans görüşü, ham TI dosyalarını yeniden dağıtma izni, D6 teknik `PASS` sonucu veya donanım doğrulaması değildir.

## 2. Aday kapısı

| Zorunlu ölçüt | Kanıt | Sonuç |
|---|---|---|
| Gerçek BUCK topolojisi | TI ürün sayfası ve SLVUCF7 Bölüm 1, TLVM13660 synchronous buck power module | `PASS` |
| Exact üretici + MPN | SLVUCF7, sayfa 16, Tablo 6-1: `TLVM13660RDLR`, Texas Instruments | `PASS` |
| 24 V nominal giriş | SLVUCF7, sayfa 5, Tablo 2-1 test koşulları | `PASS` |
| 5 V çıkış | EVM varsayılanı 5 V; SLVUCF7 sayfa 5 | `PASS` |
| Açık kapasite/yük | 0–6 A çıkış aralığı; 3 A half-load ve 6 A full-load çalışma noktaları | `PASS` |
| Doğrudan sayısal oracle | 24 V / 5 V / 3 A için `%93,5`; 24 V / 5 V / 6 A için `%91,4` | `PASS` |
| Şema | SLVUCF7, sayfa 15, Şekil 6-1 | `PASS` |
| BOM | SLVUCF7, sayfa 16, Tablo 6-1 | `PASS` |
| EN / PGOOD / soft-start | Precision EN, open-drain PGOOD ve internal soft-start üretici kaynağında mevcut | `PASS` |
| Belge kimliği/revizyonu | EVM kullanıcı kılavuzu `SLVUCF7`, Mart 2022 | `PASS` |
| Exact artifact SHA-256 | Yerel/kullanıcı-sağlanan PDF'ler henüz lock dosyasına bağlanmadı | `BLOCKED` |
| Ham dosya yeniden dağıtımı | Açık kaynak lisansı yok; TI kullanım koşulları ayrıca kaydedilecek | `REVIEW_REQUIRED` |
| Exact lisanslı simülasyon modeli | İlk statik dilim için gerekli değil; henüz doğrulanmadı | `NOT_RUN` |

Kapı kararı `QUALIFIED_FOR_LOCK`tır. `EXECUTION_READY` olabilmek için exact dosya byte'larının SHA-256 değerleri, retrieval kaydı, kullanım koşulu kaydı ve sözleşme v2 doğrulaması tamamlanmalıdır.

## 3. İlk deterministik oracle'lar

Kaynak değer ile türetilmiş değer aynı provenans sınıfında tutulmamalıdır. Verim değerleri `SOURCE_EXPLICIT`; güç ve giriş akımı hesapları `DERIVED` olmalıdır.

### Oracle A — yarım yük

- `VIN = 24 V`
- `VOUT = 5 V`
- `IOUT = 3 A`
- yayımlanmış `efficiency = 0.935`
- türetilmiş `Pout = 15 W`
- türetilmiş gösterim değeri `Pin ≈ 16.04 W`
- türetilmiş gösterim değeri `Iin ≈ 0.6684 A`

### Oracle B — tam yük

- `VIN = 24 V`
- `VOUT = 5 V`
- `IOUT = 6 A`
- yayımlanmış `efficiency = 0.914`
- türetilmiş `Pout = 30 W`
- türetilmiş gösterim değeri `Pin ≈ 32.82 W`
- türetilmiş gösterim değeri `Iin ≈ 1.368 A`

Kaynak verimler üç anlamlı basamakla yayımlanmıştır. Kanonik `Decimal` hesabı, kaynak hassasiyeti, kullanıcı gösterimi ve karşılaştırma toleransı sözleşme v2'de ayrı alan/kurallar olarak tanımlanmalıdır. Kaynak değerler normalize kayıtta değiştirilmeden tutulmalı; gösterim yuvarlaması hash kapsamındaki kanonik hesap değerini değiştirmemelidir.

## 4. Kapsam ve iddia sınırı

İlk D6 dilimi aşağıdakilerle sınırlıdır:

- exact parça ve referans kimliği;
- 24 V → 5 V rail-budget çalışma noktaları;
- yayımlanmış verim ile araç hesabının tutarlılığı;
- VIN/VOUT/IOUT ve pin/EN/PGOOD bilgisinin revizyonlu kaynaklara bağlanması;
- kanonik normalize veri, deterministik rapor ve evidence manifesti.

İlk dilimde aşağıdakiler `NOT_RUN` kalır:

- exact lisanslı model doğrulanmadan transient simülasyonu;
- exact tool/model/probe ve ölçüm kaydı olmadan loop stability;
- fiziksel kart ölçümü;
- EMC, ISO, AEC veya saha uygunluğu;
- REP-01 GAP-02/03'ün global kapanışı.

EVM verisi, yayımlanmış sabit bir karşılaştırma tabanıdır. Aracın bu veri kümesinde deterministik ve tutarlı sonuç vermesi, başka bir tasarımın veya fiziksel donanımın doğru olduğunu kanıtlamaz.

## 5. Kaynak ve lock politikası

Planlanan lock kimliği:

```text
reference_id = TI-TLVM13660EVM
reference_kind = MANUFACTURER_EVALUATION_MODULE
manufacturer = Texas Instruments
regulator_mpn = TLVM13660RDLR
evm_user_guide = SLVUCF7, March 2022
datasheet = TLVM13660 data sheet, document id/revision to be byte-locked
```

Her artifact kaydı en az şu alanları taşımalıdır:

- canonical URL;
- document id ve revision/date;
- retrieval zamanı;
- kullanıcı-sağlanan veya izinli cache dosyasının SHA-256 değeri;
- PDF için sayfa + tablo/şekil/bölüm locator'ı;
- kullanım koşulu kaydı ve raw redistributability sonucu;
- normalized output path ve onun SHA-256 değeri.

Ham PDF, ZIP, PCB/CAD veya model dosyaları varsayılan olarak Git'e eklenmez. Yeniden üretim, kullanıcı-sağlanan dosyanın lock hash'ine karşı doğrulanması veya izinli özel cache üzerinden yapılır. Repoya yalnız izinli normalize olgular, locator'lar ve deterministik çıkarım mantığı eklenir.

## 6. Değerlendirilen alternatifler

| Aday | Güçlü yön | Elenme / yedek nedeni |
|---|---|---|
| `LM53635LQEVM` / `LM53635LQRNLRQ1` | Otomotiv parçası, 5 V / 3,5 A, EN ve RESET/PGOOD | Üretici verim eğrisi 24 V için doğrudan sayısal oracle sunmuyor; ilk dilimde yorum/dijitalleştirme gerekir |
| `LMR33630ADDAEVM` / `LMR33630ADDAR` | Basit 5 V / 3 A buck, EN ve PGOOD, şema/BOM | EVM kılavuzundaki ölçüm 12 V ağırlıklı; 24 V kıyas datasheet uygulama eğrisinden gelir |
| `TIDA-01167` | Exact MPN'li, revizyonlu TI koruma tasarımı | Ana işlev 12/24 V giriş korumasıdır; 24 V → 5 V buck değildir |
| `blinken/vandal` | Sabit Git commit'i ve GPL-3.0 kaynakları | 5 V regülatör generic `LM7805_TO220`; exact üretici/MPN ve yayımlanmış rail ölçümü yoktur |

Alternatif kaynak locator'ları:

- LM53635LQEVM ürün sayfası ve kullanıcı kılavuzu `SNVU526`: <https://www.ti.com/tool/LM53635LQEVM>, <https://www.ti.com/lit/ug/snvu526/snvu526.pdf>
- LMR33630ADDAEVM ürün sayfası ve kullanıcı kılavuzu `SNVU573A`: <https://www.ti.com/tool/LMR33630ADDAEVM>, <https://www.ti.com/lit/ug/snvu573a/snvu573a.pdf>
- TIDA-01167 ürün sayfası ve tasarım kılavuzu `TIDUC41A`: <https://www.ti.com/tool/TIDA-01167>, <https://www.ti.com/lit/ug/tiduc41a/tiduc41a.pdf>
- blinken/vandal sabit commit `0e7d008c51244fb7d81e0838f091b584cd5d1c55`: <https://github.com/blinken/vandal/tree/0e7d008c51244fb7d81e0838f091b584cd5d1c55>

## 7. Sonraki oturum geçiş ölçütleri

Oturum 2 şu sırayla ilerlemelidir:

1. V1'i değiştirmeyen sözleşme v2 ve sürüm dispatcher'ı.
2. `provenance_type` ile `evidence_context` ayrımı.
3. `reference_kind = MANUFACTURER_EVALUATION_MODULE` ve kaynak-türüne özgü locator yapısı.
4. Ham kaynağı repoya almayan `reference.lock.json` sözleşmesi.
5. Exact yerel PDF'lerin SHA-256 doğrulaması.
6. İlk iki oracle için deterministik normalize fixture ve negatif testler.

Sözleşme ve lock kapısı geçmeden extraction veya motor entegrasyonu başlatılmaz.

## 8. Birincil kaynaklar

- TI TLVM13660EVM ürün sayfası: <https://www.ti.com/tool/TLVM13660EVM>
- TI TLVM13660EVM kullanıcı kılavuzu, SLVUCF7: <https://www.ti.com/lit/ug/slvucf7/slvucf7.pdf>
- TI TLVM13660 ürün sayfası: <https://www.ti.com/product/TLVM13660>
- TI TLVM13660 exact orderable part: <https://www.ti.com/product/TLVM13660/part-details/TLVM13660RDLR>
- TI TLVM13660 datasheet: <https://www.ti.com/lit/ds/symlink/tlvm13660.pdf>
- TI kullanım koşulları: <https://www.ti.com/legal/terms-conditions/terms-of-use.html>

Bu kayıt teknik uygunluk ve yeniden üretilebilirlik kontrolüdür; hukuki görüş değildir.
