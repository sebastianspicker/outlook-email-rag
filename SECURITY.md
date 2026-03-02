# Security Policy

## Supported Versions

Security fixes are applied to the latest development line.

| Version | Supported |
| --- | --- |
| `0.1.x` | Yes |
| `< 0.1.0` | No |

## Reporting a Vulnerability

Please do not report security vulnerabilities in public issues.

1. Open a private GitHub Security Advisory report for this repository.
2. Include:
   1. Affected version/commit.
   2. Reproduction steps.
   3. Impact assessment.
   4. Suggested mitigation (if available).
3. You will receive an initial triage response as soon as practical.

## Response Process

1. Triage and severity classification.
2. Reproduction and scope validation.
3. Patch development and review.
4. Coordinated disclosure with release notes.

## Hardening Baseline

The repository maintains these baseline checks:

1. `bandit -r src -q`
2. `python -m pip_audit -r requirements.txt`
3. Input validation and output sanitization for untrusted email content paths.
