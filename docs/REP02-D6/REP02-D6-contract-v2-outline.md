# REP-02 D6 — Sözleşme v2 Tasarım Sınırı

## 1. Amaç

Bu belge Oturum 2'nin uygulama sınırını tanımlar. Amaç, mevcut `1.0.0` sözleşmeleri ve 404 testlik davranış tabanını değiştirmeden referans-tasarım girdileri için yan yana `2.0.0` sözleşmeleri, sürüm dispatch'i ve merkezi kanıt politikası eklemektir.

Bu aşamada mevcut v1 doğrulayıcı gövdeleri taşınmaz veya enum genişletmesiyle değiştirilmez.

## 2. Minimum mimari

### 2.1 Sözleşme katmanı

Yeni modüller:

- `contracts/dispatch.py`: `schema_version` ile v1/v2 seçimi;
- `contracts/validation_v2.py`: yalnız v2 yapısal ve çapraz alan kuralları;
- `contracts/evidence_policy.py`: teknik kanıta katkı kararını tek yerde veren fail-closed politika;
- `schemas/*.v2.schema.json`: altı v2 entity şeması;
- `schemas/approval-record.v2.schema.json`: teknik kanıttan ayrı karar/onay kaydı;
- `contracts/field_dictionary_v2.csv`: v2 alan sözlüğü.

Mevcut `contracts/validation.py`, v1 şemaları ve `field_dictionary.csv` değiştirilmeden kalır. Dış API ve CLI, dispatcher üzerinden doğru sürüme yönelir.

Desteklenen sürümler yalnız `1.0.0` ve `2.0.0`dır. Bilinmeyen/eksik sürüm fail-closed olmalı; bir handoff içinde karışık nested sürümler `CONTRACT_VERSION_MISMATCH` üretmelidir.

### 2.2 Provenance ve evidence context

V2'de iki eksen ayrılır:

```json
{
  "provenance_type": "SOURCE_EXPLICIT",
  "evidence_context": {
    "kind": "REFERENCE_DESIGN",
    "context_id": "TI-TLVM13660EVM"
  }
}
```

`provenance_type` değerleri:

- `SOURCE_EXPLICIT`
- `DERIVED`
- `ENGINEERING_RULE`
- `ASSUMPTION`
- `UNKNOWN`
- `CONFLICT`

`evidence_context.kind` değerleri:

- `REP01_PROJECT`
- `REFERENCE_DESIGN`
- `INTERNAL_TEST_FIXTURE`

`PORTFOLIO_DECISION`, teknik entity'lerin `evidence_context` alanında geçerli değildir. Ayrı `approval_record` entity'si olarak modellenir.

Merkezi politika:

- `SOURCE_EXPLICIT + REFERENCE_DESIGN`, yalnız exact lock, scope, hash ve locator doğrulanırsa teknik desteğe katkı verebilir.
- `DERIVED`, context nedeniyle otomatik trusted olmaz; kaynak girdileri ve dönüşüm kuralı bağlanmalıdır.
- `ENGINEERING_RULE`, yalnız sürümlü ve kayıtlı kural kimliğiyle teknik desteğe katkı verebilir.
- `ASSUMPTION` ve `UNKNOWN` teknik `PASS` oluşturamaz.
- `CONFLICT` her bağlamda `BLOCKED`dır.
- `PORTFOLIO_DECISION` hiçbir koşulda elektriksel, sayısal veya model `PASS` kanıtı değildir.
- V1 girdilerindeki mevcut güven davranışı değişmez.

### 2.3 Kaynak locator union'ı

Tek serbest metin alanı yerine kapalı locator union'ı kullanılır.

PDF örneği:

```json
{
  "kind": "PDF",
  "uri": "https://www.ti.com/lit/ug/slvucf7/slvucf7.pdf",
  "document_id": "SLVUCF7",
  "document_revision": "March 2022",
  "page_start": 5,
  "page_end": 5,
  "section": "Table 2-1 Electrical Performance Specifications"
}
```

Git örneği:

```json
{
  "kind": "GIT_BLOB",
  "repository_url": "https://github.com/example/project",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "path": "hardware/bom.csv",
  "line_start": 10,
  "line_end": 25,
  "blob_hash": "0123456789abcdef0123456789abcdef01234567"
}
```

