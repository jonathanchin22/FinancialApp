from conftest import SGML_OFX
from finapp.cli import main


def test_cli_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    csv_file = tmp_path / "chase.csv"
    csv_file.write_text("Date,Description,Amount,Balance\n2026-11-03,PAY,100.00,5000.00\n")
    ofx_file = tmp_path / "td.ofx"
    ofx_file.write_text(SGML_OFX)

    assert main(["--db", db, "init"]) == 0
    main(["--db", db, "import-csv", str(csv_file), "--account-key", "csv:chase:checking", "--name", "Chase",
          "--type", "checking", "--currency", "USD", "--institution", "Chase", "--country", "US"])
    main(["--db", db, "import-ofx", str(ofx_file)])
    main(["--db", db, "import-ofx", str(ofx_file)])
    main(["--db", db, "fx-set", "2026-11-20", "1.40", "--source", "manual"])
    main(["--db", db, "networth", "--on", "2026-11-20"])
    out = capsys.readouterr().out
    assert "2 new" in out and "2 duplicates skipped" in out
    assert "Net worth: 7,824.86 USD" in out  # 5000 + 3954.80 / 1.40
