"""M3 benchmark corpus — 30 paired old/new cases across 7 languages.

Each case is a dict with:
- ``id``: stable identifier (used in expected.json mapping)
- ``language``: matches ``code_locator.indexing.symbol_extractor._LANG_PACKAGE_MAP``
- ``old``: pre-change source body
- ``new``: post-change source body
- ``expected``: ``cosmetic`` | ``semantic`` | ``uncertain`` — the
  classifier verdict the corpus expects.

Coverage (per audit v2 §F5):
  Python (12): 4 cosmetic + 4 semantic + 4 uncertain
  JavaScript (3): cosmetic + semantic + uncertain
  TypeScript (3): cosmetic + semantic + uncertain
  Go (3): cosmetic + semantic + uncertain
  Rust (3): cosmetic + semantic + uncertain
  Java (3): cosmetic + semantic + uncertain
  C# (3): cosmetic + semantic + uncertain
  Total = 30
"""

from __future__ import annotations

CASES: list[dict] = [
    # ── Python: 4 cosmetic ─────────────────────────────────────────
    {
        "id": "py_01_docstring_added", "language": "python",
        "expected": "cosmetic",
        "old": "def fetch(uid):\n    return db.lookup(uid)\n",
        "new": (
            "def fetch(uid):\n"
            '    """Fetch a user by uid."""\n'
            "    return db.lookup(uid)\n"
        ),
    },
    {
        "id": "py_02_imports_reordered", "language": "python",
        "expected": "cosmetic",
        "old": (
            "import os\nimport sys\nimport json\n\n"
            "def f(): return os.getcwd()\n"
        ),
        "new": (
            "import json\nimport os\nimport sys\n\n"
            "def f(): return os.getcwd()\n"
        ),
    },
    {
        "id": "py_03_blank_lines_added", "language": "python",
        "expected": "cosmetic",
        "old": "def f():\n    a = 1\n    b = 2\n    return a + b\n",
        "new": (
            "def f():\n\n    a = 1\n\n    b = 2\n\n    return a + b\n"
        ),
    },
    {
        "id": "py_04_comments_added", "language": "python",
        "expected": "cosmetic",
        "old": "def f(x):\n    return x * 2\n",
        "new": (
            "def f(x):\n"
            "    # double the input\n"
            "    return x * 2\n"
        ),
    },
    # ── Python: 4 semantic ──────────────────────────────────────────
    {
        "id": "py_05_logic_removed", "language": "python",
        "expected": "semantic",
        "old": (
            "def f(x):\n"
            "    if x > 0:\n"
            "        return x * 2\n"
            "    if x < 0:\n"
            "        return -x\n"
            "    return 0\n"
        ),
        "new": "def f(x):\n    return x\n",
    },
    {
        "id": "py_06_signature_changed", "language": "python",
        "expected": "semantic",
        "old": "def f(x):\n    return x\n",
        "new": "def f(x, y, z):\n    return x + y + z\n",
    },
    {
        "id": "py_07_new_function_call", "language": "python",
        "expected": "semantic",
        "old": (
            "def f(x):\n"
            "    return x + 1\n"
        ),
        "new": (
            "def f(x):\n"
            "    log_event(x)\n"
            "    audit_trail.record(x)\n"
            "    metrics.increment('f.calls')\n"
            "    return x + 1\n"
        ),
    },
    {
        "id": "py_08_branching_added", "language": "python",
        "expected": "semantic",
        "old": (
            "def process(x):\n"
            "    return transform(x)\n"
        ),
        "new": (
            "def process(x):\n"
            "    if x is None:\n"
            "        raise ValueError('null input')\n"
            "    if isinstance(x, dict):\n"
            "        return process_dict(x)\n"
            "    if isinstance(x, list):\n"
            "        return [transform(i) for i in x]\n"
            "    return transform(x)\n"
        ),
    },
    # ── Python: 4 uncertain ─────────────────────────────────────────
    {
        "id": "py_09_typing_annotation_added", "language": "python",
        "expected": "uncertain",
        "old": "def f(x):\n    return x + 1\n",
        "new": "def f(x: int) -> int:\n    return x + 1\n",
    },
    {
        "id": "py_10_variable_rename_only", "language": "python",
        "expected": "uncertain",
        "old": (
            "def f(item):\n"
            "    result = item * 2\n"
            "    return result\n"
        ),
        "new": (
            "def f(value):\n"
            "    doubled = value * 2\n"
            "    return doubled\n"
        ),
    },
    {
        "id": "py_11_assertion_text_changed", "language": "python",
        "expected": "uncertain",
        "old": (
            "def validate(x):\n"
            "    assert x > 0, 'must be positive'\n"
            "    return x\n"
        ),
        "new": (
            "def validate(x):\n"
            "    assert x > 0, 'value must be greater than zero'\n"
            "    return x\n"
        ),
    },
    {
        "id": "py_12_constant_value_tuned", "language": "python",
        "expected": "uncertain",
        "old": "DISCOUNT = 0.10\ndef apply(p): return p * (1 - DISCOUNT)\n",
        "new": "DISCOUNT = 0.15\ndef apply(p): return p * (1 - DISCOUNT)\n",
    },
    # ── JavaScript: 1 cosmetic + 1 semantic + 1 uncertain ───────────
    {
        "id": "js_01_jsdoc_added", "language": "javascript",
        "expected": "cosmetic",
        "old": "function add(x, y) {\n    return x + y;\n}\n",
        "new": (
            "/** Add two numbers. */\n"
            "function add(x, y) {\n"
            "    return x + y;\n"
            "}\n"
        ),
    },
    {
        "id": "js_02_logic_removed", "language": "javascript",
        "expected": "semantic",
        "old": (
            "function process(x) {\n"
            "    if (x === null) return 0;\n"
            "    if (x < 0) return -x;\n"
            "    return x * 2;\n"
            "}\n"
        ),
        "new": "function process(x) {\n    return x;\n}\n",
    },
    {
        "id": "js_03_default_arg_changed", "language": "javascript",
        "expected": "uncertain",
        "old": "function f(x = 10) {\n    return x;\n}\n",
        "new": "function f(x = 20) {\n    return x;\n}\n",
    },
    # ── TypeScript: 1 cosmetic + 1 semantic + 1 uncertain ───────────
    {
        "id": "ts_01_type_annotation_only", "language": "typescript",
        "expected": "cosmetic",
        "old": "function f(x) {\n    return x + 1;\n}\n",
        "new": "function f(x: number): number {\n    return x + 1;\n}\n",
    },
    {
        "id": "ts_02_signature_changed", "language": "typescript",
        "expected": "semantic",
        "old": "function f(x: number): number {\n    return x;\n}\n",
        "new": (
            "function f<T>(x: T, options: { multiplier: number }): T {\n"
            "    return apply(x, options.multiplier);\n"
            "}\n"
        ),
    },
    {
        "id": "ts_03_generic_constraint_added", "language": "typescript",
        "expected": "uncertain",
        "old": "function wrap<T>(x: T): T[] { return [x]; }\n",
        "new": (
            "function wrap<T extends object>(x: T): T[] { return [x]; }\n"
        ),
    },
    # ── Go: 1 cosmetic + 1 semantic + 1 uncertain ───────────────────
    {
        "id": "go_01_block_comment_added", "language": "go",
        "expected": "cosmetic",
        "old": (
            "func Add(x, y int) int {\n"
            "    return x + y\n"
            "}\n"
        ),
        "new": (
            "// Add adds two ints.\n"
            "func Add(x, y int) int {\n"
            "    return x + y\n"
            "}\n"
        ),
    },
    {
        "id": "go_02_logic_removed", "language": "go",
        "expected": "semantic",
        "old": (
            "func Process(x int) int {\n"
            "    if x < 0 {\n"
            "        return -x\n"
            "    }\n"
            "    return Transform(x)\n"
            "}\n"
        ),
        "new": "func Process(x int) int {\n    return x\n}\n",
    },
    {
        "id": "go_03_error_string_reworded", "language": "go",
        "expected": "uncertain",
        "old": (
            'func F(x int) error {\n'
            '    if x < 0 {\n'
            '        return errors.New("input must be non-negative")\n'
            '    }\n'
            '    return nil\n'
            '}\n'
        ),
        "new": (
            'func F(x int) error {\n'
            '    if x < 0 {\n'
            '        return errors.New("x cannot be less than zero")\n'
            '    }\n'
            '    return nil\n'
            '}\n'
        ),
    },
    # ── Rust: 1 cosmetic + 1 semantic + 1 uncertain ─────────────────
    {
        "id": "rs_01_doc_comment_added", "language": "rust",
        "expected": "cosmetic",
        "old": "fn add_one(x: i32) -> i32 {\n    x + 1\n}\n",
        "new": (
            "/// Add one to the input.\n"
            "fn add_one(x: i32) -> i32 {\n"
            "    x + 1\n"
            "}\n"
        ),
    },
    {
        "id": "rs_02_signature_changed", "language": "rust",
        "expected": "semantic",
        "old": "fn process(x: i32) -> i32 { x + 1 }\n",
        "new": (
            "fn process<T: Add<Output = T> + Copy>(x: T, n: T) -> T {\n"
            "    let mut acc = x;\n"
            "    for _ in 0..10 { acc = acc + n; }\n"
            "    acc\n"
            "}\n"
        ),
    },
    {
        "id": "rs_03_lifetime_annotation_added", "language": "rust",
        "expected": "uncertain",
        "old": "fn longest(x: &str, y: &str) -> &str {\n    x\n}\n",
        "new": (
            "fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {\n"
            "    x\n"
            "}\n"
        ),
    },
    # ── Java: 1 cosmetic + 1 semantic + 1 uncertain ─────────────────
    {
        "id": "java_01_javadoc_added", "language": "java",
        "expected": "cosmetic",
        "old": "class D {\n    int f(int x) { return x + 1; }\n}\n",
        "new": (
            "class D {\n"
            "    /** Adds one. */\n"
            "    int f(int x) { return x + 1; }\n"
            "}\n"
        ),
    },
    {
        "id": "java_02_logic_removed", "language": "java",
        "expected": "semantic",
        "old": (
            "class D {\n"
            "    int process(int x) {\n"
            "        if (x < 0) return -x;\n"
            "        if (x == 0) throw new IllegalArgumentException();\n"
            "        return transform(x);\n"
            "    }\n"
            "}\n"
        ),
        "new": (
            "class D {\n"
            "    int process(int x) {\n"
            "        return x;\n"
            "    }\n"
            "}\n"
        ),
    },
    {
        "id": "java_03_throws_clause_added", "language": "java",
        "expected": "uncertain",
        "old": (
            "class D {\n"
            "    int f(int x) { return x + 1; }\n"
            "}\n"
        ),
        "new": (
            "class D {\n"
            "    int f(int x) throws IOException { return x + 1; }\n"
            "}\n"
        ),
    },
    # ── C#: 1 cosmetic + 1 semantic + 1 uncertain ───────────────────
    {
        "id": "cs_01_xml_doc_added", "language": "c_sharp",
        "expected": "cosmetic",
        "old": (
            "class Demo {\n"
            "    int F(int x) { return x + 1; }\n"
            "}\n"
        ),
        "new": (
            "class Demo {\n"
            "    /// <summary>F adds one.</summary>\n"
            "    int F(int x) { return x + 1; }\n"
            "}\n"
        ),
    },
    {
        "id": "cs_02_signature_changed", "language": "c_sharp",
        "expected": "semantic",
        "old": (
            "class Demo {\n"
            "    int F(int x) { return x; }\n"
            "}\n"
        ),
        "new": (
            "class Demo {\n"
            "    public async Task<T> F<T>(T x, CancellationToken ct = default) {\n"
            "        await Task.Delay(10, ct);\n"
            "        return x;\n"
            "    }\n"
            "}\n"
        ),
    },
    {
        "id": "cs_03_async_modifier_added", "language": "c_sharp",
        "expected": "uncertain",
        "old": (
            "class Demo {\n"
            "    Task<int> F(int x) { return Task.FromResult(x + 1); }\n"
            "}\n"
        ),
        "new": (
            "class Demo {\n"
            "    async Task<int> F(int x) { return await Task.FromResult(x + 1); }\n"
            "}\n"
        ),
    },
]
