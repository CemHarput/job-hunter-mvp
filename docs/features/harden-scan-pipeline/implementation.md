# Implementation report: harden-scan-pipeline

Sonuç: COMPLETE (koordinatör testleri bağımsız olarak yeniden çalıştırdı: `python -m pytest -q` → 39 passed).

## Değişen dosyalar
- `jobhunter/db.py`: `jobs`'a `reviewed_status`, `reviewed_at`, `reported_at`, `reported_via`; `migrate()` (connect sonunda, `user_version` ile tek seferlik legacy taşıma, `idx_jobs_reported`); `HEURISTIC_STATUSES`, `REVIEW_STATUSES`, `effective_status`, `record_review`, `unreported_jobs`, `mark_reported`. `upsert` imzası aynı, yeni alanlara dokunmaz.
- `jobhunter/evaluate.py`: `\b` regex'ler, `TITLE_NEGATIVE`/`TITLE_BACKEND`, yeni coğrafi negatifler (kısaltma duyarlı), `contract_months`, düşük öncelik cezası, `validate_profile`.
- `jobhunter/cli.py`: `load_env`, `parse_published`, `review` alt komutu, yeni `report()`, `run_scan` int döner, `--env-file/--baseline/--max-age-days`, `send_email` doğrulaması, `list` etkin durum.
- `config/profile.yaml`, `.gitignore` (`reports/archive/`), `README.md`, `docs/ARCHITECTURE.md`.
- `tests/test_scan.py` (yeni, 35 test).

## Kabul kriterleri
AC1–AC14 karşılandı; kanıt: `tests/test_scan.py` içindeki ilgili testler (review_survives_rescan, review_validation_and_alias, verified_excluded_company_stays_out, email_failure_keeps_jobs_unreported, archive_written, baseline, age_filter, irrelevant/relevant_titles, tech_word_boundaries, new_negative/not_negative, ineligible_section_and_low_priority, contract_months, low_priority_and_policy, load_env, smtp_validation, legacy_migration) ve mevcut 4 test.

## Kontroller
- `python -m pytest -q`: 39 passed, exit 0 (implementer ve koordinatör).
- `feature_state.py check`: geçti.
- Çalıştırılmadı: gerçek ATS taraması, gerçek SMTP gönderimi (testlerde mock).
- `pip install -e ".[test]"` çalıştırıldı (yalnızca pyproject'te tanımlı bağımlılıklar); `job_hunter_mvp.egg-info/` oluştu ve `.gitignore`'da yok.

## Sapmalar ve sınırlamalar
- `--baseline`, o taramada ilk görülen tüm işleri işaretler; excluded/irrelevant olanlar zaten sorgudan elenir.
- `contract_months`: sayı, 40 karakter içinde "contract/fixed-term/temporary/duration/term" geçerse sayılır; "probation/notice/trial/experience" yanındakiler yok sayılır.
- Reviewed `INELIGIBLE` işler de "Elenen ilanlar" bölümünde görünür.
- Küçük harfli "us only" bilinçli olarak yakalanmaz (plan riski).
- `CLAUDE.md` iş akışına `review` komutu eklenmedi (kullanıcı onayı gerekir); `.env.example` değişmedi.

---
# Review düzeltmeleri (/feature-fast --feature harden-scan-pipeline, P2 + P3)

Koordinatör (ana oturum) tarafından uygulandı; önceki review.md tarihsel kanıttır, yeni `/feature-review` gerekir.

## Düzeltilen bulgular
- **P2 sözleşme regex'i** (`evaluate.py`): `CONTRACT_CTX` kaldırıldı; süre yalnızca `contract/fixed-term/temporary` ile bitişikse (`3-month contract`, `contract of 3 months`, `contract length: 3 months`, `term of ...`) sayılır. "long-term … first 3 months" ve "interview duration … 2 months" artık sözleşme süresi değildir.
- **P3 VERIFIED_TR kanıtı** (`db.latest_review`, `cli._review_text/_eligibility_text`, `list_jobs`): raporda inceleyen, kanıt URL'si ve alıntı gösterilir; kanıtsız legacy kayıtlar "legacy, kanıt yok, yeniden doğrula" (list'te "VERIFIED_TR (kanıt yok)") etiketlenir; sezgisel not ayrıca yazılır.
- **P3 AC10 testi:** elenen işe ayrı URL, kontrol "Elenen ilanlar" bölümü içinde.
- **P3 eksik testler:** run_scan'de başvurulmuş iş, profildeki `max_posting_age_days`, `review --url` çoklu eşleşme, `--baseline` + başarısız `--email`, BOM'lu `.env`, çok ülkeli Türkiye ifadesi, review kanıtı raporu, legacy etiketi, sözleşme negatifleri.
- **P3 BOM:** `load_env` `utf-8-sig` kullanır.
- **P3 `.gitignore`:** `*.egg-info/` eklendi.
- **P3 çok ülkeli ifade** (`evaluate.py`): negatif eşleşmeden sonraki 60 karakterde Turkey/Türkiye/Turkiye geçiyorsa eşleşme atlanır.
- Ek: arşiv dosya adı mikro saniye çözünürlüklü (aynı saniyede ezilme); README/ARCHITECTURE yazım hataları.

