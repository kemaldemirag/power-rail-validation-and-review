# REP-02 D5 — Gerçek Proje Girdisi Hazırlık Kapısı ve Dikey Dilim Yol Haritası

**Tarih:** 24 Ekim 2026

**Durum:** `IMPLEMENTED_AS_READINESS_GATE / CURRENT_WORKSPACE_BLOCKED`

**Kapsam:** Bir gerçek REP-01 revizyonu, bir ray ve bir exact buck regülatör için yürütme öncesi girdi hazırlığını fail-closed değerlendirmek
**Kapsam dışı:** Elektriksel uygunluk, regülatör hesabı, şematik/ERC, SPICE, laboratuvar, G1–G6 kapı onayı ve ürün serbest bırakma kararı

## 1. Yönetici sonucu

D5'in doğru ilk adımı yeni bir sentetik elektrik hesabı eklemek değil, gerçek dikey dilimin çalıştırılabilir olup olmadığını makine-okunur biçimde ayırmaktır. Yerel çalışma alanında gerçek REP-01 Git deposu vardır ve gözlenen revizyon tam olarak bağlanabilmektedir. Buna karşılık REP-01'in bugünkü içeriği bir karar/sözleşme tabanıdır; çalıştırılabilir bir proje konfigürasyonu, sayısal güç profili, ray-yük bütçesi, exact buck seçimi, şematik/BOM ve üretici modeli içermez.

Bu nedenle bugünkü güvenli sonuç `BLOCKED`'dır. Bu sonuç bir hata değildir: eksik müşteri ve tasarım girdilerinin varsayımla doldurulmasını engeller ve bitirme süresini gerçekten azaltacak en kısa işi görünür kılar. İlk gerçek dikey dilim ancak aşağıdaki dört temel REP-01 artifact'ı ile exact-part üretici kanıtı sağlandığında başlatılmalıdır:

1. proje konfigürasyonu;
2. güç giriş profili;
3. tek ray için tüketici/yük talepleri;
4. aynı rayı besleyen exact regülatör seçimi.

Hazırlık kapısının `PASS` sonucu yalnız bu girdilerin kimlik, sürüm, kapsam ve byte bütünlüğü bakımından yürütülebilir olduğunu ifade eder. Buck güç katının doğru olduğunu, pin sınırlarının sağlandığını, transient/kararlılık testlerinin geçtiğini veya donanımın onaylandığını ifade etmez.

## 2. D3 ve D4 ile sınır

D5, D3 sentetik test/kanıt kapanışını ve D4 yerel release-adayı kaydını yeniden yazmaz. `evidence/REP02-D3` ve `evidence/REP02-D4` altındaki kanonik dosyalar tarihsel snapshot olarak korunur; D5 yeni bir ardıl dilimdir. D5 nedeniyle kaynak kodu ilerlediğinde eski manifestler “güncel ağacın hash'i” olarak yorumlanmamalı, kendi kapanış anlarının değişmez kanıtı olarak okunmalıdır.

Korunan kanıt ankrajları:

| Kayıt | İç mantıksal manifest hash'i | Manifest dosyası SHA-256 | Anlamı |
|---|---|---|---|
| D3 closeout | `dff67ec8137da93d2b9d72a709e7e2761e7d859c6475a406314415992aacadf6` | `2f7946d75874a501b8dfb973e3dbf7ff99cc24b9609474cc856add674fbfc06a` | D3 sentetik/offline kapanış snapshot'ı |
| D4 release | `a0c758fcc74089a3f474559d7564775c6228d122c4cf40dad48411b474be69c9` | `5fcbf70de457386f220ca22e86f1168350bfb37219bb1a799bb51ec6f55b4176` | D4 yerel release-adayı snapshot'ı |

D3/D4'teki `PASS` kayıtları gerçek REP-01 güç katı veya fiziksel donanım sonucu değildir. D5 de bu iddia sınırını genişletmez.

## 3. Doğrulanan gerçek REP-01 snapshot'ı

