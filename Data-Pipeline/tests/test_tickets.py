"""D4 tests: ticket, ticket_event, ticket_link, commit_ticket on hand-made Jira and GitLab data.

    python -m unittest tests.test_tickets -v      # from Data-Pipeline/
"""
import gzip
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.tickets as T

RUN, SNAP = "abcdef0123456789", "2026-10-01T00:00:00Z"
RULES = {"bulk_close": {"label": "bulk-closed", "per_person": 20, "within_minutes": 60}, "subtask_takes_parent_type": True,
         "key_regex_generic": r"\b[A-Z][A-Z0-9]+-\d+\b"}
BLOCKER = {"name": "Blocker", "inward": "is blocked by", "outward": "blocks"}


def jira_ts(t):
    return t.replace("Z", ".000+0000")


def issue(key, created="2026-01-01T00:00:00Z", typ="Bug", status="Open", resolution=None, resolved=None,
          histories=(), links=(), parent=None, labels=(), subtask=False):
    return {"key": key, "changelog": {"histories": list(histories)}, "fields": {
        "created": jira_ts(created), "issuetype": {"name": typ, "subtask": subtask}, "status": {"name": status},
        "resolution": {"name": resolution} if resolution else None, "resolutiondate": resolved and jira_ts(resolved),
        "components": [{"name": "SQL"}, {"name": "Core"}], "labels": list(labels), "issuelinks": list(links),
        "parent": {"key": parent} if parent else None, "assignee": None, "reporter": {"name": "rep"},
        "description": "", "summary": key}}


def history(hid, at, *items, who="ann"):
    return {"id": str(hid), "created": jira_ts(at), "author": {"name": who},
            "items": [dict(zip(("field", "fromString", "toString"), i)) for i in items]}


