#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
دستیار دسته‌بندی و اولویت‌بندی تیکت‌های فارسی (نسخهٔ baseline مبتنی بر قواعد)

جریان کار:
    JSONL → خواندن امن → نرمال‌سازی → دسته‌بندی → فوریت → خلاصه → اقدام → CSV

اجرا:
    python ticket_triage.py
    python ticket_triage.py --input tickets.jsonl --output report.csv
    python ticket_triage.py --text "رمز عبورم کار نمی‌کند و حسابم قفل شده"

وابستگی خارجی ندارد (فقط کتابخانهٔ استاندارد Python).
"""

from __future__ import annotations

import argparse
import csv
import json
import re

import sys
import unicodedata
from dataclasses import dataclass, field


MAX_SUMMARY_WORDS = 12
MIN_CATEGORY_SCORE = 3
AMBIGUITY_MARGIN = 2
HIGH_URGENCY_SCORE = 3
MEDIUM_URGENCY_SCORE = 1

CSV_COLUMNS = ["id", "category", "urgency", "summary", "action", "confidence", "evidence"]
URGENCY_RANK = {"high": 0, "medium": 1, "low": 2}


_CHAR_MAP = str.maketrans({
    "ي": "ی", "ى": "ی",
    "ك": "ک",
    "ۀ": "ه", "ة": "ه",
    "أ": "ا", "إ": "ا", "ٱ": "ا",

    **{ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")},
    **{ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")},
})


_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")


_INVISIBLE_RE = re.compile(r"[\u200d\u200e\u200f\u202a-\u202e\ufeff]")


_MI_PREFIX_RE = re.compile(r"(?<!\w)(ن?می) (?=\w)")


def normalize_text(text: str) -> str:
    """متن فارسی را به یک شکل استاندارد برای مقایسه تبدیل می‌کند."""

    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_CHAR_MAP)
    text = _DIACRITICS_RE.sub("", text)
    text = text.replace("\u200c", " ")
    text = _INVISIBLE_RE.sub("", text)
    text = text.lower()
    text = " ".join(text.split())

    text = _MI_PREFIX_RE.sub(r"\1", text)
    return text


CATEGORY_KEYWORDS: dict[str, dict[str, int]] = {
    "billing": {
        "دوبار کسر": 6, "دو بار کسر": 6, "چند بار کسر": 6,
        "بازپرداخت": 5, "برگشت وجه": 5, "استرداد": 4,
        "سفارش ثبت نشده": 2,
        "پرداخت": 3, "کسر": 3, "تراکنش": 3, "فاکتور": 3, "صورتحساب": 3,
        "درگاه": 3, "کد تخفیف": 3, "کیف پول": 3, "حساب بانکی": 3,
        "کارت بانکی": 2, "تخفیف": 2, "پول": 2, "هزینه": 1,
    },
    "technical": {
        "باز نمیشود": 4, "باز نمیشه": 4, "لود نمیشود": 4, "لود نمیشه": 4,
        "صفحه سفید": 4, "کار نمیکند": 3, "کار نمیکنه": 3,
        "خطا": 3, "ارور": 3, "error": 3, "باگ": 3, "کرش": 3, "هنگ": 3,
        "اپلیکیشن": 2, "سایت": 2, "وب سایت": 2, "بارگذاری": 2, "لود": 2,
        "نصب": 2, "آپدیت": 2, "بروزرسانی": 2, "قطع شده": 2,
        "دانلود": 1, "سرعت": 1,
    },
    "account": {
        "حساب کاربری": 5, "رمز عبور": 5, "حذف حساب": 5, "هک شده": 5,
        "پسورد": 4, "احراز هویت": 4, "وارد نمیشوم": 4, "وارد نمیشم": 4,
        "رمز": 3, "قفل": 3, "ورود": 3, "ثبت نام": 3, "کد تایید": 3,
        "تغییر شماره": 3, "اکانت": 3,
        "حساب": 2, "ایمیل": 2, "پروفایل": 2, "شماره موبایل": 2,
    },
}


URGENCY_SIGNALS: dict[str, int] = {

    "فوری": 3, "اضطراری": 3, "همین الان": 3, "بلافاصله": 3,
    "هرچه سریعتر": 3, "هر چه سریعتر": 3,
    "قطع شده": 3, "دوبار کسر": 3, "دو بار کسر": 3, "چند بار کسر": 3,
    "کلاهبرداری": 3, "هک": 3, "سوء استفاده": 3, "پولم کم شد": 3,

    "چند روز": 2, "بی جواب": 2, "شکایت": 2,
    "مشکل": 1, "منتظر": 1, "هنوز": 1, "پیگیری": 1,
    "دوباره": 1, "ناراضی": 1, "قفل": 1,

    "فوری نیست": -3, "عجله ای ندارم": -3, "عجله ندارم": -3,
}


CATEGORY_URGENCY_BONUS = {"billing": 1}


QUEUE_BY_CATEGORY = {
    "billing": "صف مالی",
    "technical": "صف فنی",
    "account": "صف حساب کاربری",
    "general": "صف عمومی",
}

ACTION_BY_CATEGORY_URGENCY = {
    ("billing", "high"): "بررسی فوری تراکنش و تماس با درگاه پرداخت",
    ("billing", "medium"): "بررسی تراکنش و پاسخ ظرف یک روز کاری",
    ("billing", "low"): "پاسخ با قالب استاندارد مالی",
    ("technical", "high"): "بازتولید مشکل و اطلاع فوری به تیم توسعه",
    ("technical", "medium"): "ثبت باگ و درخواست اطلاعات بیشتر (مرورگر/دستگاه)",
    ("technical", "low"): "ارسال راهنمای عیب‌یابی رایج",
    ("account", "high"): "بررسی امنیتی حساب و بازیابی فوری دسترسی",
    ("account", "medium"): "کمک به بازیابی رمز یا رفع قفل حساب",
    ("account", "low"): "ارسال راهنمای مدیریت حساب",
    ("general", "high"): "بررسی دستی توسط مسئول تیم (پیام فوری اما نامشخص)",
    ("general", "medium"): "بررسی دستی و تعیین دستهٔ درست",
    ("general", "low"): "پاسخ با قالب استاندارد",
}
DEFAULT_ACTION = "بررسی دستی توسط مسئول تیم"


_SUFFIXES = "ها|های|هام|ام|م|ت|ش|مان|تان|شان|ی|ای"


@dataclass(frozen=True)
class Keyword:
    text: str
    weight: int
    pattern: re.Pattern


def _compile_keywords(table: dict[str, int]) -> list[Keyword]:
    """جدول {کلیدواژه: وزن} را به لیست الگوهای آمادهٔ regex تبدیل می‌کند."""
    result = []

    for raw, weight in table.items():
        kw = normalize_text(raw)


        pattern = re.compile(r"(?<!\w)" + re.escape(kw) + rf"(?:{_SUFFIXES})?(?!\w)")
        result.append(Keyword(kw, weight, pattern))

    result.sort(key=lambda k: (-len(k.text), -k.weight))
    return result


_CATEGORY_MATCHERS = {cat: _compile_keywords(t) for cat, t in CATEGORY_KEYWORDS.items()}
_URGENCY_MATCHER = _compile_keywords(URGENCY_SIGNALS)


def find_matches(text: str, matchers: list[Keyword]) -> tuple[int, list[tuple[str, int]]]:
    """
    کلیدواژه‌های موجود در متن (نرمال‌شده) را پیدا می‌کند.
    خروجی: (مجموع امتیاز، [(کلیدواژه, وزن), ...])
    هر کلیدواژه حداکثر یک بار شمرده می‌شود.
    """

    used_spans: list[tuple[int, int]] = []
    matched: list[tuple[str, int]] = []
    total = 0

    for kw in matchers:


        for m in kw.pattern.finditer(text):

            start, end = m.span()

            overlaps = any(not (end <= us or start >= ue) for us, ue in used_spans)
            if overlaps:
                continue
            used_spans.append((start, end))
            matched.append((kw.text, kw.weight))
            total += kw.weight
            break
    return total, matched


@dataclass
class Classification:
    """نتیجهٔ دسته‌بندی؛ اگر بعداً از مدل زبانی استفاده کنید همین ساختار را برگردانید."""
    category: str
    confidence: float
    scores: dict[str, int] = field(default_factory=dict)
    matches: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    reason: str = ""


def classify(norm_text: str) -> Classification:
    """
    مراحل:
      1) برای هر دسته امتیاز و کلیدواژه‌های match‌شده را حساب می‌کنیم.
      2) اگر امتیاز برنده کم بود یا دو دستهٔ برتر تقریباً مساوی بودند → general.
      3) confidence = سهم برنده از کل امتیازها × قدرت شواهد (تا سقف ۱).
    """
    scores: dict[str, int] = {}
    matches: dict[str, list[tuple[str, int]]] = {}
    for cat, matchers in _CATEGORY_MATCHERS.items():
        scores[cat], matches[cat] = find_matches(norm_text, matchers)


    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_cat, best = ranked[0]
    second = ranked[1][1]
    total = sum(scores.values())

    if best < MIN_CATEGORY_SCORE:

        reason = "بدون کلیدواژهٔ آشکار" if total == 0 else "امتیاز ناکافی"
        confidence = 0.5 if total == 0 else 0.3
        return Classification("general", confidence, scores, matches, reason)

    if second >= MIN_CATEGORY_SCORE and best - second < AMBIGUITY_MARGIN:

        return Classification("general", 0.3, scores, matches, "دو دسته هم‌امتیاز")

    share = best / total
    strength = min(1.0, best / 6)
    confidence = round(share * strength, 2)
    return Classification(best_cat, confidence, scores, matches, "دستهٔ برنده")


def detect_urgency(norm_text: str, category: str) -> tuple[str, int, list[tuple[str, int]]]:
    """خروجی: (سطح فوریت، امتیاز کل، نشانه‌های پیداشده)"""
    score, matched = find_matches(norm_text, _URGENCY_MATCHER)
    score += CATEGORY_URGENCY_BONUS.get(category, 0)

    if score >= HIGH_URGENCY_SCORE:
        level = "high"
    elif score >= MEDIUM_URGENCY_SCORE:
        level = "medium"
    else:
        level = "low"
    return level, score, matched


_SENTENCE_SPLIT_RE = re.compile(r"[.!؟?\n]+")


def summarize(raw_text: str, max_words: int = MAX_SUMMARY_WORDS) -> str:
    """
    خلاصه = جملهٔ اول متن (اگر حداقل ۳ واژه داشت)، و در غیر این صورت کل متن،
    و در هر حال حداکثر max_words واژه. از متن اصلی (نه نرمال‌شده) ساخته می‌شود
    تا ظاهر متن برای اپراتور دست‌نخورده بماند.
    """
    cleaned = " ".join(raw_text.split())
    first_sentence = " ".join(_SENTENCE_SPLIT_RE.split(raw_text)[0].split())
    chosen = first_sentence if len(first_sentence.split()) >= 3 else cleaned

    words = chosen.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).rstrip("،,:؛; ") + "…"
    return chosen.rstrip("،,:؛; ")


def suggest_action(category: str, urgency: str) -> str:
    """صف و اقدام بعدی را از روی (دسته، فوریت) می‌سازد."""
    queue = QUEUE_BY_CATEGORY.get(category, "صف عمومی")
    action = ACTION_BY_CATEGORY_URGENCY.get((category, urgency), DEFAULT_ACTION)
    return f"{queue}: {action}"


def build_evidence(cls: Classification, urgency_score: int, urgency_hits: list[tuple[str, int]]) -> str:
    """توضیح قابل‌خواندن برای اینکه «چرا این نتیجه؟» (امتیازدهی قابل مشاهده)"""
    parts = []
    for cat, score in sorted(cls.scores.items(), key=lambda kv: -kv[1]):
        if score > 0:
            words = ", ".join(f"{w}:{wt}" for w, wt in cls.matches[cat])
            parts.append(f"{cat}={score} [{words}]")
    if not parts:
        parts.append("هیچ کلیدواژهٔ دسته‌ای پیدا نشد")
    parts.append(f"دلیل: {cls.reason}")
    if urgency_hits:
        parts.append("فوریت=" + str(urgency_score) + " [" + ", ".join(f"{w}:{wt}" for w, wt in urgency_hits) + "]")
    return " | ".join(parts)


def process_ticket(ticket: dict) -> dict:
    """یک تیکت (دیکشنری با کلیدهای id و text) را به یک ردیف گزارش تبدیل می‌کند."""
    raw_text = ticket["text"]
    norm = normalize_text(raw_text)

    cls = classify(norm)
    urgency, u_score, u_hits = detect_urgency(norm, cls.category)

    return {
        "id": ticket.get("id", ""),
        "category": cls.category,
        "urgency": urgency,
        "summary": summarize(raw_text),
        "action": suggest_action(cls.category, urgency),
        "confidence": f"{cls.confidence:.2f}",
        "evidence": build_evidence(cls, u_score, u_hits),
    }


def read_jsonl(path: str) -> tuple[list[dict], list[str]]:
    """
    فایل JSONL را خط‌به‌خط می‌خواند.
    خط خراب یا ناقص فقط یک «هشدار» تولید می‌کند و پردازش ادامه می‌یابد.
    خروجی: (تیکت‌های سالم، لیست هشدارها)
    """

    tickets: list[dict] = []

    warnings: list[str] = []

    with open(path, "r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                warnings.append(f"خط {line_no}: JSON خراب است ({e.msg}) و رد شد")
                continue
            if not isinstance(record, dict):
                warnings.append(f"خط {line_no}: باید یک شیء JSON باشد و رد شد")
                continue
            text = record.get("text")
            if not isinstance(text, str) or not text.strip():
                warnings.append(f"خط {line_no}: فیلد text خالی یا نامعتبر است و رد شد")
                continue

            record.setdefault("id", f"line-{line_no}")
            tickets.append(record)
    return tickets, warnings


def _safe_cell(value):
    """
    محافظت در برابر «CSV injection»: اگر متن با = + - @ شروع شود، Excel آن را
    فرمول تلقی می‌کند. یک آپاستروف اولش می‌گذاریم تا به‌عنوان متن دیده شود.
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):

        return "'" + value
    return value


