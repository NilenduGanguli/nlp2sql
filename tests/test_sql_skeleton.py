from agent.sql_skeleton import sql_skeleton


def test_strips_string_literals():
    sql = "SELECT * FROM CUSTOMERS WHERE name = 'Alice' AND city = 'NYC'"
    s = sql_skeleton(sql)
    assert "Alice" not in s
    assert "NYC" not in s
    assert "?" in s


def test_strips_numeric_literals():
    sql = "SELECT * FROM ORDERS WHERE amount > 100 AND id = 42"
    s = sql_skeleton(sql)
    assert "100" not in s
    assert "42" not in s


def test_normalizes_whitespace_and_case():
    a = sql_skeleton("SELECT  *\nFROM\tCustomers   WHERE id=1")
    b = sql_skeleton("select * from CUSTOMERS where id = 2")
    assert a == b


def test_preserves_keywords_and_identifiers():
    s = sql_skeleton("SELECT c.id, c.name FROM KYC.CUSTOMERS c WHERE c.STATUS = 'A'")
    assert "select" in s
    assert "kyc.customers" in s
    assert "c.status" in s


def test_two_queries_with_different_literals_match():
    a = sql_skeleton("SELECT * FROM CUSTOMERS WHERE risk = 'HIGH'")
    b = sql_skeleton("SELECT * FROM CUSTOMERS WHERE risk = 'LOW'")
    assert a == b