class Jira(unittest.TestCase):
    def setUp(self):
        issues = [
            # resolved after the snapshot: open at it
            issue("SPARK-1", status="Resolved", resolution="Fixed", resolved="2026-10-05T00:00:00Z", histories=[
                history(1, "2026-10-05T00:00:00Z", ("status", "Open", "Resolved"), ("resolution", None, "Fixed"))]),
            # fixed before, reopened and fixed again after: Fixed at the snapshot, resolved_at from before
            issue("SPARK-2", status="Resolved", resolution="Fixed", resolved="2026-10-06T00:00:00Z", histories=[
                history(1, "2026-02-01T00:00:00Z", ("resolution", None, "Fixed"), ("labels", "a", "a b")),
                history(2, "2026-10-04T00:00:00Z", ("resolution", "Fixed", None)),
                history(3, "2026-10-06T00:00:00Z", ("resolution", None, "Fixed"))]),
            issue("SPARK-3", typ="Sub-task", subtask=True, parent="SPARK-2"),
            issue("SPARK-4", created="2026-10-02T00:00:00Z"),  # created after the snapshot
            # SPARK-5 blocks SPARK-1 (as of the pull, no event). A link to SPARK-4 (created later) is left out
            issue("SPARK-5", links=[{"type": BLOCKER, "outwardIssue": {"key": "SPARK-1"}},
                                    {"type": BLOCKER, "outwardIssue": {"key": "SPARK-4"}},
                                    {"type": BLOCKER, "inwardIssue": {"key": "HADOOP-9"}}], histories=[
                history(1, "2026-03-01T00:00:00Z", ("Link", None, "This issue is blocked by SPARK-2")),
                history(2, "2026-04-01T00:00:00Z", ("Link", "This issue is blocked by SPARK-2", None)),
                history(3, "2026-10-03T00:00:00Z", ("Link", None, "This issue is blocked by SPARK-3"))]),
            issue("SPARK-6", labels=["bulk-closed"], status="Closed", resolution="Incomplete", resolved="2026-01-02T00:00:00Z"),
            # fixed before, reopened after: Jira cleared resolutiondate, but it was Fixed at the snapshot
            issue("SPARK-7", status="Reopened", histories=[
                history(1, "2026-02-01T00:00:00Z", ("status", "Open", "Resolved"), ("resolution", None, "Fixed")),
                history(2, "2026-10-04T00:00:00Z", ("status", "Resolved", "Reopened"), ("resolution", "Fixed", None))]),
        ]
        issues[-1]["changelog"]["total"] = 3  # Jira returned 2 of 3 histories
        # 20 resolved by one person within one hour, across a clock hour (10:50-11:09): a mass close
        issues += [issue(f"SPARK-{100 + i}", status="Closed", resolution="Won't Fix", resolved="2026-05-01T11:30:00Z",
                         histories=[history(1, f"2026-05-01T{10 + (50 + i) // 60}:{(50 + i) % 60:02d}:00Z",
                                            ("resolution", None, "Won't Fix"), who="bot")])
                   for i in range(20)]
        # 20 in one hour with no author: anonymous entries are no one, never pooled into one person
        issues += [issue(f"SPARK-{200 + i}", status="Closed", resolution="Won't Fix", resolved="2026-06-01T10:30:00Z",
                         histories=[history(1, f"2026-06-01T10:{i:02d}:00Z", ("resolution", None, "Won't Fix"), who=None)])
                   for i in range(20)]
        tickets, events, links, self.counts = T.jira(issues, SNAP, RULES, RUN, "SPARK")
        self.t, self.events, self.links = {r["key"]: r for r in tickets}, events, links

    def test_created_after_snapshot_left_out(self):
        self.assertNotIn("SPARK-4", self.t)

    def test_rolled_back_to_snapshot(self):
        self.assertEqual((self.t["SPARK-1"]["status"], self.t["SPARK-1"]["resolution"], self.t["SPARK-1"]["resolved_at"]),
                         ("Open", None, None))
        self.assertEqual((self.t["SPARK-2"]["resolution"], self.t["SPARK-2"]["resolved_at"]), ("Fixed", "2026-02-01T00:00:00Z"))
        self.assertEqual((self.t["SPARK-7"]["status"], self.t["SPARK-7"]["resolution"], self.t["SPARK-7"]["resolved_at"]),
                         ("Resolved", "Fixed", "2026-02-01T00:00:00Z"))

    def test_cut_changelog_counted(self):
        self.assertEqual(self.counts["changelog_cut"], 1)

    def test_subtask_takes_parent_type_and_components_sorted(self):
        self.assertEqual((self.t["SPARK-3"]["type"], self.t["SPARK-3"]["effective_type"]), ("Sub-task", "Bug"))
        self.assertEqual(self.t["SPARK-3"]["components"], '["Core","SQL"]')

    def test_bulk_closed_by_label_or_20_in_an_hour(self):
        self.assertEqual(self.t["SPARK-6"]["bulk_closed"], 1)
        self.assertTrue(all(self.t[f"SPARK-{100 + i}"]["bulk_closed"] for i in range(20)))
        self.assertFalse(any(self.t[f"SPARK-{200 + i}"]["bulk_closed"] for i in range(20)))
        self.assertEqual(self.t["SPARK-1"]["bulk_closed"], 0)

    def test_events_stop_at_snapshot(self):
        self.assertTrue(all(e["at"] <= SNAP for e in self.events))
        two = [(e["field"], e["from_value"], e["to_value"], e["seq"]) for e in self.events if e["key"] == "SPARK-2"]
        self.assertEqual(two, [("resolution", None, "Fixed", 0), ("label", None, "b", 1)])

    def test_links(self):
        got = {(l["key"], l["link_type"], l["other_key"]): (l["added_at"], l["removed_at"]) for l in self.links}
        self.assertEqual(got[("SPARK-1", "blocked_by", "SPARK-5")], (None, None))  # 'blocks', keyed by the waiting ticket
        self.assertEqual(got[("SPARK-5", "blocked_by", "SPARK-2")], ("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"))
        self.assertEqual(got[("SPARK-5", "blocked_by", "HADOOP-9")], (None, None))  # other projects stay
        self.assertEqual(got[("SPARK-3", "parent", "SPARK-2")], (None, None))
        self.assertNotIn(("SPARK-5", "blocked_by", "SPARK-3"), got)  # added after the snapshot
        self.assertFalse([k for k in got if "SPARK-4" in k])  # created after the snapshot
        self.assertEqual(self.counts["links_to_missing"], 1)  # left out, but counted


