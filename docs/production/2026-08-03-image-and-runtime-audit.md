# Production image and runtime audit — 2026-08-03

## Висновок

Global FIFO, Multi-FOP і Commission лишаються єдиною основою продажу в
`erpnext_ua`. Їх не вилучено і не замінено. Поточний deployment image, однак,
не є clean production image і не може бути використаний для нового rollout без
cutover.

## Image findings

- production site має сім installed apps: `frappe`, `erpnext`,
  `ukrainian_integrations`, `print_designer`, `erpnext_ua`, `flow` і
  `erpnext_consignment_and_commission`;
- обидві legacy apps уже дублюють код єдиного `erpnext_ua`;
- runtime `erpnext_ua` має версію 0.4.0, тоді як approved main має 0.16.0;
- фактичний host pipeline копіює локальні apps в image і прибирає `.git`, але
  не зберігає source revision як обов'язковий OCI contract;
- bench virtualenv має несумісний graph: Frappe 16.26.3 вимагає
  `Click~=8.3.1`, Flow → LiteLLM 1.83.7 вимагає `click==8.1.8`;
- read-only counts не знайшли жодного Flow Agent, Trigger, Run, Session,
  Knowledge Base, Provider або Model. Десять Flow Tool є seed records.

## Runtime findings

Production `queue-short` і `queue-long` не працюють з 2026-08-02. Їхні останні
запуски завершилися через `No space left on device`; Redis Queue у той момент
перейшов у `MISCONF` через невдалий RDB save. На час аудиту filesystem уже мав
приблизно 12 GiB вільного місця, а Redis знову звітував останній BGSAVE як
успішний, проте workers залишалися stopped. Production services під час цього
аудиту не запускалися і не змінювалися.

Окремий ризик — близько 7 GiB Docker build cache. Його очищення допустиме лише
після перевірки точних targets; volumes, active images і database backups не
можна включати в cleanup.

## Go-live gates

1. Відновити production workers контрольованою operational процедурою й
   підтвердити черги, scheduler, Redis persistence та запас диска.
2. Побудувати clean image через `deployment/production` і отримати green
   `Production Image` CI.
3. На UAT-копії зняти legacy apps і невикористаний Flow лише з installed-apps,
   не виконуючи `uninstall-app`.
4. Двічі виконати migration, image validator, GSF/CC diagnostics і §43
   acceptance на анонімізованих production-даних.
5. Лише після підписів бухгалтера, fiscal/provider owner та операційного
   власника планувати production maintenance window.
