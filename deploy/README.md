# Running the Phase 0 e2e suite at the lowest cost

The service has no LLM, so the only cost is compute. Pick the cheapest tier that gives you the confidence you need.

| Tier | What | Cost | Status |
|---|---|---|---|
| 0 | Your laptop | $0 | Tested |
| 1 | GitHub Actions (`.github/workflows/e2e.yml`) | $0 on public repos; about 1 min per run against 2,000 free min/month on private repos | YAML valid, not run on GitHub |
| 2 | AWS one-shot (`deploy/aws_oneshot.sh`): t4g.micro launches, runs everything, self-terminates | a few minutes of instance time, well under $0.01 | **Untested** (no AWS access when written) |
| 3 | AWS always-on t4g.micro running the Docker image, e2e run from your laptop against it | about $6/month on-demand in us-east-1, about $2-3 on spot, plus EBS | **Untested** |

Prices and free-tier terms were checked in search results on 2026-11-12 and change often. Confirm on aws.amazon.com/ec2/pricing before relying on them.
New AWS accounts get a credit-based free plan ($100 on signup plus up to $100 more, 6 months). Per one source, nano instance types are not allowed on the free plan, so use t4g.micro.

## Tier 0: local
    pip install -e ".[service,e2e]"
    pytest                                   # 28 contract + conformance tests
    E2E_HOOKS=1 uvicorn gradial_contracts.service.app:app --port 8000 &
    E2E_DESTRUCTIVE=1 pytest e2e -v          # 13 HTTP e2e tests

## Tier 1: GitHub Actions
Push to a repo; the workflow runs everything. Keep the repo public to pay nothing.

## Tier 2: AWS one-shot
    export REPO_URL=https://github.com/<you>/gradial-contracts.git   # must be public
    ./deploy/aws_oneshot.sh --dry-run        # checks permissions without launching
    ./deploy/aws_oneshot.sh                  # look for E2E_EXIT_CODE=0 at the end
No inbound ports, no SSH, no S3. Afterward confirm in the EC2 console that nothing is left running.
Set a $5 AWS Budget alert first.

## Tier 3: always-on (only if you need a URL others can hit)
    docker build -t gradial-p0 .
    docker run -d -p 8000:8000 -e API_KEY=<long-random> -e APPROVAL_SECRET=<long-random> -e E2E_HOOKS=1 gradial-p0
Restrict the security group's port 8000 to your own IP. Run from your laptop:
    BASE_URL=http://<ip>:8000 API_KEY=<same> pytest e2e -v
Do not set E2E_DESTRUCTIVE=1 here (it corrupts the audit chain until restart). State is in memory and resets on restart.
Turn E2E_HOOKS off for anything beyond testing; the `/_test/*` endpoints are for tests only.

## What this proves, and what it does not
Proves: the contracts hold across a real HTTP boundary (approval gating, version conflicts, idempotent intake, retries, scope denial, audit chain).
Does not prove: real CMS behaviour (the adapter is in-memory), LLM behaviour, durable storage, concurrency under load, or real auth.
Those are Phase 1 items; swapping in a real CMS adapter must pass `tests/adapter_conformance.py` unchanged.