### 3.1 Repository kimliği

| Alan | Doğrulanan değer |
|---|---|
| Repository | `esp32-s3-24v-interface-board` |
| REP-02'ye göre konum | Kardeş proje deposu |
| Gözlenen HEAD | `b335eec28c8cfb514c79e2ddd41be1b82a4b699b` |
| Çalışma ağacı | Gözlem anında temiz |
| Bağlama yöntemi | Core validator ve D5 finalizer/evidence ile exact Git top-level + HEAD + temiz worktree + committed blob kontrolü; ayrıca normalized göreli path + pinned sürüm/belge revizyonu + SHA-256 |

`PROJECT` kapsamında core validator Git çalıştırır: `artifact_root` tam worktree üst dizini olmalı, gerçek HEAD descriptor revizyonuna eşit olmalı, staged/unstaged ve untracked dosya bulunmamalı, her artifact bildirilen revizyonda committed olmalı ve çalışma ağacı/bildirilen/committed SHA-256 değerleri eşleşmelidir. D5 finalizer/evidence bu kontrolleri bağımsız olarak tekrarlar ve blob kimliklerini de kanıta bağlar. Commit kimliği tek başına semantik yeterlilik sağlamaz. Aşağıdaki dosyalar gerçek REP-01 kaynaklarıdır, fakat bir kısmı yalnız şema, karar kaydı veya boş yer tutucudur.

### 3.2 REP-01 kaynak dosyaları ve byte ankrajları

| Repository-relative yol | Bayt | SHA-256 | Bugünkü kanıt değeri |
|---|---:|---|---|
| `docs/WF01-D1/WF01-D1-configuration-schema.json` | 27.275 | `28b13392a85f4f281d3b89a054c14084bc826ecfbe08fcf6646eb7159c942a0c` | Konfigürasyon şeması; proje konfigürasyonu örneği değildir |
| `docs/interface-contract-v1.2.md` | 11.105 | `8fbb2a737699a5b92a13cf4756dcd4a5266306d398a316612e997388794ac3f0` | 24→5 V ve 5→3,3 V topolojisini kaydeder; exact-part/yük sınırları açık |
| `requirements/requirements-traceability-v1.2.csv` | 12.477 | `237691f6f9fd2bd244a8881ce05550e1ecd0a423d94e3bfb504944c23fe2cfe2` | Gereksinim/devir izi; Q-C01–Q-C06 engellerini gösterir |
| `docs/open-decisions-v1.2.md` | 13.483 | `1be7d880f69b82830d55483c2aed932c4c390384bb3d17304447958e542d563c` | Müşteri ve tasarım kararlarının açık durumunu kaydeder |
| `engineering/power-tree/README.md` | 178 | `110e497c8596e2f54857bd15075ae19c9b6df1915713b8ab332bb24cb02b9772` | Güç ağacı için yer tutucu; değerler `UNKNOWN/TBD` |
| `engineering/calculations/part-selection.md` | 195 | `6444e981e919ff27eaf1e702aaecf8713befa943ff718e17e47822e2993d9b46` | Exact-part seçimi için yer tutucu |
| `hardware/bom/README.md` | 107 | `ca78d9b46693282483709b140ae54bc1ec5222f77bb5b1aea22512503d1f90f1` | BOM `TBD / NOT_STARTED` |
| `hardware/kicad/README.md` | 113 | `edc119ec05640b224a6a782b158f7aa5094420c4717e2de98c88c056156baa92` | Şematik/PCB `EMPTY / NOT_STARTED` |
| `simulation/README.md` | 188 | `c3c42272481e91c9d005a17ab3ebb77569c0071386c75f11df93b36a7e423b2d` | Model ve simülasyon sonucu yok |

Bu tablo “gerçek repository erişimi var” iddiasını destekler. “Gerçek elektriksel dikey dilim çalıştırılabilir” iddiasını desteklemez.

## 4. D5 project-intake hazırlık motoru

### 4.1 Amaç

