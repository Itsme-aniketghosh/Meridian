"""deploy.yml's Slack messages (S7), sent through Data-Pipeline's meridian.alert, on main only.

    deployed       a new model serves 100%: version, md5, revision, the service's URL
    canary_failed  the canary (a share of traffic, watched on errors and latency) failed; the revision that served
                   before serves 100% again
    rolled_back    the move or the live smoke test failed; the revision that served before serves 100% again
    rollback_failed  the same, and the rollback failed too: someone moves the traffic by hand
    stopped        a check failed before any traffic moved; the revision that served before keeps serving

Versions, revisions and URLs only, never a person: anything that looks like an email is removed before sending.
Slack's webhook is MERIDIAN_SLACK_WEBHOOK (deploy.toml [secrets], GitHub's secret); without it the message is only
logged. A channel that fails never changes the deploy's outcome: this always exits 0.

    python Model-Deployment/notify.py deployed --version <sha> --md5 <md5> --revision R --url URL --run-url RUN
    python Model-Deployment/notify.py canary_failed --version <sha> --revision R --serving R0 --percent 10 \
        --why "error rate 3/40" --run-url RUN
    python Model-Deployment/notify.py stopped --version <sha> --step "Smoke test, the revision" --serving R \
        --run-url RUN
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_PIPELINE = REPO / "Data-Pipeline"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
KINDS = ("deployed", "canary_failed", "rolled_back", "rollback_failed", "stopped")
EVENT = {"deployed": "deployed"}   # meridian.alert's event; settings.toml [alerts] on lists "deployed"


def message(kind, a):
    """(subject, lines) for one kind of message, from the parsed arguments."""
    model = f"model {a.version[:12]}" + (f" (md5 {a.md5})" if a.md5 else "")
    if kind == "deployed":
        subject = "Meridian: new model deployed"
        lines = [f"{model}{f', val PR-AUC {a.pr_auc}' if a.pr_auc else ''}",
                 f"revision {a.revision} serves 100% at {a.url}"]
    elif kind == "canary_failed":
        subject = "Meridian: canary failed, rolled back"
        lines = [f"{model} on revision {a.revision} took {a.percent or 'a share'}% of traffic and failed: "
                 f"{a.why or 'see the run'}",
                 f"{a.serving} serves 100% again" + (f" (/health {a.health})" if a.health else "")]
    elif kind == "rolled_back":
        subject = "Meridian: deploy rolled back"
        lines = [f"{model} on revision {a.revision} failed after the traffic move: {a.why or 'see the run'}",
                 f"{a.serving} serves 100% again" + (f" (/health {a.health})" if a.health else "")]
    elif kind == "rollback_failed":
        subject = "Meridian: deploy ROLLBACK FAILED, move the traffic by hand"
        lines = [f"{model} on revision {a.revision} failed after the traffic move: {a.why or 'see the run'}",
                 f"the rollback to {a.serving or 'the revision that served before'} failed too"]
    else:
        subject = "Meridian: deploy stopped, no traffic moved"
        lines = [f"{model} failed at: {a.step or 'see the run'}",
                 f"{a.serving or 'the revision that served before'} keeps serving"]
    lines.append(f"run: {a.run_url}")
    return subject, lines


def no_people(subject, lines):
    """The message with anything that looks like an email replaced by [removed]."""
    return EMAIL.sub("[removed]", subject), [EMAIL.sub("[removed]", x) for x in lines]


def send(subject, lines, event):
    """meridian.alert.send, or nothing if it can't be loaded: the channels that took it."""
    sys.path.insert(0, str(DATA_PIPELINE))
    try:
        from meridian import alert
        return alert.send(subject, lines, event=event)
    except Exception as e:   # noqa: BLE001  a broken channel must never change the deploy's outcome
        print(f"notify: not sent ({type(e).__name__}: {e})", file=sys.stderr)
        return []


def main(argv=None, sender=send):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("--version", required=True, help="the champion's version")
    ap.add_argument("--md5", default="", help="the champion's model md5")
    ap.add_argument("--pr-auc", default="", help="its val PR-AUC")
    ap.add_argument("--revision", default="", help="the new revision")
    ap.add_argument("--url", default="", help="the service's URL")
    ap.add_argument("--serving", default="", help="the revision that serves after the run")
    ap.add_argument("--why", default="", help="what failed after the move")
    ap.add_argument("--percent", default="", help="the canary's share of traffic")
    ap.add_argument("--health", default="", help="/health's status after a rollback")
    ap.add_argument("--step", default="", help="the step that failed before any traffic moved")
    ap.add_argument("--run-url", required=True, help="the workflow run")
    ap.add_argument("--planted", default="", help="a planted test's kind (plant.py): said in the subject")
    ap.add_argument("--github-output", type=Path, help="append slack_sent=<channels>")
    a = ap.parse_args(argv)

    subject, lines = message(a.kind, a)
    if a.planted:
        subject = f"[planted test: {a.planted}] {subject}"
    subject, lines = no_people(subject, lines)
    sent = sender(subject, lines, EVENT.get(a.kind, "failure"))
    print("\n".join([subject, *lines]))
    print(f"notify: {a.kind} sent to {', '.join(sent) or 'no channel (logged only)'}")
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            f.write(f"slack_sent={','.join(sent) or 'none'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
