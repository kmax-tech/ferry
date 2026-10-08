<!-- vendored: coding-style.md @ b0ec3b57236c (master: ~/.claude/resources) — edit the master, then `lib sync pull` -->
# Code style

Much of this code produces numbers that end up in a paper or a thesis. So the
rules below are not sorted by craft — they are sorted by **what a violation
costs you**. The first four can turn a bug into a wrong number that nobody
notices until the defense. The rest keep the code readable while it grows,
four are about the world outside the process, where the failures are quiet,
and the last keeps the code findable.

One idea runs through all of them: **nothing important stays implicit.** Types,
decisions, assumptions, side effects and errors must be visible in the code —
the reader (a supervisor, a reviewer, you in three months) should never have to
guess.

These rules are binding. If a request conflicts with one of them, **flag the
conflict** instead of silently violating it.

Two words that recur below:

- **The protocol** — the experiment's own record. `PROTOCOL-<task>.md` in a
  thesis repo, the area's protocol file in a lab repo.
- **The shared module** — the one home for logic used in more than one place.
  `shared.py` at the repo root, or whatever the repo's own `CLAUDE.md` names.

---

## CS-001 · Fail loud — never swallow an error

A swallowed error doesn't disappear. It comes back as a wrong result, far away
from its cause, with nothing pointing at it.

```python
# Bad — 3 malformed rows vanish, your N is silently 197 instead of 200
for row in rows:
    try:
        items.append(parse(row))
    except Exception:
        pass

# Good — a bad row stops you now, while you can still see why
for row in rows:
    items.append(parse(row))  # raises on malformed input
```

- No bare `except:` and no `except Exception: pass`. Catch only the specific
  exception you actually know how to handle.
- Don't skip bad data quietly. If an input violates your expectations, **raise**.
  A clear error at the source beats a silently wrong accuracy downstream.
- **Validate at the boundary** — right where data enters (the prepare script,
  the answer parser). After that, the rest of the code can trust its inputs.
- **Convert types deliberately.** `int(...)` or `str(...)` sprinkled in to make
  something run turns a real type error into a silently wrong value.
- If you *do* want to tolerate bad entries, that is a real decision: make it an
  explicit flag (`skip_invalid: bool`), never a silent `except`.

If you catch something, never discard the context — re-raise or log the
original error with it.

---

## CS-002 · Make assumptions explicit

When a choice materially changes your numbers, it must be visible in the code —
not buried in a line you'll forget you wrote.

The classic example is one you will actually hit: "average the score across the
classes" is ambiguous.

- **Macro:** average the per-class score, every class weighted equally. A class
  with 3 items counts as much as one with 300.
- **Micro:** pool all instances first, every instance weighted equally. Large
  classes dominate.

On unbalanced data these give very different numbers. So don't hardcode one —
name it:

```python
def average_score(
    per_class: dict[str, ClassStats],
    method: Literal["macro", "micro"] = "macro",
) -> float:
    """Average a score across classes.

    Args:
        per_class: Stats per class label.
        method: "macro" weights each class equally; "micro" weights each
            instance equally. These differ when class sizes are uneven.
    """
```

Now the assumption is named, documented, and changeable. Write which one you
chose — and why — into the protocol. If the choice is big and hard to reverse,
ask before you build on it.

**A threshold is a named constant that carries its provenance.** A cutoff never
sits as a literal inside a comparison; it is a constant whose comment says
what the value was optimised for, on which data, and when. `if score <
-0.8536:` tells a reader nothing; this tells them whether the number
transfers:

```python
# Optimised for FPR 0.01 % on Falcon-7B / Falcon-7B-instruct at bf16 (Hans et al. 2024, Table 3)
BINOCULARS_FPR_THRESHOLD = -0.8536
```

A value tuned on the run's own null condition belongs to the run and its
protocol, not to the module (AS-035 Schwellen gehören zum Lauf, nicht zum
Maß); the constant in the module is the rule's default, with the same comment.

---

## CS-003 · Test the functions that produce numbers

Every function whose output can become a reported number — metrics, answer
parsers, label normalization — gets a small test with hand-checked examples in
`tests/`. This is not general test discipline; it is the one thing standing
between you and a **parser bug that looks exactly like a model error**.

