# Three-minute judge path

1. Watch the four-minute final video or the 44-second actual skill execution clip (paths in README).
2. Open the nine-slide RSI deck, then docs/SKILLS-SUPPORT.md.
3. Inspect historical main-run receipt/verification/diff and the new skill's capture.json. Actual stdout, run IDs and deployed artifact hashes connect the claim to evidence.
4. Check claims.yaml against the shipped result JSON files. Model versions, bands and test families are distinct.
5. Run `python tools/verify_clean_export.py .` in the extracted package. This suite needs Python dependencies, no parent checkout, models or targets.

The complete live loop requires the operator's separate private backend and authorized lab. This review package does not include credentials, full target source or an executable payload library. The evidence text was scrubbed; signed NVIDIA verification is not claimed.
