# power-rail-validation-and-review

Durum: D1_CONTRACT_IN_PROGRESS — PRIORITY 2

Bu repo güç rayı bütçesi, derating, sıralama, sentetik hata senaryoları ve kanıt sınıflı inceleme raporları için yeniden kullanılabilir bir araç seti geliştirir.

## Başlatılan çalışma

İlk uygulama dilimi `WF-02-01 Veri Sözleşmesi`dir. Bu dilim aşağıdaki çıktıları kapsar:

- `rail`, `load`, `regulator`, `source`, `event` ve `finding` için sürümlü JSON Schema dosyaları
- CSV alan sözlüğü
- Geçerli, eksik, yanlış tipli, bilinmeyen limitli ve çelişkili sentetik fixture'lar
- Standart kütüphane ile sözleşme doğrulama ve deterministik JSON SHA-256 üretimi
- Harici paket kurulumu gerektirmeyen sözleşme testleri

## Güvenlik ve iddia sınırı

G1 kaynak kapısı `BLOCKED` durumundadır. Bu repo şu aşamada gerçek müşteri şeması, exact-part regülatör modeli, fiziksel ölçüm veya EMC, ISO, AEC ve saha uygunluk kanıtı üretmez. Testlerdeki `PASS`, yalnız test edilen sözleşme davranışını ifade eder; donanım veya proje kapısı onayı değildir. Eksik karar girdisi sayısal varsayılanla doldurulmaz ve `PASS` sonucuna yükseltilmez.

## Test

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

POSIX kabuk:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

Testler yalnız Python standart kütüphanesini kullanır.

## Kapı

WF-02-02 rail-budget ve WF-02-03 sequencing uygulaması, D1 sözleşme paketi incelenip açıkça onaylandıktan sonra başlatılacaktır. Repo bağımsız Git deposudur; bu çalışma commit veya push yapmaz.