def write_csv(rows: list[dict], path: str) -> None:
    """
    utf-8-sig یعنی UTF-8 همراه با BOM؛ Excel با آن فارسی را درست نشان می‌دهد.
    newline="" هم جلوی خط خالی اضافه در ویندوز را می‌گیرد.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)

        writer.writeheader()
        for row in rows:
            writer.writerow({k: _safe_cell(v) for k, v in row.items()})


SAMPLE_TICKETS = [
    {"id": "T-101", "customer": "سارا", "text": "پرداخت انجام شد اما سفارش ثبت نشده، فوری بررسی کنید"},
    {"id": "T-102", "customer": "علی", "text": "دوبار کسر شده از حسابم، لطفاً پیگیری کنید."},
    {"id": "T-103", "customer": "مینا", "text": "رمز عبورم کار نمی‌کند و حسابم قفل شده"},
    {"id": "T-104", "customer": "رضا", "text": "سایت قطع شده و هیچ صفحه‌ای باز نمی‌شود، همین الان رفع کنید"},
    {"id": "T-105", "customer": "نگار", "text": "اپلیکیشن هنگام ورود خطا می‌دهد"},
    {"id": "T-106", "customer": "امیر", "text": "سلام، یک سؤال داشتم"},
    {"id": "T-107", "customer": "لیلا", "text": "درخواست بازپرداخت دارم، فوری نیست ولی لطفاً پیگیری کنید"},
    {"id": "T-108", "customer": "کیان", "text": "پرداخت من با خطا مواجه شد و اپلیکیشن بسته شد"},
]


def print_result(row: dict) -> None:
    print(f"[{row['urgency']:<6}] {row['id']:<8} {row['category']:<10} "
          f"(اطمینان {row['confidence']})  {row['summary']}")


def main(argv: list[str] | None = None) -> int:

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="دسته‌بندی و اولویت‌بندی تیکت‌های فارسی")
    parser.add_argument("--input", help="مسیر فایل JSONL تیکت‌ها")
    parser.add_argument("--output", default="report.csv", help="مسیر CSV خروجی (پیش‌فرض: report.csv)")
    parser.add_argument("--text", help="فقط یک متن را پردازش و نتیجه را چاپ کن")
    args = parser.parse_args(argv)


    if args.text:
        row = process_ticket({"id": "CLI", "text": args.text})
        for key in CSV_COLUMNS:
            print(f"{key:<10}: {row[key]}")
        return 0


    if args.input:
        try:
            tickets, warnings = read_jsonl(args.input)
        except OSError as e:
            print(f"خطا: فایل ورودی خوانده نشد: {e}", file=sys.stderr)
            return 1
        for w in warnings:
            print(f"هشدار: {w}", file=sys.stderr)
    else:
        print("ورودی داده نشد؛ تیکت‌های نمونه پردازش می‌شوند.\n")
        tickets = SAMPLE_TICKETS

    rows = [process_ticket(t) for t in tickets]

    rows.sort(key=lambda r: URGENCY_RANK[r["urgency"]])

    for row in rows:
        print_result(row)

    write_csv(rows, args.output)
    print(f"\n{len(rows)} تیکت پردازش شد → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