## Değişen dosyalar
`jobhunter/evaluate.py`, `jobhunter/db.py`, `jobhunter/cli.py`, `tests/test_scan.py`, `.gitignore`, `README.md`, `docs/ARCHITECTURE.md`.

## Kontroller
`python -m pytest -q`: 49 passed, exit 0.

## Bilerek yapılmayanlar
`unreported_jobs` heuristik `irrelevant` işleri elle VERIFIED_TR olsa da raporlamaz (review gözlemi, bulgu değil); `ROLE` `\b` davranışı belgelenmedi. Gerçek ATS/SMTP çalıştırılmadı.

---
# Review #2 düzeltmeleri (/feature-fast --feature harden-scan-pipeline, P2 + P3)

Koordinatör (ana oturum) uyguladı; review.md (#2) tarihsel kanıttır, yeni `/feature-review` gerekir.

## Düzeltilen bulgular
- **P2 Türkiye istisnası** (`evaluate.py`): `NEGATIVE_RE` artık `(regex, geo)` çiftleri; on-site/hybrid kalıbı istisnaya hiç tabi değil. Geo kalıplarda yalnızca eşleşmenin hemen ardından gelen aynı ülke listesi (`TR_LIST`: `, / & or and the us uk eu eea canada europe …` ve ardından Turkey/Türkiye) kurtarır; yeni cümle, `;`, olumsuzluk ("cannot hire in", "not open to") artık kurtarmaz.
- **P3 kısa sözleşme kalıpları:** `CONTRACT_BEFORE` `for`, `(`, `,` ve `role/position/engagement/assignment` ara kelimelerini, `CONTRACT_AFTER` baştaki `(`/`,`'yi destekler ("contract (3 months)", "contract for 3 months", "contract position for 3 months", "Contract role, 3 months", "3 months (contract)"). `CONTRACT_SKIP` kelime sınırlı ve aynı cümleyle sınırlı (geniş dilim alınır, pencere kenarında kesilen "Indus|trial" yanlış isabeti yok).
- **P3 test boşluğu:** `test_turkiye_does_not_rescue_restrictions` (4 senaryo), `test_turkiye_in_same_country_list_rescues` (3), `test_common_short_contract_phrasings` (9), `test_contract_skip_is_word_bound_and_sentence_bound`.
- **Gözlem düzeltmeleri:** legacy taşımada `eligibility_status` `upper()` ile normalize (`Verified_TR` → `VERIFIED_TR`, test var); `evidence_quote` raporda tek satıra indirilir (test var).

## Değişen dosyalar
`jobhunter/evaluate.py`, `jobhunter/db.py`, `jobhunter/cli.py`, `tests/test_scan.py`.

## Kontroller
`python -m pytest -q`: 68 passed, exit 0 (ilk çalıştırmada 2 hata vardı: biri testin kendi yanlış beklentisi (6 ay < 6 değil), biri pencere kenarında "Indus|trial" yanlış isabeti; ikisi de düzeltildi).

## Bilerek yapılmayanlar
"3-month contract-to-hire" hâlâ ineligible olur (ürün kararı). Gerçek ATS/SMTP çalıştırılmadı.

---
# Review #3 düzeltmesi (/feature-fast --feature harden-scan-pipeline): Türkiye istisnasının kaldırılması

Kullanıcı kararıyla (üç review turunda istisna her seferinde yeni köşe durum üretti) Türkiye istisnası tamamen kaldırıldı; review #3'ün P2 ve P3 bulguları böylece geçersiz hale gelir.

## Değişiklikler
- `evaluate.py`: `TR_LIST`, `TR_NAME` ve `(regex, geo)` çiftleri kaldırıldı; `NEGATIVE_RE` yeniden düz regex listesi. Bir coğrafya/on-site eşleşmesi, ilanda Türkiye geçse bile `ineligible` yapar. Bu ilanlar gizlenmez: raporun "Elenen ilanlar" bölümünde eşleşen ifadeyle görünür (manuel inceleme için).
- Gözlem: `evaluate.py` içindeki yanıltıcı "wider slice" yorumu netleştirildi (davranış değişmedi).
- `tests/test_scan.py`: istisnaya dayanan testler tersine çevrildi; `test_turkiye_never_rescues_restrictions` (10 senaryo: "EU only, Turkey excluded", "Türkiye not eligible", çok ülkeli listeler vb. hep `ineligible`, `candidate_tr` değil) ve `test_multi_country_list_listed_in_rejected_section` eklendi.

## Kontroller
`python -m pytest -q`: 70 passed, exit 0.

## Bilinen takas
"Remote in the EU or Türkiye" gibi Türkiye'ye gerçekten açık çok ülkeli ilanlar da `ineligible` olur; kullanıcı raporun "Elenen ilanlar" bölümünden bunları elle görmelidir. Çözülmemiş notlar: "Temporary 3-month relocation support" nadir yanlış pozitifi, `notes` alanının tek satıra indirilmemesi (kozmetik).
