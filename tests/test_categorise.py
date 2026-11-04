import pytest

from ml.categorise import RULES, match_rule, merchant_key, normalise


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("UPI/ZOMATO LTD/412345678901/Paid via PhonePe", "ZOMATO LTD PAID VIA PHONEPE"),
        ("UPI-SWIGGY-swiggy@icici-ICIC0000001-412345678901-Order", "SWIGGY SWIGGY ORDER"),
        ("NEFT-HDFCN52023012345678-HOSTEL FEE-ST JOSEPHS", "HOSTEL FEE ST JOSEPHS"),
        ("ACH/SALARY/ACME TECH/NOV2025", "SALARY ACME TECH"),
        ("IMPS/P2A/412345678901/KAVYA NAIR/HDFC0001234/rent 05-06-2025", "KAVYA NAIR RENT"),
        ("POS 4591XXXXXX1234 DMART AVENUE SUPE 12MAR25", "DMART AVENUE SUPE"),
        ("TO TRANSFER-UPI/DR/412345678901/RAHUL S/YESB0000001/rahul.s@okaxis", "RAHUL S RAHUL S"),
        ("NACH AUTO DEBIT/NETFLIX.COM/12 Nov 2025", "NETFLIX"),
    ],
)
def test_normalising_strips_prefixes_references_dates_and_handles(description, expected):
    assert normalise(description) == expected


def test_no_reference_number_handle_or_branch_code_survives():
    text = normalise("UPI/P2M/412345678901/zomato@okhdfcbank/YESB0000001/UTR 98765/2025-11-02")
    assert text == "ZOMATO"
    assert "@" not in text and "OKHDFCBANK" not in text


@pytest.mark.parametrize(
    ("description", "key"),
    [
        ("UPI/ZOMATO LTD/412345678901/Paid via PhonePe", "ZOMATO"),
        ("VISA POS 4321XXXXXXXX9876 AMAZON PAY INDIA PRIVATE LIMITED", "AMAZON PAY INDIA"),
        ("UPI/P2M/412345678901/zomato@hdfcbank/REFUND", "ZOMATO"),
        ("412345678901", ""),
    ],
)
def test_merchant_key_is_the_first_meaningful_part(description, key):
    assert merchant_key(description) == key


@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("UPI/SWIGGY/412345678901/Order", "Dining"),
        ("POS 4591XXXXXX1234 MOONLIGHT CAFE", "Dining"),
        ("UPI/OLA CABS/412345678901", "Transport"),
        ("IRCTC/ETICKET/412345678901", "Transport"),
        ("BIL/BPAY/000123456/BESCOM", "Utilities"),
        ("UPI/JIO PREPAID RECHARGE/412345678901", "Utilities"),
        ("NEFT-SUNRISE PG-PG FEE NOV", "Rent"),
        ("UPI/MYNTRA/412345678901", "Shopping"),
        ("NACH AUTO DEBIT/SPOTIFY INDIA", "Entertainment"),
        ("UPI/BLINKIT/412345678901", "Groceries"),
        ("ACH/STIPEND/ST JOSEPHS COLLEGE", "Income"),
        ("UPI/APOLLO PHARMACY/412345678901", "Healthcare"),
        ("UPI/COURSERA/412345678901", "Education"),
        ("ATM WDL/SBI ATM ANNA NAGAR/123456", "Cash"),
        # The more specific rule wins.
        ("UPI/P2M/412345678901/zomato@hdfcbank/REFUND", "Income"),
        ("NACH AUTO DEBIT/AMAZON PRIME", "Entertainment"),
        ("UPI/SWIGGY INSTAMART/412345678901", "Groceries"),
        ("UPI/FASTAG RECHARGE/412345678901", "Transport"),
        ("NEFT-ST JOSEPHS COLLEGE HOSTEL", "Rent"),
    ],
)
def test_rules_match_each_category(description, category):
    assert match_rule(normalise(description))[0] == category


def test_every_rule_category_has_a_matching_case_above():
    covered = {"Dining", "Transport", "Utilities", "Rent", "Shopping", "Entertainment", "Groceries", "Income", "Healthcare", "Education", "Cash"}
    assert {category for category, _ in RULES} == covered


def test_keywords_match_whole_words_only():
    assert match_rule(normalise("UPI/COCA COLA/412345678901")) is None  # OLA inside COLA
    assert match_rule(normalise("UPI/PARENTS/412345678901")) is None  # RENT inside PARENTS
