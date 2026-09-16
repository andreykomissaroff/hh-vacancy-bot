// Полный Telegram-бот «чистые вакансии hh.ru», работающий 24/7 на Cloudflare
// Workers (бесплатный тариф). Ваш ПК не участвует.
//
// Настройка (один раз, в dashboard Cloudflare → воркер hhbot → Settings →
// Variables and Secrets → Add):
//   BOT_TOKEN (тип Secret) = токен от @BotFather
//   WEBHOOK_SECRET (тип Secret) = любая случайная строка, напр. my-secret-123
//
// После деплоя открыть один раз:
//   https://hhbot.andreykomissaroff.workers.dev/setup
// — воркер зарегистрирует webhook у Telegram. Готово.

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const TG_LIMIT = 4000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Регистрация webhook (открыть один раз после деплоя).
    if (url.pathname === "/setup") {
      const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/setWebhook`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: `https://${url.hostname}/webhook`,
          secret_token: env.WEBHOOK_SECRET,
          drop_pending_updates: true,
        }),
      });
      return new Response(await r.text(), { headers: { "Content-Type": "application/json" } });
    }

    // Telegram присылает сюда обновления.
    if (url.pathname === "/webhook" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      await handleUpdate(await request.json(), env);
      return new Response("ok");
    }

    return new Response("hh-vacancy-bot worker is running");
  },
};

async function handleUpdate(update, env) {
  const msg = update.message;
  if (!msg || !msg.text) return;
  const chatId = msg.chat.id;

  if (/^\/(start|help)/.test(msg.text)) {
    return send(chatId, env,
      "Пришлите сообщение со ссылкой на вакансию hh.ru — например:\n" +
      "https://hh.ru/vacancy/12345678\n\n" +
      "Можно просто переслать шаринг из мобильного приложения hh.");
  }

  const ids = [...new Set((msg.text.match(/hh\.ru\/vacancy\/(\d+)/gi) || [])
    .map((s) => s.match(/(\d+)$/)[1]))];
  if (!ids.length) {
    return send(chatId, env,
      "В сообщении нет ссылки на вакансию hh.ru. Пришлите текст с hh.ru/vacancy/…");
  }
  for (const id of ids) {
    try {
      const text = await formatVacancy(id);
      for (const part of split(text)) await send(chatId, env, part);
    } catch (e) {
      await send(chatId, env, `Не удалось получить вакансию: ${e.message}`);
    }
  }
}

async function send(chatId, env, text) {
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
  });
}

// --- получение вакансии: сначала API hh, затем парсинг HTML-страницы ---

async function formatVacancy(id) {
  let r = await fetch(`https://api.hh.ru/vacancies/${id}`, { headers: { "User-Agent": UA } });
  if (r.ok) return formatApi(await r.json());
  if (r.status !== 404) {
    r = await fetch(`https://hh.ru/vacancy/${id}`, { headers: { "User-Agent": UA, "Accept-Language": "ru" } });
    if (r.ok) return formatPage(await r.text(), id);
    if (r.status === 404) throw new Error("вакансия не найдена или удалена");
    throw new Error(`hh.ru вернул ${r.status}`);
  }
  throw new Error("вакансия не найдена или удалена");
}

function formatApi(v) {
  const L = [`📌 ${v.name || "Без названия"}`];
  if (v.employer?.name) L.push(`Компания: ${v.employer.name}`);
  if (v.address?.city || v.area?.name) L.push(`Город: ${v.address?.city || v.area.name}`);
  if (v.work_format?.name) L.push(`Формат: ${v.work_format.name}`);
  if (v.experience?.name) L.push(`Опыт: ${v.experience.name}`);
  const s = v.salary;
  if (s && (s.from || s.to)) {
    const parts = [];
    if (s.from) parts.push(`от ${s.from}`);
    if (s.to) parts.push(`до ${s.to}`);
    L.push(`Зарплата: ${parts.join(" ")} ${s.currency || ""}${s.gross ? " (до вычета)" : " (на руки)"}`);
  }
  const extra = [v.schedule?.name, v.employment?.name].filter(Boolean).join(", ");
  if (extra) L.push(`График: ${extra}`);
  skillsAndDesc(L, (v.key_skills || []).map((k) => k.name), v.description || "");
  L.push(`\nОригинал: https://hh.ru/vacancy/${v.id}`);
  return L.join("\n");
}

function formatPage(html, id) {
  const text = (re, group = 1) => {
    const m = html.match(re);
    return m ? stripTags(m[group]).trim() : null;
  };
  const ld = (() => {
    const m = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);
    try { return m ? JSON.parse(m[1]) : {}; } catch { return {}; }
  })();

  const title = text(/<h1[^>]*data-qa="vacancy-title"[^>]*>([\s\S]*?)<\/h1>/) || ld.title || "Вакансия";
  const L = [`📌 ${title}`];
  const company = text(/data-qa="vacancy-company-name"[^>]*>([\s\S]*?)<\/a>/) || ld.hiringOrganization?.name;
  if (company) L.push(`Компания: ${stripTags(company).trim()}`);
  const city = ld.jobLocation?.address?.addressLocality;
  if (city) L.push(`Город: ${city}`);
  const exp = text(/data-qa="vacancy-experience"[^>]*>([\s\S]*?)<\/span>/);
  if (exp) L.push(`Опыт: ${exp}`);
  const salary = text(/data-qa="vacancy-salary"[^>]*>([\s\S]*?)<\/div>/);
  if (salary && !/не указан/.test(salary)) L.push(`Зарплата: ${salary}`);

  const skills = (() => {
    const m = html.match(/&#34;keySkills&#34;:\{&#34;keySkill&#34;:\[([\s\S]*?)\]/);
    if (!m) return [];
    return (m[1].replace(/&#34;/g, '"').replace(/&amp;/g, "&").match(/"[^"]+"/g) || [])
      .map((s) => s.slice(1, -1));
  })();
  skillsAndDesc(L, skills, ld.description || "");
  L.push(`\nОригинал: https://hh.ru/vacancy/${id}`);
  return L.join("\n");
}

function skillsAndDesc(L, skills, descHtml) {
  if (skills.length) L.push("\nКлючевые навыки:\n" + skills.map((s) => "• " + s).join("\n"));
  const d = cleanHtml(descHtml);
  if (d) L.push(`\nОписание:\n${d}`);
}

// --- утилиты ---

function cleanHtml(html) {
  return (html || "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<li[^>]*>/gi, "\n• ")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function stripTags(s) {
  return cleanHtml(s);
}

function split(text, limit = TG_LIMIT) {
  if (text.length <= limit) return [text];
  const parts = [];
  let cur = "";
  for (const para of text.split("\n")) {
    let p = para;
    while (p.length > limit) { parts.push(p.slice(0, limit)); p = p.slice(limit); }
    if (cur.length + p.length + 1 > limit) { parts.push(cur); cur = p; }
    else cur = cur ? cur + "\n" + p : p;
  }
  if (cur) parts.push(cur);
  return parts;
}
