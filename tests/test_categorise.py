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
        ("POS 4591XXXXXX1234 ANNAPOORNA PROVISION AND GENERAL STORES", "ANNAPOORNA PROVISION"),
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


def test_rules_and_model_beat_rules_alone_on_held_out_merchants():
    import pandas as pd

    from ml.categorise import train
    from scripts.train_categoriser import SEED, accuracies, held_out_split

    fit_rows, held_out = held_out_split(pd.read_csv(SEED))
    rules_only, combined = accuracies(train(list(fit_rows["description"]), list(fit_rows["category"])), held_out)

    print(f"\nHeld-out {len(held_out)} rows. Rules alone: {rules_only:.1%}. Rules + model: {combined:.1%}.")
    assert combined > rules_only


def test_the_shipped_model_loads_with_the_pinned_scikit_learn():
    import ml.categorise

    assert ml.categorise.MODEL is not None
    keys, suggestions = ml.categorise.categorise(["POS 4591XXXXXX1234 NEW HEALTH MEDICALS PUNE"], {})
    assert suggestions[keys[0]].source == "model" and suggestions[keys[0]].category == "Healthcare"


def test_a_correction_beats_rules_and_model():
    from ml.categorise import categorise

    descriptions = ["UPI/ZOMATO/412345678901", "POS 4591XXXXXX1234 NEW HEALTH MEDICALS PUNE"]
    keys, suggestions = categorise(descriptions, {"ZOMATO": "Groceries", "NEW HEALTH MEDICALS": "Shopping"})

    assert keys == ["ZOMATO", "NEW HEALTH MEDICALS"]
    assert [(suggestions[k].category, suggestions[k].source) for k in keys] == [("Groceries", "correction"), ("Shopping", "correction")]


def test_low_confidence_and_nameless_rows_need_a_category():
    from ml.categorise import categorise

    keys, suggestions = categorise(["UPI/RAHUL S/412345678901", "412345678901"], {"": "Dining"})
    assert [suggestions[k].category for k in keys] == [None, None]


def test_a_broken_or_mismatched_model_file_falls_back_to_rules(tmp_path, monkeypatch):
    import joblib

    import ml.categorise
    from ml.categorise import categorise, load_model

    (tmp_path / "broken.joblib").write_bytes(b"not a model")
    joblib.dump({"model": None, "sklearn": "0.0.1"}, tmp_path / "old.joblib")
    assert load_model(tmp_path / "broken.joblib") is None
    assert load_model(tmp_path / "old.joblib") is None

    monkeypatch.setattr(ml.categorise, "MODEL", None)
    keys, suggestions = categorise(["UPI/ZOMATO/412345678901", "POS 4591XXXXXX1234 NEW HEALTH MEDICALS PUNE"], {})
    assert [(suggestions[k].category, suggestions[k].source) for k in keys] == [("Dining", "rule"), (None, "none")]


def test_a_payment_to_a_person_is_rarely_given_a_confident_category():
    # Transfers to people have no category a rule or the model could know; a confident guess would be a
    # wrong one the user might not notice. (It was 19 of 280 when seed rows contained people's names.)
    from ml.categorise import categorise

    first = ["RAHUL", "ANITA", "KAVYA", "ARJUN", "PRIYA", "VIKRAM", "MEERA", "SURESH", "DIVYA", "KARTHIK", "LAKSHMI", "NAVEEN", "POOJA", "SANJAY"]
    last = ["S", "K", "NAIR", "IYER", "REDDY", "SHARMA", "MENON", "RAO", "PILLAI", "DAS", "GUPTA", "JOSHI", "BOSE", "PATEL", "SINGH", "VERMA", "KUMAR", "NAIDU", "SHETTY", "KHAN"]
    people = [f"UPI/{a} {b}/412345678901/" for a in first for b in last]
    _, suggestions = categorise(people, {})

    guessed = [key for key, s in suggestions.items() if s.category]
    print(f"\n{len(guessed)} of {len(people)} people given a category: {guessed}")
    assert len(guessed) <= len(people) * 0.02


@pytest.mark.parametrize(
    "description",
    [
        "IMPS/P2A/412345678901/KAVYA NAIR/HDFC0001234/rent oct",  # person-to-account transfer
        "UPI/P2A/412345678901/ARJUN MENON/SBIN0001234",
        "UPI/ravi.kumar@oksbi/412345678901",  # a name at a personal Google Pay handle
        "UPI/98XXXXXX21@ybl/412345678901/coffee",  # a phone-number handle
        "UPI/RAHUL S/412345678901/",  # looks like a name: 2-3 plain words
        "UPI/ANITA K/412345678913/split dinner",
        "UPI/LAKSHMI VENKATARAMAN IYER/412345678913",
    ],
)
def test_transfers_to_people_are_recognised(description):
    from ml.categorise import categorise

    keys, suggestions = categorise([description], {})
    assert suggestions[keys[0]].person


@pytest.mark.parametrize(
    "description",
    [
        "UPI/ZOMATO LTD/412345678901/Paid via PhonePe",
        "UPI-SWIGGY-swiggy@icici-ICIC0000001-412345678901-Order",
        "UPI/SRI GANAPATHI TIFFIN ROOM/412345678910",  # a business word
        "POS 4591XXXXXX1234 NEW HEALTH MEDICALS PUNE",
        "UPI/sharma.kirana@okbizaxis/412345678901",  # a Google Pay business handle
        "UPI/q123456789@ybl/412345678901/GREEN VALLEY ORGANICS",  # a PhonePe QR code
        "UPI/SPEEDWAY CABS/412345678901",
        "IMPS/P2A/412345678901/ACME TECH PVT LTD/HDFC0001234",  # P2A to a company account
        "ACH/SALARY/ACME TECH",
        "BIL/BPAY/000123456/BESCOM",
    ],
)
def test_businesses_are_not_mistaken_for_people(description):
    from ml.categorise import categorise

    keys, suggestions = categorise([description], {})
    assert not suggestions[keys[0]].person
