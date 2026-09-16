# hh-vacancy-bot — Telegram-бот «чистые вакансии hh.ru» (24/7 в облаке)

Присылаете боту [@clean_hh_vacancies_bot](https://t.me/clean_hh_vacancies_bot) любое
сообщение со ссылкой на вакансию hh.ru — получаете аккуратное текстовое
описание без мусора. LLM не используется, токены не тратятся.

## Текущая архитектура (бот работает без вашего ПК)
Бот целиком работает на **Cloudflare Workers** (бесплатный тариф, файл
`bot-worker.js`). Telegram сам присылает обновления по webhook, воркер сам
читает вакансии с hh.ru и отвечает. Компьютер, VPN и Python не нужны.

Схема: Telegram → webhook → Cloudflare Worker (`hhbot.andreykomissaroff.workers.dev`)
→ hh.ru → ответ в Telegram.

## Перенастройка с нуля (если придётся)
1. dash.cloudflare.com → Build → Compute → Workers & Pages → hhbot → Edit code →
   вставить `bot-worker.js` → Deploy.
2. Settings → Variables and Secrets → добавить Secret `BOT_TOKEN` (токен бота)
   и Secret `WEBHOOK_SECRET` (любая случайная строка).
3. Открыть один раз `https://hhbot.andreykomissaroff.workers.dev/setup` —
   должен ответить `{"ok":true,...}`.

## Что принимает бот
- Голую ссылку: `https://hh.ru/vacancy/136328870?hhtmFrom=favorite_vacancy_list`
- Шаринг из мобильного приложения hh (с подписью «Отправлено с помощью…»).
- Несколько ссылок в одном сообщении — обрабатывается каждая.

Query-параметры и сопутствующий текст отбрасываются: бот извлекает только
числовой ID вакансии.

## Как воркер читает вакансии
- Сначала пробует публичный API `https://api.hh.ru/vacancies/{id}` (без ключа;
  для зарубежных IP он часто отвечает 403).
- При отказе парсит HTML-страницу вакансии: заголовок, компанию, город, опыт,
  зарплату, ключевые навыки и описание (с очисткой от HTML).
- Длинные ответы режутся на несколько сообщений (лимит Telegram 4096).

## Локальная Python-версия (запасной вариант, `bot.py`)
Работает на вашем ПК, пока запущен процесс. Запуск:
```powershell
cd hh-vacancy-bot
$env:HH_BOT_TOKEN="ваш_токен"
$env:HH_TG_API_URL="https://hhbot.andreykomissaroff.workers.dev"  # прокси, т.к. api.telegram.org заблокирован
python bot.py
```
Важно: getUpdates (polling) конфликтует с webhook — перед запуском локальной
версии удалите webhook: открыть `…/setup` заново после остановки python-процесса.
