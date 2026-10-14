import pathlib

rows = sorted(
    (
        (len(p.read_text(encoding="utf-8").splitlines()), str(p).replace("\\", "/"))
        for p in pathlib.Path("src").rglob("*.py")
    ),
    reverse=True,
)
print("\n".join(f"{n:5d}  {f}" for n, f in rows))
print("TOTAL src", sum(n for n, _ in rows))

trows = sorted(
    (
        (len(p.read_text(encoding="utf-8").splitlines()), str(p).replace("\\", "/"))
        for p in pathlib.Path("tests").rglob("*.py")
    ),
    reverse=True,
)
print("\n--- tests ---")
print("\n".join(f"{n:5d}  {f}" for n, f in trows))
print("TOTAL tests", sum(n for n, _ in trows))
