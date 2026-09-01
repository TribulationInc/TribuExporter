# Contributing

Contributions are welcome, especially small synthetic Fusion fixtures and TCN
semantic tests that represent real plywood-panel geometry.

Please keep these boundaries:

- Export geometry; leave CAM and technology to TpaCAD.
- Preserve exact lines and circular primitives.
- Never silently relax a curve tolerance or repair/move geometry.
- Fail or localize unsupported regions instead of guessing intent.
- Keep Fusion extraction, neutral IR and TCN serialization separate.
- Add a synthetic regression test for each serializer change.

Do not submit proprietary TpaCAD manuals, installed product samples, Busellato
macros, private machine programs, production exports or customer geometry.
Those resources may inform independent implementation, but cannot be copied into
this public repository.

Before opening a change, run:

```powershell
python -m unittest discover -v
```

