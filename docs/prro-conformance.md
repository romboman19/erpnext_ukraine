# Відповідність протоколу ПРРО

Базовий документ: «Опис API Фіскального Сервера (Єдине вікно подання
електронної звітності)», редакція 08.08.2025, оприлюднений на
<https://cabinet.tax.gov.ua/help/api.html>. XSD `check01`, `zrep01` і `ticket01`
в репозиторії побайтово відповідають архіву ДПС, наданому разом зі специфікацією.

Вимоги до браузерної та ESC/POS-форми чека, обов'язкових реквізитів і
допустимого сервісного footer наведені в
[receipt-printing-requirements.md](receipt-printing-requirements.md).

## Реалізовано

| Вимога ДПС | Реалізація |
|---|---|
| REST `/doc`, `/pck`, `/cmd` | `ua_fiscal.fiscal_client.FiscalClient` |
| attached CAdES-E-T, сертифікат у CMS | online-підпис через signer з `tsp: signature` |
| без content-time-stamp | signer приймає лише `signature` або `false` |
| offline без обов'язкової позначки часу | CAdES-BES для кожного offline-документа |
| DeviceRegister | стабільний 64-символьний SHA-256 ID; звірка з ДПС перед кожним online-документом |
| безперервний `ORDERNUM` | distributed lock + атомарний allocator; `NextLocalNum` звіряється перед кожним `/doc` |
| сталий `CASHDESKNUM` | окремий `register_local_number` з форми 1-ПРРО |
| offline fiscal number | session range + локальний номер сесії + офіційний CRC32 алгоритм |
| hash-chain | SHA-256 від підписаного попереднього фінансового документа |
| offline limits | 36 годин/сесія та 168 годин/місяць, блокування при перевищенні |
| offline package | length-prefixed LE, до 100 документів, ціль <200 KiB, підпис пакета |
| повернення online | `TransactionsRegistrarState` з `OfflineSessionId`/`OfflineSeed` перед `/pck` |
| невизначена доставка | `Uncertain` + `DocumentInfoByLocalNum`; без сліпого повтору `/doc` |
| остаточна відмова | номер звільняється лише якщо DPS state очікує його і це останній локальний ledger-запис |
| втрата зв'язку після передачі | `REVOKELASTONLINEDOC` і контрольований перехід offline |
| продаж/повернення | зв'язок з первинним фіскальним документом і реквізити повернення |
| Z-звіт | агрегування продажів/повернень, способів оплат, податків і службових сум |
| квитанції | unwrap CMS із криптографічною перевіркою перед зміною стану ledger |
| XML | локальна перевірка кожного документа за офіційною XSD до підпису |
| податки/округлення | `SIGN` за ознакою включення в ціну; `RNDSUM`/`NORNDSUM` за даними Sales Invoice |

## Автоматизовані перевірки

- офіційний CRC32 вектор `5008.3.4758`;
- структура offline-пакета й ліміт 100 документів;
- online: DeviceRegister/state → open → sale → return → Z → close, зі звіркою номера перед усіма 5 документами;
- timeout після передачі → offline begin → sale → Z/close/end → `/pck` → online;
- відхилення ticket → server reconciliation → контрольований повтор того самого `ORDERNUM`;
- XSD-валідація кожного XML у наскрізних тестах;
- XSD-regression для невключеного ПДВ і офіційної моделі заокруглення;
- signer roundtrip, tamper detection, TSP policy, HTTP auth/limits;
- terminal timeout/unknown без другого `sale`;
- ESC/POS кирилиця, QR, copy marker та LAN allowlist.

## Зовнішній acceptance gate

Автоматизовані тести не можуть замінити реальний КЕП, TSP конкретного КНЕДП,
зареєстрований ПРРО, банківський ECR і фізичний принтер. Перед першою бойовою
зміною треба виконати розділ «Go-live» з production runbook. Тестові документи
мають `<TESTING>true</TESTING>` і юридично не є фіскальними.