`src/power_rail_validation/project_intake` altındaki ayrı motor, bir descriptor içindeki REP-01 snapshot'ını ve artifact bağlarını doğrular. Motorun işi hesap yapmak değil, talep edilen sonraki analizlerin girdilerinin gerçekten mevcut, sürümlü ve birbirleriyle tutarlı olup olmadığını belirlemektir.

Paketin D5 geliştirme sürümü `0.6.0.dev0`, sözleşme sürümü `1.0.0`, motor sürümü `0.1.0` ve hedef topoloji `BUCK`'tır. Sözleşme şu hazırlık kabiliyetlerini ayırır:

- `REP01_BINDING`
- `STATIC`
- `BUCK`
- `PIN`
- `TRANSIENT`
- `LOOP_STABILITY`

Temel artifact rolleri şunlardır:

- `REP01_CONFIGURATION`
- `REP01_POWER_PROFILE`
- `REP01_RAIL_DEMANDS`
- `REGULATOR_SELECTION`

Destekleyici REP-01 kayıtları `REP01_INTERFACE_CONTRACT`, `REP01_REQUIREMENTS_TRACEABILITY`, `REP01_OPEN_DECISIONS` ve `REP01_POWER_TREE_PLACEHOLDER` rollerinde ayrıca bağlanabilir. Destekleyici bir sözleşme veya yer tutucu, eksik temel artifact'ın yerine geçemez.

Descriptor'ın kapalı kök alanları şunlardır: `schema_version`, `descriptor_id`, `scope`, `rep01`, `topology`, `requested_capabilities`, `operating_envelope`, `regulator`, `artifacts`, `artifact_gaps` ve `input_hash`. Kimlik ve sayısal alanlar `KNOWN / UNKNOWN / CONFLICT` bilgi durumlarıyla taşınır; `input_hash`, kendi alanı hariç descriptor'ın kanonik SHA-256 değeridir.

Her `artifacts[]` kaydı tam olarak `artifact_id`, `role`, güvenli göreli `path`, `media_type`, pinned `version`, pinned `document_revision`, `origin`, `license_status`, `sha256` ve role özgü `bindings` alanlarını taşır. `PROJECT` kapsamındaki REP-01/destek/selection kayıtları `REP01_PROJECT`, datasheet `MANUFACTURER`, model ise `MANUFACTURER` veya `AUTHORIZED_MODEL_PROVIDER` kökenli olmalıdır. Datasheet ve model lisans durumu `VERIFIED` ya da `DECLARED` olmalıdır. `TEST` kapsamı, bütün artifact'ları açıkça `INTERNAL_TEST_FIXTURE` diye işaretleyerek proje kanıtından ayırır.

`origin` ve `license_status` değerleri kapalı enum ve role politikasıyla doğrulanır; ancak bunlar bildirilen metadata'dır, üretici kimliğini veya lisansın hukuki gerçekliğini tek başına authenticate etmez. Sonuç bu sınırı `DECLARED_ORIGIN_NOT_AUTHENTICATED` ile açıklar.

`artifact_gaps[]` yalnız `gap_id`, `role` ve `reason_code` alanlarından oluşur. Aynı rol hem mevcut artifact hem gap olarak bildirilemez. Gap kaydı eksik artifact'ın yerine geçmez; engeli görünür yapar.

### 4.2 Dar core `PASS` anlamı ve evidence katmanı

Hazırlık motoru yalnız aşağıdaki koşullar birlikte sağlandığında `PASS` üretebilir:

1. Descriptor kapalı sözleşmeye uygundur; bilinmeyen alan, tekrar veya tip uyuşmazlığı yoktur.
2. REP-01 repository kimliği ile pinned tam revizyon bildirimi sözleşmeye uygundur.
3. Her zorunlu artifact benzersiz bir rol, normalized göreli yol, pinned sürüm/belge revizyonu ve SHA-256 ile gerçek baytlara bağlıdır.
4. Tek `rail_id` ve tek exact üretici parça numarası descriptor ile role özgü `bindings` alanlarında karakter-karakter eşleşir.
5. Talep edilen her kabiliyet için gereken artifact sınıfı vardır; desteklenmeyen kapsam sessizce atlanmaz.
6. Datasheet/model kayıtları izinli `origin`, pinned sürüm/belge revizyonu ve `VERIFIED` veya `DECLARED` lisans durumunu taşır.
7. Eksik, çelişkili, erişilemeyen veya hash'i uyuşmayan hiçbir zorunlu girdi yoktur.
8. `PROJECT` için `artifact_root` exact Git top-level'dır; HEAD bildirilen revizyona eşittir; worktree temizdir; her artifact o revizyonda committed'dır ve committed/çalışma ağacı/bildirilen hash değerleri aynıdır.
9. Aynı dosya baytları farklı readiness rollerini doldurmak için yeniden etiketlenmemiştir.

Bu dokuz koşul core motorun `PROJECT` `PASS` sınırıdır ve sonuçtaki `project_provenance` ledger'ında görünür. `TEST` kapsamı Git provenance kontrolü çalıştırmaz ve `SYNTHETIC_TEST_ONLY` kalır. Kanonik D5 finalizer/evidence, PROJECT Git/byte kontrollerini ikinci kez ve bağımsız olarak tekrarlar.

Bu `PASS` yalnız `EXECUTION_READINESS_ONLY` anlamındadır. Şunları özellikle iddia etmez:

- elektriksel uygunluk veya tasarım doğruluğu;
- absolute-maximum/recommended sınırların sağlandığı;
- buck endüktör, kapasitör, akım limiti, kompanzasyon veya termal hesabının geçtiği;
- pin, back-power veya power-off davranışının güvenli olduğu;
- transient veya loop-stability kabul eşiklerinin geçtiği;
- G1–G6 kapılarından herhangi birinin kapandığı;
- fiziksel donanımın doğrulandığı.

Motor ayrıca `NO_ARTIFACT_SEMANTIC_EXTRACTION` sınırını bildirir: bağlı dosyaların baytlarını, kimliklerini ve çapraz bağlarını doğrular; dosya içeriğinden elektriksel değer çıkarmaz. Değerler descriptor'ın `operating_envelope` alanında yetkili biçimde verilmelidir. `DESCRIPTOR_HASH_IS_CANONICAL_JSON_NOT_SOURCE_FILE_BYTES` sınırı da `input_hash` değerinin descriptor kaynak dosyasının byte hash'i değil, self-hash alanı çıkarılmış kanonik JSON hash'i olduğunu belirtir.

### 4.3 Fail-closed durum politikası

| Durum | Hazırlık bağlamındaki anlamı |
|---|---|
| `PASS` | Yalnız bildirilen analiz kabiliyetleri için çalışma girdileri bütün ve byte-bağlıdır |
| `UNKNOWN` | Girdi mevcuttur fakat sonucu belirleyecek değer veya sınıflandırma yetkili biçimde bilinmemektedir |
| `BLOCKED` | Zorunlu artifact, kaynak, hash, exact-part veya karar yoktur/uyuşmamaktadır; sonraki motor koşturulmamalıdır |
| `NOT_RUN` | İlgili downstream analiz bu hazırlık değerlendirmesinde hiç çalıştırılmamıştır |

Eksik sayısal değer sıfırla, örnekle veya uygulama notundaki tipik değerle doldurulmaz. Aday parça veri sayfasının bulunması, parçayı seçilmiş veya ray için uygun yapmaz.

### 4.4 CLI kullanımı

Komut şekli sabittir: `intake INPUT --artifact-root ROOT`. `--artifact-root` zorunludur; artifact yolları bu kökün altında güvenli göreli POSIX yollar olarak çözülür. `PROJECT` kapsamında ROOT tam Git top-level olmalıdır; bir alt dizin kabul edilmez. PowerShell'de repository kökünden bugünkü gerçek snapshot değerlendirmesi:

```powershell
$env:PYTHONPATH = "src"
python -m power_rail_validation intake `
  ".\scenarios\REP02-D5\project-intake.json" `
  --artifact-root "..\esp32-s3-24v-interface-board"
$LASTEXITCODE
```

Doğrulanan sonuç `BLOCKED`, proses çıkış kodu `3`'tür. Pozitif sözleşme smoke testi ise yalnız sentetik/test kapsamındadır:

```powershell
$env:PYTHONPATH = "src"
python -m power_rail_validation intake `
  ".\tests\fixtures\project_intake\ready-static-buck-pin.json" `
  --artifact-root ".\tests\fixtures\project_intake"
$LASTEXITCODE
```

Bu fixture `PASS / 0` üretir ve çıktısına `SYNTHETIC_TEST_ONLY` sınırlamasını ekler. Bir PROJECT sonucu gibi kullanılamaz.

## 5. Bugünkü hazırlık sonucu ve zorunlu gap'ler

Kanonik gerçek-workspace descriptor'ı `scenarios/REP02-D5/project-intake.json` dosyasıdır. Descriptor kanonik `input_hash` değeri `ab7e7a992532a8d061f16581c5ea19be7fb41ac8fa9ad490a5e79088776527a5`, dosya SHA-256 değeri `10002e18fb1dc287400e0ce41b6abf94657696aad978453648fce8dd709f06e7`'dir. Çalıştırma dört destek artifact'ının dörtte dördünü hem çalışma ağacı hem committed Git baytlarıyla `PASS` doğrulamış ve `project_provenance` sonucunu `PASS / CLEAN` kaydetmiş; altı zorunlu yürütme rolünün eksik olduğunu göstermiştir. Toplam 17 issue'nun 10'u `BLOCKED`, 7'si `UNKNOWN`'dır. Dört downstream değerlendirmesinin tamamı `NOT_RUN` kalmıştır.

| Hazırlık öğesi | Bugünkü kanıt | Durum | Kapatma koşulu |
|---|---|---|---|
| Gerçek REP-01 repository/revizyonu | Core ve D5 evidence exact top-level, HEAD, temiz worktree ve dört committed artifact eşitliğini doğruladı | `PROVENANCE PASS / INTAKE BLOCKED` | Her kapanışta Git/byte kontrollerini yeniden çalıştır |
| Proje konfigürasyonu | Şema var; şemaya uyan proje örneği yok | `BLOCKED` | Yetkili, sürümlü REP-01 configuration JSON |
| Güç giriş profili | 24 V nominal anlatısı var; sürekli min/max, load-dump, jump-start ve ters polarite açık | `BLOCKED` | Q-C01–Q-C03 kaynaklı sayısal profil ve fonksiyonel politika |
| Ray/yük talepleri | 5 V ve 3,3 V topolojisi var; tüketici normal/tepe/inrush akımları yok | `BLOCKED` | Tek ray için tüketici bazlı normal/peak/inrush/süre/mod kayıtları |
| Exact buck seçimi | Parça seçimi kaydı yer tutucu | `BLOCKED` | Üretici + tam MPN + seçili ray + topoloji + karar sürümü |
| Yetkili datasheet profili | Üç aday üretici PDF'si yerelde var; hiçbiri seçili buck'a bağlanmış değil | `BLOCKED` | Seçilen exact buck datasheet'i, belge revizyonu, SHA-256 ve bölüm/limit eşlemesi |
| Şematik/netlist ve BOM | KiCad/BOM alanları boş | `NOT_RUN / BLOCKED` | Seçilen parçayı ve pasifleri bağlayan sürümlü kaynaklar |
| Pin/gerilim kısıtları | EN/PGOOD/reset ve dış I/O/back-power ayrıntıları yok | `BLOCKED` | Pin bazlı abs-max/recommended/eşik/pull-up/off-state kayıtları |
| Üretici modeli | Model, lisans ve validity-bounds yok | `BLOCKED` | Exact-part model dosyası + kaynak + lisans + hash + geçerlilik aralığı |
| Transient kanıtı | Ham log, ΔI, dI/dt, Vmin/Vmax ve settling yok | `NOT_RUN` | Kaynaklı kabul eşiği ve ölçülen metriklerle ayrı kayıt |
| Loop-stability kanıtı | Bode sonucu, phase/gain margin ve kabul limiti yok | `NOT_RUN` | Model/araç/probe koşulları ve ölçülen marjinlerle ayrı kayıt |
| Sahiplik ve kapılar | Gate/Evidence/Verification owner adları `TBD`; G1 bloklu | `BLOCKED` | Yetkili insan sahipleri ve gerekli karar kanıtı |