Yerel normalize artifact örneği:

```json
{
  "kind": "LOCAL_ARTIFACT",
  "path": "references/TI-TLVM13660EVM/normalized/static-oracles.json"
}
```

Kurallar:

- teknik locator floating branch/tag veya salt URL olamaz;
- Git commit tam kimlik, path güvenli göreli yol olmalıdır;
- PDF sayfaları 1 tabanlı ve sıralı olmalıdır;
- yerel path mutlak olamaz ve traversal içeremez;
- ortak kaynak nesnesi tam 64 küçük hex SHA-256 taşımalıdır.

### 2.4 Approval record

`PORTFOLIO_DECISION`, source provenance değeri değil ayrı entity olmalıdır:

```json
{
  "schema_version": "2.0.0",
  "entity_type": "approval_record",
  "decision_id": "DEC-REP02-D6-CANDIDATE-001",
  "decision_type": "REFERENCE_SELECTION",
  "subject": {
    "kind": "REFERENCE_DESIGN",
    "id": "TI-TLVM13660EVM"
  },
  "outcome": "APPROVED",
  "approved_by": {
    "id": "portfolio-owner",
    "role": "PRODUCT_OWNER"
  },
  "approved_at": "<rfc3339-timestamp>",
  "content_hash": {
    "algorithm": "SHA-256",
    "value": "<canonical-payload-hash>"
  },
  "source_refs": [
    {
      "record_id": "QUAL-REP02-D6-CANDIDATE-001",
      "content_hash": {
        "algorithm": "SHA-256",
        "value": "<qualification-record-hash>"
      }
    }
  ]
}
```

`content_hash`, kendi alanı hariç kanonik karar payload'ını bağlar. `REFERENCE_SELECTION` kararında qualification record kimliği ve content hash referansı zorunludur. Approval record aday, kapsam, lisans, release veya açık istisna kapısını açabilir; rail değeri, limit, datasheet gerçeği, model uygunluğu veya ölçüm yerine geçemez.

### 2.5 Reference intake subject union'ı

V2 kökü kapalı subject union'ı kullanmalıdır:

```json
{
  "subject": {
    "kind": "REFERENCE_DESIGN",
    "subject_id": "TI-TLVM13660EVM",
    "primary_source_ref": "SRC-TI-SLVUCF7",
    "rail_id": "VOUT_5V",
    "regulator_id": "U1"
  }
}
```

Diğer kol `kind = REP01` olup mevcut repository/configuration/profile/rail bağlarını taşır. Referans kolunda harici kaynak byte'larının aynı Git kökünde committed olma şartı aranmaz. Exact locator + SHA-256 + lisans kaydı doğrulanır; normalize veri ve extraction script'i repo içinde committed/hash-bound olmalıdır.

V2 genel artifact rolleri:

- `DESIGN_DEFINITION`
- `RAIL_OPERATING_PROFILE`
- `RAIL_LOAD_REQUIREMENTS`
- `REGULATOR_SELECTION`
- `REGULATOR_DATASHEET`
- `PUBLISHED_TEST_RESULT`
- `LICENSE_TERMS`
- `EXTRACTION_SCRIPT`
- opsiyonel `REGULATOR_MODEL`

`REGULATOR_MODEL`, yalnız `TRANSIENT` veya `LOOP_STABILITY` istendiğinde zorunludur.

## 3. Dosya etkisi

Oturum 2'de beklenen değişiklikler:

- `src/power_rail_validation/contracts/__init__.py`
- yeni `src/power_rail_validation/contracts/dispatch.py`
- yeni `src/power_rail_validation/contracts/validation_v2.py`
- yeni `src/power_rail_validation/contracts/evidence_policy.py`
- yeni `src/power_rail_validation/contracts/schemas/*.v2.schema.json`
- yeni `src/power_rail_validation/contracts/schemas/approval-record.v2.schema.json`
- `src/power_rail_validation/contracts/contracts.csv`
- yeni `src/power_rail_validation/contracts/field_dictionary_v2.csv`
- `src/power_rail_validation/project_intake/__init__.py`
- yeni `src/power_rail_validation/project_intake/dispatch.py`
- yeni `src/power_rail_validation/project_intake/validator_v2.py`
- yeni `src/power_rail_validation/project_intake/project_intake_contract_v2.json`
- `src/power_rail_validation/cli.py`
- `pyproject.toml` package-data kayıtları
- yeni v2 fixture ve test dosyaları.