def gl_issue(iid, title="t", state="opened", closed=None, labels=(), notes=None, blocked_by=(), parent=None, ptype="Issue",
             desc="x"):
    return {"iid": str(iid), "title": title, "state": state, "closedAt": closed, "createdAt": "2025-02-01T00:00:00Z",
            "type": "TASK" if parent else "ISSUE", "description": desc, "author": {"username": "rep"},
            "assignees": {"nodes": [{"username": "zed"}, {"username": "amy"}]}, "labels": {"nodes": [{"title": l} for l in labels]},
            "blockedByIssues": {"nodes": [{"iid": str(b), "webUrl": f"https://gitlab.com/gitlab-org/gitlab/-/issues/{b}"} for b in blocked_by]},
            "parent": {"iid": str(parent), "workItemType": {"name": ptype}} if parent else None, "epic": None,
            "notes": [{"body": b, "createdAt": at, "system": True, "author": {"username": "amy"}} for at, b in notes or []]}


class GitLab(unittest.TestCase):
    def setUp(self):
        ds = {"project": "gitlab-org/gitlab", "key_format": "gitlab-org/gitlab#{iid}", "drop_titles_starting": ["[Test]"]}
        issues = [
            gl_issue(1, state="closed", closed="2026-10-05T00:00:00Z", labels=["group::a", "type::bug"], notes=[
                ("2025-03-01T00:00:00Z", "marked this issue as blocked by #2"),
                ("2025-04-01T00:00:00Z", "removed the relation with #2"),
                ("2025-05-01T00:00:00Z", "marked this issue as blocking #3 and gitlab-org/other#4"),
                ("2025-06-01T00:00:00Z", "set status to **In dev**"),
                ("2026-10-03T00:00:00Z", "marked this issue as blocked by #5")]),
            gl_issue(2, state="closed", closed="2025-07-01T00:00:00Z", blocked_by=[9]),
            gl_issue(3, parent=2),
            gl_issue(6, title="[Test] flaky spec"),
            gl_issue(7, parent=40, ptype="Epic", desc=None),  # a group epic, not issue #40
        ]
        tickets, events, links, self.counts = T.gitlab(issues, SNAP, ds, RULES, "group::", RUN)
        self.t, self.events = {r["key"]: r for r in tickets}, events
        self.links = {(l["key"], l["link_type"], l["other_key"]): (l["added_at"], l["removed_at"]) for l in links}

    def test_status_as_of_snapshot(self):
        one, two = self.t["gitlab-org/gitlab#1"], self.t["gitlab-org/gitlab#2"]
        self.assertEqual((one["status"], one["resolved_at"]), ("opened", None))  # closed after the snapshot
        self.assertEqual((two["status"], two["resolved_at"]), ("closed", "2025-07-01T00:00:00Z"))

    def test_fields(self):
        one, three = self.t["gitlab-org/gitlab#1"], self.t["gitlab-org/gitlab#3"]
        self.assertEqual((one["components"], one["assignee"], one["tracker"]), ('["group::a"]', "amy", "gitlab"))
        self.assertEqual((three["parent_key"], three["effective_type"]), ("gitlab-org/gitlab#2", "ISSUE"))
        self.assertEqual((one["description"], self.t["gitlab-org/gitlab#7"]["description"]), ("x", ""))

    def test_epic_parent_is_not_an_issue(self):
        self.assertIsNone(self.t["gitlab-org/gitlab#7"]["parent_key"])
        self.assertFalse([k for k in self.links if k[0] == "gitlab-org/gitlab#7"])

    def test_test_bot_left_out_and_counted(self):
        self.assertNotIn("gitlab-org/gitlab#6", self.t)
        self.assertEqual(self.counts["dropped_titles"], 1)

    def test_links_from_notes(self):
        self.assertEqual(self.links[("gitlab-org/gitlab#1", "blocked_by", "gitlab-org/gitlab#2")],
                         ("2025-03-01T00:00:00Z", "2025-04-01T00:00:00Z"))  # 'removed the relation' matched to it
        self.assertEqual(self.links[("gitlab-org/gitlab#3", "blocked_by", "gitlab-org/gitlab#1")], ("2025-05-01T00:00:00Z", None))
        self.assertIn(("gitlab-org/other#4", "blocked_by", "gitlab-org/gitlab#1"), self.links)
        self.assertEqual(self.links[("gitlab-org/gitlab#2", "blocked_by", "gitlab-org/gitlab#9")], (None, None))  # in the pull
        self.assertIn(("gitlab-org/gitlab#3", "parent", "gitlab-org/gitlab#2"), self.links)
        self.assertNotIn(("gitlab-org/gitlab#1", "blocked_by", "gitlab-org/gitlab#5"), self.links)  # after the snapshot

    def test_events(self):
        one = [(e["field"], e["from_value"], e["to_value"]) for e in self.events if e["key"] == "gitlab-org/gitlab#1"]
        self.assertEqual(one, [("link", None, "blocked_by gitlab-org/gitlab#2"), ("link", "blocked_by gitlab-org/gitlab#2", None),
                               ("link", None, "blocks gitlab-org/gitlab#3"), ("link", None, "blocks gitlab-org/other#4"),
                               ("status", None, "In dev")])
        self.assertEqual(T.gl_ref("&5", "gitlab-org/gitlab"), "gitlab-org&5")
        self.assertEqual(T.gl_ref("gitlab-org&5", "gitlab-org/gitlab"), "gitlab-org&5")
        self.assertEqual(T.gl_ref("gitaly#5", "gitlab-org/gitlab"), "gitlab-org/gitaly#5")  # as blockedByIssues writes it


