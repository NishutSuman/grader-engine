import json, sys
from app.db.session import SessionLocal
from app.db import repo
from grader import consistency

SLUG = "iitp-aiml-2506-78137"
model   = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None
sample  = int(sys.argv[2]) if len(sys.argv) > 2 else 5
repeats = int(sys.argv[3]) if len(sys.argv) > 3 else 9
with SessionLocal() as s:
    eid = repo.get_eval(s, SLUG).id
rep = consistency.measure(eid, sample=sample, repeats=repeats, model=model, workers=8)
consistency.print_report(rep)
fn = f"scratch_cons_{(model or 'default').replace('-','_')}.json"
with open(fn, "w") as f:
    json.dump(rep, f, indent=2)
print("saved ->", fn)