```python
def test_parse_answer_rejects_ambiguous_output() -> None:
    assert parse_answer("pro, or maybe con") == "?"


def test_average_score_macro_weights_classes_equally() -> None:
    stats = {"a": ClassStats(score=1.0, n=1), "b": ClassStats(score=0.0, n=99)}
    assert average_score(stats, method="macro") == 0.5
```

Cover the normal case **and** the edges: empty input, boundaries, and the errors
from CS-001 Fail loud — assert that bad input really does raise. Run `python3 -m pytest tests/`
**before** you report any number, and update tests in the same change that
changes behavior.

Scale the tests to the change, though — don't gold-plate. Cover the edges that
can realistically occur for *this* code, not every edge the language permits.

---

## CS-004 · One home per piece of logic

Before writing a function, check whether it already exists — search, don't
assume. The failure mode is two slightly different `compute_f1` helpers that
drift apart, so the same experiment reports two different numbers depending on
which script ran.

- If it exists, call it. If it almost fits, extend it rather than clone it.
- The **second** time you need something, move it to the shared module and
  import it from both places. Never copy-paste between experiments.
- Don't generalize on speculation — extract once the repetition is real.

A single shared function also means one signature and one behavior everywhere
it is used, which is what makes CS-005 Keep interfaces consistent between functions hold.

---

## CS-005 · Keep interfaces consistent between functions

When one function's output feeds another's input, the **order, names and shape**
stay identical along the chain.

```python
def pre_filter(doc: Doc) -> tuple[list[Flag], bool]:
    return flags, needs_judge

def resume_filter(needs_judge: bool, flags: list[Flag]) -> Result:   # WRONG ORDER
```

Same two values, opposite order — the type checker won't always catch it, and
the call site looks fine. Once more than about two values cross a boundary, stop
using a tuple and give them names:

```python
@dataclass(frozen=True)
class FilterOutcome:
    flags: list[Flag]
    needs_judge: bool
```

Now position is irrelevant, and adding a third field later doesn't reshuffle
every call site.

---

## CS-006 · One function, one thing — and keep I/O at the edges

A function does exactly one thing, at one level of abstraction. Warning signs
that it has grown too big:

- The name needs an "and" to be honest (`load_and_score`).
- It mixes orchestration with byte-level detail.
- It has blocks separated by `# now do X` comments.
- It's hard to name, or hard to test without elaborate setup.

Prefer **pure** functions for anything that computes: output depends only on the
inputs, nothing outside changes. Don't mutate arguments, don't touch global
state, and keep disk/network/randomness at the edges of the script. That is what
makes CS-003 Test the functions that produce numbers easy — a pure metric function is a two-line test; one that reads a file
is not.

If a side effect is genuinely needed, isolate it and say so:

```python
def write_report(report: Report, path: Path) -> None:
    """Render `report` and write it to disk.

    Side effects:
        Creates or overwrites the file at `path`.
    """
```

---

## CS-007 · Type hints and docstrings

- **Type hints** on every signature — parameters and return. Prefer precise
  types (`list[str]`, `Path`, a dataclass) over `Any` or a bare `dict`.
- A **docstring** on every function that isn't trivially obvious: what it does
  and *why it exists*, not a line-by-line how. Document `Args`, `Returns` and
  `Raises`; for non-obvious return shapes describe each element, so the caller
  never has to read the body to use it right.