Bugünkü overall değerlendirme `BLOCKED`'dır. Core ve D5 evidence Git/revizyon provenance sonucu `PASS` olsa da core validator bütün capability sonuçlarını overall hazırlık durumuna göre `BLOCKED` bırakır; yalnız repository binding kanıtı sonucu yükseltmez.

## 6. Yerel üretici PDF'leri neden exact buck kanıtı değildir?

Yerel referans klasöründeki üç üretici belgesi byte olarak doğrulanmıştır:

| Belge | Bayt | SHA-256 | Neden mevcut D5 hedefini nitelemez? |
|---|---:|---|---|
| `PDF-05_PDF-06_TPS2553_TPS2553-1_Datasheet.pdf` | 2.091.666 | `88e453700cea2b263cdb5b44fce1e883e0f6d3b975457f879ac1423eeea42071` | TPS2553/TPS2553-1 bir akım sınırlı güç dağıtım anahtarı ailesidir; 24→5 V veya 5→3,3 V buck dönüştürücü değildir. USB VBUS koruma adayı olarak ayrıca değerlendirilebilir, fakat Q-C04 ve exact rol seçimi açıkken seçilmiş parça sayılamaz. |
| `PDF-07_TPS2051C_Datasheet.pdf` | 2.726.989 | `898c093489ac30dde4c62f1ccaeaf71573239021a93da4182eb8efadf4be3fcd` | TPS2051C bir güç dağıtım/USB yük anahtarıdır; buck güç katını sağlamaz. USB yükü, inrush, akım limiti, fault/EN davranışı ve exact seçim kararı bulunmadan dikey dilimin regülatörü olamaz. |
| `PDF-08_LM7480-Q1_Datasheet.pdf` | 9.773.286 | `38ea1e89cc677020c8a10096649fa7933d2aeff8c0cd97ca9a78c1f8f5b9b500` | LM7480-Q1 harici MOSFET'lerle giriş koruma/ideal-diyot denetleyicisidir; 24→5 V buck regülatörü değildir. Q-C01–Q-C03 transient, jump-start ve ters polarite profilleri ile koruma topolojisi kararı olmadan giriş kademesi için de nitelendirilmiş sayılamaz. |

Bu belgeler silinmemeli; ileride USB VBUS veya otomotiv giriş koruma alt-dilimlerinde yararlı olabilir. Ancak mevcut buck dikey diliminin exact-part datasheet boşluğunu kapatmazlar. Belge varlığı ile tasarım seçimi arasındaki ayrım korunmalıdır.

## 7. Bitirme süresini minimize eden kritik yol

### Aşama A — D5 hazırlık kapısını aç

En kısa güvenli yol, REP-01'de geniş mimariyi tamamlamaya çalışmak yerine yalnız bir rayı seçip dört temel artifact'ı üretmektir:

1. `24 V → 5 V` veya `5 V → 3,3 V` raylarından, ilk yetkili exact buck ve yük verisi sağlanabileni seçin.
2. Seçimi bir tam üretici MPN'siyle kaydedin; ürün ailesi veya referans tasarım adı kullanmayın.
3. Yalnız bu rayın normal/tepe/inrush yüklerini ve sürelerini yetkili kaynaklarla çıkarın.
4. İlgili giriş min/nom/max ve transient profilini bağlayın.
5. Datasheet'in tam belge kodu/revizyonu ve SHA-256'sını kaydedin; kullanılan her limit için bölüm/tablo/sayfa bağını ekleyin.
6. Descriptor'ı gerçek REP-01 commit'i ve artifact byte hashleriyle üretin.
7. Hazırlık motoru `PASS` vermeden elektriksel downstream motoru çalıştırmayın.

