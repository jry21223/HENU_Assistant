# Frozen dependency locks

The plugin has one hash-verified POSIX lock for every supported Python minor:

- `py310.txt` through `py314.txt`: POSIX locks generated locally with
  pip-tools 7.6.0 under the matching CPython minor.

Every transitive requirement is exact and hashed. CI and supported installs use
`scripts/select_lockfile.py` and install the selected file with
`pip install --require-hashes`. Windows is outside the 2.1.0 support scope.

`langbot-plugin==0.5.0` is a direct dependency in all five runtime locks. Those
locks never contain the incompatible legacy `lbp` distribution. The separate
`lbp-py313.txt` lock contains `lbp==0.1.2` and its exact
`langbot-plugin==0.1.1b1` dependency for ZIP construction only. Release builds
run that CLI in `.lbp-build-venv`, then verify the artifact in the modern
runtime environment.

`langbot-plugin==0.5.0` requires `pip>=25.2`; therefore runtime lock generation
uses `--allow-unsafe` so pip itself is exact and hash-verified. Fresh installs
must not rely on whatever pip version happened to ship with `ensurepip`.

## Regeneration

Generate a POSIX lock on the local POSIX host with the matching interpreter:

```bash
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL= \
python3.13 -m piptools compile --generate-hashes \
  --resolver=backtracking --strip-extras --allow-unsafe \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file=requirements-lock/py313.txt requirements-dev.txt
```

The pip-tools generation environment pins `pip==25.3` and
`pip-tools==7.6.0`.

Generate the isolated release builder with Python 3.13:

```bash
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL= \
python3.13 -m piptools compile --generate-hashes \
  --resolver=backtracking --strip-extras --allow-unsafe \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file=requirements-lock/lbp-py313.txt requirements-build.txt
```