- **Names use the field's standard term.** An identifier, a field name or a
  label says what the lecture notes call the thing — `feature_vector`,
  `hypothesis_space`, `gold_label` — never a house coinage or a private
  synonym; a coinage may appear as a second name in a comment. Look the term
  up before you coin one: the Webis ML notation
  ([Syntax Overview](https://downloads.webis.de/lecturenotes/machine-learning/unit-en-ml-syntax-overview.pdf),
  [Elements](https://downloads.webis.de/lecturenotes/machine-learning/unit-en-ml-elements.pdf)),
  in lab repos `lib term <term>`. A reader who knows the field should never
  have to translate your names.
- **A ported method names its source.** The first docstring block of a class
  or function that reimplements a published method cites the paper and the
  original code: `Refactored from https://github.com/ahans30/Binoculars;
  Hans et al. 2024, arXiv:2401.12070.` A reader who doubts a number can then
  compare against the original in a minute; without the line they first have
  to discover that it is a port at all (AS-026 Eine wiederverwendete
  Komponente bringt ihre Fehler mit).

---

## CS-008 · Run the linter and the type checker

```bash
ruff check .      # lint
ruff format .     # format
pyright           # type-check — catches the swapped-argument bug from CS-005 Keep interfaces consistent between functions
```

Fix what they report rather than silencing it; a `# noqa` or `# type: ignore`
needs a stated reason. A change isn't finished until both run clean.

**Know when to stop.** If the checker floods you with errors from a cause
outside your control — most often an untyped third-party library — do not
refactor for days chasing them. Say what is happening and what the likely cause
is, propose the scoped options (a targeted `# type: ignore` at the boundary, or
excluding the library in the pyright config), and let the decision be made.

**And a cold reader.** Linter and type checker do not see a condition
hidden behind a negated name or one word used for three things — the
findings a person meets first (CS-017 The entry point shows the flow and its
conditions). Before a change is committed, a reader who did not write it
reads the diff: the `code-clarity` agent (`MODE: review · DIFF: <patch> ·
REPO: <repo>`, dotfiles `agents/code-clarity.md`), run as a different agent
from the one that wrote the code. What it would answer wrong is fixed, not
explained in a reply; `verdict: 0 findings` is a valid result. Measured
before it was trusted (labkit `docs/usability/2026-10-07-code-navigation.md`,
round 10): it found every planted defect and invented none; on a real
refactor it found eight, five of which a person would have answered wrong
(2026-10-08). When the person wants to read the code, `MODE: questions`
gives three behaviour questions with the answers held back (`/code-quiz`).

---

## CS-009 · Record every dependency

Adding a dependency is a real decision — it affects everyone who runs the
project. Don't pull one in silently.

- Before installing a new package, **say so and say why**. A new dependency
  shouldn't appear unannounced.
- The moment you `pip install` something, add it to `requirements.txt` (or the
  `pyproject.toml` dependencies). A package that is installed but unrecorded
  works on your laptop and breaks on the cluster — usually the week before
  submission.
- Prefer the standard library, or something already in the project (CS-004 One home per piece of logic), over a
  new package.

---

## CS-010 · Read the docs first, and keep them in sync

- **Before changing anything**, check the `README` and any relevant docs to
  understand how the affected part is meant to work. Don't infer the design
  from the code alone when it's already written down.
- **After changing a module**, update the documentation if your change made it
  inaccurate — new or changed behavior, interfaces, configuration, usage. Code
  and docs must not drift apart.
- If a change is significant enough that no doc covers it yet, add a short note
  where a reader would look for it.
- Keep this distinct from the notes file (see *Where the why goes*): docs
  explain *how the system works* for people using it; the notes record *why* a
  tricky problem was solved a particular way.

---

## CS-011 · Verify against the real system, not only against your tests

Tests prove the code does what *you* expect. They cannot tell you whether that
expectation matches reality. Before calling something done, put one real
artifact through the real path and look at what comes out.

- **Exercise the actual integration once, by hand.** Drop a real file into the
  watched folder and confirm it was picked up. Call the real endpoint. Start the
  real container. Assumptions about someone else's system are the ones that turn
  out to be wrong, and they fail silently — a folder watcher that never fires
  looks exactly like a user who hasn't scanned anything yet.
- **Look at the artifact, not just the exit code.** A file writer can return
  plausible bytes, pass every structural assertion, and still produce pages that
  render blank, inverted, or with the wrong aspect ratio. Open the output.
- **Gate on the check's own exit code.** `pytest -q | tail -1 && git commit`
  commits on red: a pipeline reports the status of its last stage, here
  `tail`. Prefix `set -o pipefail;` or capture the status first, and write
  "N passed" into a commit message only after it was checked. The hook
  `~/.claude/hooks/pipe-gate-hook.py` blocks the shape (2026-09-30 and
  2026-10-05, the second time "56 failed" was committed).
- **"Zero findings" needs a known positive.** A checker, regex or scan that
  reports nothing must first find one case you know is there; otherwise
  skipped input is counted as clean (2026-09-18: "20 notes without a
  finding", 14 of them silently skipped).
- **After changing a format, a name or a file, grep every reader.** The
  writer's tests stay green while a template, a second script or a doc still
  reads the old key (2026-10-07: 539 tests green, the analysis template read
  `k1_accuracy`, which the measure skeleton no longer wrote).
- **A review of an evaluation reads real output rows, not only the code.**
  Spelling variants, empty answers, a skewed label distribution or an
  unreachable gold value are invisible in the source. Give the reviewer a
  sample of the run's output rows, or let it read the output files (2026-09-09:
  a verdict that flipped with capitalisation, missed by the code-only
  preflight twice; 2026-10-05: two request types effectively unchecked, 8/8
  accepted, found only by a review that ran the code on the data).
- **Clean up what your verification created.** A test document left in a real
  archive is indistinguishable from real data six months later.
- **Say what you verified and what you did not.** An untested path presented as
  finished is worse than an untested path labelled as such — mark it where a
  reader will look, not only in the reply:

```markdown
3. ~~Scanner per eSCL → `./consume/`~~ — `scripts/scan.py` steht, gegen einen
   nachgebauten eSCL-Server getestet; **am echten Gerät noch nicht erprobt**.
```

---

## CS-012 · Fake the external system — including the ways it misbehaves

When code talks to a device, service, or API you don't control, write a small
fake that speaks its protocol and test against that. It turns "works while the
hardware is on my desk" into something anyone can run, on any machine, later.

The fake earns its keep by reproducing **known misbehavior**, not the happy
path. Those are the branches that otherwise only ever execute in production:

```python
def do_GET(self) -> None:
    if self.path.endswith("/NextDocument"):
        if not cls.hiccuped:  # real devices return busy mid-job
            cls.hiccuped = True
            return self._send(503)
        ...


def do_POST(self) -> None:
    # Deliberately wrong host, as some firmwares really do send.
    return self._send(
        201, b"", extra={"Location": "http://192.0.2.99/eSCL/ScanJobs/x1"}
    )
```

- Keep the fake beside the tests, in the same language, with no new dependency
  (CS-009 Record every dependency) — a fake that needs its own toolchain stops being run.
- Every quirk the fake simulates should carry a comment saying it is real, not
  invented. Otherwise the next reader deletes it as noise.
- The fake covers the protocol. It does **not** cover the hardware — see the
  labelling rule in CS-011 Verify against the real system.

---

## CS-013 · Publish state atomically

Anything that watches a location — a consume folder, a queue directory, a config
path another process reads — will eventually read your file while you are still
writing it. A partially written file is usually accepted as a whole one.

Write to a staging path on the **same filesystem**, then rename:

```python
def deliver(pdf: bytes, target: Path) -> Path:
    """Write the PDF elsewhere, then atomically rename it into place.

    Side effects:
        Creates TMPDIR if missing; creates or replaces the file at `target`.
    """
    TMPDIR.mkdir(exist_ok=True)
    staged = TMPDIR / f".{target.name}.{os.getpid()}{random.randrange(1 << 20):05x}"
    staged.write_bytes(pdf)
    os.replace(staged, target)  # rename is atomic; a write is not
    return target
```

- Same filesystem is the requirement, not a detail: across devices the rename
  degrades into a copy, and the atomicity is gone.
- Stage **outside** the watched directory. A temp file inside it may match the
  watcher's patterns and be consumed half-written — the exact failure you were
  avoiding.
- Assert in a test that a successful hand-off leaves no staging file behind.

---

## CS-014 · Shedding a dependency is as legitimate a fix as adding one

CS-009 Record every dependency governs taking a dependency on. This is the counterpart: when one breaks, or
turns out to be heavy for what you actually use it for, the first question is
not "how do I work around this" but **"what do I actually need from it?"** Often
the honest answer is a fraction of what it offers.

- A handful of flat scalar fields does not need a full XML parser. A single
  timestamp format does not need a date library.
- This weighs more when the code must run in **more than one environment** —
  this laptop today, a cluster node later. Every dependency is a thing that can
  be missing or broken over there.
- Don't do it by feel: shed a dependency when the replacement is genuinely small
  and testable. Reimplementing something subtle (crypto, timezones, real
  parsing) to avoid an import is the worse trade.
- **Record why the plain-looking code is plain**, or the next reader will
  helpfully restore the import:

```python
def xml_text(doc: bytes, tag: str) -> str:
    """First text value of <ns:tag>, namespace-agnostic.

    Deliberately a regex and not ElementTree: the replies we care about are a
    handful of flat scalar fields, and this keeps the script free of pyexpat —
    which is broken in some Homebrew Python builds and would otherwise take the
    whole scanner down with an ImportError.
    """
```

---

## CS-015 · Make the code findable by name

A newcomer — a person or an agent — looking for where the code does a thing
does not start from your README. Measured on two lab repos (labkit
`docs/usability/2026-10-07-code-navigation.md`, three rounds, 16 cold
agents): the strong readers never opened a code map; they listed the
definitions (`grep -n "^def \|^class "`), searched for the words of the
question and their synonyms, and read the first lines of what they hit. Only
weaker readers, when lost, opened the README — and it did not lead them to
the answer. **The names and the first docstring lines are the map.** Where
the weaker readers missed, the repo had two similar mechanisms whose names
did not say how they differ, or logic with no name of its own.

- **Logic someone will look for gets its own function**, named for what it
  does in the field's everyday words (CS-007 Type hints and docstrings): `majority`,
  `label_for`, `load_profile`. Logic buried in the middle of a long `main`
  has no name to find — the agents located the template-text check of
  `protocol_check` only by reading `main`, because it has no function of
  its own.
- **The first docstring line says what it does in plain words**, with the
  word a person would search for — it is what a definition listing or an
  editor outline shows: `"""Load the profile that says which checks a run
  activates."""` was found by an agent asking "which tests are switched on",
  though no word of the question was in the name.
- **Inside one file, a labelled section is found as well as a function.**
  A 210-line entry point whose stations open with numbered section
  comments was followed without a miss by the weakest reader we use (round
  5); the misses were all across files, where only a file, function or
  constant name is found. So findability alone is no reason to split a
  function — CS-006 One function, one thing — and keep I/O at the edges
  still is: section comments are its warning sign, and the split makes
  the data a station depends on visible in its signature.
- **Package and file names name a responsibility** (`deciding/`, `model/`,
  `running/`; `scope_sha.py`, `runs.py`): the agents chose which files to
  list by their names.
- **Two mechanisms that do similar things carry names that say how they
  differ.** labkit has two blocks of settings (the protocol's head, the
  run's config), two code fingerprints (the area stamp, the declared-scope
  digest) and two checks against what a run recorded; a weaker reader
  took the wrong one of each pair. "head" and "config", "scope" and
  "scope" do not tell a newcomer which is which. Name the members of such a
  pair in parallel, so the name shows what they share and where they part:
  `promised_constant_mismatches`, `criterion_n_input_mismatches`. A first
  docstring line may name the counterpart ("not the run's config.json") —
  only when there is a real neighbour a reader confuses it with; a "not X"
  on every function is noise.
- **One word, one thing — across functions, values and domain objects.**
  labkit's gate was a function `run`, the experiment's execution a "run",
  and its directory `run_dir`: the person read "no run" as "no protocol"
  (round 9). uniagent had `verdict` as a helper returning the verdict
  keys, an area function returning a draw's whole metrics, and a `bool`
  parameter; and `binding_case` as a function beside the field
  `PositionDesign.binding_case` holding a case id (2026-10-08, five of
  eight clarity findings). A word that names a domain object is not free
  for a function; a field is named like the key it is reported under
  (`new_false_rejection_cases` for the list, `new_false_rejections` for
  the count — not one name for both).
- **Error kinds and constants say what they mean** (`quote_rejected`,
  `UNSTABLE`): they are search hits too — the quote check was found through
  its error kind.
- A README code map is what a struggling reader turns to; it does not
  rescue names that do not separate, and a name that needs the map to be
  understood is the wrong name.

Self-check before you call a piece of code done:

- Can I find it with the words I would use for my question?
- Does its name say how it differs from its nearest neighbour?
- Does an entry point still hide a decision that has no name?
- Does this word already mean something else in the module or the domain?
- Can I use it from its name, types and first docstring line alone?
- Is there exactly one home for this question?

```python
# Bad — the check exists, but nothing names it; it is found only by reading main
def main(argv):
    ...
    left = sorted(
        {s for n, s in enumerate(lines, 1) if s in tmpl and n not in in_placeholder}
    )
    ...


# Good — one grep for "template" or "leftover" lands on it
def leftover_template_lines(text: str, template: set[str]) -> list[str]:
    """The lines of the empty form still in a filled protocol."""
```

---

## CS-016 · The experiment area reconstructs itself

Months later someone opens `experiments/NN-<name>/` to find out which run
produced the reported number, from which code and data, how to repeat it,
and where the criterion was decided. Measured on a real lab repo
(code-navigation round 11, 2026-10-08): agents found all of it at the
highest effort of any round; the lab's own author, a day after writing the
area with agents, got 1 of 8 such questions right in seven minutes. Every
fact existed; none stood where a person looks. The area has to say it:

- **Every registered key is set in the area script, beside its conjunct.**
  `evaluate_rows` in `01_measure.py` is the one place where `c2_pass`,
  `a1_pass`, `b1_branch` are literal strings, one line per conjunct with the
  protocol's sentence as the comment (`# [C2] no new false rejection on
  either subset`); the script head says so. Shared helpers under `src/` or
  `lib/` return typed results named for what they count
  (`LaneComparison.new_false_rejection_cases`, `ClassifierAgainstLabels.
  false_bound`) and never write a `c<n>_`, `a<n>_` or `b<n>_` key: a helper
  seven areas import cannot know whose `[C4]` it is, and the static check
  counts an id in shared code for every importing area (2026-10-08: a
  reader took `[C4] [C5] [A4]` in `src/measure/positions.py` for one
  protocol's intent; areas 10 and 13 were warned of ids they never
  registered). The verdict is one call on the area's own dicts
  (`verdict(criteria, admissibility)`). A chain of helpers that reuse one
  name across modules (`evaluate_rows` → `evaluate` → `evaluate_rows`) is
  one word for three things — CS-015 Make the code findable by name, across
  modules.
- **The data an area measures on lies under its `data/`**, as a symlink
  where the file is another area's (`data/prepared/check_cases_v2.jsonl →
  ../../../05-…/data/prepared/check_cases_v2.jsonl`), and the constant is
  named for what a person searches: `CASES_WITH_GOLD`, not `ITEMS`. A
  repo-relative path into another area's folder hides which data this is
  (round 11: the author picked the wrong file).
- **What a run is stands in one listing** (`runlog <area>`: fresh or
  derived from which run, committed or not, the command) — the lab tool's
  job; a script that bypasses `runlog` loses it.
- **The area README asserts nothing the stamp cannot know** and holds no
  unfilled placeholder: a stamped sentence "the formula stands in
  `01_measure.py`", left standing after the formula moved, misled more than
  no README. `finalize` warns on placeholders left in it.

---

## CS-017 · The entry point shows the flow and its conditions

The best way to understand code is to understand its control flow, and the
person who reviews agent-written code reads it in jumps, a minute per
question. Measured on labkit's `finalize` (code-navigation rounds 8 and 9,
2026-10-08): agents answered every behaviour question about the flow
right; the person 2 of 5 on each of two versions, and what the person
missed was always the same kind of thing — a skip whose condition sat one
level down, a condition behind a negated helper name, a stage numbering
that went 2, 2b, 3, and a word that meant three things. The third version
(flow plan in the docstring, every condition literal in the body, flat
numbers) was read right.

- **The entry point lists the stages and every condition under which a
  stage is skipped or the operation ends early.** Its docstring is the
  plan (0 … n, one line each); its body keeps the `if` that decides
  whether a stage runs — one level down is *how* a stage works, never
  *whether* it runs. "Does a failed check stop the rest?" and "what does
  the last line return?" are answered at the entry point, in words
  (`return report(findings)  # 1 if any step found a blocker, else 0`).
- **A condition stands literally where it decides.** `if head.cost !=
  "seconds":` is read right; `if not provenance_owed(head):` was read as
  its opposite (round 9). A named helper is for a search, not for a
  one-line condition — least of all a negated one. The same for a helper
  that changes the caller's result: when `verdict(criteria, admissibility)`
  turns every criterion to `None` because an admissibility condition
  failed, the line above the call says so; a reader asking "where is [C2]
  decided" otherwise answers from the line that computes it and is wrong
  whenever an [A] fails (2026-10-08).
- **Stages are numbered flat.** Section comments that name a stage carry a
  reader through a long entry point (CS-015 Make the code findable by
  name); sub-numbers (`2b`, `4b`) lost the person (round 8). A stage that
  needs a sub-number is a function of its own, and what it needs from an
  earlier stage is in its signature, not in state set far above
  (CS-006 One function, one thing — and keep I/O at the edges).
- **One word for one thing on the flow's path** — the function, its
  objects and its variables do not share a name (CS-015 Make the code
  findable by name).

Agents are no proxy for a person on these questions: in every round the
agents scored higher than the author on the author's own code. So the
check is a reader who did not write the code, with the questions the
person will ask — CS-008 Run the linter and the type checker, last
paragraph.

```python
# Bad — whether provenance runs is decided behind a negated name; the skip
# of step 7 sits inside check_area_tests
def run(area, protocol):
    gate = ...
    if not provenance_owed(head):
        check_provenance(gate, run_dir)
    check_area_tests(gate, area)  # returns early when no run exists
    return report(gate)


# Good — the plan in the docstring, every condition literal, the exit named
def finalize_area(area, protocol):
    """0 protocol · 1 newest completed run · 2 intent keys · 3 declared scopes ·
    4 open fields · 5 provenance (cost = seconds only) · 6 area tests (if a
    run exists) · 7 report."""
    ...
    if head.cost == "seconds":  # 5
        check_provenance(findings, measurement_run)
    if measurement_run is not None:  # 6
        check_area_tests(findings, area)
    return report(findings)  # 1 if any step found a blocker, else 0
```

---

## Where the *why* goes

Code can show what it does, never why it ended up that way. When you solve a
non-obvious problem — a bug that took a day, a surprising constraint, a design
decision with non-obvious trade-offs — write it down where the future reader
will look. They see the working code, but not the three approaches that failed
first.

Keep each entry short:

- **Problem:** what was hard, and why the obvious approach didn't work.
- **Solution:** the approach you landed on.
- **Notes:** any gotcha or constraint to watch out for later.

```markdown
## 2026-06-04 — Filter ordering between pre_filter and resume_filter

**Problem:** The two stages passed (flags, needs_judge) in opposite order,
causing a silent mismatch that the type checker didn't flag.

**Solution:** Introduced a frozen `FilterOutcome` dataclass so the values are
named and order no longer matters across the boundary.

**Notes:** Any new filter stage must consume/produce `FilterOutcome`, not a tuple.
```

One place per repo, not three: the protocol's "Notes" section while the problem
belongs to one task, a `NOTES.md` (or `docs/notes.md`) when it belongs to the
codebase, its own file in `notes/` once it outgrows either.

Only record what is worth remembering — this is not a changelog of every edit.
When you do add an entry, **say so in one line at the end of your reply**,
naming what you recorded, so the framing is easy to correct (e.g. "Added a
NOTES.md entry on the filter-ordering fix.").

---

## Self-check before you report a number

- [ ] No swallowed errors; bad input raises instead of being skipped, boundaries
      validate, and type conversions are deliberate rather than reflexive.
- [ ] Every choice that moves the number (averaging, thresholds, filtering) is
      a named parameter, and the choice is written in the protocol.
- [ ] `python3 -m pytest tests/` passes, and covers the parser/metric you used.
- [ ] No duplicated logic — shared code lives in the shared module.
- [ ] Data passed between functions keeps a consistent shape, or has names.
- [ ] Each function does one thing; side effects are isolated and documented.
- [ ] New/changed functions have type hints and a docstring.
- [ ] Names are the field's standard terms; a house coinage is at most a comment.
- [ ] Every threshold is a named constant whose comment says what it was
      optimised for, on which data, and when.
- [ ] Every ported method cites paper and original code in its docstring.
- [ ] `ruff check`, `ruff format`, `pyright` run clean — or an external cause
      was reported instead of endlessly refactored against.
- [ ] A cold reader (`code-clarity`) read the diff, and what it would answer
      wrong was fixed.
- [ ] The entry point lists its stages flat and keeps every skip and early
      end literal in its body; no condition hides behind a negated name;
      no word on the flow's path means two things.
- [ ] New packages were announced and are in `requirements.txt`.
- [ ] Relevant docs/README were read beforehand, and updated afterward if the
      change made them inaccurate.
- [ ] One real artifact went through the real path, the output was looked at
      (not just its exit code), leftovers were cleaned up, and anything still
      unverified is labelled where a reader will find it.
- [ ] Every check gated on its own exit code (`pipefail`); every "0 findings"
      preceded by a known positive; after a rename, every reader grepped.
- [ ] Code talking to a system you don't control has a fake, and the fake
      reproduces that system's known misbehavior, each quirk commented as real.
- [ ] Files handed to a watcher or another process are staged outside the
      watched location and renamed into place on the same filesystem.
- [ ] A dependency that broke or proved oversized was reconsidered, not just
      worked around — and a deliberately plain replacement says why it is plain.
- [ ] Any hard problem solved is captured where the *why* goes, and the entry is
      mentioned in the reply.
