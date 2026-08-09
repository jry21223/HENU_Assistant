# Frozen dependency locks

The application has one hash-verified POSIX lock for every supported Python
minor:

- `py310.txt` through `py314.txt`: POSIX locks generated locally with
  pip-tools 7.6.0 under the matching CPython minor.

Every transitive requirement is exact and hashed. CI and supported installs use
`scripts/select_lockfile.py` and install the selected file with
`pip install --require-hashes`. Windows is outside the 2.1.0 support scope.

## Regeneration

Generate a POSIX lock on the local POSIX host with the matching interpreter:

```bash
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL= \
python3.11 -m piptools compile --generate-hashes \
  --resolver=backtracking --strip-extras \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file=requirements-lock/py311.txt requirements.txt
```

The pip-tools generation environment pins `pip==25.3` and
`pip-tools==7.6.0`.
