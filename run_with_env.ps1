$env:PYTHONPATH = "$PWD;$env:PYTHONPATH"
if ($args.Count -eq 0) {
    python --version
} else {
    & python @args
}
