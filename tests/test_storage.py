import pandas as pd

from ta_research.storage import duckdb_store as store


def _sample_prices():
    return pd.DataFrame([
        dict(symbol="AAA", date=pd.Timestamp("2023-01-03").date(),
             open=10, high=11, low=9, close=10.5, volume=1000),
        dict(symbol="AAA", date=pd.Timestamp("2023-01-04").date(),
             open=10.5, high=12, low=10, close=11.8, volume=1200),
    ])


def test_upsert_is_idempotent(tmp_path):
    con = store.connect(tmp_path / "t.duckdb")
    df = _sample_prices()
    store.upsert_prices(con, df, source="stooq")
    store.upsert_prices(con, df, source="stooq")  # re-run same day
    n = con.execute("SELECT count(*) FROM prices_raw").fetchone()[0]
    assert n == 2, "re-running must not duplicate rows"


def test_two_sources_coexist(tmp_path):
    con = store.connect(tmp_path / "t.duckdb")
    df = _sample_prices()
    store.upsert_prices(con, df, source="stooq")
    store.upsert_prices(con, df, source="fmp")
    n = con.execute("SELECT count(*) FROM prices_raw").fetchone()[0]
    assert n == 4, "same bar from two sources must coexist for cross-check"


def test_fx_upsert(tmp_path):
    con = store.connect(tmp_path / "t.duckdb")
    fx = pd.DataFrame([
        dict(date=pd.Timestamp("2023-01-03").date(), rate=1.34),
        dict(date=pd.Timestamp("2023-01-04").date(), rate=1.35),
    ])
    store.upsert_fx(con, fx, series="FXUSDCAD", source="boc")
    store.upsert_fx(con, fx, series="FXUSDCAD", source="boc")
    n = con.execute("SELECT count(*) FROM fx_rates").fetchone()[0]
    assert n == 2
