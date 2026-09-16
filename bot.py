# -*- coding: utf-8 -*-
"""Telegram-бот: чистое текстовое описание вакансии hh.ru по ссылке.

Вход — любой текст, содержащий ссылку вида hh.ru/vacancy/<id>
(с параметрами, подписью «Отправлено с помощью мобильного приложения» и т.п.).
Сначала пробуем публичный API hh.ru; если он недоступен (403 для зарубежных
IP), парсим HTML-страницу вакансии. LLM не используется — ноль токенов.
"""

import json
import logging
import os
import re
import sys

import requests
from bs4 import BeautifulSoup
import telebot

HH_API = "https://api.hh.ru/vacancies/{id}"
HH_PAGE = "https://hh.ru/vacancy/{id}"
HH_LINK = "https://hh.ru/vacancy/{id}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "ru"}
TG_LIMIT = 4000  # запас до лимита Telegram 4096

VACANCY_RE = re.compile(r"hh\.ru/vacancy/(\d+)", re.IGNORECASE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hh-bot")


def extract_vacancy_ids(text: str) -> list[str]:
    """Извлечь ID вакансий из произвольного текста: ссылка может нести
    query-параметры и быть окружена «мусором» мобильного шаринга."""
    return list(dict.fromkeys(VACANCY_RE.findall(text)))


# --- источник 1: публичный API hh.ru ---------------------------------------

def fetch_api(vacancy_id: str) -> dict | None:
    try:
        r = requests.get(HH_API.format(id=vacancy_id), headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            raise ValueError("Вакансия не найдена или удалена.")
        log.info("API вернул %s, переходим к парсингу страницы", r.status_code)
    except requests.RequestException as e:
        log.info("API недоступен (%s), переходим к парсингу страницы", e)
    return None


def format_from_api(v: dict) -> str:
    lines = [f"📌 {v.get('name', 'Без названия')}"]
    emp = (v.get("employer") or {}).get("name")
    if emp:
        lines.append(f"Компания: {emp}")
    addr = v.get("address") or {}
    city = addr.get("city") or (v.get("area") or {}).get("name") or ""
    if city:
        lines.append(f"Город: {city}")
    if (v.get("work_format") or {}).get("name"):
        lines.append(f"Формат: {v['work_format']['name']}")
    if (v.get("experience") or {}).get("name"):
        lines.append(f"Опыт: {v['experience']['name']}")
    salary = _fmt_salary(v.get("salary") or {})
    if salary:
        lines.append(f"Зарплата: {salary}")
    extra = ", ".join(x for x in ((v.get("schedule") or {}).get("name"),
                                  (v.get("employment") or {}).get("name")) if x)
    if extra:
        lines.append(f"График: {extra}")
    _append_skills_desc(lines, [k["name"] for k in v.get("key_skills", [])],
                        v.get("description") or "")
    lines.append(f"\nОригинал: {HH_LINK.format(id=v.get('id'))}")
    return "\n".join(lines)


def _fmt_salary(s: dict) -> str | None:
    parts = []
    if s.get("from"):
        parts.append(f"от {s['from']:,.0f}".replace(",", " "))
    if s.get("to"):
        parts.append(f"до {s['to']:,.0f}".replace(",", " "))
    if not parts:
        return None
    gross = " (до вычета)" if s.get("gross") else " (на руки)"
    return " ".join(parts) + f" {s.get('currency', '')}{gross}"


# --- источник 2: парсинг HTML-страницы вакансии -----------------------------

def fetch_page(vacancy_id: str) -> str:
    r = requests.get(HH_PAGE.format(id=vacancy_id), headers=HEADERS, timeout=20)
    if r.status_code == 404:
        raise ValueError("Вакансия не найдена или удалена.")
    r.raise_for_status()
    return r.text


def format_from_page(html: str, vacancy_id: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    def qa(name):
        el = soup.select_one(f'[data-qa="{name}"]')
        return el.get_text(" ", strip=True) if el else None

    ld_tag = soup.find("script", type="application/ld+json")
    ld = json.loads(ld_tag.string) if ld_tag and ld_tag.string else {}

    title = (soup.h1.get_text(" ", strip=True) if soup.h1 else None) or ld.get("title", "Вакансия")
    lines = [f"📌 {title}"]

    company = qa("vacancy-company-name") or (ld.get("hiringOrganization") or {}).get("name")
    if company:
        lines.append(f"Компания: {company}")

    loc = ld.get("jobLocation") or {}
    addr = loc.get("address") or {}
    loc_el = soup.select_one('[data-qa="vacancy-view-location"]')
    city = addr.get("addressLocality") or (loc_el.get_text(" ", strip=True) if loc_el else None)
    if city:
        lines.append(f"Город: {city}")

    exp = qa("work-experience-text")
    if exp:
        lines.append(f"Опыт: {exp.replace('Опыт работы :', '').strip()}")

    # зарплата: блок data-qa="vacancy-salary" (или текст «Уровень дохода …»)
    salary = None
    sal_el = soup.select_one('[data-qa="vacancy-salary"]')
    if sal_el:
        salary = sal_el.get_text(" ", strip=True)
    else:
        for span in soup.select("span"):
            t = span.get_text(" ", strip=True)
            if t.startswith("Уровень дохода"):
                salary = t
                break
    if salary and "не указан" not in salary:
        lines.append(f"Зарплата: {salary}")

    _append_skills_desc(
        lines,
        _page_key_skills(html),
        ld.get("description") or "",
    )
    lines.append(f"\nОригинал: {HH_LINK.format(id=vacancy_id)}")
    return "\n".join(lines)


def _page_key_skills(html: str) -> list[str]:
    """keySkills лежат во встроенном экранированном JSON состояния страницы."""
    m = re.search(r'&#34;keySkills&#34;:\{&#34;keySkill&#34;:\[(.*?)\]', html)
    if not m:
        return []
    raw = m.group(1).replace("&#34;", '"').replace("&amp;", "&")
    return re.findall(r'"([^"]+)"', raw)


# --- общий вывод -------------------------------------------------------------

def _append_skills_desc(lines: list[str], skills: list[str], desc_html: str) -> None:
    if skills:
        lines.append("\nКлючевые навыки:\n" + "\n".join(f"• {s}" for s in skills))
    desc = _clean_html(desc_html)
    if desc:
        lines.append(f"\nОписание:\n{desc}")


def _clean_html(html: str) -> str:
    """Убрать HTML-разметку hh, сохранив абзацы и списки."""
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    text = soup.get_text()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        while len(para) > limit:
            parts.append(para[:limit])
            para = para[limit:]
        if len(cur) + len(para) + 1 > limit:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


def handle_vacancy(vacancy_id: str) -> list[str]:
    v = fetch_api(vacancy_id)
    if v is not None:
        text = format_from_api(v)
        if v.get("archived"):
            text = text.replace("📌", "📌 (архивная)", 1)
    else:
        text = format_from_page(fetch_page(vacancy_id), vacancy_id)
    return split_message(text)


# --- Telegram ----------------------------------------------------------------

def main() -> None:
    token = os.environ.get("HH_BOT_TOKEN")
    if not token:
        log.error("Не задана переменная окружения HH_BOT_TOKEN")
        sys.exit(1)
    proxy = os.environ.get("HH_BOT_PROXY")  # напр. socks5://127.0.0.1:1080
    if proxy:
        telebot.apihelper.proxy = {"https": proxy, "http": proxy}
        log.info("Используется прокси %s", proxy)
    # Обход блокировки: API-адрес можно проксировать через Cloudflare Worker
    # (worker.js в этом каталоге), напр. HH_TG_API_URL=https://x.y.workers.dev
    api_base = os.environ.get("HH_TG_API_URL")
    if api_base:
        telebot.apihelper.API_URL = api_base.rstrip("/") + "/bot{0}/{1}"
        log.info("Telegram API через %s", api_base)
    bot = telebot.TeleBot(token)

    @bot.message_handler(commands=["start", "help"])
    def _help(m):
        bot.reply_to(
            m,
            "Пришлите сообщение со ссылкой на вакансию hh.ru — например:\n"
            "https://hh.ru/vacancy/12345678\n\n"
            "Можно просто переслать шаринг из мобильного приложения hh.",
        )

    @bot.message_handler(func=lambda m: True, content_types=["text"])
    def _text(m):
        ids = extract_vacancy_ids(m.text or "")
        if not ids:
            bot.reply_to(m, "В сообщении нет ссылки на вакансию hh.ru. "
                            "Пришлите текст с hh.ru/vacancy/…")
            return
        for vid in ids:
            try:
                bot.send_chat_action(m.chat.id, "typing")
                for part in handle_vacancy(vid):
                    bot.send_message(m.chat.id, part)
            except ValueError as e:
                bot.send_message(m.chat.id, str(e))
            except Exception as e:
                log.exception("Ошибка обработки вакансии %s", vid)
                bot.send_message(m.chat.id, f"Не удалось получить вакансию: {e}")

    log.info("Бот запущен, ожидание сообщений…")
    bot.infinity_polling(timeout=30, skip_pending=True)


if __name__ == "__main__":
    main()
