# Plan: harden-scan-pipeline

Doğrulanmış olgular okunan koddan geliyor; kararlar "Karar", varsayımlar bölüm 4'te işaretli.

## 1. Davranış ve kabul kriterleri

- **AC1:** `python -m jobhunter review --job-id N|--url U --status S --reviewer R [--evidence-url] [--evidence-quote] [--notes]` `review_events`'e satır yazar, `jobs.reviewed_status` ve `jobs.reviewed_at` alanlarını günceller. Sonraki `scan` bu alanlara ve `review_events`'e dokunmaz.
- **AC2:** `VERIFIED_TR` yalnızca `https` `--evidence-url` ve boş olmayan `--evidence-quote` ile kabul edilir; eksikse kayıt yazılmaz, sıfırdan farklı çıkış kodu. İzinli durumlar: `VERIFIED_TR`, `GLOBAL_UNCONFIRMED`, `UNKNOWN`, `INELIGIBLE`. `REMOTE_WORLDWIDE_UNCONFIRMED` → `GLOBAL_UNCONFIRMED`. İş bulunamazsa veya `--url` birden çok işle eşleşirse hata.
- **AC3:** Rapor ve `list` "etkin durum" kullanır: `reviewed_status` doluysa o, yoksa sezgisel `eligibility_status`. Sezgisel sonuç `excluded` ise her zaman dışlanmış kalır.
- **AC4:** `jobs`'a `reported_at` ve `reported_via` eklenir. Rapor, `reported_at IS NULL` olan tüm uygun ve başvurulmamış işleri listeler. İşler yalnızca rapor dosyası yazıldıktan (ve `--email` ise e-posta başarıyla gittikten) sonra raporlanmış sayılır. E-posta hatasında işler sonraki taramada yeniden listelenir ve komut 1 ile çıkar.
- **AC5:** Her rapor ayrıca `reports/archive/scan-<UTC YYYYmmddTHHMMSSZ>.md` olarak arşivlenir; `.gitignore`'a `reports/archive/` eklenir.
- **AC6:** `scan --baseline` o taramada ilk kez görülen işleri `reported_at=now`, `reported_via='baseline'` ile işaretler, listelemez, yalnızca sayısını yazar. Önceki raporlanmamış işler normal raporlanır. `--email` ile birlikte kullanılabilir.
- **AC7:** `scan --max-age-days N` veya profilde `max_posting_age_days` (varsayılan kapalı) ile `published_at` N günden eski işler raporlanmaz (işaretlenmez, yalnızca sayı gösterilir). Tarihsiz/ayrıştırılamayanlar korunur.
- **AC8:** Regex düzeltmeleri: `java` ≠ `javascript`; `rag`/`aws` yalnızca tam kelime; `llms?` → `llm`; başlıkta frontend/front-end/ios/android/mobile/react native/flutter/designer/ux/ui geçip backend/back-end/platform/distributed/python/`\bjava\b`/llm/`\bai\b` geçmiyorsa `irrelevant`.
- **AC9:** Yeni negatif kalıplar → `ineligible`: "must be US-based", "US-based candidates", "authorized to work in the US/UK/EU/Canada", "EU only", "remote within the EU/EEA". Kısaltmalar (`US`, `U.S.`, `USA`, `UK`, `EU`) büyük/küçük harfe duyarlı, tam adlar duyarsız ("join us only" ineligible olmaz). Yalın "Europe" kısıtlama değil. Kanıt metni eşleşen ifadeyi içerir.
- **AC10:** `ineligible` işler raporda "Elenen ilanlar (ineligible)" bölümünde şirket, başlık, URL ve eşleşen ifadeyle listelenir; raporlanma işareti aynı şekilde konur.
- **AC11:** `minimum_contract_months`: ilanda açıkça daha kısa sözleşme yazıyorsa (aralıkta üst sınır; "probation"/"notice" yok sayılır) `ineligible`; diğerlerinde "Sözleşme süresi: Doğrulanmadı". `low_priority_companies`: skor −20 (alt sınır 0), "Düşük öncelik" etiketi, dışlanmaz. `exclude_company_policy`: yalnızca `all_roles`; başka değerde `scan` açık hatayla durur.
- **AC12:** Repo kökündeki `.env` (veya `--env-file`) stdlib ile yüklenir; mevcut ortam değişkenleri ezilmez. `SMTP_USER` var ve `SMTP_PASSWORD` yoksa ağa bağlanmadan `SMTP configuration missing: SMTP_PASSWORD` hatası. Geçersiz `SMTP_PORT` anlaşılır hata verir.
- **AC13:** Eski DB'ler otomatik taşınır: eksik sütunlar eklenir; mevcut tüm işler `reported_via='legacy'`, `reported_at=first_seen_at`; sezgisel olmayan `eligibility_status` değerleri `reviewed_status`'a taşınır (`review_events`'e reviewer=`legacy-migration`, notes="structured evidence missing; re-verify"); taşıma idempotent.
- **AC14:** Hariç tutulan şirketler, başvurulmuş işler ve "candidate_tr hiçbir zaman VERIFIED_TR değildir" kuralı korunur; mevcut testler geçer.

## 2. İlgili dosyalar ve değişiklik

- **`jobhunter/db.py`:** `jobs`'a `reviewed_status`, `reviewed_at`, `reported_at`, `reported_via`. `migrate(conn)` (`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`, `PRAGMA user_version < 1` ise eski veri taşıma, `connect()` sonunda çağrılır). `idx_jobs_reported` `SCHEMA`'da değil `migrate`'te oluşturulur. `upsert` `reviewed_*`/`reported_*`'a dokunmaz; imza/dönüş aynı. Yeni: `HEURISTIC_STATUSES`, `REVIEW_STATUSES`, `effective_status(row)`, `record_review(...)`, `unreported_jobs(conn)`, `mark_reported(conn, ids, via, at)`.
- **`jobhunter/evaluate.py`:** `ROLE`/`TECH` `\b` ile; `TITLE_NEGATIVE`/`TITLE_BACKEND`; `NEGATIVE` orijinal metne (kısaltma duyarlı, tam ad `(?i:...)`); `contract_months(text)`, düşük öncelik cezası, `validate_profile(profile)`. Dönüş anahtarları aynı: `status`, `evidence`, `score`, `tech`.
- **`jobhunter/cli.py`:** `load_env`, `parse_published`; `report()` yeni bölümlerle; `run_scan` int döner; `review` alt komutu; `--env-file`, `--baseline`, `--max-age-days`; `main` `sys.exit(rc or 0)`; `list_jobs` etkin durumu gösterir; `send_email` doğrulaması.
- **`config/profile.yaml`, `.gitignore`, `README.md`, `docs/ARCHITECTURE.md`:** güncellenir. `CLAUDE.md` ve agent dosyaları bu planda değiştirilmez.

## 3. Uygulama adımları

1. `db.py`: şema, `migrate`, yardımcılar; `upsert` korunur.
2. `evaluate.py`: sıra excluded → irrelevant → ineligible (coğrafya) → ineligible (kısa sözleşme) → mevcut candidate_tr/global_unconfirmed/unknown → skor + düşük öncelik cezası.
3. `cli.py`: `load_env`, e-posta doğrulaması; `run_scan` akışı: profil doğrula → topla + upsert (ilk görülen id'ler) → `--baseline` ise işaretle → `unreported_jobs` (başvurulanlar çıkar, yaş filtresi) → aday/ineligible ayır → `latest.md` + arşiv → e-posta → hata: stderr, rc=1, işaret yok; başarı: `mark_reported`. `review` ve `list`.
4. Yapılandırma ve dokümanlar.
5. Testler: `tests/test_core.py` veya `tests/test_scan.py`; `run_scan` testleri `collect` ve `send_email`'i monkeypatch ile değiştirir.

## 4. Doğrulama

Komut: `python -m pytest -q` (kurulum `pip install -e ".[test]"`).

| AC | Test | Beklenen |
|---|---|---|
| AC1 | `record_review` VERIFIED_TR, sonra `upsert` | `reviewed_status` korunur, `review_events` 1 satır |
| AC2 | URL/alıntısız VERIFIED_TR; alias | hata, satır yok; alias GLOBAL_UNCONFIRMED |
| AC3 | Excluded şirkete VERIFIED_TR | rapor ve `list`'te yok |
| AC4 | 1. tarama e-posta hata, 2. başarılı | rc=1 ve `reported_at` NULL; 2'de rapor dolu; 3'te boş |
| AC5 | Tarama sonrası | `reports/archive/scan-*.md` var |
| AC6 | `--baseline` | liste boş, `reported_via='baseline'`, sonraki taramada yok |
| AC7 | 100g, 2g, tarihsiz; `max_age_days=30` | yalnızca 2g ve tarihsiz |
| AC8 | JavaScript Developer, Frontend, iOS → irrelevant; Senior Java, Backend → ilgili; "drag", "laws" → tech'te yok |
| AC9 | "Join us only…", "Remote worldwide" → değil; "Must be US-based", "authorized to work in the United States", "EU only" → ineligible; mevcut `test_ineligible` geçer |
| AC10 | Ineligible'lı tarama | "Elenen ilanlar" bölümü ve URL |
| AC11 | 3-month → ineligible; 12-month, "3 months probation, permanent" → değil; Supabase −20 + etiket; `some_roles` → ValueError |
| AC12 | tmp `.env`; mevcut değişken ezilmez; `SMTP_USER` var `SMTP_PASSWORD` yok → `ValueError`, `smtplib` çağrılmaz |
| AC13 | Eski şemalı DB, `connect()` iki kez | sütunlar var, `legacy`, hata yok |
| AC14 | Mevcut 4 test + excluded/applied | geçer |

**Riskler:** Kısaltma duyarlılığı küçük harfli "us only"u kaçırır (bilinçli takas). Başlık negatif listesi bazı full-stack işleri yanlış eleyebilir. Sözleşme regex'i yanılabilir; elenenler raporda göründüğü için etki sınırlı. `legacy` işareti eski raporların okunduğunu varsayar.

**Varsayımlar (doğrulanmadı):** `data/jobs.sqlite3` muhtemelen boş/yok. Eski DB'deki elle yazılmış VERIFIED_TR korunur (veri kaybı yok). `python-dotenv` eklenmez. Yaş filtresi varsayılan kapalı.

**Açık sorular (engelleyici değil):** `CLAUDE.md` iş akışına `review` komutunun eklenmesi kullanıcı onayı gerektirir; bu planda değiştirilmiyor. Agent'taki `REMOTE_WORLDWIDE_UNCONFIRMED` adı alias ile karşılanıyor.

READY