class CommitTicket(unittest.TestCase):
    def test_subject_first_body_fallback_dead_keys(self):
        msgs = [("a", "[SPARK-1][SQL] fix", "see SPARK-2"), ("b", "[MINOR] typo", "branch SPARK-2-fix"),
                ("c", "[SPARK-1][SPARK-99] two", ""), ("d", "no key", "")]
        rows, dead = T.commit_tickets(msgs, "apache/spark", re.compile(r"\bSPARK-\d+\b"), {"SPARK-1", "SPARK-2"}, RUN)
        self.assertEqual([(r["sha"], r["key"], r["source"]) for r in rows],
                         [("a", "SPARK-1", "subject"), ("b", "SPARK-2", "body"), ("c", "SPARK-1", "subject")])
        self.assertEqual(dead, 1)  # SPARK-99 isn't a ticket



class Build(unittest.TestCase):
    """build() from raw Jira pages, as collect writes them."""
    def test_rows_from_raw_pages(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        d = tmp / "jira" / "SPARK"
        d.mkdir(parents=True)
        fixed = issue("SPARK-1", status="Resolved", resolution="Fixed", resolved="2026-03-01T00:00:00Z",
                      histories=[history(1, "2026-03-01T00:00:00Z", ("resolution", None, "Fixed"))],
                      links=[{"type": BLOCKER, "inwardIssue": {"key": "SPARK-2"}}])
        fixed["fields"]["description"] = "boom"
        batch = [issue(f"SPARK-{10 + i}", status="Closed", resolution="Won't Fix", resolved="2026-05-01T11:00:00Z",
                       histories=[history(1, f"2026-05-01T10:{i:02d}:00Z", ("resolution", None, "Won't Fix"), who="bot")])
                 for i in range(20)]
        batch[0]["fields"]["description"] = None
        for i, page in enumerate([[fixed, issue("SPARK-2")], [issue("SPARK-2"), *batch]]):  # SPARK-2 twice: paging shifted
            (d / f"page-{i:04d}.json.gz").write_bytes(gzip.compress(json.dumps({"issues": page}).encode()))
        cfg = {"tickets": RULES, "datasets": {"spark": {"tracker": "jira", "jira": {"jql": "project=SPARK ORDER BY key ASC"},
                                                         "key_regex": r"\bSPARK-\d+\b", "repos": [{"repo": "apache/spark"}]}}}
        manifest = {"snapshot_at": SNAP, "sources": {"jira/SPARK": "x"}, "repos": {"apache/spark": "f00"}}
        out = T.build(tmp, manifest, cfg, RUN, lambda repo: [("a1", "[SPARK-1] fix", ""), ("b2", "no key", "SPARK-77")])
        t = {r["key"]: r for r in out["ticket"]}
        self.assertEqual(len(out["ticket"]), 22)
        one = t["SPARK-1"]
        self.assertEqual((one["effective_type"], one["status"], one["resolution"], one["resolved_at"], one["bulk_closed"], one["description"]),
                         ("Bug", "Resolved", "Fixed", "2026-03-01T00:00:00Z", 0, "boom"))
        self.assertTrue(all(t[f"SPARK-{10 + i}"]["bulk_closed"] for i in range(20)))
        self.assertEqual(t["SPARK-10"]["description"], "")
        self.assertEqual([(l["key"], l["link_type"], l["other_key"]) for l in out["ticket_link"]], [("SPARK-1", "blocked_by", "SPARK-2")])
        self.assertEqual([(r["sha"], r["key"], r["source"]) for r in out["commit_ticket"]], [("a1", "SPARK-1", "subject")])


if __name__ == "__main__":
    unittest.main()
