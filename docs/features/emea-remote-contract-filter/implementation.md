# Implementation report: emea-remote-contract-filter

Sonuç: COMPLETE (koordinatör bağımsız olarak doğruladı: `python -m pytest -q` → 256 passed; gerçek DB'nin bir kopyası üzerinde `reevaluate --dry-run` implementer'ın çıktısıyla aynı sonucu verdi; `data/jobs.sqlite3` sha256 önce/sonra aynı; kaynak dosyalarda kontrol karakteri yok).

## Değişen dosyalar
- `jobhunter/locations.py`: `regions` bölümü (`SECTIONS`, `LocationTable.regions`, doğrulama, varsayılan EMEA), `_classify_alt` → `_scan_alt` + aynı sınıflandırma mantığı (davranış aynı), `RegionResult`, `region_of`.
- `jobhunter/evaluate.py`: `SEARCH_REJECT_PREFIX`, `DEFAULT_ROLES`, `ENGAGEMENT_VALUES`, `PERMANENT_VALUES`, `_ENG_TEXT`, `_PAY_RX`, `search_config`, `validate_search`, `role_ok`, `engagement`, `pay_quote`, `_negative_geo`, `_evaluate_search`; `FTC` `_KW`'ye eklendi; `validate_profile` uyarı listesi döndürür ve `search` + `worldwide_or_b2b` çatışmasında hata verir; `evaluate` `search` varsa arama yoluna gider (eski yol ve `ROLE` değişmedi).
- `jobhunter/db.py`: `employment_type`, `salary` kolonları (SCHEMA + `NEW_COLUMNS`), `upsert` yazar; `user_version` değişmedi.
- `jobhunter/collectors.py`: Ashby `employment_type` + `salary`, Lever `categories.commitment`; Greenhouse değişmedi.
- `jobhunter/cli.py`: uyarılar ve `validate_search`, `_search_group`/`_group`/`_cell`/`_search_row`/`_search_table`, `report(..., search=None)`, `Elenen:` başlığında `arama profili: Y` (Y>0), `reevaluate` arama profili özeti.
- `config/locations.yaml`: `Africa` regional'a, 19 EMEA ülkesi restricted'a, `regions: EMEA` bölümü ("Georgia" bilerek eklenmedi).
- `config/profile.yaml`: `worldwide_or_b2b` satırı kaldırıldı, `search:` bloğu eklendi (varsayılan rol listeleri açık yazılı).
- `README.md`, `docs/ARCHITECTURE.md`.
- Yeni `tests/test_search_profile.py` (74 test). Mevcut 182 test değiştirilmedi.

## Kabul kriterleri
AC1–AC13 karşılandı; kanıt `tests/test_search_profile.py` (yapılandırma doğrulama ve `Profile error` rc 1, rol politikası 10+9 başlık, uzaktan testi, ekran görüntüsü fikstürleri `country` + `unknown` + "ülke bazlı EMEA", US ve bare Remote retleri, metin kısıtları, çalışma şekli pozitif/negatif ifadeler ve çökme testleri, Türkiye istisnası, sözleşme süresi, skor sırası, rapor tablosu ve gruplar, `-rejected.md`, `list --rejected`, `reevaluate` yalnızca 4 sezgisel alanı değiştirir ve dry-run yazmaz, eski DB migrasyonu ve Ashby/Lever MockTransport testleri) ve 182 eski testin değişmeden geçmesi.

## V1 doğrulaması (canlı, tek GET)
- Ashby (supabase, 69 ilan): `employmentType` alanı var (değer `FullTime` ×69); `compensation.compensationTierSummary` alanı var (hepsi None). "Contract" değeri bu board'da gözlenmedi, Ashby dokümanından.
- Lever (spotify, 86 ilan): `categories.commitment` alanı var (`Permanent` ×82, None ×3, `Short Term` ×1). "Short Term" bilerek contract sayılmadı.

## Kontroller
- `python -m pytest -q`: 256 passed, exit 0 (182 eski + 74 yeni).
- `feature_state.py check`: geçti.
- Gerçek DB (kopya üzerinde, dry-run): değişen 977 / 4566 (`irrelevant -> ineligible: 422`, `ineligible -> irrelevant: 19`, `global_unconfirmed -> ineligible: 14`); **Arama profili: geçen 0 / 4566**. En büyük ret grupları: Uzaktan belirtilmemiş 471, B2B/Contract sinyali yok 157, On-site/hybrid 107, EMEA dışı: United States 100, Bölge belirsiz 32, EMEA dışı: Canada 32, Americas 15, Singapore 11, Australia 6, India 5. Gerçek DB dosyası değişmedi.
- Çalıştırılmadı: gerçek `scan` (ağ), `--dry-run`'sız `reevaluate`, lint (repoda yok).

