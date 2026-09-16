// Прокси к api.telegram.org для обхода блокировки (развернуть на Cloudflare Workers).
// После деплоя адрес воркера (https://<имя>.<аккаунт>.workers.dev) указать
// боту в переменной окружения HH_TG_API_URL.
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = "https://api.telegram.org" + url.pathname + url.search;
    const resp = await fetch(target, {
      method: request.method,
      headers: { "Content-Type": "application/json" },
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    });
    return new Response(resp.body, {
      status: resp.status,
      headers: { "Content-Type": "application/json" },
    });
  },
};
