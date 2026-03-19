import os

root = os.getcwd()
skip = {"tests", "test", "scripts", "docs", "deployment", "data"}
for d, _, files in os.walk(root):
    if "__init__.py" not in files and any(f.endswith(".py") for f in files):
        base = os.path.basename(d).lower()
        if base not in skip:
            init_path = os.path.join(d, "__init__.py")
            with open(init_path, "w") as f:
                f.write("# auto-added\n")
            print(f"Added {init_path}")