## Sapmalar ve notlar
1. Dry-run DB'nin kopyası üzerinde çalıştırıldı (`connect()` her zaman `migrate` çağırır ve yeni kolonları eklerdi); gerçek DB yeni kolonları bir sonraki normal çalıştırmada alır.
2. `source_name`, `source_url`, `apply_url` DB'de saklanmıyor; rapor `.get` ile okur, yoksa `source`/`url` (plandaki gibi). Bu alanlar `job-discovery-sources` ile saklanacak.
3. Plan, geçersiz-`regions` ve South Africa testlerinin `tests/test_locations.py`'ye eklenmesini söylüyordu; mevcut test dosyaları değişmesin diye yeni `tests/test_search_profile.py` içine kondu.
4. `Arama profili: geçen K / M` içinde M tüm saklı satırlar (excluded/irrelevant dahil); K, durumu excluded/irrelevant/ineligible olmayanlar (planda M tanımsızdı).
5. Planın sessiz kaldığı kararlar: `turkiye` ilanı UK/EU metin kısıtıyla `Explicit geographic` olarak elenir (eski yolla aynı); ikinci rapor tablosu `section=='turkiye'` veya `reviewed_status=='VERIFIED_TR'` satırlarını listeler; eşlemesiz `search:` anahtarı hatadır ("mod kapalı" değil); birden çok alan eşleşirse `employment_type` alanı metne baskındır; kanıt parçası eşleşme çevresinde 30 karakter.
6. Plan kuralları section 4'teki hiçbir örnekte çatışmadı.

## Sınırlamalar
- Yalnızca ATS board'larıyla liste boş ya da neredeyse boş kalır (plan öngördü; gerçek veride 0/4566).
- Saklı satırlarda `employment_type` bir sonraki `scan`'e kadar NULL.
- Çalışma şekli ifadeleri sezgisel; bazı gerçek contract ilanları kaçabilir (elenenler gruplarda, `-rejected.md` ve `list --rejected` ile denetlenebilir). `FTC` büyük/küçük harfe duyarlı. "Greater London Area" gibi bilinmeyen ek kelime içeren konum `Bölge belirsiz` sayılır. "remote" kelimesi olmayan salt "Worldwide" konumu uzaktan testinde elenir (tasarım gereği). Ücret alıntısı yanlış tutar yakalayabilir ("$80k ARR"), sıralamada kullanılmaz.
- Kalan iş: yok. `job-discovery-sources` başlamadan önce bu özelliğe göre yeniden planlanmalı (`employment_type`/`salary` doldurma, migration birleştirme).

---
# Kullanıcı isteği sonrası değişiklik: B2B/contract şartı kaldırıldı (fast path, koordinatör)

Gerekçe: gerçek veride B2B/contract şartıyla geçen ilan 0 (yalnızca ATS board'ları). Kullanıcı "b2b kaldır" dedi.

- `search.engagement: []` artık geçerli ve çalışma şekli testini kapatır (`evaluate.search_config`). Boş olmayan liste ve varsayılan davranış (anahtar yoksa `[b2b, contract]`) değişmedi.
- Test kapalıyken B2B/contract kanıtı olan ilan yine +10 skor alır (kanıt aranır, ret nedeni olmaz); belirtilen kısa sözleşme (< `minimum_contract_months`) yine elenir.
- Ülke bazlı EMEA notu B2B'yi varsaymaz: kanıt varsa "(B2B/contract kanıtı var)", yoksa "(B2B/contract ifadesi yok: çoğu zaman ülkede ikamet ister)".
- Rapor başlığı: şart kapalıyken `## EMEA · Remote (N)`, açıkken `## EMEA · Remote · B2B/Contract (N)` (`cli._search_table(rows, engagement_required)`, `report(..., engagement_required=True)`).
- `config/profile.yaml`: `engagement: []` ve açıklama.
- `tests/test_search_profile.py`: 9 yeni test (kapalı modda EMEA remote geçer, bölge/remote testleri sürer, B2B kanıtı +10, kısa sözleşme elenir, ülke notu, varsayılan ve açık liste şartı korur, rapor başlığı).
- Kontroller: `python -m pytest -q` → 265 passed. Gerçek DB'nin kopyası üzerinde `reevaluate`: arama profilini geçen 157 / 4566 (raporlanmamış+başvurulmamış 61); gerçek DB dosyası değişmedi (sha256 aynı). Gerçek DB'ye henüz uygulanmadı.
- Bilinen zayıflık: uzaktan testi, konumda remote yazmasa da başlıkta/açıklamada herhangi bir remote ifadesi varsa geçer (planın AC3'ü); tek şehir konumlu hibrit ilanlar (ör. GetYourGuide "Berlin") bu yüzden listeye girebilir.
