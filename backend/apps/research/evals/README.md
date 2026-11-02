# Golden evaluation set

Labelled cases for the qualification step, one JSON file per case in `cases/`. Each holds a
campaign's rules, a company's facts and dated signals, the model's answer, and the expected
outcome (ICP fit, recommendation, trigger). Run it with `python manage.py run_evals`.

All examples are invented. Do not add real client data, real company records or keys here.

Everything about how it works, how to add a case, how to run it against a live model and what to
do after a prompt change is in [docs/research/evaluation.md](../../../../docs/research/evaluation.md).
