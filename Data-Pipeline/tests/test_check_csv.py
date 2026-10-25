"""check_csv.py against CSV-POLICY.md, on small CSVs written here.

    python -m unittest tests.test_check_csv -v      # from Data-Pipeline/
"""
import shutil, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_csv


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def check(self, text, encoding="utf-8"):
        p = self.tmp / "t.csv"
        p.write_text(text, encoding=encoding)
        return check_csv.check(p)

    def test_people_columns(self):
        for col in ("author", "authors", "authorEmail", "commit author", "commit-author", "AuthorName", "added_by", "e_mail", "userName"):
            self.assertEqual(self.check(f"id,{col}\n1,x\n"), [f"people column '{col}'"], col)

    def test_ok_columns(self):
        for col in ("checked_by", "ended_by", "checkedBy", "symbol_name", "authored_at", "byte_count", "owner_team"):
            self.assertEqual(self.check(f"id,{col}\n1,x\n"), [], col)

    def test_bom_doesnt_hide_the_first_header(self):
        self.assertEqual(self.check("author,id\n1,x\n", encoding="utf-8-sig"), ["people column 'author'"])

    def test_emails(self):
        self.assertEqual(self.check("id,note\n1,mail ann@corp.io\n"), ["line 2: email in column note"])
        self.assertEqual(self.check("id,note\n1,ann@example.com or bo@x.test\n"), [])  # RFC 2606: nobody

    def test_handles(self):
        self.assertEqual(self.check("id,evidence_quote\n1,\"@jdoe said no\"\n"), ["line 2: @handle '@jdoe' (write @user)"])
        self.assertEqual(self.check("id,evidence_quote\n1,\"@user said no, ann@example.com\"\n"), [])

    def test_quote_length(self):
        self.assertEqual(self.check(f"id,evidence_quote\n1,{'x' * 200}\n"), [])
        self.assertEqual(self.check(f"id,evidence_quote\n1,{'x' * 201}\n"), ["line 2: quote is 201 chars (max 200)"])


if __name__ == "__main__":
    unittest.main()