Bu yaklaşım iki güç kademesini, USB VBUS'u, giriş korumasını ve bütün arayüzleri aynı anda çözmeye çalışmaktan daha kısa ve denetlenebilirdir.

### Aşama B — Tek buck güç katı

Hazırlık `PASS` sonrasında küçük bir `buck_power_stage` kontrolü eklenmelidir. İlk sürüm yalnız şu kaynaklı kontrolleri içermelidir:

- VIN/VOUT/IOUT recommended sınırları ve abs-max ayrımı;
- switching frequency ve duty-cycle geçerliliği;
- endüktans toleransı, ripple ve peak inductor current;
- current-limit marjı ve saturation current;
- giriş/çıkış kapasitansı, DC-bias, ESR ve ripple-current sınırları;
- feedback divider toleransı ve çıktı aralığı;
- datasheet'e bağlı minimum on/off-time gibi topoloji kısıtları;
- verim/loss/termal değerleri için sabit varsayım yerine açık kaynaklı profil.

Bir parametre veya kabul limiti yoksa sonuç `BLOCKED/UNKNOWN` kalmalıdır.

### Aşama C — Gerilim ve pin kısıtları

Aynı exact part için ayrı bir voltage/pin reviewer şu alanları denetlemelidir:

- VIN, SW, BOOT, FB, EN, PGOOD ve varsa bias pinleri için abs-max ile recommended aralıkların ayrı tutulması;
- EN high/low eşikleri ve toleransları;
- açık-drain PGOOD pull-up kaynağı, pin max gerilimi ve sıralama etkisi;
- rail kapalıyken dış I/O/reference/pull-up üzerinden back-power yolu;
- startup, brownout, shutdown ve restart sırası;
- discharge/bleeder ve power-off davranışı.

Mevcut sabit `VIN_VALID → EN → PGOOD → RESET` FSM'i yalnız orchestration izi olarak korunmalıdır; genel çok-raylı veya shutdown sıralaması kanıtı diye sunulmamalıdır.

### Aşama D — Gerçek transient ve loop stability

Transient ile loop-stability aynı kanıt türüne dönüştürülmemelidir.

Transient kaydı en az `ΔI`, `dI/dt`, başlangıç/son yük, Vmin, Vmax, undershoot/overshoot, settling-time, giriş koşulu, sıcaklık ve kabul limitini taşımalıdır. Loop-stability kaydı ise injection yöntemi, operating point, loop probe konumu, frequency sweep, crossover, phase margin, gain margin, model/araç sürümü ve kabul limitini taşımalıdır.

Ham log/trace ile özet metrik aynı evidence manifestinde ayrı hashlerle bağlanmalıdır. Datasheet veya uygulama notu genel prensip sağlayabilir; ölçülmüş/simüle edilmiş vaka kanıtının yerine geçmez.

## 8. Kritik yol dışına alınacak işler

İlk gerçek dikey dilim geçene kadar aşağıdakiler ertelenebilir:

- bütün rayları kapsayan genel güç ağacı motoru;
- sabit FSM'in çok-raylı genel sıralama diline dönüştürülmesi;
- yeni geniş sentetik fixture kampanyaları;
- DART entegrasyonu;
- ikinci bir release/package turu;
- fiziksel PCB ve kapsamlı uygunluk otomasyonu.

D4 CLI/reporting altyapısı yeniden yazılmamalı; D5 intake sonucu mevcut durum/çıkış-kodu modeline ince bir komut olarak bağlanmalıdır.

## 9. Kabul ölçütleri

D5 hazırlık dilimi aşağıdaki koşullarda tamamlanmış sayılır:

1. Intake descriptor sözleşmesi, validator ve CLI yolu paket içinde erişilebilirdir.
2. Pozitif fixture yalnız tam ve byte-bağlı bir örnekte `PASS` üretir.
3. Eksik rol, güvenli olmayan path, hash/binding uyuşmazlığı, yanlış Git root/HEAD, dirty veya untracked worktree, uncommitted/uyuşmayan artifact ve farklı readiness rollerinde duplicate içerik ayrı reason-code'larla `BLOCKED` olur.
4. Bugünkü gerçek REP-01 snapshot'ı için eksikler açık ve makine-okunur `BLOCKED` sonucu üretir; aday PDF'ler yanlışlıkla buck datasheet'i sayılmaz.
5. Downstream buck, pin, transient ve loop-stability motorları çalıştırılmadığı için sonuç bunlar hakkında `PASS` iddiası üretmez.
6. D3 ve D4 kanonik evidence dosyaları değiştirilmez; D5 kanıtı ayrı dizinde tutulur.
7. Byte-bağlı D5 taban regresyonu, Python 3.9 sözdizimi ve `git diff --check` temizdir.
8. `origin` metadata'sının authenticate edilmediği ve descriptor hash'inin kaynak dosya byte hash'i olmadığı sonuç sınırlamalarında açıkça kalır.

Uygulama kapanışında 29 project-intake testi, 21 CLI testi ve `D5-WORKSPACE-FULL-REGRESSION` adlı byte-bağlı tam çalışma alanı regresyonunda toplam 404 test geçmiştir (`29/29`, `21/21`, `404/404 PASS`). Bu kanonik regresyon, HEAD taban test modülleriyle birlikte çalışma ağacında bulunan ve kendi SHA-256 kaydıyla bağlanan tüm üst düzey `tests/test*.py` modüllerini kapsar; eşzamanlı NCS01 modeli, örnek girdisi ve testi de D5 kaynak envanterindeki SHA-256 kayıtlarıyla bağlanır ve sessizce dışlanmaz. Python 3.9 AST kontrolü geçmiştir. Paketlenmiş sözleşme kaynağının SHA-256 değeri `91f89178113807e5da2901b3394495c73ccbcf3eace6aa8deae05566721a743c` olup validator içindeki sabit kimlikle eşleşmektedir.

Kanonik D5 kanıt paketi aşağıdaki ayrı dosyalardan oluşur:

- `evidence/REP02-D5/intake-result.json`
- `evidence/REP02-D5/test-results.json`
- `evidence/REP02-D5/report.md`
- `evidence/REP02-D5/checkpoint-manifest.json`

Bu ana doküman D5 kaynak envanterine byte olarak bağlandığından, D5 manifest/output hashleri burada tekrarlanmaz; nihai değerler yalnız `checkpoint-manifest.json` içinde tutulur. Böylece belge–manifest self-reference döngüsü oluşmaz.

Kanonik evidence, canlı Git commit veya dal adını hash kapsamına almaz; bunun yerine tüm D5/NCS01 kaynak baytlarını `d5_source_hash_inventory` ile bağlar ve her envanter kaydının çalışma ağacı SHA-256 değerini `HEAD:path` committed baytlarıyla yeniden karşılaştırır. Kaynak commit'inden sonra üretilen evidence ayrı bir commit'e eklense dahi aynı kaynaklar ve zaman damgasıyla yeniden üretim byte-identical kalır; böylece commit kimliğinin kendi kanıtını sonsuz kez değiştirdiği bir self-reference zinciri oluşmaz.

## 10. Sonraki karar

Teknik olarak sonraki engel kod değil, yetkili proje girdisidir. En değerli bir sonraki teslim; REP-01 içinde tek bir ray için gerçek konfigürasyon, sayısal profil, ray talepleri ve exact buck seçim kayıtlarının hazırlanmasıdır. Bunlar gelmeden buck hesabı, pin PASS'i veya transient/stability sonucu üretmek süre kazandırmaz; yalnız doğrulanmamış varsayımı büyütür.
