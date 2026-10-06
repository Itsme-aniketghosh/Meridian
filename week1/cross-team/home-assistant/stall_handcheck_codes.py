import csv,json
OUT="data/stall"
C=[
(137582,"y","owner","review-wait","n","No review at all in 60 d; stale bot fired; author (core member) closed it next day. Core-owned http code."),
(147237,"y","upstream","waiting-on-other-PR","n","Body: 'This is PR 2/6. PR #147226 should be merged first!' - base PR never reviewed; stale-closed."),
(135790,"partly","unknown","changes-requested-no-response","n","epenet CHANGES_REQUESTED 'Please rebase/refactor over #132787'; author never replied; stale-closed."),
(140934,"y","owner","design-decision","y","joostlek (core, not fyta codeowner): 'I kinda think that we shouldn't make it an option'; author disagreed; idle 6 weeks until merged."),
(137101,"y","owner","design-decision","n","balloob: 'I'm a little hesitant of even adding support for this'; draft, stale-closed after more review rounds. Core helper code."),
(146100,"y","owner","review-wait","y","Author is KNX codeowner; no review from 2025-06-03 until joostlek approved 2025-07-09 (37 d)."),
(145387,"y","owner","review-wait","y","Author: 'I've been waiting for two months for someone to tell me what to do next.' Core (emontnemery) replied 2025-09-16; later stale-closed."),
(145848,"y","owner","design-decision","y","Calendar vs sensor question: joostlek 'I will throw this in the architecture meeting'; emontnemery 2 months later: 'discussed in the architecture meeting, but it was forgotten to update here'."),
(143809,"partly","upstream","frontend/docs dependency","y","Label awaiting-frontend; then 'Superseded by #145504'."),
(139888,"partly","unknown","changes-requested-no-response","n","edenhaus: 'Please also add device information... add a test'; no author reply; stale-closed."),
(136482,"y","owner","review-wait","y","joostlek: 'Would love for another core member to also take a look'; MartinHjelmare review rounds with 46 d gap (2025-03-11 ready -> 2025-04-26 review). Codeowner fucm not involved."),
(145972,"partly","capacity","deprioritized","n","Author: 'since last month I have much less time for private projects'; frenck closed as stale."),
(135060,"y","owner","review-wait","n","CloCkWeRX: '@tkdrob could you take a peek'; 'Not stale; reasonable change, awaiting review from a maintainer'; codeowner never reviewed; stale-closed."),
(144078,"n","other","design-decision","n","mkmer (co-codeowner): 'Shouldn't this removal wait the customary 6 months?' - deliberate deprecation wait, later reopened and merged."),
(145070,"partly","owner","design-decision","y","epenet (core, not tuya codeowner) asked for preliminary fixture PR, then did #149677 'Make Tuya complex type handling explicit'; author closed."),
(134442,"partly","owner","review-wait","y","Ready 2025-02-07, next review by edenhaus (core) 2025-04-02 (54 d); then author did not address; stale-closed. Codeowner pvizeli silent."),
(145262,"y","owner","review-wait","y","Ready 2025-05-20; no review until joostlek approved 2025-06-23 (34 d). (ntfy had no codeowners in the 2025-04 manifest snapshot - new integration.)"),
(141192,"partly","unknown","changes-requested-no-response","n","New integration; reviewers asked to limit to one platform and add tests; draft, no author follow-up; stale-closed."),
(140497,"n","unknown","unknown","n","Codeowner opened as draft, never marked ready, no comments; stale-closed."),
(136660,"partly","unknown","changes-requested-no-response","n","frenck: 'This is actually going into a direction we like to avoid'; author did not rework; stale-closed."),
(142489,"y","owner","review-wait","n","Author answered synesthesiam's design question and re-readied 2025-04-16; no reviewer reply for 60 d; stale-closed. media_player is core-owned."),
(135107,"partly","unknown","changes-requested-no-response","n","emontnemery: 'this PR will not be reviewed until you've fixed the merge errors in your branch'; 170 d author-side gap, merged 2026-04."),
(134903,"y","owner","review-wait","y","User: 'Why is the merging blocked?' Author: 'I actually don't know'; first review 2025-03-02 (55 d) by dlna_dmr owner chishm, not an owner of labelled http/media_source/tts."),
(135009,"y","owner","review-wait","y","Codeowner author: '@joostlek ... any chance of feedback on this and the other PRs'; next review 2025-02-18 by andrewsayre (core)."),
(146922,"partly","owner","design-decision","n","frenck: 'we made the decision (architecturally), not to bring all options to the UI'; effectively rejected; stale-closed."),
(134594,"y","upstream","waiting-on-other-PR","n","joostlek: 'Integrations first need to be migrated to a config flow'; author: 'the config flow migration is something that the enocean maintainer(s) are in the best position to do'."),
(143822,"partly","unknown","changes-requested-no-response","n","Author: 'We are still working on it' (x2), later replaced by #155260."),
(140112,"n","other","design-decision","n","frenck: 'Let's split this PR in smaller pull requests'; author split (#140265) and closed this one."),
(144910,"y","owner","design-decision","y","emontnemery: 'I'll discuss this again with the core team. I'm sorry to block your PR because of this.' Also first review took 61 d; aiobotocore upstream issue."),
(146709,"y","owner","review-wait","y","Codeowner author; no review 2025-06-13 -> 2025-07-14 (abmantis approved)."),
(140261,"y","owner","design-decision","n","epenet: 'Closed until architecture discussion home-assistant/architecture#1234 is approved'; also needs #139730."),
(145140,"partly","capacity","deprioritized","n","Codeowner author, asked what it waits for: 'My free time :)'."),
(147120,"y","upstream","frontend/docs dependency","y","emontnemery: 'I'm waiting for input on the way forward'; 'Not stale, I need some help from frontend team'; merged after discussing with @bramkragten."),
(140566,"partly","owner","design-decision","n","MartinHjelmare: 'I think this may need an architecture discussion'; no follow-up; stale-closed. group is core-owned."),
(143129,"partly","unknown","changes-requested-no-response","n","Codeowner bachya approved; joostlek requested device split; no author reply; stale-closed."),
(140525,"y","owner","review-wait","n","edurenye approved 2025-04-01; 'This PR just needs another review' (05-31, 07-31); merged 2025-09-02 after voice reviewer. lawn_mower is core-owned."),
(140160,"y","owner","design-decision","y","Author asked whether virtual manifests should allow codeowners; joostlek answered 46 d later 'we should not even care'; closed."),
(145606,"y","owner","design-decision","n","Waited 2025-06-06 -> 2025-09-12 for 'The suggestion from the core team is...'; calendar is core-owned; plus frontend PR needed."),
(141896,"y","owner","review-wait","n","Codeowner author; ready 2025-05-26, first review 2025-06-25 by co-codeowner emontnemery."),
(144877,"y","owner","review-wait","n","No review 2025-05-14 -> stale 2025-07-13; codeowner then: 'will need to be rebased on #135440 once that is merged'."),
]
S=[r["number"] for r in json.load(open(f"{OUT}/stall_sample40.json"))]
assert sorted(S)==sorted(c[0] for c in C), set(S)^set(c[0] for c in C)
meta={r["number"]:r for r in json.load(open(f"{OUT}/stall_sample40.json"))}
with open("stall_hand_check_40.csv","w") as f:
    w=csv.writer(f); w.writerow(["pr","url","state","max_idle_days","integrations","author_codeowner","really_stuck","reason_category","reason_detail","cross_team","evidence","checked_by"])
    for n,rs,rc,rd,ct,ev in C:
        m=meta[n]; w.writerow([n,f"https://github.com/home-assistant/core/pull/{n}","merged" if m["merged"] else "closed",round(m["max_gap"]),";".join(m["integrations"]),m["author_codeowner"],rs,rc,rd,ct,ev,"claude (read via API), not a human"])
import collections
for i,k in ((1,"really_stuck"),(2,"reason"),(3,"detail"),(4,"cross")): print(k,collections.Counter(c[i] for c in C))
print("stuck=y by cross",collections.Counter(c[4] for c in C if c[1]=="y"))
print("y/partly by reason",collections.Counter(c[2] for c in C if c[1] in("y","partly")))
