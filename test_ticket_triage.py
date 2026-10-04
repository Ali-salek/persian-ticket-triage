# -*- coding: utf-8 -*-
"""تست‌های معیارهای پذیرش. اجرا: pytest -v"""

import contextlib
import csv
import io
import os

import pytest

import ticket_triage as s


class TestNormalize:

    def test_arabic_letters_and_digits(self):
        assert s.normalize_text("ك ي ١٢٣ ۴۵") == "ک ی 123 45"

    def test_zwnj_variants_are_equal(self):
        a = s.normalize_text("باز نمی\u200cشود")
        b = s.normalize_text("باز نمیشود")
        c = s.normalize_text("باز نمی شود")

        assert a == b
        assert b == c

    def test_whitespace_and_tatweel(self):
        assert s.normalize_text("سلاـم    \n\t دنیا") == "سلام دنیا"


class TestClassification:

    def row(self, text):
        return s.process_ticket({"id": "X", "text": text})

    def test_payment_is_billing(self):
        result = self.row(
            "پرداخت انجام شد اما سفارش ثبت نشده، فوری بررسی کنید"
        )

        assert result["category"] == "billing"

    def test_urgent_phrases_are_high(self):
        texts = [
            "لطفاً فوری جواب بدهید",
            "سایت قطع شده است",
            "دوبار کسر شده",
        ]

        for text in texts:
            assert self.row(text)["urgency"] == "high", text

    def test_ambiguous_is_general(self):
        assert self.row("سلام، یک سؤال داشتم")["category"] == "general"

    def test_account_ticket(self):
        result = self.row("رمز عبورم کار نمی‌کند و حسابم قفل شده")

        assert result["category"] == "account"

    def test_no_double_counting(self):

        cls = s.classify(s.normalize_text("دوبار کسر شد"))

        assert cls.scores["billing"] == 6

    def test_word_boundary(self):

        cls = s.classify(s.normalize_text("حسابداری"))

        assert cls.scores["account"] == 0

    def test_not_urgent_phrase(self):
        result = self.row("سوال درباره پیگیری سفارش، فوری نیست")

        assert result["urgency"] != "high"


class TestSummary:

    def test_max_12_words(self):
        long_text = " ".join(["واژه"] * 50)

        assert len(s.summarize(long_text).split()) <= 12

    def test_short_text_unchanged(self):
        assert s.summarize("سلام وقت بخیر") == "سلام وقت بخیر"


class TestFile:

    def test_bad_line_only_warns(self, tmp_path):
        src = tmp_path / "t.jsonl"

        src.write_text(
            '{"id": "A", "text": "پرداخت من انجام نشد"}\n'
            "این خط خراب است {\n"
            '{"id": "B", "text": "رمز عبورم را فراموش کردم"}\n',
            encoding="utf-8",
        )

        tickets, warnings = s.read_jsonl(str(src))
        assert len(tickets) == 2
        assert len(warnings) == 1
        assert "خط 2" in warnings[0]

    def test_csv_columns_and_encoding(self, tmp_path):
        out = tmp_path / "r.csv"

        rows = [s.process_ticket(t) for t in s.SAMPLE_TICKETS]
        s.write_csv(rows, str(out))

        with open(out, "rb") as f:
            assert f.read(3) == b"\xef\xbb\xbf"

        with open(out, encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f))

        for col in ["id", "category", "urgency", "summary", "action", "confidence",]:
            assert col in header

    def test_cli_text_mode(self, capsys):
        code = s.main(["--text", "پرداخت من دوبار کسر شد"])

        captured = capsys.readouterr()

        assert code == 0
        assert "billing" in captured.out