Motor entegrasyonu Oturum 3'e bırakılır:

- `rail_budget/calculator.py`
- `sequencing/evaluator.py`
- `fault_injection/runner.py`
- `regulator_review/reviewer.py`

`ncs01.py`, `integration/file_api.py` ve mevcut `report-model.schema.json` ilk D6 sözleşme diliminde kapsam dışındadır.

## 4. Minimum kabul testleri

### V1 koruma

1. Mevcut 404 test değişmeden PASS.
2. V1 fixture'ları aynı issue kodlarını üretir.
3. D5 ready fixture aynı `input_hash`, durum, limitation ve artifact sonuçlarını üretir.
4. V1 girdisine v2 alanı verildiğinde `UNEXPECTED_FIELD` korunur.
5. D4/D5 hash ankrajları değişmez.

### Sürüm dispatch

6. V1 entity, handoff ve intake v1 doğrulayıcıya gider.
7. V2 entity, handoff ve intake v2 doğrulayıcıya gider.
8. Bilinmeyen veya eksik sürüm fail-closed olur.
9. Karışık nested sürüm `CONTRACT_VERSION_MISMATCH` üretir.
10. Dosya API, assertion API ve CLI aynı dispatch'i kullanır.

### Kanıt semantiği

11. `DERIVED + REFERENCE_DESIGN` yapısal olarak geçerlidir ancak otomatik trusted değildir.
12. Lock'a bağlı `SOURCE_EXPLICIT + REFERENCE_DESIGN` teknik desteğe katkı verebilir.
13. Hash, locator veya context uyuşmazlığı `BLOCKED` olur.
14. Teknik entity içinde `PORTFOLIO_DECISION` context'i reddedilir; approval record ile teknik `PASS` üretmeye çalışan fixture bloklanır.
15. Approval record yalnız karar kapsamındaki kapıyı açar.
16. `ASSUMPTION + REFERENCE_DESIGN` sonucu `UNKNOWN` kalır.
17. `CONFLICT` her bağlamda `BLOCKED` kalır.

### Locator

18. PDF revision/page eksikliği bloklanır.
19. Git floating ref, kısa commit, traversal path veya ters line range bloklanır.
20. Yerel mutlak path/traversal bloklanır.
21. SHA-256 tam 64 küçük hex olmalı ve lock kaydıyla eşleşmelidir.

### Intake v2

22. `REFERENCE_DESIGN + STATIC + BUCK + PIN` fixture hazırlık `PASS` sonucu üretir.
23. Model yokluğu statik kapsamı engellemez.
24. `TRANSIENT` istenirse model yokluğu `BLOCKED` olur.
25. Placeholder MPN kabul edilmez.
26. `subject_id`, evidence context ve lock kayıtlarında aynı olmalıdır.
27. Ham kaynak repo dışında olabilir; normalize veri ve extraction script'i repo içinde hash-bound olmalıdır.
28. REP01 union kolu v1 eşdeğeriyle aynı readiness sonucunu üretir.

### Motor sınırı

29. V1 ile eşdeğer v2-normalize fixture aynı teknik sonucu üretir.
30. Dört motor yerel güven kümeleri yerine merkezi politikayı kullanır.
31. Decision record dört motorda da teknik `PASS` desteği veremez.
32. Reference context çıktı manifestinde kaybolmadan korunur.

## 5. Uygulama sırası

1. V1 golden/regression testlerini sabitle.
2. V2 şemalarını, locator union'ını ve approval record'u ekle.
3. Merkezi evidence policy ve negatif testleri ekle.
4. Contract dispatcher'ı bağla.
5. Intake v2 dispatcher ve reference subject kolunu ekle.
6. CLI ve package-data testlerini tamamla.
7. Motor boundary adapter'larını Oturum 3'te uygula.

Bu sıra düz enum genişletmesi riskini kaldırır, v1 davranışını korur ve lock için önerilen exact-BUCK referansını motorlara minimum değişiklikle vermeyi mümkün kılar.
